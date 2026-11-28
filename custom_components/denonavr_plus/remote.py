"""Remote platform for the Denon AVR Plus integration.

One remote per receiver for the on-screen menu: cursor keys, enter,
return, option, info, the channel level menu and the setup menu, as
`remote.send_command` commands (see the profile's "remote" section for the
names). Turning it on or off switches the main zone, like the receiver's
own remote. The menu belongs to the receiver, not to a zone, so there is
one remote, not one per zone.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import (
    ATTR_DELAY_SECS,
    ATTR_NUM_REPEATS,
    DEFAULT_DELAY_SECS,
    RemoteEntity,
)
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, add_planned, receiver_action

# Commands queue in the telnet client, which orders and paces them.
PARALLEL_UPDATES = 0


def planned(coordinator: DenonAvrCoordinator, options: dict[str, Any]) -> list[DenonAvrRemote]:
    """Create the receiver's remote.

    The plan of this platform (entity_plan.py): what is set up, and what a
    start compares once the receiver has answered everything. Builds the
    entities without side effects.
    """

    if coordinator.device.remote_commands:
        return [DenonAvrRemote(coordinator)]
    return []


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the planned entities."""

    coordinator = entry.runtime_data
    async_add_entities(add_planned(coordinator, Platform.REMOTE, planned(coordinator, dict(entry.options))))

class DenonAvrRemote(DenonAvrEntity, RemoteEntity):
    """The receiver's on-screen menu keys."""

    # Its on/off is the main zone's power, a second switch beside the media
    # player on the device page; the menu keys are for scripts and cards.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "remote")
        self._attr_translation_key = "remote"

    @property
    def _main_zone_id(self) -> str:
        zones = self._device.discovery.zones
        return next((zone.id for zone in zones if zone.is_main), "main")

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.zone(self._main_zone_id).power

    @receiver_action
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._device.async_set_power(self._main_zone_id, True)

    @receiver_action
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._device.async_set_power(self._main_zone_id, False)

    @receiver_action
    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        commands = list(command)
        known = self._device.remote_commands
        unknown = [name for name in commands if name not in known]
        if unknown:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_remote_command",
                translation_placeholders={
                    "command": ", ".join(unknown),
                    "commands": ", ".join(known),
                },
            )
        repeats = int(kwargs.get(ATTR_NUM_REPEATS, 1))
        delay = float(kwargs.get(ATTR_DELAY_SECS, DEFAULT_DELAY_SECS))
        for index in range(repeats):
            for position, name in enumerate(commands):
                if index or position:
                    await asyncio.sleep(delay)
                await self._device.async_send_remote(name)
