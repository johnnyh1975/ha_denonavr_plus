"""Sensor platform for the Denon AVR Plus integration.

Creates read only sensors from what the receiver reports: the audio input
information (sample rate, decoder, format, signal), the current sound mode, and
the per channel calibration levels. Which sensors exist depends on the device.
"""

from __future__ import annotations

from typing import Any

from collections.abc import Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import Platform
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import choices, linked_players
from .activity import ACTIVITIES, zone_activity
from .avr.models import ZoneDescriptor
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .avr import signal_info
from .entity import DenonAvrEntity, add_planned, zone_device

# The audio information fields that start enabled. The decoder ('Dolby Atmos',
# 'PCM') is what automations react to; the raw signal code, sample rate,
# channel layout and mode text change with every track or programme and would
# fill the history, so they start disabled.
_ENABLED_READONLY = frozenset({"decoder"})
#: Signal sensors that start enabled: what a viewer checks (4K? HDR?).
_ENABLED_SIGNAL = frozenset({"video_input_resolution", "video_input_hdr"})


# Read only; the coordinator pushes every update.
PARALLEL_UPDATES = 0

def planned(coordinator: DenonAvrCoordinator, options: dict[str, Any]) -> list[SensorEntity]:
    """Create the read only sensors discovered for this receiver.

    The plan of this platform (entity_plan.py): what is set up, and what a
    start compares once the receiver has answered everything. Builds the
    entities without side effects.
    """

    device = coordinator.device
    entities: list[SensorEntity] = []

    # Audio information sensors, one per read only field in the profile. The
    # display name comes from the translation files, keyed by the control id.
    for control_id in device.profile.readonly:
        entities.append(DenonAvrReadonlySensor(coordinator, control_id))

    # Live video and channel information, where the setup interface has it.
    if device.signal_supported:
        entities.extend(DenonAvrSignalSensor(coordinator, key) for key in signal_info.FIELDS)

    # How each speaker position is connected (Web Control generation).
    speakers = device.speaker_config
    entities.extend(
        DenonAvrSpeakerConnectionSensor(coordinator, slot)
        for slot in speakers.entity_slots(SPEAKER_CONNECTION)
    )
    # The subwoofer mode and layout, where the receiver reports them (SSSPCSWM/SWL).
    entities.extend(
        DenonAvrSubwooferSetupSensor(coordinator, group)
        for group in SUBWOOFER_SETUP
        if group in device.state.speaker_sizes
    )

    # The current sound mode as a diagnostic sensor (also on the media player).
    entities.append(DenonAvrSoundModeSensor(coordinator))

    # The selected source of each zone, under the name Home Assistant shows,
    # and what the zone plays in one word.
    for zone in device.discovery.zones:
        if choices.zone_enabled(options, zone.id):
            entities.append(DenonAvrSourceSensor(coordinator, zone))
            entities.append(DenonAvrActivitySensor(coordinator, zone))

    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the planned entities."""

    coordinator = entry.runtime_data
    async_add_entities(add_planned(coordinator, Platform.SENSOR, planned(coordinator, dict(entry.options))))

class DenonAvrReadonlySensor(DenonAvrEntity, SensorEntity):
    """A sensor for a single read only audio information field."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator, control_id: str) -> None:
        super().__init__(coordinator, f"sensor_{control_id}")
        self._control_id = control_id
        self._attr_translation_key = control_id
        self._attr_entity_registry_enabled_default = control_id in _ENABLED_READONLY

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.readonly.get(self._control_id)


class DenonAvrSignalSensor(DenonAvrEntity, SensorEntity):
    """What the receiver receives and sends: resolution, HDR, channels (package 39).

    Video on the Video sub-device, channels on the Audio one. The input
    resolution and HDR start enabled; the rest is diagnostic and starts
    disabled.
    """

    def __init__(self, coordinator: DenonAvrCoordinator, key: str) -> None:
        sub_device = "video" if key.startswith("video_") else "audio"
        super().__init__(coordinator, f"sensor_{key}", sub_device=sub_device)
        self._key = key
        self._attr_translation_key = key
        if key not in _ENABLED_SIGNAL:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.signal.get(self._key)


