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
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
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
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
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
from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry

_LOGGER = logging.getLogger(__name__)

#: Option: per input code, {"player": entity id, "follow": bool, "full_volume": bool}.
OPT_LINKED_PLAYERS = "linked_players"
KEY_PLAYER = "player"
KEY_FOLLOW = "follow"
KEY_FULL_VOLUME = "full_volume"
#: Switch the player on when a zone switches to its input, and off when the
#: zones that played it are switched off.
KEY_POWER = "power"
#: Option: a zone on an input with a signal is "playing", without one "idle"
#: (instead of "on"). Off by default: not yet tried on a receiver.
OPT_SIGNAL_STATE = "signal_state"
#: Option: switching a zone off from Home Assistant pauses the linked player
#: of the input it played, unless another zone still plays that input.
#: On by default (missing means on).
OPT_PAUSE_ON_OFF = "pause_on_off"

ISSUE = "linked_player_missing"
#: Fired when the main zone followed a player: entity_id (the main zone's
#: player), source, previous_source (None when it was off), player.
EVENT_INPUT_FOLLOWED = f"{DOMAIN}_input_followed"

#: A second player starting this soon after the receiver followed another
#: is paused instead of followed, so two players cannot take turns.
FOLLOW_COOLDOWN = 5.0
#: Full volume as players report it (some round 1.0 down).
FULL_LEVEL = 0.999
#: The zone whose input follows a player.
FOLLOW_ZONE = "main"
#: How long a follow may take to reach its input (power on, settle, switch).
FOLLOW_SETTLE = 30.0
#: The main zone's input before the receiver has reported it.
UNKNOWN = "?"

#: The states a player is busy in; a pause stops it, else a stop.
_BUSY = (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING)
_GONE = (STATE_UNAVAILABLE, STATE_UNKNOWN)


@dataclass(frozen=True)
class Link:
    """The player linked to one input, and what the receiver does with it."""

    entity_id: str
    follow: bool = False
    full_volume: bool = False
    power: bool = False

    def stored(self) -> dict[str, Any]:
        return {
            KEY_PLAYER: self.entity_id,
            KEY_FOLLOW: self.follow,
            KEY_FULL_VOLUME: self.full_volume,
            KEY_POWER: self.power,
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
            bool(value.get(KEY_POWER, False)),
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


def wire_code(device: Any, code: str) -> str:
    """The code a zone reports for a listed input.

    Receivers that list the network input as NETWORK (in Deviceinfo.xml)
    report it as NET; the options keep the listed code.
    """

    if code == "NETWORK":
        return str(device.network_source or code)
    return code


def listed_code(device: Any, wire: str) -> str:
    """The listed code of a reported input (NET back to NETWORK where listed so)."""

    codes = {source.code for source in device.discovery.sources}
    if wire not in codes and wire == device.network_source and "NETWORK" in codes:
        return "NETWORK"
    return wire


def links_on_wire(options: dict[str, Any], device: Any) -> dict[str, Link]:
    """The linked players by the code a zone reports for their input."""

    return {wire_code(device, code): link for code, link in links(options).items()}


def _features(state: State) -> MediaPlayerEntityFeature:
    return MediaPlayerEntityFeature(int(state.attributes.get(ATTR_SUPPORTED_FEATURES, 0)))


#: Input names that say nothing about the device on them (the receivers'
#: default names, words only).
_GENERIC_INPUTS = frozenset(
    {
        "dvd", "blu ray", "bluray", "bd", "cd", "game", "game 2", "aux", "aux 1", "aux 2",
        "aux1", "aux2", "tv", "tv audio", "cbl sat", "sat cbl", "media player", "mplay",
        "tuner", "network", "net", "phono", "bluetooth", "usb", "8k", "heos music",
    }
)


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower())


