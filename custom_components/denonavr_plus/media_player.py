"""Media player platform for the Denon AVR Plus integration.

One media player entity is created per discovered zone. The main zone also
exposes the sound mode; additional zones do not. The selectable source list and
the sound mode list are both built from what the receiver reports.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_EXTRA,
    BrowseError,
    BrowseMedia,
    SearchMedia,
    SearchMediaQuery,
    MediaClass,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    RepeatMode,
)
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.translation import async_get_translations
from homeassistant.util import dt as dt_util
from homeassistant.helpers.trigger import PluggableAction
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import artwork, choices, heos_media
from . import volume as _volume
from .avr.device import FOLLOW_MAIN
from .avr.errors import CommandNotSent
from .avr.transport.heos import HeosClient, HeosCommandError
from .const import DOMAIN
from .avr.models import NowPlaying, ZoneDescriptor, ZoneSnapshot, ZoneState
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .device_trigger import turn_on_trigger
from .entity import DenonAvrEntity, receiver_action, zone_device

_LOGGER = logging.getLogger(__name__)

# Media ids of the browser's own items: a quick select preset or a source.
_QUICK_SELECT = "quick_select/"
_SOURCE = "source/"
_TUNER_PRESET = "tuner_preset/"
_TRANSPORT = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
)
_PLAY_MODES = MediaPlayerEntityFeature.REPEAT_SET | MediaPlayerEntityFeature.SHUFFLE_SET
_SKIP = MediaPlayerEntityFeature.NEXT_TRACK | MediaPlayerEntityFeature.PREVIOUS_TRACK

# HEOS play modes and Home Assistant's repeat modes.
_REPEAT_FROM_HEOS = {"off": RepeatMode.OFF, "on_all": RepeatMode.ALL, "on_one": RepeatMode.ONE}
_REPEAT_TO_HEOS = {mode: heos for heos, mode in _REPEAT_FROM_HEOS.items()}

# The turn-on volume waits until the zone reports that it is on, then a
# moment longer: a receiver that is still starting can drop the command
# (docs/protocol/02, "Timing": 1 s after PWON, 3 to 5 s for a full boot).
TURN_ON_WAIT = 15.0
TURN_ON_SETTLE = 1.0
#: An announcement: how long until HEOS reports it playing, how long a stop
#: must last to count as its end (HEOS stops briefly while it switches
#: streams, as Music Assistant's HEOS provider learnt), and the longest it may
#: play before the zone is put back.
ANNOUNCE_START_WAIT = 10.0
ANNOUNCE_POLL = 0.25
ANNOUNCE_STOP_GRACE = 3.0
ANNOUNCE_MAX_LENGTH = 300.0

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
    # The receiver's players know each other: a zone joins the main zone's
    # group by playing its source.
    players: dict[str, DenonAvrMediaPlayer] = {}
    for zone in coordinator.device.discovery.zones:
        if choices.zone_enabled(dict(entry.options), zone.id):
            players[zone.id] = DenonAvrMediaPlayer(coordinator, zone, players)
    async_add_entities(players.values())


class DenonAvrMediaPlayer(DenonAvrEntity, MediaPlayerEntity):
    """A media player representing one zone of the receiver."""

    _attr_device_class = MediaPlayerDeviceClass.RECEIVER

    def __init__(
        self,
        coordinator: DenonAvrCoordinator,
        zone: ZoneDescriptor,
        players: dict[str, DenonAvrMediaPlayer],
    ) -> None:
        super().__init__(coordinator, f"media_player_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        self._players = players
        self._announce_task: asyncio.Task[None] | None = None
        self._announce_snapshot: ZoneSnapshot | None = None
        self._snapshot: ZoneSnapshot | None = None
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
        # Every zone browses its quick selects, sources and tuner presets;
        # only the main zone carries the surround / sound mode. Transport is
        # added while a network source plays (see supported_features).
        features |= (
            MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.PLAY_MEDIA
            | MediaPlayerEntityFeature.SEARCH_MEDIA
        )
        if zone.is_main:
            features |= MediaPlayerEntityFeature.SELECT_SOUND_MODE
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
        self.async_on_remove(self._cancel_announcement)

    def _cancel_announcement(self) -> None:
        if self._announce_task is not None:
            self._announce_task.cancel()

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
        features = self._attr_supported_features
        if len(self._players) > 1:
            features |= MediaPlayerEntityFeature.GROUPING
        if self._network_media:
            # Play, pause and skip only where they reach what plays.
            features |= self._media_features(self.coordinator)
        if self._zone.is_main and self._device.heos is not None:
            # HEOS queues albums and playlists now, next or at the end, and
            # plays announcements, after which the zone is put back. Only
            # while HEOS answers: a receiver without HEOS never offers them.
            features |= (
                MediaPlayerEntityFeature.MEDIA_ENQUEUE
                | MediaPlayerEntityFeature.MEDIA_ANNOUNCE
                | MediaPlayerEntityFeature.CLEAR_PLAYLIST
            )
        if self._device.is_tuner(self._source_code):
            # Skip steps through the tuner presets.
            features |= _SKIP
        return features

    @property
    def _network_media(self) -> bool:
        """Whether this zone hears the network player (playing, or its source)."""

        now = self._now_playing
        if now is None:
            return False
        return now.active or self._source_code in self._device.profile.netaudio.get("sources", [])

    @staticmethod
    def _media_features(coordinator: DenonAvrCoordinator) -> MediaPlayerEntityFeature:
        """Transport, and the HEOS play modes, where they reach the network player."""

        device = coordinator.device
        if not device.transport_available:
            return MediaPlayerEntityFeature(0)
        if device.heos_enabled and not device.telnet_media:
            return _TRANSPORT | _PLAY_MODES
        return _TRANSPORT

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
    def _zone_state(self) -> ZoneState:
        return self.coordinator.data.zone(self._zone.id)

    @property
    def _now_playing(self) -> NowPlaying | None:
        """The network player's now playing, where this zone hears it.

        The main zone always; another zone while it plays a network source or
        the same source as the main zone (Z2SOURCE included).
        """

        if self._zone.is_main:
            return self.coordinator.data.now_playing
        if not self._zone_state.power:
            return None
        source = self._source_code
        main = self.coordinator.data.zone("main")
        network = source in self._device.profile.netaudio.get("sources", [])
        if network or (source is not None and main.power and source == main.source):
            return self.coordinator.data.now_playing
        return None

    @property
    def _source_code(self) -> str | None:
        """The source this zone plays (the main zone's when it follows it)."""

        return self._device.zone_source(self._zone.id)

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
        return bool(self._zone_state.muted) if self._zone_state.muted is not None else None

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
        """The zone's inputs, then the main zone's quick selects by name.

        Picking a quick select from the source menu recalls it, as the HEOS
        integration does with its favorites.
        """

        sources = choices.source_list(self._device.discovery, self._entry_options, self._zone.id)
        if not self._entry_options.get(choices.OPT_SOURCE_SHORTCUTS, True):
            return sources
        extra = [*self._quick_select_sources(), *self._favorite_sources()]
        return sources + [name for name in dict.fromkeys(extra) if name not in sources]

    def _favorite_sources(self) -> dict[str, int]:
        """HEOS favorite name -> number (from 1), for the main zone's source menu.

        As the HEOS integration lists them; a name already taken by an input
        or a quick select stays out.
        """

        heos = self._device.heos
        if not self._zone.is_main or heos is None:
            return {}
        taken = set(choices.source_list(self._device.discovery, self._entry_options, self._zone.id))
        taken |= set(self._quick_select_sources())
        named: dict[str, int] = {}
        for number, name in enumerate(heos.favorites, start=1):
            if name and name not in taken:
                named.setdefault(name, number)
        return named

    def _quick_select_sources(self) -> dict[str, int]:
        """Quick select name -> number, for the main zone's source menu."""

        if not self._zone.is_main:
            return {}
        sources = set(choices.source_list(self._device.discovery, self._entry_options, self._zone.id))
        named: dict[str, int] = {}
        for number, name in sorted(self._device.discovery.quick_select_names.items()):
            if name and name not in sources:
                named.setdefault(name, number)
        return named

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
    def _tuner_title(self) -> str | None:
        """Station name, else the frequency, while the main zone plays the tuner."""

        if not self._device.is_tuner(self._source_code):
            return None
        values = self.coordinator.data.values
        if values.get("tuner_station"):
            return str(values["tuner_station"])
        frequency = values.get("tuner_frequency")
        if not isinstance(frequency, int) or frequency <= 0:
            return None
        if frequency < 50000:
            return f"FM {frequency / 100:.2f} MHz"
        return f"AM {frequency // 100} kHz"

    @property
    def media_content_id(self) -> str | None:
        """The recalled tuner preset, as the browser names it."""

        if not self._device.is_tuner(self._source_code):
            return None
        preset = self.coordinator.data.values.get("tuner_preset")
        return f"{_TUNER_PRESET}{preset}" if preset else None

    @property
    def media_content_type(self) -> str | None:
        now = self._now_playing
        if now and now.active:
            # A radio station is a channel, the rest music.
            return MediaType.CHANNEL if now.media_type == "station" else MediaType.MUSIC
        return MediaType.CHANNEL if self._tuner_title else None

    @property
    def media_image_url(self) -> str | None:
        now = self._now_playing
        if now and now.active and now.image_url:
            return now.image_url
        return self._device.media_art_url() if now is not None else None

    @property
    def _artwork_kind(self) -> str | None:
        """The kind of artwork to show for an input without cover art."""

        if not self._zone_state.power or self.media_image_url:
            return None
        return artwork.kind_for_icon(self.icon)

    @property
    def media_image_hash(self) -> str | None:
        if kind := self._artwork_kind:
            return f"artwork-{kind}-{artwork.VERSION}"
        return super().media_image_hash

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """The cover, or else the artwork of the input (served by the image proxy)."""

        if kind := self._artwork_kind:
            return artwork.svg(kind).encode(), "image/svg+xml"
        return await super().async_get_media_image()

    @property
    def media_title(self) -> str | None:
        """The track, the station, or else the input that plays."""

        now = self._now_playing
        if now and now.active:
            return now.title
        if tuner := self._tuner_title:
            return tuner
        source = self._source_code
        if not self._zone_state.power or not source:
            return None
        return choices.source_display(self._device.discovery, self._entry_options, source)

    @property
    def media_artist(self) -> str | None:
        """The artist, or else the main zone's sound mode under the input."""

        now = self._now_playing
        if now and now.active:
            return now.artist
        if not self._zone_state.power:
            return None
        return self.sound_mode

    @property
    def media_album_name(self) -> str | None:
        """The album, or else the signal of an input: decoder and sample rate.

        Only for inputs: on a network source the album is the album.
        """

        now = self._now_playing
        if now and now.active:
            return now.album
        if not self._zone.is_main or not self._zone_state.power or self._tuner_title:
            return None
        readonly = self.coordinator.data.readonly
        parts = [
            str(value).strip()
            for value in (readonly.get("decoder"), _sample_rate(readonly.get("sample_rate")))
            if value
        ]
        return " · ".join(parts) or None

    @property
    def media_channel(self) -> str | None:
        """The radio station: over HEOS, or the tuner's station or frequency."""

        now = self._now_playing
        if now and now.active and now.station:
            return now.station
        return self._tuner_title

    @property
    def app_name(self) -> str | None:
        """The HEOS music source it plays from (TuneIn, Spotify, a media server)."""

        now = self._now_playing
        return now.service if now and now.active else None

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
        if self._zone.is_main and self._entry_options.get(choices.OPT_MAIN_OFF_STANDBY):
            # The whole receiver: no other zone keeps playing.
            await self._device.async_set_system_power(False)
            return
        await self._device.async_set_power(self._zone.id, False)

    @receiver_action
    async def async_set_volume_level(self, volume: float) -> None:
        raw = _volume.raw_from_level(self._device, self._zone.id, volume, self._entry_options)
        await self._device.async_set_volume_raw(self._zone.id, raw)

    @receiver_action
    async def async_volume_up(self) -> None:
        if await self._step_by_option(up=True):
            return
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
        if await self._step_by_option(up=False):
            return
        if self._zone.is_main:
            await self._device.async_volume_step(self._zone.id, up=False)
        else:
            # Additional zones have no dedicated up/down command, so step by one
            # device volume step on the raw scale.
            await self._step_zone_volume(up=False)

    async def _step_by_option(self, up: bool) -> bool:
        """Step by the zone's step from the options, if one is set (and known volume)."""

        step = _volume.zone_options(self._entry_options, self._zone.id).step_db
        current = self._zone_state.volume_raw
        if step is None or current is None:
            return False
        raw = _volume.clamp_raw(
            self._device, self._zone.id, current + step if up else current - step, self._entry_options
        )
        await self._device.async_set_volume_raw(self._zone.id, raw)
        return True

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
        await self._ensure_on()
        if code is None and (number := self._quick_select_sources().get(source)):
            await self._device.async_select_quick_select(number)
            return
        heos = self._device.heos
        if code is None and heos is not None and (favorite := self._favorite_sources().get(source)):
            await heos_media.async_play(heos, f"heos_favorite/{favorite}", None)
            return
        await self._device.async_select_source(self._zone.id, code or source)

    @receiver_action
    async def async_select_sound_mode(self, sound_mode: str) -> None:
        await self._ensure_on()
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
        if self._device.is_tuner(self._source_code):
            await self._device.async_tuner_preset_step(up=True)
            return
        await self._device.async_media_next()

    @receiver_action
    async def async_media_previous_track(self) -> None:
        if self._device.is_tuner(self._source_code):
            await self._device.async_tuner_preset_step(up=False)
            return
        await self._device.async_media_previous()

    # Media browser and play_media (main zone) -----------------------------

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Quick selects, sources and, where URLs can play, Home Assistant's media."""

        if media_content_id and media_source.is_media_source_id(media_content_id):
            return await media_source.async_browse_media(
                self.hass,
                media_content_id,
                content_filter=lambda item: item.media_content_type.startswith("audio/"),
            )
        names = await self._folder_names()
        if heos_media.is_heos_id(media_content_id) and self._zone.is_main:
            heos = self._device.heos
            if heos is None:
                raise BrowseError(
                    translation_domain=DOMAIN,
                    translation_key="heos_unavailable",
                    translation_placeholders={"model": self._model},
                )
            return await heos_media.async_browse(
                heos, str(media_content_id), "HEOS", names["more"], names["queue"]
            )
        if media_content_id == "quick_select":
            return self._folder("quick_select", names["quick_select"], self._quick_select_items())
        if media_content_id == "sources":
            return self._folder("sources", names["sources"], self._source_items())
        if media_content_id == "tuner_presets":
            return self._folder("tuner_presets", names["tuner_presets"], self._tuner_preset_items())
        children: list[BrowseMedia] = []
        if self._quick_select_numbers():
            children.append(self._folder("quick_select", names["quick_select"], None))
        children.append(self._folder("sources", names["sources"], None))
        if self._has_tuner_presets:
            children.append(self._folder("tuner_presets", names["tuner_presets"], None))
        if self._zone.is_main and self._device.heos is not None:
            children.append(self._folder(heos_media.ROOT, "HEOS", None))
        if self._device.can_play_url and self._zone.is_main:
            try:
                media = await media_source.async_browse_media(
                    self.hass,
                    None,
                    content_filter=lambda item: item.media_content_type.startswith("audio/"),
                )
            except BrowseError:
                # No media folder: the receiver's own items still show.
                pass
            else:
                # Home Assistant's media sources (text to speech, radio, ...)
                # next to the receiver's own folders, not one level down.
                if (
                    isinstance(media, media_source.BrowseMediaSource)
                    and media.domain is None
                    and media.children
                ):
                    children.extend(media.children)
                else:
                    children.append(media)
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id="",
            media_content_type="",
            title=self._device.discovery.device.model_name or "Denon AVR",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
        )

    async def _folder_names(self) -> dict[str, str]:
        """The browser's folder names, in the user's language."""

        translations = await async_get_translations(
            self.hass, self.hass.config.language, "selector", [DOMAIN]
        )
        prefix = f"component.{DOMAIN}.selector.browse_folder.options."
        return {
            key: translations.get(prefix + key, fallback)
            for key, fallback in (
                ("quick_select", "Quick Select"),
                ("sources", "Sources"),
                ("tuner_presets", "Tuner presets"),
                ("more", "More"),
                ("queue", "Queue"),
            )
        }

    @staticmethod
    def _folder(content_id: str, title: str, children: list[BrowseMedia] | None) -> BrowseMedia:
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=content_id,
            media_content_type="",
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.MUSIC if children is not None else None,
        )

    def _quick_select_numbers(self) -> dict[int, str]:
        """This zone's quick selects: number -> name.

        The main zone's carry the names the receiver reports; another zone's
        (Z2QUICK) are numbered, as many as the main zone has, when the
        receiver answered their query.
        """

        names = self._device.discovery.quick_select_names
        if self._zone.is_main:
            return dict(names)
        spec = self._device.profile.control(f"{self._zone.id}_quick_select")
        if spec is None or not self._device.discovery.has_control(spec.scope, spec.feature, spec.id):
            return {}
        return {number: f"Quick Select {number}" for number in sorted(names)}

    # Media classes give each kind its own icon in the browser: a quick select
    # a playlist, an input a channel, a tuner preset music.

    def _quick_select_items(self) -> list[BrowseMedia]:
        return [
            BrowseMedia(
                media_class=MediaClass.PLAYLIST,
                media_content_id=f"{_QUICK_SELECT}{number}",
                media_content_type=MediaType.PLAYLIST,
                title=name,
                can_play=True,
                can_expand=False,
            )
            for number, name in sorted(self._quick_select_numbers().items())
        ]

    def _source_items(self) -> list[BrowseMedia]:
        discovery = self._device.discovery
        options = self._entry_options
        return [
            BrowseMedia(
                media_class=MediaClass.CHANNEL,
                media_content_id=f"{_SOURCE}{code}",
                media_content_type=MediaType.CHANNEL,
                title=choices.source_display(discovery, options, code),
                can_play=True,
                can_expand=False,
            )
            for code in choices.source_codes(discovery, options, self._zone.id)
        ]

    @property
    def _has_tuner_presets(self) -> bool:
        """A tuner among this zone's sources, and a known preset numbering."""

        discovery = self._device.discovery
        return bool(self._device.tuner_preset_codes) and any(
            self._device.is_tuner(code)
            for code in choices.source_codes(discovery, self._entry_options, self._zone.id)
        )

    def _tuner_preset_items(self) -> list[BrowseMedia]:
        if not self._has_tuner_presets:
            return []
        return [
            BrowseMedia(
                media_class=MediaClass.MUSIC,
                media_content_id=f"{_TUNER_PRESET}{code}",
                media_content_type=MediaType.CHANNEL,
                title=code,
                can_play=True,
                can_expand=False,
            )
            for code in self._device.tuner_preset_codes
        ]

    async def async_search_media(self, query: SearchMediaQuery) -> SearchMedia:
        """Find quick selects, inputs and HEOS favorites by name (voice: "play Kino").

        Exact names first, then names that start with the query, then names
        that contain it. Assist plays the first.
        """

        wanted = query.search_query.casefold().strip()
        candidates = [*self._quick_select_items(), *self._source_items()]
        heos = self._device.heos
        if self._zone.is_main and heos is not None:
            candidates += [
                BrowseMedia(
                    media_class=MediaClass.CHANNEL,
                    media_content_id=f"heos_favorite/{number}",
                    media_content_type=MediaType.CHANNEL,
                    title=name,
                    can_play=True,
                    can_expand=False,
                )
                for number, name in enumerate(heos.favorites, start=1)
            ]
        if query.media_filter_classes:
            candidates = [
                item for item in candidates if item.media_class in query.media_filter_classes
            ]

        def rank(item: BrowseMedia) -> int | None:
            title = item.title.casefold()
            if not wanted:
                return None
            if title == wanted:
                return 0
            if title.startswith(wanted):
                return 1
            return 2 if wanted in title else None

        ranked = [(rank(item), index, item) for index, item in enumerate(candidates)]
        found = sorted((r, i, item) for r, i, item in ranked if r is not None)
        return SearchMedia(result=[item for _, _, item in found])

    @receiver_action
    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """A quick select, a source, or a URL (also from Home Assistant's media, TTS)."""

        if (
            kwargs.get(ATTR_MEDIA_ANNOUNCE)
            and self._zone.is_main
            and self._device.can_play_url
            and not media_id.startswith((_QUICK_SELECT, _SOURCE, _TUNER_PRESET, *heos_media.PREFIXES))
        ):
            await self._async_announce(media_id, kwargs.get(ATTR_MEDIA_EXTRA) or {})
            return
        media_id = self._named_media(str(media_type), media_id)
        await self._ensure_on()
        if media_id.startswith(heos_media.PREFIXES):
            heos = self._device.heos if self._zone.is_main else None
            if heos is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="heos_unavailable",
                    translation_placeholders={"model": self._model},
                )
            await heos_media.async_play(heos, media_id, kwargs.get(ATTR_MEDIA_ENQUEUE))
            return
        if media_id.startswith(_QUICK_SELECT):
            number = media_id[len(_QUICK_SELECT):]
            if not number.isdigit() or int(number) not in self._quick_select_numbers():
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_quick_select",
                    translation_placeholders={"number": number, "model": self._model},
                )
            await self._device.async_select_quick_select(int(number), self._zone.id)
            return
        if media_id.startswith(_SOURCE):
            await self._device.async_select_source(self._zone.id, media_id[len(_SOURCE):])
            return
        if media_id.startswith(_TUNER_PRESET):
            code = media_id[len(_TUNER_PRESET):]
            if not self._has_tuner_presets or code not in self._device.tuner_preset_codes:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_tuner_preset",
                    translation_placeholders={
                        "preset": code,
                        "model": self._device.discovery.device.model_name or "receiver",
                    },
                )
            await self._device.async_tuner_preset(self._zone.id, code)
            return
        if not self._device.can_play_url:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="play_url_unsupported",
                translation_placeholders={"model": self._model},
            )
        if not self._zone.is_main:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="play_url_main_zone",
                translation_placeholders={"model": self._model},
            )
        if self._device.heos is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="heos_unavailable",
                translation_placeholders={"model": self._model},
            )
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = item.url
        await self._device.async_play_url(async_process_play_media_url(self.hass, media_id))

    @property
    def _model(self) -> str:
        return self._device.discovery.device.model_name or "receiver"

    # Snapshot and restore (actions) -------------------------------------------

    def take_snapshot(self) -> None:
        """Keep what the zone does now (denonavr_plus.snapshot)."""

        self._snapshot = self._device.zone_snapshot(self._zone.id)

    async def async_restore_snapshot(self) -> None:
        """Put the zone back as the last snapshot found it (denonavr_plus.restore)."""

        if self._snapshot is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_snapshot",
                translation_placeholders={"entity": self.entity_id or self._zone.id},
            )
        try:
            await self._device.async_restore(self._zone.id, self._snapshot)
        except CommandNotSent as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="receiver_unreachable",
                translation_placeholders={"model": self._model},
            ) from err

    # Announcements -----------------------------------------------------------

    @receiver_action
    async def async_clear_playlist(self) -> None:
        """Empty the HEOS queue."""

        await self._heos_call(lambda heos: heos.async_clear_queue())

    async def async_get_queue(self) -> list[dict[str, Any]]:
        """The HEOS queue (denonavr_plus.get_queue)."""

        result: list[dict[str, Any]] = []

        async def read(heos: HeosClient) -> None:
            result.extend(await heos.async_get_queue())

        await self._heos_call(read)
        return result

    async def async_remove_from_queue(self, queue_ids: list[int]) -> None:
        await self._heos_call(lambda heos: heos.async_remove_from_queue(queue_ids))

    async def async_move_queue_item(self, queue_ids: list[int], destination: int) -> None:
        await self._heos_call(lambda heos: heos.async_move_queue_item(queue_ids, destination))

    async def _heos_call(self, call: Callable[[HeosClient], Awaitable[None]]) -> None:
        """A HEOS command of the main zone, with its failures as translated errors."""

        if not self._zone.is_main:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="heos_main_zone",
                translation_placeholders={"model": self._model},
            )
        heos = self._device.heos
        if heos is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="heos_unavailable",
                translation_placeholders={"model": self._model},
            )
        try:
            await call(heos)
        except (HeosCommandError, ConnectionError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="heos_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    def _named_media(self, media_type: str, media_id: str) -> str:
        """A favorite or a quick select by name or number, as the HEOS integration takes them.

        `media_content_type: favorite` with `media_content_id: Bayern 3` (or
        `2`), and `quick_select` with `Kino` (or `1`), become the browser's ids.
        """

        if media_type == "favorite":
            heos = self._device.heos
            favorites = heos.favorites if heos is not None else []
            number = int(media_id) if media_id.isdigit() else (
                favorites.index(media_id) + 1 if media_id in favorites else None
            )
            if number is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="unknown_favorite",
                    translation_placeholders={"favorite": media_id},
                )
            return f"heos_favorite/{number}"
        if media_type == "queue" and media_id.isdigit():
            return f"heos_queue/{media_id}"
        if media_type == "quick_select" and not media_id.startswith(_QUICK_SELECT):
            names = {name: number for number, name in self._quick_select_numbers().items()}
            number = int(media_id) if media_id.isdigit() else names.get(media_id)
            return f"{_QUICK_SELECT}{number if number is not None else media_id}"
        return media_id

    async def _async_announce(self, media_id: str, extra: dict[str, Any]) -> None:
        """Play an announcement, then put the zone back as it was.

        The zone's power, input, volume and mute are kept before; the
        announcement plays over HEOS at the announcement volume (the
        `volume` of the call's extra data, 0 to 1, or the option), and once
        it has played they are restored in the background. A second
        announcement before then keeps the first snapshot.
        """

        if self._announce_task is not None and not self._announce_task.done():
            self._announce_task.cancel()
        else:
            self._announce_snapshot = self._device.zone_snapshot(self._zone.id)
        was_playing = self._heos_state() == "play"
        if self._device.heos is None:
            # Nothing would play: say so before switching anything.
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="heos_unavailable",
                translation_placeholders={"model": self._model},
            )
        try:
            await self._ensure_on()
            raw = self._announce_volume(extra)
            if raw is not None:
                await self._device.async_set_volume_raw(self._zone.id, raw)
            if self._zone_state.muted:
                # A muted zone would swallow it; the restore mutes it again.
                await self._device.async_set_mute(self._zone.id, False)
            if media_source.is_media_source_id(media_id):
                item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
                media_id = item.url
            await self._device.async_play_url(async_process_play_media_url(self.hass, media_id))
        except BaseException:
            # It did not start: put the zone back now instead of after it.
            snapshot, self._announce_snapshot = self._announce_snapshot, None
            if snapshot is not None:
                with suppress(CommandNotSent):
                    await self._device.async_restore(self._zone.id, snapshot)
            raise
        self._announce_task = self.hass.async_create_background_task(
            self._async_restore_after_announcement(was_playing),
            f"{DOMAIN} restore after announcement",
        )

    def _announce_volume(self, extra: dict[str, Any]) -> float | None:
        level = extra.get("volume")
        if isinstance(level, (int, float)) and 0 <= level <= 1:
            return _volume.raw_from_level(self._device, self._zone.id, float(level), self._entry_options)
        announce_db = _volume.zone_options(self._entry_options, self._zone.id).announce_db
        if announce_db is None:
            return None
        return _volume.clamp_raw(
            self._device,
            self._zone.id,
            self._device.volume_reference + announce_db,
            self._entry_options,
        )

    def _heos_state(self) -> str | None:
        return self.coordinator.data.now_playing.state

    async def _until(self, condition: Callable[[str | None], bool]) -> None:
        while not condition(self._heos_state()):
            await asyncio.sleep(ANNOUNCE_POLL)

    async def _async_restore_after_announcement(self, was_playing: bool) -> None:
        """Wait for the announcement to start and to end, then restore. Never raises."""

        snapshot = self._announce_snapshot
        try:
            async with asyncio.timeout(ANNOUNCE_START_WAIT):
                if was_playing:
                    # A play before the restart stop is still the previous stream.
                    await self._until(lambda state: state != "play")
                await self._until(lambda state: state == "play")
        except TimeoutError:
            _LOGGER.debug("Announcement on %s: no clear start", self._zone.id)
        try:
            async with asyncio.timeout(ANNOUNCE_MAX_LENGTH):
                while self._heos_state() == "play":
                    await self._until(lambda state: state != "play")
                    try:
                        # A stop counts as the end only once it lasts.
                        async with asyncio.timeout(ANNOUNCE_STOP_GRACE):
                            await self._until(lambda state: state == "play")
                    except TimeoutError:
                        break
        except TimeoutError:
            _LOGGER.debug("Announcement on %s: no clear end, restoring", self._zone.id)
        if snapshot is None:
            return
        self._announce_snapshot = None
        try:
            await self._device.async_restore(self._zone.id, snapshot)
        except CommandNotSent as err:
            _LOGGER.debug("Restoring %s after an announcement failed: %s", self._zone.id, err)

    async def _ensure_on(self) -> None:
        """Turn the zone on and wait until it reports so.

        A receiver in standby ignores a quick select, a source or a preset.
        """

        await _volume.async_zone_on(
            self._device, self._zone.id, self._entry_options, TURN_ON_WAIT, TURN_ON_SETTLE
        )

    # Zones as a group: a zone joins the main zone by playing its source ----

    @property
    def _follows_main(self) -> bool:
        return (
            not self._zone.is_main
            and bool(self._zone_state.power)
            and self._zone_state.source == FOLLOW_MAIN
        )

    @property
    def group_members(self) -> list[str] | None:
        """The main zone first, then the zones that play its source."""

        if len(self._players) < 2 or self.entity_id is None:
            return None
        main = self._players.get("main")
        followers = [player for player in self._players.values() if player._follows_main]
        if main is None or main.entity_id is None or not (self._zone.is_main or self._follows_main):
            return [self.entity_id]
        if not followers:
            return [main.entity_id]
        return [main.entity_id] + [player.entity_id for player in followers if player.entity_id]

    @receiver_action
    async def async_join_players(self, group_members: list[str]) -> None:
        """Let the receiver's other zones play the main zone's source."""

        model = self._device.discovery.device.model_name or "receiver"
        members = []
        for entity in group_members:
            if entity == self.entity_id:
                continue
            player = next(
                (item for item in self._players.values() if item.entity_id == entity), None
            )
            if player is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="join_other_device",
                    translation_placeholders={"entity": entity, "model": model},
                )
            members.append(player)
        if members and not self._zone.is_main:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="join_only_main",
                translation_placeholders={"model": model},
            )
        await self._ensure_on()
        for player in members:
            await player._ensure_on()
            await self._device.async_select_source(player._zone.id, FOLLOW_MAIN)

    @receiver_action
    async def async_unjoin_player(self) -> None:
        """A zone keeps the main zone's input but no longer follows it.

        Unjoining the main zone lets every zone go.
        """

        leaving = (
            [player for player in self._players.values() if player._follows_main]
            if self._zone.is_main
            else [self] if self._follows_main else []
        )
        # Unknown before the main zone first reports its input: nothing to switch to.
        if source := self.coordinator.data.zone("main").source:
            for player in leaving:
                await self._device.async_select_source(player._zone.id, source)

    @receiver_action
    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self._device.async_media_repeat(_REPEAT_TO_HEOS[repeat])

    @receiver_action
    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._device.async_media_shuffle(shuffle)


#: Sample rates some receivers report as bare digits (an AVR-X2600H sends
#: SSINFAISFSV 441 for 44.1 kHz).
_BARE_RATES = {"32": "32", "441": "44.1", "48": "48", "882": "88.2", "96": "96", "1764": "176.4", "192": "192"}


def _sample_rate(token: object) -> str | None:
    """'48K' -> '48 kHz', '44.1K' or '441' -> '44.1 kHz'; other tokens as they are."""

    if not isinstance(token, str) or not token.strip():
        return None
    token = token.strip()
    if token.upper().endswith("K") and token[:-1].replace(".", "", 1).isdigit():
        return f"{token[:-1]} kHz"
    if token in _BARE_RATES:
        return f"{_BARE_RATES[token]} kHz"
    return token
