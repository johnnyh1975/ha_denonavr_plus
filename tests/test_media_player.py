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
    # The station is the channel, as other receiver integrations report it.
    assert hass.states.get(main).attributes["media_channel"] == "Bayern 3"


async def test_the_browser_offers_sources_and_home_assistant_media(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import BrowseMedia
    from homeassistant.setup import async_setup_component

    from custom_components.denonavr_plus.media_player import DenonAvrMediaPlayer

    assert await async_setup_component(hass, "media_source", {})
    main = entity_id(hass, loaded_entry, "media_player_main")
    entity = next(
        item
        for item in hass.data["entity_components"]["media_player"].entities
        if item.entity_id == main
    )
    assert isinstance(entity, DenonAvrMediaPlayer)
    media = BrowseMedia(
        media_class="directory",
        media_content_id="media-source://",
        media_content_type="",
        title="Media",
        can_play=False,
        can_expand=True,
    )
    with patch(
        "custom_components.denonavr_plus.media_player.media_source.async_browse_media",
        AsyncMock(return_value=media),
    ):
        root = await entity.async_browse_media()
    ids = [child.media_content_id for child in root.children or []]
    assert "sources" in ids
    assert any(content_id.startswith("media-source://") for content_id in ids)
    sources = await entity.async_browse_media(None, "sources")
    assert isinstance(sources, BrowseMedia)
    assert any(child.media_content_id == "source/CD" for child in sources.children or [])

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": main, "media_content_type": "channel", "media_content_id": "source/CD"},
        blocking=True,
    )
    assert "SICD" in telnet.sent


async def test_a_url_goes_to_heos(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from .test_init import FakeHeos

    main = entity_id(hass, loaded_entry, "media_player_main")
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": main,
            "media_content_type": "music",
            "media_content_id": "http://example.com/stream.mp3",
        },
        blocking=True,
    )
    assert ("url", "http://example.com/stream.mp3") in FakeHeos.instances[-1].calls


async def test_quick_selects_and_home_assistant_media_play(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from types import SimpleNamespace

    from homeassistant.components.media_player import BrowseMedia

    from .test_init import FakeHeos

    device = loaded_entry.runtime_data.device
    device.discovery.quick_select_names = {1: "Kino", 2: "Radio"}
    main = entity_id(hass, loaded_entry, "media_player_main")
    entity = next(
        item
        for item in hass.data["entity_components"]["media_player"].entities
        if item.entity_id == main
    )
    folder = await entity.async_browse_media(None, "quick_select")
    assert [child.title for child in folder.children or []] == ["Kino", "Radio"]
    stub = BrowseMedia(
        media_class="directory",
        media_content_id="media-source://media_source",
        media_content_type="",
        title="Media",
        can_play=False,
        can_expand=True,
    )
    with patch(
        "custom_components.denonavr_plus.media_player.media_source.async_browse_media",
        AsyncMock(return_value=stub),
    ):
        assert (await entity.async_browse_media(None, "media-source://media_source")) is stub

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": main, "media_content_type": "music", "media_content_id": "quick_select/2"},
        blocking=True,
    )
    assert "MSQUICK2" in telnet.sent

    # Home Assistant's media (a TTS message, say) resolves to a URL for HEOS.
    with patch(
        "custom_components.denonavr_plus.media_player.media_source.async_resolve_media",
        AsyncMock(return_value=SimpleNamespace(url="http://192.0.2.1:8123/api/tts_proxy/x.mp3")),
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "media-source://tts/x",
            },
            blocking=True,
        )
    assert ("url", "http://192.0.2.1:8123/api/tts_proxy/x.mp3") in FakeHeos.instances[-1].calls


def _player(hass: HomeAssistant, entity: str) -> Any:
    return next(
        item
        for item in hass.data["entity_components"]["media_player"].entities
        if item.entity_id == entity
    )


