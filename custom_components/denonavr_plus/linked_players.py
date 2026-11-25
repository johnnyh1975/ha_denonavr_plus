"""Linked media players: the device on an input, such as an Apple TV.

While a zone plays an input with a linked player, the zone's media player
shows that player's title, artwork, position and state, and its play, pause,
skip, seek, shuffle and repeat go to it; power, volume and input stay the
receiver's (media_player.py). Per input, the receiver can also

- follow the player: when it starts playing, the main zone turns on and
  switches to its input, and the player of the input it leaves is paused;
- keep the player at full volume, since the volume is set on the receiver.

A linked player that is renamed is followed; one that is removed raises a
repair that unlinks it. The idea and its details (a cooldown against two
players taking turns, a short drop is no new start, renames are followed)
come from the Rotel integration (1orgar/ha_rotel_amp).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_VOLUME_LEVEL,
    ATTR_MEDIA_VOLUME_MUTED,
    DOMAIN as MEDIA_PLAYER,
    SERVICE_SELECT_SOURCE,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_MUTE,
    SERVICE_VOLUME_SET,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    CoreState,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.start import async_at_started

from . import choices
from .avr.device import FOLLOW_MAIN
from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Option: per input code, {"player": entity id, "follow": bool, "full_volume": bool}.
OPT_LINKED_PLAYERS = "linked_players"
KEY_PLAYER = "player"
KEY_FOLLOW = "follow"
KEY_FULL_VOLUME = "full_volume"
#: Option: a zone on an input with a signal is "playing", without one "idle"
#: (instead of "on"). Off by default: not yet tried on a receiver.
OPT_SIGNAL_STATE = "signal_state"

ISSUE = "linked_player_missing"

#: A second player starting this soon after the receiver followed another
#: is paused instead of followed, so two players cannot take turns.
FOLLOW_COOLDOWN = 5.0
#: Full volume as players report it (some round 1.0 down).
FULL_LEVEL = 0.999
#: The zone whose input follows a player.
FOLLOW_ZONE = "main"

#: The states a player is busy in; a pause stops it, else a stop.
_BUSY = (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING)
_GONE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


@dataclass(frozen=True)
class Link:
    """The player linked to one input, and what the receiver does with it."""

    entity_id: str
    follow: bool = False
    full_volume: bool = False

    def stored(self) -> dict[str, Any]:
        return {
            KEY_PLAYER: self.entity_id,
            KEY_FOLLOW: self.follow,
            KEY_FULL_VOLUME: self.full_volume,
        }


def links(options: dict[str, Any]) -> dict[str, Link]:
    """The linked players by input code; entries without a player are left out."""

    stored = options.get(OPT_LINKED_PLAYERS)
    if not isinstance(stored, dict):
        return {}
    found: dict[str, Link] = {}
    for code, value in stored.items():
        if not isinstance(value, dict) or not value.get(KEY_PLAYER):
            continue
        found[str(code)] = Link(
            str(value[KEY_PLAYER]),
            bool(value.get(KEY_FOLLOW, False)),
            bool(value.get(KEY_FULL_VOLUME, False)),
        )
    return found


def stored_links(options: dict[str, Any]) -> dict[str, Any]:
    """The links as they are stored, without entries that hold no player."""

    return {key: value.stored() for key, value in links(options).items()}


def with_link(options: dict[str, Any], code: str, link: Link | None) -> dict[str, Any]:
    """The stored links with one input linked, changed or (None) unlinked."""

    stored = stored_links(options)
    if link is None:
        stored.pop(code, None)
    else:
        stored[code] = link.stored()
    return stored


def issue_id(entry_id: str) -> str:
    return f"{ISSUE}_{entry_id}"


def is_busy(state: State | None) -> bool:
    return state is not None and state.state in _BUSY


class LinkedPlayers:
    """Follows the linked players of one receiver: renames, removal, follow, volume."""

    def __init__(self, hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._tracked: frozenset[str] = frozenset()
        self._unsub_states: CALLBACK_TYPE | None = None
        # The last state other than unavailable: playing, a short drop, then
        # playing again is no new start.
        self._last_real: dict[str, str] = {}
        self._followed_at = 0.0
        self._followed: str | None = None
        self._full_volume_active: frozenset[str] = frozenset()

    # Setup -----------------------------------------------------------------

    @callback
    def async_start(self) -> Callable[[], None]:
        """Start listening; returns what stops it again."""

        unsubs: list[CALLBACK_TYPE] = [
            self._hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated),
            self._entry.runtime_data.async_add_listener(self._options_maybe_changed),
        ]
        self._track()
        self._refresh_full_volume()

        # Other integrations add their players while Home Assistant starts.
        @callback
        def started(_hass: HomeAssistant) -> None:
            self.async_check_missing()

        unsubs.append(async_at_started(self._hass, started))

        def stop() -> None:
            for unsub in unsubs:
                unsub()
            if self._unsub_states is not None:
                self._unsub_states()
                self._unsub_states = None

        return stop

    @property
    def _links(self) -> dict[str, Link]:
        return links(dict(self._entry.options))

    @callback
    def _options_maybe_changed(self) -> None:
        """Options are applied through a coordinator update: track the new players."""

        if self._track() and self._hass.state is CoreState.running:
            self.async_check_missing()
        self._refresh_full_volume()

    @callback
    def _track(self) -> bool:
        """Listen to the linked players; whether they changed."""

        players = frozenset(link.entity_id for link in self._links.values())
        if players == self._tracked:
            return False
        if self._unsub_states is not None:
            self._unsub_states()
            self._unsub_states = None
        self._tracked = players
        if players:
            self._unsub_states = async_track_state_change_event(
                self._hass, sorted(players), self._state_changed
            )
        for entity_id in players:
            self._remember(entity_id, self._hass.states.get(entity_id))
        return True

    # Renamed or removed players --------------------------------------------

    @callback
    def _registry_updated(self, event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        data = event.data
        current = self._links
        players = {link.entity_id for link in current.values()}
        if data["action"] == "update" and data.get("old_entity_id") in players:
            old, new = data["old_entity_id"], data["entity_id"]
            _LOGGER.info("Linked player %s is now %s", old, new)
            renamed = {
                code: (Link(new, link.follow, link.full_volume) if link.entity_id == old else link)
                for code, link in current.items()
            }
            self._hass.config_entries.async_update_entry(
                self._entry,
                options={
                    **self._entry.options,
                    OPT_LINKED_PLAYERS: {code: link.stored() for code, link in renamed.items()},
                },
            )
            return
        if data["action"] in ("create", "remove") and data["entity_id"] in players:
            self.async_check_missing()

    def _missing(self) -> dict[str, Link]:
        registry = er.async_get(self._hass)
        return {
            code: link
            for code, link in self._links.items()
            if registry.async_get(link.entity_id) is None
            and self._hass.states.get(link.entity_id) is None
        }

    @callback
    def async_check_missing(self) -> None:
        """A repair while a linked player no longer exists."""

        missing = self._missing()
        if not missing:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id(self._entry.entry_id))
            return
        discovery = self._entry.runtime_data.device.discovery
        options = dict(self._entry.options)
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id(self._entry.entry_id),
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE,
            translation_placeholders={
                "title": self._entry.title,
                "players": ", ".join(
                    f"{choices.source_display(discovery, options, code)} → {link.entity_id}"
                    for code, link in missing.items()
                ),
            },
            data={"entry_id": self._entry.entry_id},
        )

    # Player changes --------------------------------------------------------

    def _remember(self, entity_id: str, state: State | None) -> None:
        if state is not None and state.state not in _GONE:
            self._last_real[entity_id] = state.state

    def _full_volume_now(self) -> frozenset[str]:
        """The players kept at full volume now: their input plays in a zone that is on.

        Not otherwise: a television linked to an input may play on its own
        speakers while the receiver is off.
        """

        return frozenset(
            link.entity_id
            for code, link in self._links.items()
            if link.full_volume and self._zone_playing(code)
        )

    @callback
    def _refresh_full_volume(self) -> None:
        """Full volume for a player whose input just started (or that just got the option)."""

        now = self._full_volume_now()
        for entity_id in now - self._full_volume_active:
            self._keep_full_volume(self._hass.states.get(entity_id), None)
        self._full_volume_active = now

    @callback
    def _state_changed(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data["entity_id"]
        new = event.data["new_state"]
        before = self._last_real.get(entity_id)
        self._remember(entity_id, new)
        if entity_id in self._full_volume_active:
            self._keep_full_volume(new, event.data["old_state"])
        if new is None or new.state != MediaPlayerState.PLAYING:
            return
        if event.data["old_state"] is None or before in (None, MediaPlayerState.PLAYING):
            # The player just appeared or came up playing (Home Assistant
            # starting), is back after a short drop, or only the title changed.
            return
        code = next(
            (code for code, link in self._links.items() if link.entity_id == entity_id and link.follow),
            None,
        )
        if code is not None:
            self._follow(code, entity_id, event.context)

    # Full volume -----------------------------------------------------------

    @callback
    def _keep_full_volume(self, state: State | None, old: State | None) -> None:
        """Full level and unmuted: the volume is set on the receiver.

        Only when the volume or mute changed (or the player just came), so a
        player that refuses does not get the same command at every update.
        """

        if state is None or state.state in (*_GONE, MediaPlayerState.OFF):
            return
        level = state.attributes.get(ATTR_MEDIA_VOLUME_LEVEL)
        muted = state.attributes.get(ATTR_MEDIA_VOLUME_MUTED)
        if old is not None and old.state not in (*_GONE, MediaPlayerState.OFF):
            if (
                old.attributes.get(ATTR_MEDIA_VOLUME_LEVEL) == level
                and old.attributes.get(ATTR_MEDIA_VOLUME_MUTED) == muted
            ):
                return
        features = MediaPlayerEntityFeature(int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)))
        if (
            features & MediaPlayerEntityFeature.VOLUME_SET
            and isinstance(level, (int, float))
            and level < FULL_LEVEL
        ):
            self._call_later(state.entity_id, SERVICE_VOLUME_SET, {ATTR_MEDIA_VOLUME_LEVEL: 1.0})
        if features & MediaPlayerEntityFeature.VOLUME_MUTE and muted:
            self._call_later(state.entity_id, SERVICE_VOLUME_MUTE, {ATTR_MEDIA_VOLUME_MUTED: False})

    @callback
    def _call_later(
        self,
        entity_id: str,
        service: str,
        data: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> None:
        self._entry.async_create_background_task(
            self._hass,
            self._async_call(entity_id, service, data, context),
            f"denonavr_plus {service} {entity_id}",
        )

    async def _async_call(
        self,
        entity_id: str,
        service: str,
        data: dict[str, Any] | None = None,
        context: Context | None = None,
    ) -> bool:
        """A media player action; a player that refuses is logged, not raised."""

        try:
            await self._hass.services.async_call(
                MEDIA_PLAYER,
                service,
                {**(data or {}), ATTR_ENTITY_ID: entity_id},
                blocking=True,
                context=context,
            )
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.warning("%s on %s did not work: %s", service, entity_id, err)
            return False
        return True

    # Following a player ------------------------------------------------------

    def _zone_playing(self, code: str, *, besides: str | None = None) -> bool:
        """Whether a shown zone is on and plays the input.

        `besides`: leave out that zone and the zones that follow it (Zone 2
        on the main zone's source moves along with it).
        """

        coordinator = self._entry.runtime_data
        data = coordinator.data
        return any(
            data.zone(zone).power and coordinator.device.zone_source(zone) == code
            for zone in coordinator.shown_zones
            if zone != besides
            and not (besides == FOLLOW_ZONE and data.zone(zone).source == FOLLOW_MAIN)
        )

    @callback
    def _follow(self, code: str, entity_id: str, context: Context) -> None:
        if self._zone_playing(code):
            return
        now = time.monotonic()
        if (
            self._followed is not None
            and self._followed != code
            and now - self._followed_at < FOLLOW_COOLDOWN
        ):
            _LOGGER.info(
                "%s started right after the receiver followed another player; pausing it",
                entity_id,
            )
            self._pause_later(entity_id, context)
            return
        self._followed, self._followed_at = code, now
        self._entry.async_create_background_task(
            self._hass, self._async_follow(code, context), f"denonavr_plus follow {entity_id}"
        )

    def _main_player(self) -> str | None:
        registry = er.async_get(self._hass)
        return next(
            (
                item.entity_id
                for item in er.async_entries_for_config_entry(registry, self._entry.entry_id)
                if item.domain == MEDIA_PLAYER and item.unique_id.endswith(f"_media_player_{FOLLOW_ZONE}")
            ),
            None,
        )

    async def _async_follow(self, code: str, context: Context) -> None:
        """The main zone on and to the input, then the player it left paused.

        Through the zone's own action, so the turn-on volume and the volume
        limit apply. The context is the player's: the logbook shows the
        receiver's change as caused by it.
        """

        coordinator = self._entry.runtime_data
        target = self._main_player()
        state = self._hass.states.get(target) if target is not None else None
        if target is None or state is None or state.state == STATE_UNAVAILABLE:
            # Home Assistant would skip the action without a word, and the
            # player of the input left would be paused for nothing.
            _LOGGER.info("The main zone is unavailable; not following the player")
            return
        main = coordinator.data.zone(FOLLOW_ZONE)
        left = coordinator.device.zone_source(FOLLOW_ZONE) if main.power else None
        name = choices.source_display(coordinator.device.discovery, dict(self._entry.options), code)
        _LOGGER.debug("Following the player of %s", name)
        if not await self._async_call(target, SERVICE_SELECT_SOURCE, {ATTR_INPUT_SOURCE: name}, context):
            return
        if left is None or left == code:
            return
        previous_code = left
        previous = self._links.get(previous_code)
        followed = self._links.get(code)
        if previous is None or followed is None or previous.entity_id == followed.entity_id:
            # Unlinked meanwhile, or one player on both inputs.
            return
        if self._zone_playing(previous_code, besides=FOLLOW_ZONE):
            # Another zone still plays it.
            return
        if is_busy(self._hass.states.get(previous.entity_id)):
            self._pause_later(previous.entity_id, context)

    @callback
    def _pause_later(self, entity_id: str, context: Context) -> None:
        state = self._hass.states.get(entity_id)
        features = MediaPlayerEntityFeature(
            int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)) if state else 0
        )
        if features & MediaPlayerEntityFeature.PAUSE:
            self._call_later(entity_id, SERVICE_MEDIA_PAUSE, context=context)
        elif features & MediaPlayerEntityFeature.STOP:
            self._call_later(entity_id, SERVICE_MEDIA_STOP, context=context)


class LinkedPlayerRepairFlow(ConfirmRepairFlow):
    """The repair "linked player not found": unlinks the players that are gone."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is None:
            return await super().async_step_confirm()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return self.async_create_entry(data={})
        registry = er.async_get(self.hass)
        kept = {
            code: link.stored()
            for code, link in links(dict(entry.options)).items()
            if registry.async_get(link.entity_id) is not None
            or self.hass.states.get(link.entity_id) is not None
        }
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, OPT_LINKED_PLAYERS: kept}
        )
        ir.async_delete_issue(self.hass, DOMAIN, issue_id(self._entry_id))
        return self.async_create_entry(data={})
