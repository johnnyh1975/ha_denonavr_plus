"""Update platform: the receiver's firmware under Settings → Updates.

HEOS tells only that there is an update (player/check_update), not its
version, and the receiver installs it from its own menu or the HEOS app.
So the entity invents no version: the latest version is the installed one
while there is no update, and "newer version" (translated) while there is
one. It installs nothing; the diagnostic binary sensor says the same for
automations.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, add_planned

# Read only; the coordinator pushes every update.
PARALLEL_UPDATES = 0

#: The latest version while HEOS reports an update; translated under
#: entity.update.firmware.state_attributes.latest_version.
NEWER = "newer"


def planned(coordinator: DenonAvrCoordinator, options: dict[str, Any]) -> list[DenonAvrFirmwareUpdate]:
    """Create the firmware entity on receivers whose HEOS can be asked.

    The plan of this platform (entity_plan.py): what is set up, and what a
    start compares once the receiver has answered everything. Builds the
    entities without side effects.
    """

    device = coordinator.device
    if device.heos_enabled and not device.telnet_media:
        return [DenonAvrFirmwareUpdate(coordinator)]
    return []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the planned entities."""

    coordinator = entry.runtime_data
    async_add_entities(add_planned(coordinator, Platform.UPDATE, planned(coordinator, dict(entry.options))))

class DenonAvrFirmwareUpdate(DenonAvrEntity, UpdateEntity):
    """Whether HEOS has a firmware update for the receiver, as an update entity."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "update_firmware")
        self._attr_translation_key = "firmware"
        self._newer = "Newer version"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        translations = await async_get_translations(
            self.hass, self.hass.config.language, "entity", [DOMAIN]
        )
        key = (
            f"component.{DOMAIN}.entity.update.firmware"
            f".state_attributes.latest_version.state.{NEWER}"
        )
        self._newer = translations.get(key, self._newer)

    @property
    def installed_version(self) -> str | None:
        return self._device.discovery.device.firmware_version

    @property
    def latest_version(self) -> str | None:
        update = self._device.firmware_update
        if update is None:
            return None
        return self._newer if update else self.installed_version

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        # No version to compare: newer exactly while HEOS reports an update.
        return bool(self._device.firmware_update)
