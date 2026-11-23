"""Tests for avr/device.py."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.denonavr_plus.avr import capability_check
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.avr.transport import GoformClient

from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_a_raw_command_to_an_unreachable_receiver_raises(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    # The media player is unavailable then, so the action never gets here;
    # the device still refuses rather than dropping the command silently.
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    send = AsyncMock(return_value=True)
    with (
        patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)),
        patch.object(GoformClient, "async_send_command", send),
    ):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        with pytest.raises(CommandNotSent) as err:
            await loaded_entry.runtime_data.device.async_send_command("PSDYNEQ ON")
    assert err.value.reason == "unreachable"
    send.assert_not_awaited()


async def test_an_own_query_is_dropped_quietly_without_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Queries are the integration's own; only a command asked for raises."""
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    device = loaded_entry.runtime_data.device
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        await device._send("MV?")
        with pytest.raises(CommandNotSent):
            await device._send("MVUP")


async def test_the_capability_check_asks_every_query(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    device = loaded_entry.runtime_data.device
    answers = {"PW?": ["PWON"], "MV?": ["MV50", "MVMAX 80"], "PSFOO ?": ["?"]}

    async def answer(command: str) -> None:
        telnet.sent.append(command)
        if command in answers:
            hass.loop.call_soon(telnet.push, *answers[command])

    progress: list[float] = []
    queries = (("PW?", "Power"), ("MV?", "Volume"), ("PSFOO ?", "Test"), ("SSINF ?", "Test"))
    with (
        patch.object(telnet, "async_send", answer),
        patch.object(capability_check, "QUERIES", queries),
        patch("custom_components.denonavr_plus.avr.device.CHECK_REPLY_FIRST", 0.02),
        patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_QUIET", 0.02),
    ):
        result = await device.async_check_capabilities(progress.append)
    assert {command: item["result"] for command, item in result["queries"].items()} == {
        "PW?": "answered",
        "MV?": "answered",
        "PSFOO ?": "rejected",
        "SSINF ?": "no reply",
    }
    assert result["queries"]["MV?"]["lines"] == ["MV50", "MVMAX 80"]
    assert result["model"] == device.discovery.device.model_name
    assert progress == [0.25, 0.5, 0.75, 1.0]


async def test_the_capability_check_needs_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    with pytest.raises(CommandNotSent):
        await loaded_entry.runtime_data.device.async_check_capabilities()
