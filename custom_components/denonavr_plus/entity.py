"""Base entity for the Denon AVR Plus integration.

All entities share the device identity (built from discovery data) and become
unavailable together when the telnet link drops. Entity names come from the
receiver, so the entity ids naturally get the model based prefix (for example
`sensor.denon_avr_x3600h_sample_rate`) through Home Assistant's has_entity_name.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, cast

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .avr.errors import SetupWriteError
from .const import DOMAIN
from .coordinator import DenonAvrCoordinator

# Display names for the logical sub-devices grouped under the receiver. Each
# keeps a cohesive subsystem on its own device page (mirroring the receiver's
# setup menus) instead of crowding the main receiver device. Routing of a
# control to one of these lives in helpers.control_sub_device.
SUB_DEVICE_NAMES = {
    "audio": "Audio",
    "video": "Video",
    "picture": "Picture",
    "speakers": "Speakers",
    "eq": "Graphic EQ",
}


def setup_write[_FuncT: Callable[..., Coroutine[Any, Any, Any]]](func: _FuncT) -> _FuncT:
    """Report a setting the receiver rejected as an error in Home Assistant.

    For actions that write through the setup API (graphic EQ, video and speaker
    setup). Without this a rejected write only shows as the old value coming
    back, with no hint why.
    """

    @wraps(func)
    async def wrapper(self: DenonAvrEntity, *args: Any, **kwargs: Any) -> Any:
        try:
            return await func(self, *args, **kwargs)
        except SetupWriteError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="setup_write_failed",
                translation_placeholders={
                    "model": self._device.discovery.device.model_name or "Denon AVR"
                },
            ) from err

    return cast(_FuncT, wrapper)


class DenonAvrEntity(CoordinatorEntity[DenonAvrCoordinator]):
    """Base class providing device info and availability for all entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DenonAvrCoordinator, key: str, sub_device: str | None = None
    ) -> None:
        super().__init__(coordinator)
        self._device = coordinator.device
        device_info = self._device.discovery.device
        # Prefer the MAC as the stable identifier; then the serial number (also
        # stable), and only as a last resort the host, which can change.
        identifier = (
            device_info.mac_address
            or device_info.serial_number
            or self._device.host
        )
        self._attr_unique_id = f"{identifier}_{key}"
        if sub_device:
            # A logical sub-device linked to the main receiver by its registry id
            # (via_device_id); the deprecated via_device identifier tuple is
            # removed in HA 2027.8.0. The main device is registered up front in
            # __init__ so its id is available here.
            suffix = SUB_DEVICE_NAMES.get(sub_device, sub_device)
            info = DeviceInfo(
                identifiers={(DOMAIN, f"{identifier}_{sub_device}")},
                manufacturer=device_info.manufacturer or "Denon",
                model=device_info.model_name,
                name=f"{device_info.model_name or 'Denon AVR'} {suffix}",
            )
            if coordinator.main_device_id:
                info["via_device_id"] = coordinator.main_device_id
            self._attr_device_info = info
            return
        # Expose the MAC as a device connection (the standard HA convention for
        # network devices) so the device registry can de-duplicate it.
        connections = set()
        if device_info.mac_address:
            connections.add((CONNECTION_NETWORK_MAC, device_info.mac_address))
        self._attr_device_info = DeviceInfo(
            connections=connections,
            identifiers={(DOMAIN, identifier)},
            manufacturer=device_info.manufacturer or "Denon",
            model=device_info.model_name,
            name=device_info.model_name or "Denon AVR",
            sw_version=device_info.firmware_version,
            hw_version=device_info.hardware_type,
            serial_number=device_info.serial_number,
            # The receiver's own web control page, useful as a device link.
            configuration_url=f"https://{self._device.host}:10443/",
        )

    @property
    def available(self) -> bool:
        """Entities are available only while the telnet link is up."""

        return super().available and self.coordinator.available
