"""Tests for number.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.profile import load_profile

from .common import entity_id, registry_entry
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401

__all__ = ["loaded_entry", "receiver_network"]


async def test_distances_and_eq_bands_start_disabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for key in ("number_bass", "number_sleep_timer", "number_channel_trim_C"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is None, key
    for key in (
        "number_channel_distance_C",
        "number_eq_Eq1kHz",
        "number_lfe",
        "number_audio_delay",
    ):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key


async def test_setting_a_level_sends_its_command(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    spec = load_profile().control("bass")
    assert spec is not None
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entity_id(hass, loaded_entry, "number_bass"), "value": 2},
        blocking=True,
    )
    assert any(line.startswith(spec.prefix) for line in telnet.sent), telnet.sent


async def test_the_volume_number_shows_and_sets_db(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from custom_components.denonavr_plus import volume

    number = entity_id(hass, loaded_entry, "number_volume_main")
    FakeTelnet.instances[-1].push("MV425")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    state = hass.states.get(number)
    assert state is not None and float(state.state) == -37.5

    # The range follows the zone's limit, and a value above it is not sent.
    hass.config_entries.async_update_entry(
        loaded_entry, options={"main": {volume.OPT_MAX_VOLUME: -20.0}}
    )
    await hass.async_block_till_done()
    state = hass.states.get(number)
    assert state is not None and state.attributes["max"] == -20.0
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": -20.0}, blocking=True
    )
    assert telnet.sent == ["MV60"]
