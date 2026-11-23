"""Tests for device_trigger.py: "Turn on requested" for an unreachable receiver."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_get_device_automations,
    async_mock_service,
)

from custom_components.denonavr_plus import device_trigger
from custom_components.denonavr_plus.avr.transport import GoformClient
from custom_components.denonavr_plus.const import DOMAIN

from .common import entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


def _receiver_device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    main = er.async_get(hass).async_get(entity_id(hass, entry, "media_player_main"))
    assert main is not None and main.device_id is not None
    device = dr.async_get(hass).async_get(main.device_id)
    assert device is not None
    return device


async def _automation(hass: HomeAssistant, device_id: str) -> list[ServiceCall]:
    calls = async_mock_service(hass, "test", "wake")
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": "turn_on",
                },
                "action": {
                    "service": "test.wake",
                    "data": {"description": "{{ trigger.description }}"},
                },
            }
        },
    )
    await hass.async_block_till_done()
    return calls


async def _receiver_gone(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Telnet drops and HTTP does not answer: the receiver is off without network standby."""
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)):
        for _ in range(2):
            freezer.tick(timedelta(seconds=11))
            async_fire_time_changed(hass)
            await hass.async_block_till_done()


async def test_the_receiver_offers_the_trigger(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = _receiver_device(hass, loaded_entry)
    triggers = await async_get_device_automations(
        hass, DeviceAutomationType.TRIGGER, device.id
    )
    assert [t for t in triggers if t["domain"] == DOMAIN] == [
        device_trigger.turn_on_trigger(device.id) | {"metadata": {}}
    ]
    # The sub-devices (Audio, Video …) do not offer it.
    sub = next(
        d
        for d in dr.async_entries_for_config_entry(dr.async_get(hass), loaded_entry.entry_id)
        if d.via_device_id == device.id
    )
    assert await device_trigger.async_get_triggers(hass, sub.id) == []


async def test_turning_on_an_unreachable_receiver_runs_the_automation(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    calls = await _automation(hass, _receiver_device(hass, loaded_entry).id)
    await _receiver_gone(hass, freezer)

    state = hass.states.get(main)
    assert state is not None and state.state == STATE_OFF
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call("media_player", "turn_on", {"entity_id": main}, blocking=True)
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["description"] == (
        "turn on requested while the receiver is unreachable"
    )
    assert telnet.sent == []


async def test_a_reachable_receiver_is_turned_on_over_the_network(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    calls = await _automation(hass, _receiver_device(hass, loaded_entry).id)
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call("media_player", "turn_on", {"entity_id": main}, blocking=True)
    await hass.async_block_till_done()
    assert calls == []
    assert telnet.sent


@pytest.mark.usefixtures("loaded_entry")
async def test_without_the_automation_an_unreachable_player_is_unavailable(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    await _receiver_gone(hass, freezer)
    state = hass.states.get(main)
    assert state is not None and state.state == STATE_UNAVAILABLE


async def test_after_a_restart_without_the_receiver_the_automation_still_works(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The media players start from the last discovery, so turning on still works."""
    main = entity_id(hass, loaded_entry, "media_player_main")
    calls = await _automation(hass, _receiver_device(hass, loaded_entry).id)
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    with (
        patch.object(GoformClient, "async_get_device_info", AsyncMock(return_value=None)),
        patch.object(FakeTelnet, "async_start", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(loaded_entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get(main)
    assert state is not None and state.state == STATE_OFF
    await hass.services.async_call("media_player", "turn_on", {"entity_id": main}, blocking=True)
    await hass.async_block_till_done()
    assert len(calls) == 1
