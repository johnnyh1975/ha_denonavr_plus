"""Tests for switch.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.profile import load_profile

from .common import entity_id, registry_entry
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_switches_that_repeat_the_media_player_start_disabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for key in ("switch_dynamic_eq", "switch_cinema_eq", "switch_all_zone_stereo"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is None, key
    # Power and mute are on the media player; the rest is set once.
    for key in ("switch_main_power", "switch_main_mute", "switch_auto_lip_sync"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key


async def test_turning_a_switch_on_and_off_sends_its_command(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    spec = load_profile().control("dynamic_eq")
    assert spec is not None
    telnet = FakeTelnet.instances[-1]
    switch = entity_id(hass, loaded_entry, "switch_dynamic_eq")
    for service, value in (("turn_on", spec.get("on")), ("turn_off", spec.get("off"))):
        telnet.sent.clear()
        await hass.services.async_call("switch", service, {"entity_id": switch}, blocking=True)
        assert any(line == f"{spec.prefix}{value}" for line in telnet.sent), telnet.sent


async def test_a_switch_the_receiver_has_not_reported_shows_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    switch = entity_id(hass, loaded_entry, "switch_dynamic_eq")
    loaded_entry.runtime_data.data.values.pop("dynamic_eq", None)
    loaded_entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off"
