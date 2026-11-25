"""Tests for number.py."""

from __future__ import annotations

import pytest

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
    telnet.echo = True
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


async def test_times_are_durations(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    for key in ("number_sleep_timer", "number_audio_delay"):
        entry = registry_entry(hass, loaded_entry, key)
        assert entry.original_device_class == "duration", key


@pytest.mark.assumed(
    "TFAN followed by six digits tunes directly per the X-series documents; "
    "not yet tried on a receiver"
)
async def test_the_tuner_frequency_reads_and_tunes(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from homeassistant.const import STATE_UNKNOWN

    number = entity_id(hass, loaded_entry, "number_tuner_frequency")
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SITUNER", "TFAN009290")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    state = hass.states.get(number)
    assert state is not None and float(state.state) == 92.9
    assert state.attributes["unit_of_measurement"] == "MHz"
    telnet.sent.clear()
    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 104.4}, blocking=True
    )
    assert telnet.sent == ["TFAN010440"]

    # AM is no MHz value, and off the tuner the frequency is unknown.
    telnet.push("TFAN105000")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert hass.states.get(number).state == STATE_UNKNOWN
    telnet.push("TFAN009290", "SICD")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert hass.states.get(number).state == STATE_UNKNOWN
    # Tuning from another input switches the main zone to the tuner first.
    telnet.sent.clear()

    async def tune() -> None:
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number, "value": 99.0}, blocking=True
        )

    task = asyncio.ensure_future(tune())
    await asyncio.sleep(0.05)
    telnet.push("SITUNER")
    await task
    assert telnet.sent == ["SITUNER", "TFAN009900"]


async def test_an_integer_takes_the_range_the_receiver_publishes(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Audio Delay: AudioDelay/Adjust from 2016, AudioDelay itself on older ones."""
    from custom_components.denonavr_plus.number import _resolve_bounds

    device = loaded_entry.runtime_data.device
    spec = device.profile.control("audio_delay")
    assert spec is not None
    meta = device.discovery.numeric_meta
    meta.pop("AudioDelay/Adjust", None)
    meta["AudioDelay"] = {"min": 0.0, "max": 200.0, "step": 1.0}
    assert _resolve_bounds(device, spec) == (0.0, 200.0, 1.0)
    meta["AudioDelay/Adjust"] = {"min": 0.0, "max": 300.0, "step": 1.0}
    assert _resolve_bounds(device, spec) == (0.0, 300.0, 1.0)
    del meta["AudioDelay/Adjust"], meta["AudioDelay"]
    assert _resolve_bounds(device, spec) == (0, 500, 1)
