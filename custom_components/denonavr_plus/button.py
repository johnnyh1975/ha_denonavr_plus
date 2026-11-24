"""Button platform for the Denon AVR Plus integration.

Momentary actions that do not map to a stateful entity: the graphic-EQ
helpers on the EQ sub-device (when the receiver has a graphic EQ this
profile can drive), saving a quick select preset (one per preset the
receiver reports), and the profile's command buttons (channel level reset,
network restart), which start disabled.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, receiver_action


# The graphic EQ buttons write the setup interface one at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the graphic-EQ action buttons when the receiver supports them."""

    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    if coordinator.device.graphic_eq.supported:
        entities.append(DenonAvrEqApply(coordinator))
        entities.append(DenonAvrEqCurveCopy(coordinator))
        entities.append(DenonAvrEqSetDefaults(coordinator))
    device = coordinator.device
    entities.extend(
        DenonAvrQuickSelectSave(coordinator, number)
        for number in sorted(device.discovery.quick_select_names)
    )
    entities.extend(DenonAvrCommandButton(coordinator, name) for name in device.button_commands)
    entities.append(DenonAvrRecoverAudio(coordinator))
    async_add_entities(entities)


class DenonAvrQuickSelectSave(DenonAvrEntity, ButtonEntity):
    """Store the current settings in one quick select preset (MSQUICK<n> MEMORY).

    It overwrites the preset, so it is a configuration button and starts
    disabled.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator, number: int) -> None:
        super().__init__(coordinator, f"button_quick_select_save_{number}")
        self._number = number
        self._attr_translation_key = "quick_select_save"
        self._attr_translation_placeholders = {
            "name": coordinator.device.discovery.quick_select_names[number]
        }

    @receiver_action
    async def async_press(self) -> None:
        await self.coordinator.device.async_save_quick_select(self._number)


class DenonAvrCommandButton(DenonAvrEntity, ButtonEntity):
    """A command without state from the profile (channel level reset, network restart)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator, name: str) -> None:
        sub_device = "speakers" if name == "channel_level_reset" else None
        super().__init__(coordinator, f"button_{name}", sub_device=sub_device)
        self._name = name
        self._attr_translation_key = name
        if name == "network_restart":
            self._attr_device_class = ButtonDeviceClass.RESTART

    @receiver_action
    async def async_press(self) -> None:
        await self.coordinator.device.async_press(self._name)


class DenonAvrEqApply(DenonAvrEntity, ButtonEntity):
    """Write the edited band curve to the receiver.

    The band sliders stage their values locally (the receiver accepts only a
    full band block, not a single band); pressing this applies the whole curve.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_apply", sub_device="eq")
        self._attr_translation_key = "eq_apply"

    @receiver_action
    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.apply()


class DenonAvrEqCurveCopy(DenonAvrEntity, ButtonEntity):
    """Copy the reference curve into the manual graphic EQ as a starting point."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_curve_copy", sub_device="eq")
        self._attr_translation_key = "eq_copy_curve"

    @receiver_action
    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.curve_copy()


class DenonAvrEqSetDefaults(DenonAvrEntity, ButtonEntity):
    """Reset the manual graphic EQ to its defaults (a flat curve)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_eq_set_defaults", sub_device="eq")
        self._attr_translation_key = "eq_default"

    @receiver_action
    async def async_press(self) -> None:
        await self.coordinator.device.graphic_eq.set_defaults()


class DenonAvrRecoverAudio(DenonAvrEntity, ButtonEntity):
    """Standby and back on with the same input, for a receiver stuck silent.

    As TheFab21/ha-denon-marantz-avr offers it. Starts disabled: rarely
    needed, and it interrupts what plays.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_recover_audio")
        self._attr_translation_key = "recover_audio"

    @receiver_action
    async def async_press(self) -> None:
        await self.coordinator.device.async_recover_audio()

