"""Tests for sensor.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import registry_entry
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_only_the_decoder_starts_enabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert registry_entry(hass, loaded_entry, "sensor_decoder").disabled_by is None
    # Values that change with every track, and the sound mode the media player
    # already shows, start disabled.
    for key in (
        "sensor_sample_rate",
        "sensor_input_signal",
        "sensor_audio_format",
        "sensor_mode_info",
        "sensor_sound_mode",
    ):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key


async def test_the_sound_mode_sensor_shows_the_mode(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from .common import async_enable, entity_id
    from .test_init import FakeTelnet

    await async_enable(hass, loaded_entry, "sensor_sound_mode")
    FakeTelnet.instances[-1].push("MSSTEREO")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, loaded_entry, "sensor_sound_mode"))
    assert state is not None and state.state not in ("unknown", "unavailable")


async def test_the_source_sensor_shows_the_name_home_assistant_uses(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from custom_components.denonavr_plus import choices

    from .common import async_enable, entity_id
    from .test_init import FakeTelnet

    assert registry_entry(hass, loaded_entry, "sensor_source_main").disabled_by is not None
    hass.config_entries.async_update_entry(
        loaded_entry, options={choices.OPT_SOURCE_NAMES: {"TUNER": "Radio"}}
    )
    await async_enable(hass, loaded_entry, "sensor_source_main")
    FakeTelnet.instances[-1].push("SITUNER")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, loaded_entry, "sensor_source_main"))
    assert state is not None and state.state == "Radio"
