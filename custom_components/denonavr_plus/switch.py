"""Switch platform for the Denon AVR Plus integration.

One switch is created per on/off feature control the receiver advertises (for
example Tone Control, Dynamic EQ, Loudness Management, Cinema EQ).

Each zone also gets a Music switch for Music Assistant and automations: on
switches the zone on and to the network input, off switches the zone off only
while it still plays the network, so it never ends a film.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from . import choices, clarity, linked_players, volume
from .avr.models import ZoneDescriptor
from .avr.video_config import VideoConfigController
from .avr.profile import ControlSpec
from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator
from .avr import refusals
from .avr.errors import SettingNotTaken
from .entity import DenonAvrEntity, add_planned, grayed_attributes, receiver_action, zone_device
from .helpers import control_enabled_by_default, control_sub_device
from .media_player import TURN_ON_SETTLE, TURN_ON_WAIT


# Some switches write the setup interface, which takes one write at a time.
PARALLEL_UPDATES = 1

def planned(coordinator: DenonAvrCoordinator, options: dict[str, Any]) -> list[SwitchEntity]:
    """Create a switch for each supported on/off feature control.

    The plan of this platform (entity_plan.py): what is set up, and what a
    start compares once the receiver has answered everything. Builds the
    entities without side effects.
    """

    discovery = coordinator.device.discovery
    entities: list[SwitchEntity] = [
        DenonAvrSwitch(coordinator, spec)
        for spec in coordinator.device.profile.controls.values()
        if spec.kind == "onoff"
        and discovery.has_control(spec.scope, spec.feature, spec.id)
    ]
    # On/off video setup menu items (HDMI Control/CEC, ARC, TV audio switching,
    # power saving, smart menu, pass-through, OSD info), read/written over the
    # /ajax config API, on the Video sub-device. Only advertised items appear.
    video = coordinator.device.video_config
    if video.supported:
        entities.extend(
            DenonAvrVideoSwitch(coordinator, control)
            for control in video.controls()
            if control.get("kind") == "switch" and video.present(control["id"])
        )
    if coordinator.device.network_source is not None:
        entities.extend(
            DenonAvrMusicSwitch(coordinator, zone)
            for zone in discovery.zones
            if choices.zone_enabled(options, zone.id)
        )
    if clarity.controls(coordinator.device):
        entities.append(DenonAvrSpeechClaritySwitch(coordinator))
    return entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the planned entities."""

    coordinator = entry.runtime_data
    async_add_entities(add_planned(coordinator, Platform.SWITCH, planned(coordinator, dict(entry.options))))

class _Before(ExtraStoredData):
    """The values speech clarity changed, kept over a restart."""

    def __init__(self, before: dict[str, Any] | None) -> None:
        self.before = before

    def as_dict(self) -> dict[str, Any]:
        return {"before": self.before}


class DenonAvrSpeechClaritySwitch(DenonAvrEntity, RestoreEntity, SwitchEntity):
    """Speech clarity (clarity.py): on raises speech, off puts back what it changed."""

    def __init__(self, coordinator: DenonAvrCoordinator) -> None:
        super().__init__(coordinator, "switch_speech_clarity")
        self._attr_translation_key = "speech_clarity"
        self._before: dict[str, Any] | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (stored := await self.async_get_last_extra_data()) is not None:
            before = stored.as_dict().get("before")
            self._before = before if isinstance(before, dict) else None

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return _Before(self._before)

    @property
    def is_on(self) -> bool:
        return self._before is not None

    @receiver_action
    async def async_turn_on(self, **kwargs: Any) -> None:
        # Again while on tries what a refusal left out; what it changed
        # first keeps its value to put back.
        if self._before is None:
            self._before = {}
        try:
            await clarity.async_turn_on(self._device, self._before)
        finally:
            # On even after a refusal: off puts back what did change.
            self.async_write_ha_state()

    @receiver_action
    async def async_turn_off(self, **kwargs: Any) -> None:
        before = dict(self._before or {})
        try:
            await clarity.async_turn_off(self._device, before)
        finally:
            # Off once everything is back; what was refused stays to put back.
            self._before = before or None
            self.async_write_ha_state()