def suggest_player(
    hass: HomeAssistant,
    input_name: str,
    *,
    exclude: list[str],
    network: bool,
    receiver_names: list[str],
) -> str | None:
    """A media player that is likely the device on an input; None unless one fits.

    An input renamed after its device ("Apple TV") fits a player whose name
    holds those words ("Living room Apple TV"). The network input fits a
    Music Assistant player named after the receiver. Only a single fit is
    suggested, and only as a suggestion: the user still picks.
    """

    if network:
        registry = er.async_get(hass)
        candidates = [
            item.entity_id
            for item in registry.entities.values()
            if item.platform == "music_assistant" and item.domain == "media_player"
        ]
        wanted = [_words(name) for name in receiver_names if _words(name)]
    else:
        words = _words(input_name)
        if not words or " ".join(words) in _GENERIC_INPUTS:
            return None
        candidates = hass.states.async_entity_ids("media_player")
        wanted = [words]
    fits = []
    for entity_id in candidates:
        if entity_id in exclude:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            continue
        have = set(_words(str(state.attributes.get("friendly_name") or entity_id)))
        if any(want and set(want) <= have for want in wanted):
            fits.append(entity_id)
    return fits[0] if len(fits) == 1 else None


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
        # Players switched with their input: the zones that play each now.
        self._powered: dict[str, frozenset[str]] = {}
        # The main zone's input while it is on, to see it change.
        self._main_input: str | None = None
        # A follow on its way: its input, the player's context (for the
        # pauses that come of it) and when it began.
        self._follow_target: tuple[str, Context, float] | None = None

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
        # What plays at the start is no switch: nothing is woken then.
        self._powered = self._powered_now()
        self._main_input = self._current_main_input()

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
        self._refresh_power()
        self._main_input_changed()

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
                code: (replace(link, entity_id=new) if link.entity_id == old else link)
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
        speakers while the receiver is off. Never on a network input: Music
        Assistant's player there may have the receiver itself as its volume
        control, and full volume would be the receiver's.
        """

        device = self._entry.runtime_data.device
        network = set(device.profile.netaudio.get("sources", []))
        return frozenset(
            link.entity_id
            for code, link in self._links.items()
            if link.full_volume
            and wire_code(device, code) not in network
            and self._zone_playing(code)
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
        if code is not None and not self._entry.runtime_data.announcing:
            # Not for an announcement's stream: Music Assistant's HEOS
            # player may report it as playing.
            self._follow(code, entity_id, event.context)

    # Switched with the input ---------------------------------------------------

    def _powered_now(self) -> dict[str, frozenset[str]]:
        """The players switched with their input, and the zones on that play it."""

        coordinator = self._entry.runtime_data
        device = coordinator.device
        found: dict[str, frozenset[str]] = {}
        for code, link in self._links.items():
            if not link.power:
                continue
            wire = wire_code(device, code)
            zones = frozenset(
                zone_id
                for zone_id, zone in coordinator.data.zones.items()
                if zone.power and device.zone_source(zone_id) == wire
            )
            if zones:
                found[link.entity_id] = zones
        return found

    @callback
    def _refresh_power(self) -> None:
        """Switch a player on with its input, and off with the zones that played it.

        On when a zone that is on switches to the input (or is switched on on
        it) and the player is off. Off only when every zone that played it is
        switched off: a zone moving to another input leaves the player on.
        """

        now = self._powered_now()
        data = self._entry.runtime_data.data
        for entity_id in now.keys() - self._powered.keys():
            state = self._hass.states.get(entity_id)
            if state is not None and state.state == MediaPlayerState.OFF:
                if _features(state) & MediaPlayerEntityFeature.TURN_ON:
                    self._call_later(entity_id, SERVICE_TURN_ON)
        for entity_id in self._powered.keys() - now.keys():
            zones = self._powered[entity_id]
            if any(data.zone(zone_id).power for zone_id in zones):
                continue
            state = self._hass.states.get(entity_id)
            if state is not None and state.state not in (*_GONE, MediaPlayerState.OFF):
                if _features(state) & MediaPlayerEntityFeature.TURN_OFF:
                    self._call_later(entity_id, SERVICE_TURN_OFF)
        self._powered = now

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

    def _zone_playing(self, code: str) -> bool:
        """Whether a zone is on and plays the input (Zone 2 on the main zone's
        source plays the main zone's input). Zones hidden in Home Assistant
        count too: the receiver still plays them."""

        coordinator = self._entry.runtime_data
        data = coordinator.data
        wire = wire_code(coordinator.device, code)
        return any(
            zone.power and coordinator.device.zone_source(zone_id) == wire
            for zone_id, zone in data.zones.items()
        )

    def _current_main_input(self) -> str | None:
        """The main zone's input while it is on; None while off, UNKNOWN before
        the receiver has said (a start or reload)."""

        coordinator = self._entry.runtime_data
        power = coordinator.data.zone(FOLLOW_ZONE).power
        if power is None:
            return UNKNOWN
        if not power:
            return None
        return coordinator.device.zone_source(FOLLOW_ZONE) or UNKNOWN

    # Only the player that is heard keeps playing --------------------------

    @callback
    def _main_input_changed(self) -> None:
        """The main zone went to another input (by a follow, by hand, by remote).

        While the receiver follows a player, the other linked players whose
        input no zone plays are paused: they would play into nothing. As in
        the Rotel integration, only when the new input has a linked player
        itself: switching to the TV or the tuner pauses nothing, and an
        announcement (network and back) never does. Not either on the way to a follow's
        input (the zone first turns on with its old input), when the main
        zone only turns on, or when the receiver first reports after a start
        or a reload.
        """

        previous, current = self._main_input, self._current_main_input()
        if current == previous:
            return
        self._main_input = current
        if self._entry.runtime_data.announcing:
            # An announcement switched to the network and back: no one's choice.
            return
        if self._follow_target is not None:
            code, context, since = self._follow_target
            if current == code:
                self._follow_target = None
                self._pause_unheard(context)
                return
            if time.monotonic() - since < FOLLOW_SETTLE:
                return
            # The receiver did not take the input: let the context go.
            self._follow_target = None
        if previous in (None, UNKNOWN) or current in (None, UNKNOWN):
            return
        if current in links_on_wire(dict(self._entry.options), self._entry.runtime_data.device):
            self._pause_unheard(None)

    @callback
    def _pause_unheard(self, context: Context | None) -> None:
        if not any(link.follow for link in self._links.values()):
            return
        heard = {
            link.entity_id for code, link in self._links.items() if self._zone_playing(code)
        }
        for entity_id in sorted({link.entity_id for link in self._links.values()} - heard):
            if is_busy(self._hass.states.get(entity_id)):
                self._pause_later(entity_id, context)

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
        """The main zone on and to the input; the other players pause once it is there.

        Through the zone's own action, so the turn-on volume and the volume
        limit apply. The context is the player's: the logbook shows the
        receiver's change as caused by it. An event tells automations.
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
        self._follow_target = (wire_code(coordinator.device, code), context, time.monotonic())
        if not await self._async_call(target, SERVICE_SELECT_SOURCE, {ATTR_INPUT_SOURCE: name}, context):
            self._follow_target = None
            return
        options = dict(self._entry.options)
        link = self._links.get(code)
        self._hass.bus.async_fire(
            EVENT_INPUT_FOLLOWED,
            {
                ATTR_ENTITY_ID: target,
                "source": name,
                "previous_source": (
                    choices.source_display(
                        coordinator.device.discovery,
                        options,
                        listed_code(coordinator.device, left),
                    )
                    if left is not None
                    else None
                ),
                "player": link.entity_id if link is not None else None,
            },
            context=context,
        )

    @callback
    def _pause_later(self, entity_id: str, context: Context | None) -> None:
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
