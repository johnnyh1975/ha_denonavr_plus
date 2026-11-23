"""Media player platform for the Denon AVR Plus integration.

One media player entity is created per discovered zone. The main zone also
exposes the sound mode; additional zones do not. The selectable source list and
the sound mode list are both built from what the receiver reports.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.trigger import PluggableAction
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import choices
from . import volume as _volume
from .avr.errors import CommandNotSent
from .avr.models import ZoneDescriptor
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .device_trigger import turn_on_trigger
from .entity import DenonAvrEntity, receiver_action, zone_device

_LOGGER = logging.getLogger(__name__)

# HEOS play modes and Home Assistant's repeat modes.
_REPEAT_FROM_HEOS = {"off": RepeatMode.OFF, "on_all": RepeatMode.ALL, "on_one": RepeatMode.ONE}
_REPEAT_TO_HEOS = {mode: heos for heos, mode in _REPEAT_FROM_HEOS.items()}

# The turn-on volume waits until the zone reports that it is on, then a
# moment longer: a receiver that is still starting can drop the command
# (docs/protocol/02, "Timing": 1 s after PWON, 3 to 5 s for a full boot).
TURN_ON_WAIT = 15.0
TURN_ON_SETTLE = 1.0

# An icon-style picture of the selected input, shown when there is no album art
# to display. Keyed by the receiver's wire source code (what the state holds);
# a keyword fallback matches the display name too so renamed sources still map.
_SOURCE_ICONS = {
    "TV": "mdi:television",
    "CD": "mdi:disc",
    "DVD": "mdi:disc-player",
    "BD": "mdi:disc-player",
    "TUNER": "mdi:radio",
    "PHONO": "mdi:record-player",
    "GAME": "mdi:controller-classic",
    "SAT/CBL": "mdi:satellite-variant",
    "MPLAY": "mdi:cast-audio",
    "NET": "mdi:cast-audio",
    "BT": "mdi:bluetooth",
    "AUX1": "mdi:audio-input-rca",
    "AUX2": "mdi:audio-input-rca",
    "USB/IPOD": "mdi:usb",
}
_SOURCE_ICON_KEYWORDS = (
    ("blu", "mdi:disc-player"),
    ("dvd", "mdi:disc-player"),
    ("cd", "mdi:disc"),
    ("tv", "mdi:television"),
    ("tuner", "mdi:radio"),
    ("radio", "mdi:radio"),
    ("phono", "mdi:record-player"),
    ("game", "mdi:controller-classic"),
    ("sat", "mdi:satellite-variant"),
    ("cbl", "mdi:satellite-variant"),
    ("cable", "mdi:satellite-variant"),
    ("bluet", "mdi:bluetooth"),
    ("heos", "mdi:cast-audio"),
    ("net", "mdi:cast-audio"),
    ("media", "mdi:cast-audio"),
    ("usb", "mdi:usb"),
    ("ipod", "mdi:usb"),
    ("aux", "mdi:audio-input-rca"),
    ("airplay", "mdi:apple"),
    ("spotify", "mdi:spotify"),
)


# Commands queue in the telnet client, which orders and paces them.
PARALLEL_UPDATES = 0

async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one media player per discovered zone."""

    coordinator = entry.runtime_data
    entities = [
        DenonAvrMediaPlayer(coordinator, zone)
        for zone in coordinator.device.discovery.zones
        if choices.zone_enabled(dict(entry.options), zone.id)
    ]
    async_add_entities(entities)


