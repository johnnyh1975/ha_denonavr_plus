"""Tests for media_player.py: the zones stay usable over HTTP without telnet."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.denonavr_plus import volume
from custom_components.denonavr_plus.avr.transport import GoformClient

from .common import entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401

__all__ = ["loaded_entry", "receiver_network"]

MAIN_STATUS: dict[str, Any] = {"power": True, "volume_db": -30.0, "muted": False}
ZONE2_STATUS: dict[str, Any] = {"power": True, "volume_db": -50.0, "source": "TUNER"}


async def _status(path: str) -> dict[str, Any] | None:
    return ZONE2_STATUS if "Zone2" in path else MAIN_STATUS


async def _drop_telnet_and_poll(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    for _ in range(2):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_zones_stay_usable_over_http_without_telnet(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    dynamic_eq = entity_id(hass, loaded_entry, "switch_dynamic_eq")
    with patch.object(GoformClient, "async_get_status", side_effect=_status):
        await _drop_telnet_and_poll(hass, freezer)

    main_state = hass.states.get(main)
    zone2_state = hass.states.get(zone2)
    assert main_state is not None and main_state.state == "on"
    assert zone2_state is not None and zone2_state.state == "on"
    assert zone2_state.attributes["source"] is not None
    # Settings need telnet.
    eq_state = hass.states.get(dynamic_eq)
    assert eq_state is not None and eq_state.state == STATE_UNAVAILABLE


async def test_commands_go_over_http_without_telnet(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    send = AsyncMock(return_value=True)
    with (
        patch.object(GoformClient, "async_get_status", side_effect=_status),
        patch.object(GoformClient, "async_send_command", send),
    ):
        await _drop_telnet_and_poll(hass, freezer)
        await hass.services.async_call(
            "media_player", "volume_up", {"entity_id": main}, blocking=True
        )
    send.assert_awaited_once_with("MVUP")
    assert "MVUP" not in FakeTelnet.instances[-1].sent


async def test_unreachable_receiver_makes_the_players_unavailable(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)):
        await _drop_telnet_and_poll(hass, freezer)
    state = hass.states.get(main)
    assert state is not None and state.state == STATE_UNAVAILABLE


async def _push(hass: HomeAssistant, *lines: str) -> None:
    FakeTelnet.instances[-1].push(*lines)
    # Device updates are debounced by 0.1 s before they reach the coordinator.
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def test_official_scale_sets_db_plus_80_over_100(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry, options={volume.OPT_OFFICIAL_SCALE: True}
    )
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": main, "volume_level": 0.4}, blocking=True
    )
    # 0.4 is -40 dB on the official scale: raw 40.
    assert "MV40" in telnet.sent


async def test_volume_up_stops_at_the_zone_limit(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry, options={"main": {volume.OPT_MAX_VOLUME: -30.0}}
    )
    await _push(hass, "MV50")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call("media_player", "volume_up", {"entity_id": main}, blocking=True)
    assert telnet.sent == ["MV50"]
    assert "MVUP" not in telnet.sent


async def test_turn_on_applies_the_turn_on_volume(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry, options={"main": {volume.OPT_TURN_ON_VOLUME: -35.0}}
    )
    await _push(hass, "ZMOFF")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.media_player.TURN_ON_SETTLE", 0):
        await hass.services.async_call("media_player", "turn_on", {"entity_id": main}, blocking=True)
        # Nothing before the zone reports that it is on.
        assert "MV45" not in telnet.sent
        await _push(hass, "ZMON")
        await asyncio.sleep(0.3)
        await hass.async_block_till_done()
    assert "MV45" in telnet.sent


async def test_sources_follow_the_selection_and_names(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus import choices

    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            choices.OPT_SOURCES: {"main": ["DVD", "TUNER"]},
            choices.OPT_SOURCE_NAMES: {"DVD": "Player"},
        },
    )
    await _push(hass, "SIDVD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    state = hass.states.get(main)
    assert state is not None
    assert state.attributes["source_list"] == ["Player", "TUNER"]
    assert state.attributes["source"] == "Player"
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": main, "source": "Player"}, blocking=True
    )
    assert "SIDVD" in telnet.sent


async def test_a_renamed_sound_mode_is_sent_under_the_receiver_name(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus import choices

    hass.config_entries.async_update_entry(
        loaded_entry, options={choices.OPT_SOUND_MODE_NAMES: {"Stereo": "Two channels"}}
    )
    await _push(hass, "OPSMLALL MUS011Stereo", "OPSMLALL END")
    main = entity_id(hass, loaded_entry, "media_player_main")
    state = hass.states.get(main)
    assert state is not None and "Two channels" in state.attributes["sound_mode_list"]
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "select_sound_mode",
        {"entity_id": main, "sound_mode": "Two channels"}, blocking=True,
    )
    assert "MSSTEREO" in telnet.sent


async def test_a_command_that_does_not_reach_the_receiver_is_reported(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Over HTTP the receiver refused: the user learns it, nothing is lost silently."""
    main = entity_id(hass, loaded_entry, "media_player_main")
    with (
        patch.object(GoformClient, "async_get_status", side_effect=_status),
        patch.object(GoformClient, "async_send_command", AsyncMock(return_value=False)),
    ):
        await _drop_telnet_and_poll(hass, freezer)
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call(
                "media_player", "volume_up", {"entity_id": main}, blocking=True
            )
    assert err.value.translation_key == "receiver_unreachable"


