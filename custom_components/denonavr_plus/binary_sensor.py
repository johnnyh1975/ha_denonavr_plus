"""Binary sensor platform for the Denon AVR Plus integration.

Exposes the telnet connectivity as a diagnostic connectivity binary sensor. It
reports the raw link state and therefore stays functional even when the other
entities are unavailable because the link is down.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity


# Read only; the coordinator pushes every update.
PARALLEL_UPDATES = 0

async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the connectivity binary sensor, and the firmware one on HEOS receivers."""

    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [DenonAvrConnectivitySensor(coordinator)]
    if coordinator.device.heos_enabled and not coordinator.device.telnet_media:
        entities.append(DenonAvrFirmwareSensor(coordinator))
    async_add_entities(entities)


class DenonAvrConnectivitySensor(DenonAvrEntity, BinarySensorEntity):
    """Reports whether the telnet control link is currently up."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "binary_sensor_connectivity")
        self._attr_translation_key = "connectivity"

    @property
    def available(self) -> bool:
        # This sensor must remain available so it can report a lost connection.
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return self.coordinator.available


class DenonAvrFirmwareSensor(DenonAvrEntity, BinarySensorEntity):
    """Whether HEOS has a firmware update for the receiver (package 31).

    HEOS tells only that there is one (player/check_update), not its
    version; the receiver installs it from its menu or the HEOS app. For
    automations; the update entity (update.py) shows the same under
    Settings → Updates. Asked every 12 hours while HEOS answers. Starts
    disabled: the update entity is where people look for it.
    """

    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "binary_sensor_firmware_update")
        self._attr_translation_key = "firmware_update"

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.device.firmware_update

