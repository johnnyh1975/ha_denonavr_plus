"""Tests for select.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.profile import load_profile

from .common import entity_id, registry_entry
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401

__all__ = ["loaded_entry", "receiver_network"]


async def test_setup_selects_start_disabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for key in ("select_dynamic_volume", "select_dimmer", "select_sound_mode"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is None, key
    for key in (
        "select_volume_limit",
        "select_lpf_for_lfe",
        "select_aspect_ratio",
        "select_eq_channel",
        "select_eq_speaker_selection",
    ):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key


async def test_choosing_an_option_sends_its_command(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    spec = load_profile().control("dynamic_volume")
    assert spec is not None
    select = entity_id(hass, loaded_entry, "select_dynamic_volume")
    state = hass.states.get(select)
    assert state is not None
    option = state.attributes["options"][-1]
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": option}, blocking=True
    )
    assert any(line.startswith(spec.prefix) for line in telnet.sent), telnet.sent