class DenonAvrMusicSwitch(DenonAvrEntity, SwitchEntity):
    """Music from the network on a zone (package 20).

    On: the zone on and on the network input. Off: the zone off, but only
    while it plays the network; on another input (someone watches TV) it is
    left alone. Meant as the power control of a Music Assistant player.
    """

    def __init__(self, coordinator: DenonAvrCoordinator, zone: ZoneDescriptor) -> None:
        super().__init__(coordinator, f"switch_music_{zone.id}", sub_device=zone_device(zone))
        self._zone = zone
        self._attr_translation_key = "music"
        # Shown with a player linked to the network input (Music Assistant);
        # linking one later shows it then (_music_follows_link in __init__).
        self._attr_entity_registry_enabled_default = linked_players.network_linked(coordinator)

    @property
    def is_on(self) -> bool:
        return self.coordinator.device.plays_network(self._zone.id)

    @receiver_action
    async def async_turn_on(self, **kwargs: Any) -> None:
        device = self.coordinator.device
        await volume.async_zone_on(
            device,
            self._zone.id,
            dict(entry.options) if (entry := self.coordinator.config_entry) else {},
            TURN_ON_WAIT,
            TURN_ON_SETTLE,
        )
        source = device.network_source
        if source and device.zone_source(self._zone.id) != source:
            await device.async_select_source(self._zone.id, source)

    @receiver_action
    async def async_turn_off(self, **kwargs: Any) -> None:
        device = self.coordinator.device
        if device.plays_network(self._zone.id):
            await device.async_set_power(self._zone.id, False)


class DenonAvrSwitch(DenonAvrEntity, SwitchEntity):
    """A switch backed by a profile on/off control."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DenonAvrCoordinator, spec: ControlSpec) -> None:
        # Route to a logical sub-device (Audio/Video/Speakers/EQ) based on the
        # control's command group, honouring any explicit profile override.
        sub_device = control_sub_device(spec)
        super().__init__(coordinator, f"switch_{spec.id}", sub_device=sub_device)
        self._spec = spec
        self._http_control = spec.id
        self._attr_translation_key = spec.id
        self._attr_entity_registry_enabled_default = control_enabled_by_default(spec)

    @property
    def is_on(self) -> bool:
        # Treat a not-yet-reported value as off so every toggle renders as a
        # single slider for a consistent look. A switch left at "unknown"
        # otherwise shows as a split on/off pair, which looks inconsistent next
        # to the others. Disconnection is handled by the entity's availability,
        # not here, so this only affects a control the receiver has not (yet)
        # reported a value for (e.g. auto lip sync with no active video source).
        return bool(self.coordinator.data.values.get(self._spec.id))

    @receiver_action
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.device.async_set_control_confirmed(
            self._spec.id, True, turn_on=self._turn_on_for_settings
        )

    @receiver_action
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.device.async_set_control_confirmed(
            self._spec.id, False, turn_on=self._turn_on_for_settings
        )


class DenonAvrVideoSwitch(DenonAvrEntity, SwitchEntity):
    """An on/off video setup item, backed by the /ajax video controller.

    Its state comes from the receiver via the config API (device.video_config).
    A grayed-out item (e.g. ARC until HDMI Control is on) shows the value its
    menu shows and refuses a change with the reason; its attributes say
    whether it can be changed now and what the receiver itself has stored.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DenonAvrCoordinator, control: dict[str, Any]) -> None:
        super().__init__(coordinator, f"switch_{control['id']}", sub_device="video")
        self._id = control["id"]
        self._on = control["on"]
        self._off = control["off"]
        self._attr_translation_key = control["id"]

    @property
    def _video(self) -> VideoConfigController:
        return self.coordinator.device.video_config

    @property
    def available(self) -> bool:
        return super().available and self._video.present(self._id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return grayed_attributes(self._device, self._video, self._id)

    @property
    def is_on(self) -> bool:
        return bool(self._video.value(self._id) == self._on)

    async def _set(self, wire: str) -> None:
        if not self._video.available(self._id):
            raise SettingNotTaken(self._id, refusals.MENU_GRAYED)
        await self._video.async_set(self._id, wire)

    @receiver_action
    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(self._on)

    @receiver_action
    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(self._off)