class DenonAvrMediaPlayer(DenonAvrEntity, MediaPlayerEntity):
    """A media player representing one zone of the receiver."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor) -> None:
        super().__init__(coordinator, f"media_player_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        # The device's own entity: the receiver for the main zone, the zone's
        # device ("AVR-X2600H Zone 2") for the others.
        self._attr_name = None

        features = (
            MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.SELECT_SOURCE
        )
        # Only the main zone carries the surround / sound mode and the network
        # transport controls (HEOS drives the whole receiver, i.e. the main
        # zone); without HEOS (a connection option) there is no transport.
        if zone.is_main:
            features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE
            if coordinator.device.heos_enabled:
                features |= (
                    MediaPlayerEntityFeature.PLAY
                    | MediaPlayerEntityFeature.PAUSE
                    | MediaPlayerEntityFeature.STOP
                    | MediaPlayerEntityFeature.NEXT_TRACK
                    | MediaPlayerEntityFeature.PREVIOUS_TRACK
                    | MediaPlayerEntityFeature.REPEAT_SET
                    | MediaPlayerEntityFeature.SHUFFLE_SET
                )
        self._attr_supported_features = features
        # Automations with the "Turn on requested" device trigger; see
        # device_trigger.py.
        self._turn_on_action = PluggableAction(self.async_write_ha_state)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (entry := self.registry_entry) and entry.device_id:
            self.async_on_remove(
                self._turn_on_action.async_register(
                    self.hass, turn_on_trigger(entry.device_id)
                )
            )

    # State ----------------------------------------------------------------

    @property
    def _reachable(self) -> bool:
        return self.coordinator.available or self._device.http_reachable

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Only on and off while the receiver does not answer.

        The player is shown then only with a turn-on automation; volume, mute
        and source could not reach the receiver.
        """

        if not self._reachable:
            return MediaPlayerEntityFeature.TURN_ON | MediaPlayerEntityFeature.TURN_OFF
        return self._attr_supported_features

    @property
    def available(self) -> bool:
        """Stay usable over HTTP while the telnet link is down.

        Power, volume, mute and source then come from the HTTP poll and
        commands go over HTTP. The other entities need telnet and go
        unavailable. With a "Turn on requested" automation the player also
        stays usable while the receiver does not answer at all, shown as off,
        so it can be turned on through that automation.
        """

        if self._turn_on_action and not self._reachable:
            return True
        return self.coordinator.last_update_success and self._reachable

    @property
    def _zone_state(self):
        return self.coordinator.data.zone(self._zone.id)

    @property
    def _now_playing(self):
        """HEOS now-playing, only meaningful for the main zone."""

        if not self._zone.is_main:
            return None
        return self.coordinator.data.now_playing

    @property
    def state(self) -> MediaPlayerState | None:
        if not self._reachable:
            # Only reached with a turn-on automation; the last state is stale.
            return MediaPlayerState.OFF
        power = self._zone_state.power
        if power is None:
            return None
        if not power:
            return MediaPlayerState.OFF
        # When a network source is playing, reflect the transport state so the
        # card shows play/pause; otherwise the receiver is simply on.
        now = self._now_playing
        if now and now.active:
            return (
                MediaPlayerState.PAUSED
                if now.state == "pause"
                else MediaPlayerState.PLAYING
            )
        return MediaPlayerState.ON

    @property
    def volume_level(self) -> float | None:
        raw = self._zone_state.volume_raw
        if raw is None:
            return None
        return _volume.level_from_raw(self._device, self._zone.id, raw, self._entry_options)

    @property
    def is_volume_muted(self) -> bool | None:
        return self._zone_state.muted

    @property
    def source(self) -> str | None:
        code = self._zone_state.source
        if code is None:
            return None
        return choices.source_display(self._device.discovery, self._entry_options, code)

    @property
    def icon(self) -> str | None:
        """Icon-style picture of the selected input.

        HA shows the album art (media image) when one is available; when there
        is none - a hardware input, or an idle network source - the entity icon
        reflects the selected input instead. Match the wire code first, then
        fall back to a keyword match on the display name so a renamed source
        (e.g. "Chromecast" on the NET input) still gets a sensible icon.
        """

        code = self._zone_state.source
        if not code:
            return None
        key = code.strip().upper()
        if key in _SOURCE_ICONS:
            return _SOURCE_ICONS[key]
        name = choices.source_display(self._device.discovery, self._entry_options, code).lower()
        for keyword, icon in _SOURCE_ICON_KEYWORDS:
            if keyword in name or keyword in key.lower():
                return icon
        return None

    @property
    def source_list(self) -> list[str]:
        return choices.source_list(self._device.discovery, self._entry_options, self._zone.id)

    @property
    def sound_mode(self) -> str | None:
        if not self._zone.is_main:
            return None
        # Prefer the receiver's display name for the active mode; fall back to
        # the raw wire token when the display name is not known yet.
        mode = self._current_sound_mode
        return choices.sound_mode_display(self._entry_options, mode) if mode else None

    @property
    def _current_sound_mode(self) -> str | None:
        """The active mode under the receiver's name for it, before renaming."""

        values = self.coordinator.data.values
        mode = values.get("sound_mode_display") or values.get("sound_mode")
        if not mode:
            return None
        discovery = self._device.discovery
        if not (discovery.all_sound_modes or discovery.current_sound_modes):
            # A receiver without its own list reports the wire token only.
            return self._device.fallback_sound_mode_name(str(mode)) or str(mode)
        return str(mode)

    @property
    def sound_mode_list(self) -> list[str] | None:
        if not self._zone.is_main:
            return None
        # All modes the receiver currently offers across groups (OPSMLALL), in
        # the receiver's own order, plus any current-context extras (e.g. Auto)
        # and the active mode. Not sorted, to keep a sensible order. The set is
        # signal dependent: the receiver only lists modes valid for the signal.
        discovery = self._device.discovery
        modes = list(discovery.all_sound_modes)
        for mode in discovery.current_sound_modes:
            if mode not in modes:
                modes.append(mode)
        if not modes:
            modes = self._device.fallback_sound_modes
        shown = choices.sound_mode_list(self._entry_options, modes, self._current_sound_mode)
        return shown or None

    # Now-playing media (network sources, via HEOS) ------------------------

    @property
    def media_content_type(self) -> str | None:
        now = self._now_playing
        return MediaType.MUSIC if now and now.active else None

    @property
    def media_image_url(self) -> str | None:
        now = self._now_playing
        if now and now.active and now.image_url:
            return now.image_url
        return None

    @property
    def media_title(self) -> str | None:
        now = self._now_playing
        return now.title if now and now.active else None

    @property
    def media_artist(self) -> str | None:
        now = self._now_playing
        return now.artist if now and now.active else None

    @property
    def media_album_name(self) -> str | None:
        now = self._now_playing
        return now.album if now and now.active else None

    @property
    def media_position(self) -> int | None:
        now = self._now_playing
        if now and now.active and now.position is not None:
            return int(now.position)
        return None

    @property
    def media_duration(self) -> int | None:
        now = self._now_playing
        if now and now.active and now.duration:
            return int(now.duration)
        return None

    @property
    def media_position_updated_at(self) -> datetime | None:
        now = self._now_playing
        if now and now.active and now.position_at is not None:
            return dt_util.utc_from_timestamp(now.position_at)
        return None

    @property
    def repeat(self) -> RepeatMode | None:
        now = self._now_playing
        if now is None or now.repeat is None:
            return None
        return _REPEAT_FROM_HEOS.get(now.repeat)

    @property
    def shuffle(self) -> bool | None:
        now = self._now_playing
        return now.shuffle if now is not None else None

    # Commands -------------------------------------------------------------

    @receiver_action
    async def async_turn_on(self) -> None:
        if not self._reachable and self._turn_on_action:
            _LOGGER.debug("%s is unreachable; running the turn-on automation", self.entity_id)
            await self._turn_on_action.async_run(self.hass, self._context)
            return
        await self._device.async_set_power(self._zone.id, True)
        target = _volume.zone_options(self._entry_options, self._zone.id).turn_on_db
        entry = self.coordinator.config_entry
        if target is not None and entry is not None:
            entry.async_create_background_task(
                self.hass,
                self._apply_turn_on_volume(target),
                f"denonavr_plus turn-on volume {self._zone.id}",
            )

    async def _apply_turn_on_volume(self, db: float) -> None:
        """Set the turn-on volume once the zone reports that it is on."""

        try:
            async with asyncio.timeout(TURN_ON_WAIT):
                while not self._zone_state.power:
                    await asyncio.sleep(0.25)
        except TimeoutError:
            _LOGGER.debug("Zone %s did not report on; turn-on volume skipped", self._zone.id)
            return
        await asyncio.sleep(TURN_ON_SETTLE)
        raw = _volume.clamp_raw(
            self._device, self._zone.id, self._device.volume_reference + db, self._entry_options
        )
        try:
            await self._device.async_set_volume_raw(self._zone.id, raw)
        except CommandNotSent:
            # A background task: the receiver went away again; nothing to report to.
            _LOGGER.debug("Zone %s turn-on volume not sent; receiver unreachable", self._zone.id)

    @receiver_action
    async def async_turn_off(self) -> None:
        if not self._reachable:
            # Shown as off already (only with a turn-on automation): an
            # automation that switches everything off must not fail here.
            return
        await self._device.async_set_power(self._zone.id, False)

    @receiver_action
    async def async_set_volume_level(self, volume: float) -> None:
        raw = _volume.raw_from_level(self._device, self._zone.id, volume, self._entry_options)
        await self._device.async_set_volume_raw(self._zone.id, raw)

    @receiver_action
    async def async_volume_up(self) -> None:
        current = self._zone_state.volume_raw
        ceiling = _volume.ceiling_raw(self._device, self._zone.id, self._entry_options)
        if current is not None and current + self._device.volume_step > ceiling:
            # At the zone's limit: never step past it.
            await self._device.async_set_volume_raw(self._zone.id, ceiling)
        elif self._zone.is_main:
            await self._device.async_volume_step(self._zone.id, up=True)
        else:
            await self._step_zone_volume(up=True)

    @receiver_action
    async def async_volume_down(self) -> None:
        if self._zone.is_main:
            await self._device.async_volume_step(self._zone.id, up=False)
        else:
            # Additional zones have no dedicated up/down command, so step by one
            # device volume step on the raw scale.
            await self._step_zone_volume(up=False)

    async def _step_zone_volume(self, up: bool) -> None:
        current = self._zone_state.volume_raw
        if current is None:
            return
        step = self._device.volume_step
        raw = _volume.clamp_raw(
            self._device, self._zone.id, current + step if up else current - step, self._entry_options
        )
        await self._device.async_set_volume_raw(self._zone.id, raw)

    @receiver_action
    async def async_mute_volume(self, mute: bool) -> None:
        await self._device.async_set_mute(self._zone.id, mute)

    @receiver_action
    async def async_select_source(self, source: str) -> None:
        code = choices.source_code_for(self._device.discovery, self._entry_options, source)
        await self._device.async_select_source(self._zone.id, code or source)

    @receiver_action
    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._device.async_select_sound_mode(
            choices.sound_mode_for(self._entry_options, sound_mode)
        )

    @receiver_action
    async def async_media_play(self) -> None:
        await self._device.async_media_play()

    @receiver_action
    async def async_media_pause(self) -> None:
        await self._device.async_media_pause()

    @receiver_action
    async def async_media_stop(self) -> None:
        await self._device.async_media_stop()

    @receiver_action
    async def async_media_next_track(self) -> None:
        await self._device.async_media_next()

    @receiver_action
    async def async_media_previous_track(self) -> None:
        await self._device.async_media_previous()

    @receiver_action
    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self._device.async_media_repeat(_REPEAT_TO_HEOS[repeat])

    @receiver_action
    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._device.async_media_shuffle(shuffle)
