"""Actions of the Denon AVR Plus integration.

`denonavr_plus.send_command` sends a raw telnet command, such as `PSDYNEQ ON`
or `MV?`, to the receiver behind a media player. It covers what has no
entity yet, and replaces `denonavr.get_command` of the official integration.
Asked for a response, it returns the receiver's replies.

`denonavr_plus.store_tuner_preset` stores the station that plays as a tuner
preset (TPANMEM), as yamaha_ynca's store_preset does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.typing import VolDictType
from homeassistant.util.json import JsonValueType

from .avr.const import MAX_MESSAGE_LENGTH
from .avr.errors import CommandNotSent
from .const import DOMAIN

if TYPE_CHECKING:
    from .entity import DenonAvrEntity

SERVICE_SEND_COMMAND = "send_command"
ATTR_COMMAND = "command"
SERVICE_STORE_TUNER_PRESET = "store_tuner_preset"
ATTR_PRESET = "preset"
#: Presets 1-56, as the receivers count them (A1-G8 on older ones).
TUNER_PRESETS = 56

#: The longest command: a message is at most MAX_MESSAGE_LENGTH bytes,
#: the closing carriage return included.
MAX_COMMAND_LENGTH = MAX_MESSAGE_LENGTH - 1

_SEND_COMMAND_SCHEMA: VolDictType = {vol.Required(ATTR_COMMAND): cv.string}
_STORE_TUNER_PRESET_SCHEMA: VolDictType = {
    vol.Required(ATTR_PRESET): vol.All(vol.Coerce(int), vol.Range(min=1, max=TUNER_PRESETS))
}


def valid_command(command: str) -> bool:
    """Whether the receiver can take the command: printable ASCII, not too long.

    A line break would end the command early and send the rest as a second
    one, so control characters are refused, as is anything outside ASCII.
    """

    return (
        bool(command.strip())
        and len(command) <= MAX_COMMAND_LENGTH
        and all(" " <= char <= "~" for char in command)
    )


async def _async_send_command(entity: DenonAvrEntity, call: ServiceCall) -> ServiceResponse:
    command: str = call.data[ATTR_COMMAND]
    if not valid_command(command):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_command",
            translation_placeholders={"max_length": str(MAX_COMMAND_LENGTH)},
        )
    device = entity.coordinator.device
    try:
        replies = await device.async_send_command(
            command, collect_replies=call.return_response
        )
    except CommandNotSent as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=f"command_{err.reason}",
            translation_placeholders={
                "command": command,
                "model": device.discovery.device.model_name or "Denon AVR",
            },
        ) from err
    if not call.return_response:
        return None
    lines: list[JsonValueType] = list(replies)
    return {"replies": lines}


async def _async_store_tuner_preset(entity: DenonAvrEntity, call: ServiceCall) -> None:
    device = entity.coordinator.device
    model = device.discovery.device.model_name or "Denon AVR"
    codes = device.tuner_preset_codes
    number: int = call.data[ATTR_PRESET]
    if not device.has_tuner or number > len(codes):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_tuner",
            translation_placeholders={"model": model},
        )
    if device.tuner_zone is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="tuner_not_playing",
            translation_placeholders={"model": model},
        )
    try:
        await device.async_store_tuner_preset(codes[number - 1])
    except CommandNotSent as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="receiver_unreachable",
            translation_placeholders={"model": model},
        ) from err


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions; called once from async_setup."""

    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_SEND_COMMAND,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=_SEND_COMMAND_SCHEMA,
        func=_async_send_command,
        supports_response=SupportsResponse.OPTIONAL,
        description_placeholders={"max_length": str(MAX_COMMAND_LENGTH)},
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_STORE_TUNER_PRESET,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=_STORE_TUNER_PRESET_SCHEMA,
        func=_async_store_tuner_preset,
    )
