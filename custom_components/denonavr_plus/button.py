"""Button platform for the Denon AVR Plus integration.

Momentary actions that do not map to a stateful entity: the graphic-EQ
helpers on the EQ sub-device (when the receiver has a graphic EQ this
profile can drive), saving a quick select preset (one per preset the
receiver reports), and the profile's command buttons (channel level reset,
network restart), which start disabled.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import choices, layout_repair, no_sound
from .avr.models import ZoneDescriptor
from .avr.speaker_layout import LayoutNotSeen
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .entity import DenonAvrEntity, add_planned, receiver_action, zone_device
from .helpers import control_zone_shown


# The graphic EQ buttons write the setup interface one at a time.
PARALLEL_UPDATES = 1


def planned(coordinator: DenonAvrCoordinator, options: dict[str, Any]) -> list[ButtonEntity]:
    """Create the graphic-EQ action buttons when the receiver supports them.

    The plan of this platform (entity_plan.py): what is set up, and what a
    start compares once the receiver has answered everything. Builds the
    entities without side effects.
    """

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
    # Recall each quick select: for dashboards, Apple Home and voice, where a
    # select is clumsy. Zone 2 where the receiver answers Z2QUICK ?.
    options = options
    zone2 = device.profile.control("zone2_quick_select")
    for zone in device.discovery.zones:
        if zone.is_main or (
            zone.id == "zone2"
            and zone2 is not None
            and device.discovery.has_control(zone2.scope, zone2.feature, zone2.id)
            and control_zone_shown(zone2, options)
        ):
            entities.extend(
                DenonAvrQuickSelectRecall(coordinator, zone, number)
                for number in sorted(device.discovery.quick_select_names)
            )
    entities.extend(DenonAvrCommandButton(coordinator, name) for name in device.button_commands)
    entities.append(DenonAvrRecoverAudio(coordinator))
    if device.speaker_layout.supported:
        entities.append(DenonAvrSpeakerLayoutApply(coordinator))
    entities.extend(
        DenonAvrWhyNoSound(coordinator, zone)
        for zone in device.discovery.zones
        if choices.zone_enabled(options, zone.id)
    )
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the planned entities."""

    coordinator = entry.runtime_data
    async_add_entities(add_planned(coordinator, Platform.BUTTON, planned(coordinator, dict(entry.options))))

class DenonAvrQuickSelectRecall(DenonAvrEntity, ButtonEntity):
    """Recall one quick select preset of a zone (MSQUICK<n>, Z2QUICK<n>).

    The main zone's start shown, Zone 2's hidden with the zone's other
    settings.
    """

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor, number: int) -> None:
        super().__init__(
            coordinator, f"button_quick_select_{zone.id}_{number}", sub_device=zone_device(zone)
        )
        self._zone = zone.id
        self._number = number
        self._attr_translation_key = "quick_select_recall"
        self._attr_translation_placeholders = {
            "name": coordinator.device.discovery.quick_select_names[number]
        }
        self._attr_entity_registry_enabled_default = zone.is_main

    @receiver_action
    async def async_press(self) -> None:
        await self.coordinator.device.async_select_quick_select(self._number, self._zone)


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



class DenonAvrWhyNoSound(DenonAvrEntity, ButtonEntity):
    """Why is there no sound? Fixes what it safely can and posts what it found.

    See no_sound.py: switching the zone on, unmuting and HDMI audio back to
    the receiver are fixed; volume, speakers and inputs are only named.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor) -> None:
        super().__init__(coordinator, f"button_why_no_sound_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        self._attr_translation_key = "why_no_sound"

    @receiver_action
    async def async_press(self) -> None:
        result = await no_sound.async_check(
            self.hass, self.coordinator, self._zone.id, self._entry_options, fix=True
        )
        entry = self.coordinator.config_entry
        assert entry is not None
        await no_sound.async_notify(self.hass, entry.entry_id, self._zone.id, result)


class DenonAvrSpeakerLayoutApply(DenonAvrEntity, ButtonEntity):
    """Write the speaker layout prepared in its select (Web Control generation).

    The receiver resets the speaker positions and front speaker A/B with a new
    layout; what it reset becomes a repair that puts it back.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "speaker_layout_apply"

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "button_speaker_layout_apply", sub_device="speakers")

    @property
    def available(self) -> bool:
        layout = self.coordinator.device.speaker_layout
        return super().available and layout.supported and not layout.busy

    @receiver_action
    async def async_press(self) -> None:
        entry = self.coordinator.config_entry
        assert entry is not None
        entry_id = entry.entry_id
        # A repair still open keeps its state before: that is the one to go back to.
        earlier = layout_repair.open_point(self.hass, entry_id)
        try:
            point = await self.coordinator.device.speaker_layout.async_apply(
                earlier["before"] if earlier else None
            )
        except LayoutNotSeen as err:
            # Not seen in time, maybe taken all the same: the way back stays.
            layout_repair.async_report(self.hass, self.coordinator, err.point)
            raise
        finally:
            self.coordinator.async_update_listeners()
        if point is not None:
            layout_repair.async_report(self.hass, self.coordinator, point)
