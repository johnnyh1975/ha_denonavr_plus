"""Actions of the Denon AVR Plus integration.

`denonavr_plus.send_command` sends a raw telnet command, such as `PSDYNEQ ON`
or `MV?`, to the receiver behind a media player. It covers what has no
entity yet, and replaces `denonavr.get_command` of the official integration.
Asked for a response, it returns the receiver's replies.

`denonavr_plus.store_tuner_preset` stores the station that plays as a tuner
preset (TPANMEM), as yamaha_ynca's store_preset does.

`denonavr_plus.get_queue`, `remove_from_queue` and `move_queue_item` read
and change the HEOS queue, as the HEOS integration's actions do.

`denonavr_plus.volume_ramp` moves a zone's volume to a level over a few
seconds, in the receiver's own steps, as a wake-up or a fade-out would.

`denonavr_plus.snapshot` keeps what a zone does (power, input, volume,
mute) and `denonavr_plus.restore` puts it back, as announcements do on their
own.
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
SERVICE_GET_QUEUE = "get_queue"
SERVICE_REMOVE_FROM_QUEUE = "remove_from_queue"
SERVICE_MOVE_QUEUE_ITEM = "move_queue_item"
ATTR_QUEUE_IDS = "queue_ids"
ATTR_DESTINATION = "destination"
SERVICE_SNAPSHOT = "snapshot"
SERVICE_RESTORE = "restore"
ATTR_PRESET = "preset"
SERVICE_VOLUME_RAMP = "volume_ramp"
SERVICE_WHY_NO_SOUND = "why_no_sound"
ATTR_FIX = "fix"
ATTR_VOLUME_LEVEL = "volume_level"
ATTR_DURATION = "duration"
#: The longest ramp, in seconds: long enough for a wake-up.
MAX_RAMP_DURATION = 3600
#: Presets 1-56, as the receivers count them (A1-G8 on older ones).
TUNER_PRESETS = 56

#: The longest command: a message is at most MAX_MESSAGE_LENGTH bytes,
#: the closing carriage return included.
MAX_COMMAND_LENGTH = MAX_MESSAGE_LENGTH - 1

_SEND_COMMAND_SCHEMA: VolDictType = {vol.Required(ATTR_COMMAND): cv.string}
_STORE_TUNER_PRESET_SCHEMA: VolDictType = {
    vol.Required(ATTR_PRESET): vol.All(vol.Coerce(int), vol.Range(min=1, max=TUNER_PRESETS))
}

_VOLUME_RAMP_SCHEMA: VolDictType = {
    vol.Required(ATTR_VOLUME_LEVEL): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    vol.Optional(ATTR_DURATION, default=10): vol.All(
        vol.Coerce(float), vol.Range(min=0, max=MAX_RAMP_DURATION)
    ),
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


_QUEUE_IDS = vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(min=1))])
_REMOVE_SCHEMA: VolDictType = {vol.Required(ATTR_QUEUE_IDS): _QUEUE_IDS}
_MOVE_SCHEMA: VolDictType = {
    vol.Required(ATTR_QUEUE_IDS): _QUEUE_IDS,
    vol.Required(ATTR_DESTINATION): vol.All(vol.Coerce(int), vol.Range(min=1)),
}


async def _async_get_queue(entity: DenonAvrEntity, call: ServiceCall) -> ServiceResponse:
    return {"items": await entity.async_get_queue()}  # type: ignore[attr-defined]


async def _async_remove_from_queue(entity: DenonAvrEntity, call: ServiceCall) -> None:
    await entity.async_remove_from_queue(call.data[ATTR_QUEUE_IDS])  # type: ignore[attr-defined]


async def _async_move_queue_item(entity: DenonAvrEntity, call: ServiceCall) -> None:
    await entity.async_move_queue_item(  # type: ignore[attr-defined]
        call.data[ATTR_QUEUE_IDS], call.data[ATTR_DESTINATION]
    )


async def _async_volume_ramp(entity: DenonAvrEntity, call: ServiceCall) -> None:
    await entity.async_volume_ramp(  # type: ignore[attr-defined]
        call.data[ATTR_VOLUME_LEVEL], call.data[ATTR_DURATION]
    )


_WHY_NO_SOUND_SCHEMA: VolDictType = {vol.Optional(ATTR_FIX, default=False): cv.boolean}


async def _async_why_no_sound(entity: DenonAvrEntity, call: ServiceCall) -> ServiceResponse:
    found = await entity.async_why_no_sound(call.data[ATTR_FIX])  # type: ignore[attr-defined]
    return {"findings": found}


async def _async_snapshot(entity: DenonAvrEntity, call: ServiceCall) -> None:
    entity.take_snapshot()  # type: ignore[attr-defined]


async def _async_restore(entity: DenonAvrEntity, call: ServiceCall) -> None:
    await entity.async_restore_snapshot()  # type: ignore[attr-defined]


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
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_VOLUME_RAMP,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=_VOLUME_RAMP_SCHEMA,
        func=_async_volume_ramp,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_WHY_NO_SOUND,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=_WHY_NO_SOUND_SCHEMA,
        func=_async_why_no_sound,
        supports_response=SupportsResponse.OPTIONAL,
    )
    for name, func in ((SERVICE_SNAPSHOT, _async_snapshot), (SERVICE_RESTORE, _async_restore)):
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain=MEDIA_PLAYER_DOMAIN,
            schema=None,
            func=func,
        )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_GET_QUEUE,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=None,
        func=_async_get_queue,
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_REMOVE_FROM_QUEUE,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=_REMOVE_SCHEMA,
        func=_async_remove_from_queue,
    )
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_MOVE_QUEUE_ITEM,
        entity_domain=MEDIA_PLAYER_DOMAIN,
        schema=_MOVE_SCHEMA,
        func=_async_move_queue_item,
    )
