"""Tests for sensor.py."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import entity_id, registry_entry
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

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


async def test_signal_sensors_follow_the_receiver(hass: HomeAssistant, receiver_network: Any) -> None:
    """Video and channel sensors appear where the setup interface reports them, and follow an input change."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    from homeassistant.const import CONF_HOST

    from custom_components.denonavr_plus.avr.transport import WebConfigClient
    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

    from .test_avr_signal_info import AUDIO, VIDEO

    answers = {3: VIDEO, 2: AUDIO}

    async def get(section: str, type_id: int, extra: Any = None) -> str | None:
        return answers.get(type_id) if section == "info" else None

    with (
        patch.object(WebConfigClient, "async_get", AsyncMock(side_effect=get)),
        patch("custom_components.denonavr_plus.avr.device.SIGNAL_SETTLE", 0.01),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        resolution = entity_id(hass, entry, "sensor_video_input_resolution")
        assert hass.states.get(resolution).state == "3840x2160p 60Hz"
        assert registry_entry(hass, entry, "sensor_audio_active_speakers").disabled_by is not None

        telnet = FakeTelnet.instances[-1]
        # The answer to the resync, then a change of input.
        telnet.push("SIDVD")
        answers[3] = VIDEO.replace("3840x2160p 60Hz", "1920x1080p 50Hz")
        telnet.push("SICD")
        await asyncio.sleep(0.2)
        await hass.async_block_till_done()
        assert hass.states.get(resolution).state == "1920x1080p 50Hz"
        await hass.config_entries.async_unload(entry.entry_id)


async def test_the_activity_follows_the_input_and_the_linked_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from custom_components.denonavr_plus.linked_players import OPT_LINKED_PLAYERS

    def activity() -> str:
        state = hass.states.get(entity_id(hass, loaded_entry, "sensor_activity_main"))
        assert state is not None
        return state.state

    async def settle() -> None:
        await asyncio.sleep(0.15)
        await hass.async_block_till_done()

    telnet = FakeTelnet.instances[-1]
    assert registry_entry(hass, loaded_entry, "sensor_activity_main").disabled_by is None
    telnet.push("ZMON", "SIGAME")
    await settle()
    assert activity() == "game"
    telnet.push("ZMOFF")
    await settle()
    assert activity() == "off"

    # A player linked to the Blu-ray input while the zone is on it.
    hass.states.async_set("media_player.shield", "idle", {})
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={**loaded_entry.options, OPT_LINKED_PLAYERS: {"BD": {"player": "media_player.shield"}}},
    )
    telnet.push("ZMON", "SIBD")
    await settle()
    assert activity() == "film"
    hass.states.async_set("media_player.shield", "playing", {"media_content_type": "episode"})
    await settle()
    assert activity() == "tv"

    # An announcement on the zone.
    loaded_entry.runtime_data.announcing.add("main")
    loaded_entry.runtime_data.async_update_listeners()
    await settle()
    assert activity() == "announcement"
    loaded_entry.runtime_data.announcing.discard("main")


async def test_zone_2_on_the_main_zones_input_has_its_activity(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    FakeTelnet.instances[-1].push("PWON", "ZMON", "SITUNER", "Z2ON", "Z2SOURCE")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, loaded_entry, "sensor_activity_zone2"))
    assert state is not None and state.state == "radio"