async def test_without_heos_there_is_no_transport(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    from homeassistant.components.media_player import MediaPlayerEntityFeature
    from homeassistant.const import CONF_HOST

    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10"},
        options={"heos": False},
        minor_version=CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, entry, "media_player_main"))
    assert state is not None
    features = MediaPlayerEntityFeature(state.attributes["supported_features"])
    assert not features & MediaPlayerEntityFeature.PLAY
    assert features & MediaPlayerEntityFeature.SELECT_SOUND_MODE


@pytest.mark.assumed(
    "the MS tokens come from the AVR-X2000/E400 protocol document (v03); that the "
    "receiver takes them from a Home Assistant select has not been tried on one"
)
async def test_an_avr_x2000_offers_the_sound_modes_it_lists(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """An AVR-X2000 answers no OPSML: its list came out empty but for the active mode."""
    from homeassistant.const import CONF_HOST, CONF_PORT

    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

    from .conftest import deviceinfo

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.11", CONF_PORT: 80},
        minor_version=CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    with patch.object(
        GoformClient,
        "async_get_device_info",
        AsyncMock(return_value=deviceinfo("AVR-X2000-Deviceinfo.xml")),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.device.discovery.device.hardware_type == "avr-x"
    await _push(hass, "MSMCH STEREO")
    main = entity_id(hass, entry, "media_player_main")
    state = hass.states.get(main)
    assert state is not None
    assert state.attributes["sound_mode"] == "MULTI CH STEREO"
    modes = state.attributes["sound_mode_list"]
    assert {"STEREO", "PURE DIRECT", "MULTI CH STEREO", "MOVIE"} <= set(modes)
    assert "PURE" not in modes and "MCH STEREO" not in modes
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "select_sound_mode",
        {"entity_id": main, "sound_mode": "MULTI CH STEREO"}, blocking=True,
    )
    assert "MSMCH STEREO" in telnet.sent


async def test_position_repeat_and_shuffle_come_from_heos(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    from .test_init import FakeHeos

    heos_client = FakeHeos.instances[-1]
    heos_client.on_update(
        {
            "state": "play",
            "media_type": "song",
            "title": "Track",
            "artist": None,
            "album": None,
            "image_url": None,
            "position": 42.0,
            "duration": 240.0,
            "position_at": 1_700_000_000.0,
            "repeat": "on_one",
            "shuffle": True,
        }
    )
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    main = entity_id(hass, loaded_entry, "media_player_main")
    state = hass.states.get(main)
    assert state is not None
    assert state.attributes["media_position"] == 42
    assert state.attributes["media_duration"] == 240
    assert state.attributes["repeat"] == "one"
    assert state.attributes["shuffle"] is True
    features = MediaPlayerEntityFeature(state.attributes["supported_features"])
    assert features & MediaPlayerEntityFeature.REPEAT_SET
    await hass.services.async_call(
        "media_player", "repeat_set", {"entity_id": main, "repeat": "all"}, blocking=True
    )
    await hass.services.async_call(
        "media_player", "shuffle_set", {"entity_id": main, "shuffle": False}, blocking=True
    )
    assert heos_client.calls == [("repeat", "on_all"), ("shuffle", False)]


async def test_the_tuner_shows_its_station_or_frequency(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "SITUNER", "TFAN009290")
    main = entity_id(hass, loaded_entry, "media_player_main")
    state = hass.states.get(main)
    assert state is not None
    assert state.attributes["media_title"] == "FM 92.90 MHz"
    await _push(hass, "TFANNAMEBayern 3")
    assert hass.states.get(main).attributes["media_title"] == "Bayern 3"
