"""Tests for remote.py: the receiver's on-screen menu keys."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import async_enable, entity_id, registry_entry
from .fakes import FakeTelnet


@pytest.fixture(autouse=True)
async def remote_shown(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    """The remote starts hidden; these tests show it, as a user does."""
    assert registry_entry(hass, loaded_entry, "remote").disabled_by is not None
    await async_enable(hass, loaded_entry, "remote")


async def _send(hass: HomeAssistant, entry: MockConfigEntry, **data: object) -> None:
    await hass.services.async_call(
        "remote",
        "send_command",
        {"entity_id": entity_id(hass, entry, "remote"), **data},
        blocking=True,
    )


@pytest.mark.assumed(
    "the MN cursor, enter, return, option, info, channel level and setup menu commands "
    "are documented in four Denon protocol documents but not yet sent by this "
    "integration; pressing each from the remote on a receiver would settle it"
)
async def test_menu_keys_send_their_commands(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await _send(hass, loaded_entry, command=["setup_menu_on", "down", "enter", "back"])
    assert telnet.sent == ["MNMEN ON", "MNCDN", "MNENT", "MNRTN"]


async def test_repeats_with_a_delay(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.remote.asyncio.sleep") as sleep:
        await _send(hass, loaded_entry, command=["up"], num_repeats=3, delay_secs=0.2)
    assert telnet.sent == ["MNCUP", "MNCUP", "MNCUP"]
    assert [call.args[0] for call in sleep.await_args_list] == [0.2, 0.2]


async def test_an_unknown_command_is_refused(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with pytest.raises(ServiceValidationError) as err:
        await _send(hass, loaded_entry, command=["up", "PWSTANDBY"])
    assert err.value.translation_key == "unknown_remote_command"
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["command"] == "PWSTANDBY"
    assert telnet.sent == []


async def test_on_and_off_switch_the_main_zone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    remote = entity_id(hass, loaded_entry, "remote")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call("remote", "turn_off", {"entity_id": remote}, blocking=True)
    await hass.services.async_call("remote", "turn_on", {"entity_id": remote}, blocking=True)
    assert telnet.sent == ["ZMOFF", "ZMON"]
