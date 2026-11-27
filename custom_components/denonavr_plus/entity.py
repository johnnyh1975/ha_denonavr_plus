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
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import choices
from .avr.device import DenonAvrDevice
from .avr.models import ZoneDescriptor
from .avr.errors import CommandNotSent, SettingNotTaken, SetupWriteError, SourceNotTaken
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
    "zone2": "Zone 2",
    "zone3": "Zone 3",
}

#: Sub-devices of their own room: a zone plays elsewhere than the receiver.
ZONE_DEVICES = frozenset({"zone2", "zone3"})


def zone_device(zone: ZoneDescriptor) -> str | None:
    """The sub-device of a zone's entities: none for the main zone."""

    return None if zone.is_main else zone.id


def receiver_action[_FuncT: Callable[..., Coroutine[Any, Any, Any]]](func: _FuncT) -> _FuncT:
    """Report an action the receiver did not take as an error in Home Assistant.

    For every action of an entity. Two cases: a setting written through the
    setup API (graphic EQ, video and speaker setup) that the receiver
    rejected, and a command that reached the receiver neither over telnet nor
    over HTTP. Without this, the first only shows as the old value coming
    back and the second goes nowhere, with no hint why.
    """

    @wraps(func)
    async def wrapper(self: DenonAvrEntity, *args: Any, **kwargs: Any) -> Any:
        model = self._device.discovery.device.model_name or "Denon AVR"
        try:
            return await func(self, *args, **kwargs)
        except SetupWriteError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="setup_write_failed",
                translation_placeholders={"model": model},
            ) from err
        except CommandNotSent as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="receiver_unreachable",
                translation_placeholders={"model": model},
            ) from err
        except SourceNotTaken as err:
            discovery = self._device.discovery
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="source_not_taken",
                translation_placeholders={
                    "model": model,
                    "source": choices.source_display(discovery, self._entry_options, err.source),
                },
            ) from err
        except SettingNotTaken as err:
            # The receiver kept the old value; say why it takes it only in a
            # context (avr/refusals.py), named as the user sees the entity.
            state = self.hass.states.get(self.entity_id) if self.entity_id else None
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=f"setting_not_taken_{err.reason}",
                translation_placeholders={
                    "model": model,
                    "setting": state.name if state is not None else err.control_id,
                },
            ) from err

    return cast(_FuncT, wrapper)


def _identifier(device: DenonAvrDevice) -> str:
    """The receiver in unique ids: the MAC, as the stable identifier; then the
    serial number (also stable), and only as a last resort the host, which
    can change."""

    info = device.discovery.device
    return info.mac_address or info.serial_number or device.host


def unique_id(device: DenonAvrDevice, key: str) -> str:
    """The unique id of one of the receiver's entities."""

    return f"{_identifier(device)}_{key}"


class DenonAvrEntity(CoordinatorEntity[DenonAvrCoordinator]):
    """Base class providing device info and availability for all entities."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: DenonAvrCoordinator, key: str, sub_device: str | None = None
    ) -> None:
        super().__init__(coordinator)
        self._device = coordinator.device
        device_info = self._device.discovery.device
        identifier = _identifier(self._device)
        self._attr_unique_id = unique_id(self._device, key)
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
            serial_number=device_info.serial_number,
            # The receiver's own web control page, useful as a device link.
            configuration_url=self._device.setup_url,
        )

    @property
    def _turn_on_for_settings(self) -> bool:
        """Whether a setting in standby turns the main zone on first (option)."""

        return bool(self._entry_options.get(choices.OPT_SETTINGS_TURN_ON, False))

    @property
    def _entry_options(self) -> dict[str, Any]:
        """The entry options (volume settings), empty before the entry exists."""

        entry = self.coordinator.config_entry
        return dict(entry.options) if entry is not None else {}

    @property
    def suggested_object_id(self) -> str | None:
        """Build the entity id from the English name, whatever the language.

        Home Assistant builds entity ids from the name in the user's language
        for many languages, German among them (NATIVE_ENTITY_IDS). Ids here
        are always English so they read the same in every installation and
        can be documented and shared. The displayed name stays translated.
        """

        if not self.platform_data:
            return super().suggested_object_id
        name = self._name_internal(
            None, self.platform_data.default_language_platform_translations
        )
        return None if name is UNDEFINED else name

    #: The profile control an entity shows, if any (select, switch, number):
    #: available without telnet once its value was read over HTTP.
    _http_control: str | None = None

    @property
    def available(self) -> bool:
        """Available while the telnet link is up.

        While it is down, also a control whose value was read over HTTP
        (avr/appcommand.py): sound mode, tone and Audyssey settings.
        """

        if not super().available:
            return False
        if self.coordinator.available:
            return True
        return (
            self._http_control is not None
            and self._device.http_reachable
            and self._http_control in self._device.http_values
        )
