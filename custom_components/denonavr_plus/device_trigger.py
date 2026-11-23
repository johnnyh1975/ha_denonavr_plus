"""Device trigger "Turn on requested" for the Denon AVR Plus integration.

A receiver with network standby off cannot be switched on over the
network: once it is off, it no longer answers. With this trigger in an
automation, a media player of the receiver stays usable while the receiver
is unreachable (shown as off), and turning it on runs the automation, which
can switch the receiver on by infrared or HDMI-CEC. As soon as the receiver
answers again, turning on works over the network as usual.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.trigger import PluggableAction, TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

TRIGGER_TYPE_TURN_ON = "turn_on"
TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {vol.Required(CONF_TYPE): vol.In({TRIGGER_TYPE_TURN_ON})}
)


def turn_on_trigger(device_id: str) -> dict[str, str]:
    """The trigger that a media player of this device runs when turned on."""

    return {
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: device_id,
        CONF_TYPE: TRIGGER_TYPE_TURN_ON,
    }


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, str]]:
    """Devices with a media player offer the trigger: the receiver and each zone.

    Audio, Video and the other setup sub-devices do not.
    """

    entities = er.async_entries_for_device(
        er.async_get(hass), device_id, include_disabled_entities=True
    )
    if not any(entity.domain == "media_player" for entity in entities):
        return []
    return [turn_on_trigger(device_id)]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Run the action when a media player of the device is turned on unreachable."""

    device_id: str = config[CONF_DEVICE_ID]
    variables = {
        **trigger_info["trigger_data"],
        CONF_PLATFORM: "device",
        CONF_DOMAIN: DOMAIN,
        CONF_DEVICE_ID: device_id,
        CONF_TYPE: TRIGGER_TYPE_TURN_ON,
        "description": "turn on requested while the receiver is unreachable",
    }
    return PluggableAction.async_attach_trigger(
        hass, turn_on_trigger(device_id), action, {"trigger": variables}
    )
