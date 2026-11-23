"""Tests for services.py: the send_command action."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.denonavr_plus.avr.transport import GoformClient
from custom_components.denonavr_plus.const import DOMAIN
from custom_components.denonavr_plus.services import MAX_COMMAND_LENGTH, valid_command

from .common import entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def _send(
    hass: HomeAssistant, entry: MockConfigEntry, command: str, *, response: bool = False
) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        "send_command",
        {"entity_id": entity_id(hass, entry, "media_player_main"), "command": command},
        blocking=True,
        return_response=response,
    )


async def _drop_telnet(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, http_status: dict[str, Any] | None
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=http_status)):
        for _ in range(2):
            freezer.tick(timedelta(seconds=11))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()


async def test_the_action_exists_before_an_entry_loads(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "send_command")
    # The field description names the limit through a placeholder.
    service = hass.services.async_services_for_domain(DOMAIN)["send_command"]
    assert service.description_placeholders == {"max_length": str(MAX_COMMAND_LENGTH)}


async def test_a_command_goes_out_as_given(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    assert await _send(hass, loaded_entry, "PSDYNEQ ON") is None
    assert telnet.sent == ["PSDYNEQ ON"]


async def test_the_replies_come_back_as_the_response(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]

    async def answer(command: str) -> None:
        telnet.sent.append(command)
        # The volume and its maximum, with a push of another kind in between.
        hass.loop.call_soon(telnet.push, "MV50", "PWON", "MVMAX 80")

    with (
        patch.object(telnet, "async_send", answer),
        patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_QUIET", 0.05),
    ):
        response = await _send(hass, loaded_entry, "MV?", response=True)
    assert response == {
        entity_id(hass, loaded_entry, "media_player_main"): {"replies": ["MV50", "MVMAX 80"]}
    }


async def test_no_reply_gives_an_empty_list(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    with patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_FIRST", 0.05):
        response = await _send(hass, loaded_entry, "PSDYNEQ ON", response=True)
    main = entity_id(hass, loaded_entry, "media_player_main")
    assert response == {main: {"replies": []}}


@pytest.mark.parametrize(
    "command",
    ["", "   ", "MV50\rPWSTANDBY", "MV50\n", "SIÜ", "X" * (MAX_COMMAND_LENGTH + 1)],
)
async def test_an_unusable_command_is_refused(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, command: str
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with pytest.raises(ServiceValidationError) as err:
        await _send(hass, loaded_entry, command)
    assert err.value.translation_key == "invalid_command"
    assert telnet.sent == []


@pytest.mark.assumed(
    "whether the 135 bytes per message include the closing carriage return; "
    "sending a 135-character command to a receiver would settle it"
)
def test_the_longest_command_leaves_room_for_the_carriage_return() -> None:
    assert MAX_COMMAND_LENGTH == 134
    assert valid_command("X" * 134)
    assert not valid_command("X" * 135)


async def test_without_telnet_a_command_goes_over_http(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await _drop_telnet(hass, freezer, {"power": True})
    send = AsyncMock(return_value=True)
    with patch.object(GoformClient, "async_send_command", send):
        assert await _send(hass, loaded_entry, "PSDYNEQ ON", response=True)
    send.assert_awaited_once_with("PSDYNEQ ON")


async def test_without_telnet_a_query_cannot_be_answered(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await _drop_telnet(hass, freezer, {"power": True})
    send = AsyncMock(return_value=True)
    with (
        patch.object(GoformClient, "async_send_command", send),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _send(hass, loaded_entry, "MV?")
    assert err.value.translation_key == "command_query_needs_telnet"
    send.assert_not_awaited()


async def test_a_refused_http_command_is_reported(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    await _drop_telnet(hass, freezer, {"power": True})
    with (
        patch.object(GoformClient, "async_send_command", AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await _send(hass, loaded_entry, "PSDYNEQ ON")
    assert err.value.translation_key == "command_unreachable"
