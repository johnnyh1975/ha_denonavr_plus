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
