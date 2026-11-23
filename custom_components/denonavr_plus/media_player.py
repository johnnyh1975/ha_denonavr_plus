"""Media player platform for the Denon AVR Plus integration.

One media player entity is created per discovered zone. The main zone also
exposes the sound mode; additional zones do not. The selectable source list and
the sound mode list are both built from what the receiver reports.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
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

from . import choices
from . import volume as _volume
from .avr.errors import CommandNotSent
from .const import DOMAIN
from .avr.models import NowPlaying, ZoneDescriptor
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
            features |= (
                MediaPlayerEntityFeature.SELECT_SOUND_MODE
                | MediaPlayerEntityFeature.BROWSE_MEDIA
                | MediaPlayerEntityFeature.PLAY_MEDIA
            )
            features |= self._media_features(coordinator)
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
        features = self._attr_supported_features
        if not self._zone.is_main and self._now_playing is not None:
            # A zone playing the network player or the main zone's source.
            features |= self._media_features(self.coordinator)
        if self._device.is_tuner(self._source_code):
            # Skip steps through the tuner presets.
            features |= _SKIP
        return features

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
    def _zone_state(self):
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
    def media_content_type(self) -> str | None:
        now = self._now_playing
        if now and now.active:
            return MediaType.MUSIC
        return MediaType.CHANNEL if self._tuner_title else None

    @property
    def media_image_url(self) -> str | None:
        now = self._now_playing
        if now and now.active and now.image_url:
            return now.image_url
        return self._device.media_art_url() if now is not None else None

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
    def media_channel(self) -> str | None:
        """The recalled tuner preset while the tuner plays."""

        if not self._device.is_tuner(self._source_code):
            return None
        preset = self.coordinator.data.values.get("tuner_preset")
        return str(preset) if preset else None

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
        if media_content_id == "quick_select":
            return self._folder("quick_select", names["quick_select"], self._quick_select_items())
        if media_content_id == "sources":
            return self._folder("sources", names["sources"], self._source_items())
        if media_content_id == "tuner_presets":
            return self._folder("tuner_presets", names["tuner_presets"], self._tuner_preset_items())
        children: list[BrowseMedia] = []
        if self._device.discovery.quick_select_names:
            children.append(self._folder("quick_select", names["quick_select"], None))
        children.append(self._folder("sources", names["sources"], None))
        if self._has_tuner_presets:
            children.append(self._folder("tuner_presets", names["tuner_presets"], None))
        if self._device.can_play_url:
            try:
                children.append(
                    await media_source.async_browse_media(
                        self.hass,
                        None,
                        content_filter=lambda item: item.media_content_type.startswith("audio/"),
                    )
                )
            except BrowseError:
                # No media folder: the receiver's own items still show.
                pass
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

    def _quick_select_items(self) -> list[BrowseMedia]:
        return [
            BrowseMedia(
                media_class=MediaClass.MUSIC,
                media_content_id=f"{_QUICK_SELECT}{number}",
                media_content_type=MediaType.MUSIC,
                title=name,
                can_play=True,
                can_expand=False,
            )
            for number, name in sorted(self._device.discovery.quick_select_names.items())
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
                media_class=MediaClass.CHANNEL,
                media_content_id=f"{_TUNER_PRESET}{code}",
                media_content_type=MediaType.CHANNEL,
                title=code,
                can_play=True,
                can_expand=False,
            )
            for code in self._device.tuner_preset_codes
        ]

    @receiver_action
    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """A quick select, a source, or a URL (also from Home Assistant's media, TTS)."""

        if media_id.startswith(_QUICK_SELECT):
            await self._device.async_select_quick_select(int(media_id[len(_QUICK_SELECT):]))
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
                translation_placeholders={"model": self._device.discovery.device.model_name or "receiver"},
            )
        if media_source.is_media_source_id(media_id):
            item = await media_source.async_resolve_media(self.hass, media_id, self.entity_id)
            media_id = item.url
        await self._device.async_play_url(async_process_play_media_url(self.hass, media_id))

    @receiver_action
    async def async_set_repeat(self, repeat: RepeatMode) -> None:
        await self._device.async_media_repeat(_REPEAT_TO_HEOS[repeat])

    @receiver_action
    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._device.async_media_shuffle(shuffle)