class DenonAvrSoundModeSensor(DenonAvrEntity, SensorEntity):
    """A diagnostic sensor exposing the current sound mode.

    Starts disabled: the media player and the sound mode select show the same.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "sensor_sound_mode")
        self._attr_translation_key = "sound_mode"

    @property
    def native_value(self) -> str | None:
        value = self.coordinator.data.values.get("sound_mode")
        return str(value) if value is not None else None


class DenonAvrSourceSensor(DenonAvrEntity, SensorEntity):
    """The selected source of a zone, as a sensor for history and automations.

    Starts disabled: the media player shows the same as its source.
    """

    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor) -> None:
        super().__init__(coordinator, f"sensor_source_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        self._attr_translation_key = "source"

    @property
    def native_value(self) -> str | None:
        code = self.coordinator.data.zone(self._zone.id).source
        if code is None:
            return None
        return choices.source_display(self._device.discovery, self._entry_options, code)



class DenonAvrActivitySensor(DenonAvrEntity, SensorEntity):
    """What a zone plays: off, announcement, music, radio, film, TV, game or on.

    See activity.py for how it is told. Follows the linked players' state as
    well as the receiver's.
    """

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ACTIVITIES

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor) -> None:
        super().__init__(coordinator, f"sensor_activity_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        self._attr_translation_key = "activity"
        self._tracked: tuple[str, ...] = ()
        self._untrack: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._track_linked_players()

    async def async_will_remove_from_hass(self) -> None:
        if self._untrack is not None:
            self._untrack()
            self._untrack = None
        await super().async_will_remove_from_hass()

    def _track_linked_players(self) -> None:
        """Follow the linked players (the options can change while running)."""

        players = tuple(sorted({link.entity_id for link in linked_players.links(self._entry_options).values()}))
        if players == self._tracked:
            return
        if self._untrack is not None:
            self._untrack()
            self._untrack = None
        self._tracked = players
        if players:
            self._untrack = async_track_state_change_event(self.hass, players, self._linked_changed)

    @callback
    def _linked_changed(self, _event: Event[EventStateChangedData]) -> None:
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._track_linked_players()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        zone = data.zone(self._zone.id)
        device = self._device
        # Zone 2 on SOURCE plays the main zone's input.
        source = device.zone_source(self._zone.id)
        link = (
            linked_players.links_on_wire(self._entry_options, device).get(source)
            if source is not None
            else None
        )
        now = data.now_playing
        return zone_activity(
            power=zone.power,
            announcing=self._zone.id in self.coordinator.announcing,
            source=source,
            linked=self.hass.states.get(link.entity_id) if link is not None else None,
            network_media_type=now.media_type if now.active else None,
        )


SPEAKER_CONNECTION = "speaker_connection"

#: Subwoofer setup groups of SSSPC -> (translation key, token -> state).
#: Tokens as LaserGuruGuy saw them on an AVC-X3900H: STD Standard, DRC
#: Directional, 2LR Left/Right, 2FR Front/Rear.
SUBWOOFER_SETUP: dict[str, tuple[str, dict[str, str]]] = {
    "SWM": ("subwoofer_directional_mode", {"STD": "standard", "DRC": "directional"}),
    "SWL": ("subwoofer_layout", {"2LR": "left_right", "2FR": "front_rear"}),
}


class DenonAvrSpeakerConnectionSensor(DenonAvrEntity, SensorEntity):
    """How a speaker position is connected now: speakers and pre-out, or pre-out only."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: DenonAvrCoordinator, slot: str) -> None:
        super().__init__(coordinator, f"sensor_speaker_connection_{slot}", sub_device="speakers")
        self._slot = slot
        spec = coordinator.device.speaker_config.collection_spec(SPEAKER_CONNECTION) or {}
        self._keys: dict[str, str] = spec.get("value_keys", {})
        self._attr_options = list(self._keys.values())
        self._attr_translation_key = f"speaker_connection_{spec['position_keys'][slot]}"

    @property
    def native_value(self) -> str | None:
        item = self.coordinator.device.speaker_config.collection(SPEAKER_CONNECTION).get(self._slot)
        return self._keys.get(item["value"]) if item else None


class DenonAvrSubwooferSetupSensor(DenonAvrEntity, SensorEntity):
    """The subwoofer mode (standard, directional) or layout, as the receiver reports it."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: DenonAvrCoordinator, group: str) -> None:
        key, self._tokens = SUBWOOFER_SETUP[group]
        super().__init__(coordinator, f"sensor_{key}", sub_device="speakers")
        self._group = group
        self._attr_translation_key = key
        self._attr_options = list(self._tokens.values())

    @property
    def native_value(self) -> str | None:
        sizes = self.coordinator.device.state.speaker_sizes
        if self._group == "SWL" and sizes.get("SWM") != "DRC":
            # The layout applies in the directional mode only; the receiver
            # still reports one (2LR) in the standard mode.
            return None
        token = sizes.get(self._group)
        # A token not seen yet stays unknown rather than an invalid state.
        return self._tokens.get(token) if token else None
