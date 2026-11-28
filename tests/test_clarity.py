"""Tests for clarity.py."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import clarity

from .fakes import FakeTelnet


async def _push(hass: HomeAssistant, *lines: str) -> None:
    FakeTelnet.instances[-1].push(*lines)
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def test_the_recorded_receiver_raises_the_center_and_dynamic_volume(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    # The AVC-X3700H has Center Level Adjust and Dynamic Volume, no Dialogue Enhancer.
    assert clarity.controls(device) == [clarity.CENTER_LEVEL, clarity.DYNAMIC_VOLUME]
    await _push(hass, "PWON", "ZMON", "PSCLV 50", "PSDYNVOL OFF")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        before: dict[str, object] = {}
        await clarity.async_turn_on(device, before)
    assert telnet.sent == ["PSCLV 53", "PSDYNVOL MED"]
    assert before == {clarity.CENTER_LEVEL: 0.0, clarity.DYNAMIC_VOLUME: "OFF"}

    await _push(hass, "PSCLV 53", "PSDYNVOL MED")
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await clarity.async_turn_off(device, before)
    assert telnet.sent == ["PSCLV 50", "PSDYNVOL OFF"]
    # Everything back: nothing left to put back.
    assert before == {}


async def test_values_already_set_are_left_alone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    # Dynamic Volume already on: kept as the listener chose; the center
    # stops at the top of its range.
    await _push(hass, "PWON", "ZMON", "PSCLV 61", "PSDYNVOL LIT")
    assert clarity.targets(device) == {clarity.CENTER_LEVEL: clarity.CENTER_MAX_DB}
    await _push(hass, "PSCLV 62")
    assert clarity.targets(device) == {}


@pytest.mark.assumed(
    "PSDEH MED for the Dialogue Enhancer comes from the denonavr library; "
    "a receiver with the enhancer answering PSDEH ? would settle it"
)
async def test_the_dialogue_enhancer_comes_before_the_center(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    device.discovery.answered.add(clarity.DIALOG_ENHANCER)
    await _push(hass, "PWON", "ZMON", "PSCLV 50", "PSDEH OFF", "PSDYNVOL MED")
    assert clarity.controls(device)[0] == clarity.DIALOG_ENHANCER
    assert clarity.targets(device) == {clarity.DIALOG_ENHANCER: clarity.ENHANCER_ON}
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        before = {}
        await clarity.async_turn_on(device, before)
    assert before == {clarity.DIALOG_ENHANCER: "OFF"}
    assert telnet.sent == ["PSDEH MED"]


async def test_what_was_changed_before_a_refusal_is_noted(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus.avr.errors import CommandNotSent

    device = loaded_entry.runtime_data.device
    await _push(hass, "PWON", "ZMON", "PSCLV 50", "PSDYNVOL OFF")
    real = device.async_set_control

    async def _refuse_dynamic_volume(control_id: str, value: object) -> None:
        if control_id == clarity.DYNAMIC_VOLUME:
            raise CommandNotSent("PSDYNVOL MED")
        await real(control_id, value)

    before: dict[str, object] = {}
    with (
        patch.object(device, "async_set_control", _refuse_dynamic_volume),
        patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05),
        pytest.raises(CommandNotSent),
    ):
        await clarity.async_turn_on(device, before)
    assert before[clarity.CENTER_LEVEL] == 0.0
