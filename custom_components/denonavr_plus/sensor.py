"""Sensor platform for the Denon AVR Plus integration.

Creates read only sensors from what the receiver reports: the audio input
information (sample rate, decoder, format, signal), the current sound mode, and
the per channel calibration levels. Which sensors exist depends on the device.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity

# The audio information fields that start enabled. The decoder ('Dolby Atmos',
# 'PCM') is what automations react to; the raw signal code, sample rate,
# channel layout and mode text change with every track or programme and would
# fill the history, so they start disabled.
_ENABLED_READONLY = frozenset({"decoder"})


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the read only sensors discovered for this receiver."""

    coordinator = entry.runtime_data
    device = coordinator.device
    entities: list[SensorEntity] = []

    # Audio information sensors, one per read only field in the profile. The
    # display name comes from the translation files, keyed by the control id.
    for control_id in device.profile.readonly:
        entities.append(DenonAvrReadonlySensor(coordinator, control_id))

    # The current sound mode as a diagnostic sensor (also on the media player).
    entities.append(DenonAvrSoundModeSensor(coordinator))

    async_add_entities(entities)


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