async def test_an_input_shows_its_name_and_the_sound_mode(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Nothing plays on the network: the card names the input instead of staying empty."""
    await _push(hass, "PWON", "ZMON", "SIDVD", "MSDOLBY ATMOS")
    main = entity_id(hass, loaded_entry, "media_player_main")
    state = hass.states.get(main)
    assert state is not None
    assert state.attributes["media_title"] == state.attributes["source"]
    assert state.attributes["media_artist"] == state.attributes["sound_mode"]
    assert state.attributes["media_artist"]

    await _push(hass, "ZMOFF")
    state = hass.states.get(main)
    assert state is not None and "media_title" not in state.attributes


async def test_zone_2_shows_what_the_main_zone_plays(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone 2 on SOURCE hears the main zone's network player, and can pause it."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    from .test_init import FakeHeos

    await _push(hass, "PWON", "ZMON", "SINET", "Z2ON", "Z2CD")
    FakeHeos.instances[-1].on_update(
        {
            "state": "play",
            "media_type": "song",
            "title": "Track",
            "artist": "Band",
            "album": None,
            "image_url": "http://example.com/cover.jpg",
            "position": None,
            "duration": None,
            "position_at": None,
            "repeat": None,
            "shuffle": None,
        }
    )
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    state = hass.states.get(zone2)
    assert state is not None
    # On its own input it shows that input, not the main zone's track.
    assert state.attributes["media_title"] == state.attributes["source"]
    assert not MediaPlayerEntityFeature(state.attributes["supported_features"]) & (
        MediaPlayerEntityFeature.PAUSE
    )

    await _push(hass, "Z2SOURCE")
    state = hass.states.get(zone2)
    assert state is not None
    assert state.attributes["media_title"] == "Track"
    assert state.attributes["entity_picture"]
    features = MediaPlayerEntityFeature(state.attributes["supported_features"])
    assert features & MediaPlayerEntityFeature.PAUSE
    assert features & MediaPlayerEntityFeature.REPEAT_SET
    await hass.services.async_call("media_player", "media_pause", {"entity_id": zone2}, blocking=True)
    assert ("pause", None) in FakeHeos.instances[-1].calls


async def test_skip_steps_through_the_tuner_presets(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    await _push(hass, "PWON", "ZMON", "SITUNER", "TPAN07", "Z2ON", "Z2SOURCE")
    telnet = FakeTelnet.instances[-1]
    for zone in ("media_player_main", "media_player_zone2"):
        player = entity_id(hass, loaded_entry, zone)
        state = hass.states.get(player)
        assert state is not None
        assert state.attributes["media_content_id"] == "tuner_preset/07"
        assert MediaPlayerEntityFeature(state.attributes["supported_features"]) & (
            MediaPlayerEntityFeature.NEXT_TRACK
        )
        telnet.sent.clear()
        await hass.services.async_call(
            "media_player", "media_next_track", {"entity_id": player}, blocking=True
        )
        await hass.services.async_call(
            "media_player", "media_previous_track", {"entity_id": player}, blocking=True
        )
        assert telnet.sent == ["TPANUP", "TPANDOWN"]


async def test_the_browser_recalls_a_tuner_preset(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import BrowseError
    from homeassistant.exceptions import ServiceValidationError

    await _push(hass, "PWON", "ZMON", "SITUNER")
    main = entity_id(hass, loaded_entry, "media_player_main")
    entity = _player(hass, main)
    with patch(
        "custom_components.denonavr_plus.media_player.media_source.async_browse_media",
        AsyncMock(side_effect=BrowseError),
    ):
        root = await entity.async_browse_media()
    assert "tuner_presets" in [child.media_content_id for child in root.children or []]
    folder = await entity.async_browse_media(None, "tuner_presets")
    codes = [child.title for child in folder.children or []]
    assert (codes[0], codes[-1], len(codes)) == ("01", "56", 56)

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": main, "media_content_type": "channel", "media_content_id": "tuner_preset/05"},
        blocking=True,
    )
    assert telnet.sent == ["TPAN05"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": main, "media_content_type": "channel", "media_content_id": "tuner_preset/99"},
            blocking=True,
        )


async def test_zone_2_joins_the_main_zone_as_a_group(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """join puts zone 2 on the main zone's source (Z2SOURCE), unjoin keeps the input."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature
    from homeassistant.exceptions import ServiceValidationError

    await _push(hass, "PWON", "ZMON", "SIDVD", "Z2ON", "Z2CD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    state = hass.states.get(main)
    assert state is not None
    assert MediaPlayerEntityFeature(state.attributes["supported_features"]) & (
        MediaPlayerEntityFeature.GROUPING
    )
    assert state.attributes["group_members"] == [main]
    assert hass.states.get(zone2).attributes["group_members"] == [zone2]

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    # A zone that follows nothing has nothing to leave.
    await hass.services.async_call("media_player", "unjoin", {"entity_id": zone2}, blocking=True)
    assert telnet.sent == []
    await hass.services.async_call(
        "media_player", "join", {"entity_id": main, "group_members": [main, zone2]}, blocking=True
    )
    assert telnet.sent == ["Z2SOURCE"]
    await _push(hass, "Z2SOURCE")
    for player in (main, zone2):
        assert hass.states.get(player).attributes["group_members"] == [main, zone2]

    telnet.sent.clear()
    await hass.services.async_call("media_player", "unjoin", {"entity_id": zone2}, blocking=True)
    assert telnet.sent == ["Z2DVD"]
    await _push(hass, "Z2SOURCE")
    telnet.sent.clear()
    await hass.services.async_call("media_player", "unjoin", {"entity_id": main}, blocking=True)
    assert telnet.sent == ["Z2DVD"]

    # Only the main zone leads, and only the receiver's own zones join.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player", "join", {"entity_id": zone2, "group_members": [main]}, blocking=True
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "join",
            {"entity_id": main, "group_members": ["media_player.kitchen"]},
            blocking=True,
        )


async def test_transport_only_while_the_network_plays(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """On a TV input play and pause would reach nothing, so they are not offered."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    main = entity_id(hass, loaded_entry, "media_player_main")
    await _push(hass, "PWON", "ZMON", "SIDVD")
    features = MediaPlayerEntityFeature(hass.states.get(main).attributes["supported_features"])
    assert not features & MediaPlayerEntityFeature.PAUSE
    await _push(hass, "SINET")
    features = MediaPlayerEntityFeature(hass.states.get(main).attributes["supported_features"])
    assert features & MediaPlayerEntityFeature.PAUSE


async def test_play_media_turns_the_zone_on_first(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMOFF")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    async def answer() -> None:
        for _ in range(100):
            if "ZMON" in telnet.sent:
                telnet.push("ZMON")
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(answer())
    with patch("custom_components.denonavr_plus.media_player.TURN_ON_SETTLE", 0):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": main, "media_content_type": "channel", "media_content_id": "source/CD"},
            blocking=True,
        )
    await task
    assert telnet.sent == ["ZMON", "SICD"]

    # A zone that does not report on in time still gets the command.
    await _push(hass, "ZMOFF")
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.media_player.TURN_ON_WAIT", 0.05):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": main, "media_content_type": "channel", "media_content_id": "source/CD"},
            blocking=True,
        )
    assert telnet.sent == ["ZMON", "SICD"]


async def test_home_assistant_media_sits_next_to_the_receivers_folders(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_source import BrowseMediaSource

    child = BrowseMediaSource(
        domain="tts",
        identifier=None,
        media_class="app",
        media_content_type="",
        title="Text to speech",
        can_play=False,
        can_expand=True,
    )
    overview = BrowseMediaSource(
        domain=None,
        identifier=None,
        media_class="directory",
        media_content_type="",
        title="Media",
        can_play=False,
        can_expand=True,
        children=[child],
    )
    entity = _player(hass, entity_id(hass, loaded_entry, "media_player_main"))
    with patch(
        "custom_components.denonavr_plus.media_player.media_source.async_browse_media",
        AsyncMock(return_value=overview),
    ):
        root = await entity.async_browse_media()
    assert [item.title for item in root.children or []][-1] == "Text to speech"


async def test_zone_2_browses_and_plays_its_own_items(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Zone 2 has its own quick selects (Z2QUICK), sources and tuner presets; URLs stay on main."""
    from homeassistant.exceptions import ServiceValidationError

    from .test_avr_device import _x2000_entry

    entry = await _x2000_entry(hass)
    device = entry.runtime_data.device
    device.discovery.quick_select_names = {1: "Kino", 2: "Radio"}
    zone2 = entity_id(hass, entry, "media_player_zone2")
    player = _player(hass, zone2)
    root = await player.async_browse_media()
    ids = [child.media_content_id for child in root.children or []]
    assert ids[:2] == ["quick_select", "sources"]
    assert not any(content_id.startswith("media-source://") for content_id in ids)
    folder = await player.async_browse_media(None, "quick_select")
    assert [child.title for child in folder.children or []] == ["Quick Select 1", "Quick Select 2"]

    telnet = FakeTelnet.instances[-1]
    telnet.connected = True
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": zone2, "media_content_type": "playlist", "media_content_id": "quick_select/2"},
        blocking=True,
    )
    assert telnet.sent == ["Z2QUICK2"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": zone2, "media_content_type": "playlist", "media_content_id": "quick_select/9"},
            blocking=True,
        )


async def test_a_url_plays_on_the_main_zone_only(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.exceptions import ServiceValidationError

    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": zone2, "media_content_type": "music", "media_content_id": "http://example.com/a.mp3"},
            blocking=True,
        )
    assert err.value.translation_key == "play_url_main_zone"


async def test_volume_steps_by_the_zones_option(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={"main": {volume.OPT_VOLUME_STEP: 2.0}, "zone2": {volume.OPT_VOLUME_STEP: 1.0}},
    )
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMON", "MV50", "Z2ON", "Z240")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    main = entity_id(hass, loaded_entry, "media_player_main")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    await hass.services.async_call("media_player", "volume_up", {"entity_id": main}, blocking=True)
    await hass.services.async_call("media_player", "volume_down", {"entity_id": main}, blocking=True)
    await hass.services.async_call("media_player", "volume_down", {"entity_id": zone2}, blocking=True)
    assert telnet.sent == ["MV52", "MV48", "Z239"]


async def test_choosing_a_source_turns_the_zone_on_first(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMOFF")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.media_player.TURN_ON_WAIT", 0.05):
        await hass.services.async_call(
            "media_player", "select_source", {"entity_id": main, "source": "CD"}, blocking=True
        )
    assert telnet.sent[0] == "ZMON" and telnet.sent[-1] == "SICD"


async def test_quick_selects_sit_in_the_source_menu(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """As the HEOS integration lists its favorites: pick "Kino" from the source menu."""
    device = loaded_entry.runtime_data.device
    device.discovery.quick_select_names = {1: "Kino", 2: "CD", 3: "Radio"}
    await _push(hass, "PWON", "ZMON", "SICD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    sources = hass.states.get(main).attributes["source_list"]
    # A quick select named like an input stays out: the input wins.
    assert sources[-2:] == ["Kino", "Radio"] and sources.count("CD") == 1
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    assert "Kino" not in hass.states.get(zone2).attributes["source_list"]

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": main, "source": "Radio"}, blocking=True
    )
    assert telnet.sent == ["MSQUICK3"]


async def test_an_input_shows_its_signal_below(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "SIDVD", "SYSDA DOLBY TRUEHD   ", "SSINFAISFSV48K")
    main = entity_id(hass, loaded_entry, "media_player_main")
    assert hass.states.get(main).attributes["media_album_name"] == "DOLBY TRUEHD · 48 kHz"
    await _push(hass, "SSINFAISFSVNON")
    assert hass.states.get(main).attributes["media_album_name"] == "DOLBY TRUEHD"


async def test_main_off_can_put_the_whole_receiver_in_standby(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "Z2ON")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": main}, blocking=True)
    assert telnet.sent == ["ZMOFF"]

    hass.config_entries.async_update_entry(loaded_entry, options={"main_off_standby": True})
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMON", "Z2ON")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call("media_player", "turn_off", {"entity_id": main}, blocking=True)
    assert telnet.sent == ["PWSTANDBY"]
