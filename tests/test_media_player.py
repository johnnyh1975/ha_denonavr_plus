"""Tests for media_player.py: the zones stay usable over HTTP without telnet."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.media_player import (
    BrowseError,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.denonavr_plus import volume
from custom_components.denonavr_plus.avr.errors import CommandNotSent
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
        "media_player",
        "volume_set",
        {"entity_id": main, "volume_level": 0.4},
        blocking=True,
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
    await hass.services.async_call(
        "media_player", "volume_up", {"entity_id": main}, blocking=True
    )
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
        await hass.services.async_call(
            "media_player", "turn_on", {"entity_id": main}, blocking=True
        )
        # Nothing before the zone reports that it is on.
        assert "MV45" not in telnet.sent
        await _push(hass, "ZMON")
        await asyncio.sleep(0.3)
        await hass.async_block_till_done()
    assert "MV45" in telnet.sent


_RAMP_INTERVAL = "custom_components.denonavr_plus.media_player.RAMP_MIN_INTERVAL"


async def _ramp(hass: HomeAssistant, entity: str, level: float, duration: float) -> None:
    from custom_components.denonavr_plus.const import DOMAIN

    await hass.services.async_call(
        DOMAIN,
        "volume_ramp",
        {"entity_id": entity, "volume_level": level, "duration": duration},
        blocking=True,
    )


async def test_volume_ramp_steps_to_the_level(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """One receiver step at a time while the time allows, fewer steps when it does not."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={volume.OPT_OFFICIAL_SCALE: True}
    )
    await _push(hass, "PWON", "ZMON", "MV50")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.echo = True
    telnet.sent.clear()
    with patch(_RAMP_INTERVAL, 0.01):
        # 0.48 is raw 48: four half-dB steps down.
        await _ramp(hass, main, 0.48, 0.1)
        assert telnet.sent == []
        await _eventually(lambda: "MV48" in telnet.sent)
    assert telnet.sent == ["MV495", "MV49", "MV485", "MV48"]

    await _push(hass, "MV48")
    telnet.sent.clear()
    # Ten dB in half a second: two steps, a quarter second apart.
    await _ramp(hass, main, 0.58, 0.5)
    await _eventually(lambda: "MV58" in telnet.sent)
    assert telnet.sent == ["MV53", "MV58"]

    telnet.sent.clear()
    await _ramp(hass, main, 0.4, 0)
    assert telnet.sent == ["MV40"]


async def test_a_volume_command_stops_the_ramp(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry, options={volume.OPT_OFFICIAL_SCALE: True}
    )
    await _push(hass, "PWON", "ZMON", "MV50")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with patch(_RAMP_INTERVAL, 0.01):
        # Twenty steps, a tenth of a second apart.
        await _ramp(hass, main, 0.4, 2)
        await hass.services.async_call(
            "media_player", "volume_set", {"entity_id": main, "volume_level": 0.3}, blocking=True
        )
        await asyncio.sleep(0.3)
    assert telnet.sent == ["MV30"]


@pytest.mark.parametrize("elsewhere", ["ZMOFF", "MV45"])
async def test_the_ramp_stops_when_the_zone_changes_elsewhere(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, elsewhere: str
) -> None:
    """Switched off by the power switch or the remote, or the volume set by the knob."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={volume.OPT_OFFICIAL_SCALE: True}
    )
    await _push(hass, "PWON", "ZMON", "MV50")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.echo = True
    telnet.sent.clear()
    with patch(_RAMP_INTERVAL, 0.01):
        # Twenty steps, a tenth of a second apart.
        await _ramp(hass, main, 0.4, 2)
        await _eventually(lambda: "MV495" in telnet.sent)
        telnet.push(elsewhere)
        sent = len(telnet.sent)
        await asyncio.sleep(0.3)
    assert len(telnet.sent) == sent


async def test_an_announcement_or_a_restore_stops_the_ramp(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus.const import DOMAIN

    hass.config_entries.async_update_entry(
        loaded_entry, options={volume.OPT_OFFICIAL_SCALE: True}
    )
    await _push(hass, "PWON", "ZMON", "MV50")
    main = entity_id(hass, loaded_entry, "media_player_main")
    await hass.services.async_call(DOMAIN, "snapshot", {"entity_id": main}, blocking=True)
    telnet = FakeTelnet.instances[-1]
    with patch(_RAMP_INTERVAL, 0.01):
        await _ramp(hass, main, 0.4, 2)
        await hass.services.async_call(DOMAIN, "restore", {"entity_id": main}, blocking=True)
        telnet.sent.clear()
        await asyncio.sleep(0.3)
    assert not [command for command in telnet.sent if command.startswith("MV")]


async def test_volume_ramp_needs_the_zone_on_and_survives_a_lost_receiver(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.exceptions import ServiceValidationError

    main = entity_id(hass, loaded_entry, "media_player_main")
    await _push(hass, "PWON", "ZMOFF", "MV50")
    with pytest.raises(ServiceValidationError) as err:
        await _ramp(hass, main, 0.4, 5)
    assert err.value.translation_key == "volume_ramp_zone_off"

    await _push(hass, "ZMON")
    device = loaded_entry.runtime_data.device
    with (
        patch(_RAMP_INTERVAL, 0.01),
        patch.object(
            type(device), "async_set_volume_raw", AsyncMock(side_effect=CommandNotSent("gone"))
        ) as set_volume,
    ):
        await _ramp(hass, main, 0.4, 0.1)
        await _eventually(lambda: set_volume.await_count == 1)
        await asyncio.sleep(0.2)
    # The first failed step ends the ramp; nothing is raised from the task.
    assert set_volume.await_count == 1


async def test_sound_modes_show_readable_names(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """As an AVR-X2600H lists them; the receiver's names still choose a mode."""
    main = entity_id(hass, loaded_entry, "media_player_main")
    await _push(
        hass,
        "PWON",
        "ZMON",
        "OPSMLALL MOV010Stereo",
        "OPSMLALL MOV021Dolby Audio - DD + DSur",
        "OPSMLALL MOV030Multi Ch Stereo",
        "OPSMLALL END",
        "OPSML 010Stereo",
        "OPSML 021Dolby Audio - DD + DSur",
        "OPSML END",
    )
    state = hass.states.get(main)
    assert state.attributes["sound_mode_list"] == ["Stereo", "Dolby Surround", "Multichannel stereo"]
    assert state.attributes["sound_mode"] == "Dolby Surround"
    telnet = FakeTelnet.instances[-1]
    for name in ("Multichannel stereo", "Multi Ch Stereo"):
        telnet.sent.clear()
        await hass.services.async_call(
            "media_player", "select_sound_mode", {"entity_id": main, "sound_mode": name}, blocking=True
        )
        assert "MSMCH STEREO" in telnet.sent

    # Switched off in the options: the receiver's names.
    hass.config_entries.async_update_entry(
        loaded_entry, options={"readable_sound_modes": False}
    )
    await hass.async_block_till_done()
    assert hass.states.get(main).attributes["sound_mode"] == "Dolby Audio - DD + DSur"


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
        "media_player",
        "select_source",
        {"entity_id": main, "source": "Player"},
        blocking=True,
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
        "media_player",
        "select_sound_mode",
        {"entity_id": main, "sound_mode": "Two channels"},
        blocking=True,
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
    # Shown as words; the receiver's capitals stay valid for choosing.
    assert state.attributes["sound_mode"] == "Multichannel stereo"
    modes = state.attributes["sound_mode_list"]
    assert {"Stereo", "Pure Direct", "Multichannel stereo", "Movie"} <= set(modes)
    assert "Pure" not in modes and "MCH STEREO" not in modes
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "select_sound_mode",
        {"entity_id": main, "sound_mode": "MULTI CH STEREO"},
        blocking=True,
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
        "media_player",
        "repeat_set",
        {"entity_id": main, "repeat": "all"},
        blocking=True,
    )
    await hass.services.async_call(
        "media_player",
        "shuffle_set",
        {"entity_id": main, "shuffle": False},
        blocking=True,
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
    assert any(
        child.media_content_id == "source/CD" for child in sources.children or []
    )

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": main,
            "media_content_type": "channel",
            "media_content_id": "source/CD",
        },
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
        assert (
            await entity.async_browse_media(None, "media-source://media_source")
        ) is stub

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": main,
            "media_content_type": "music",
            "media_content_id": "quick_select/2",
        },
        blocking=True,
    )
    assert "MSQUICK2" in telnet.sent

    # Home Assistant's media (a TTS message, say) resolves to a URL for HEOS.
    with patch(
        "custom_components.denonavr_plus.media_player.media_source.async_resolve_media",
        AsyncMock(
            return_value=SimpleNamespace(
                url="http://192.0.2.1:8123/api/tts_proxy/x.mp3"
            )
        ),
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
    assert ("url", "http://192.0.2.1:8123/api/tts_proxy/x.mp3") in FakeHeos.instances[
        -1
    ].calls


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
    await hass.services.async_call(
        "media_player", "media_pause", {"entity_id": zone2}, blocking=True
    )
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
        {
            "entity_id": main,
            "media_content_type": "channel",
            "media_content_id": "tuner_preset/05",
        },
        blocking=True,
    )
    assert telnet.sent == ["TPAN05"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "channel",
                "media_content_id": "tuner_preset/99",
            },
            blocking=True,
        )


async def test_zone_2_joins_the_main_zone_as_a_group(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """join puts zone 2 on the main zone's source (Z2SOURCE), unjoin keeps the input.

    Z2SOURCE is confirmed on an AVR-X2600H, which turned zone 2 on with it
    (tests/fixtures/command_test).
    """
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
    await hass.services.async_call(
        "media_player", "unjoin", {"entity_id": zone2}, blocking=True
    )
    assert telnet.sent == []
    await hass.services.async_call(
        "media_player",
        "join",
        {"entity_id": main, "group_members": [main, zone2]},
        blocking=True,
    )
    assert telnet.sent == ["Z2SOURCE"]
    await _push(hass, "Z2SOURCE")
    for player in (main, zone2):
        assert hass.states.get(player).attributes["group_members"] == [main, zone2]

    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "unjoin", {"entity_id": zone2}, blocking=True
    )
    assert telnet.sent == ["Z2DVD"]
    await _push(hass, "Z2SOURCE")
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "unjoin", {"entity_id": main}, blocking=True
    )
    assert telnet.sent == ["Z2DVD"]

    # Only the main zone leads, and only the receiver's own zones join.
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "join",
            {"entity_id": zone2, "group_members": [main]},
            blocking=True,
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
    features = MediaPlayerEntityFeature(
        hass.states.get(main).attributes["supported_features"]
    )
    assert not features & MediaPlayerEntityFeature.PAUSE
    await _push(hass, "SINET")
    features = MediaPlayerEntityFeature(
        hass.states.get(main).attributes["supported_features"]
    )
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
            {
                "entity_id": main,
                "media_content_type": "channel",
                "media_content_id": "source/CD",
            },
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
            {
                "entity_id": main,
                "media_content_type": "channel",
                "media_content_id": "source/CD",
            },
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
    assert [child.title for child in folder.children or []] == [
        "Quick Select 1",
        "Quick Select 2",
    ]

    telnet = FakeTelnet.instances[-1]
    telnet.connected = True
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": zone2,
            "media_content_type": "playlist",
            "media_content_id": "quick_select/2",
        },
        blocking=True,
    )
    assert telnet.sent == ["Z2QUICK2"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": zone2,
                "media_content_type": "playlist",
                "media_content_id": "quick_select/9",
            },
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
            {
                "entity_id": zone2,
                "media_content_type": "music",
                "media_content_id": "http://example.com/a.mp3",
            },
            blocking=True,
        )
    assert err.value.translation_key == "play_url_main_zone"


async def test_volume_steps_by_the_zones_option(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            "main": {volume.OPT_VOLUME_STEP: 2.0},
            "zone2": {volume.OPT_VOLUME_STEP: 1.0},
        },
    )
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMON", "MV50", "Z2ON", "Z240")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    main = entity_id(hass, loaded_entry, "media_player_main")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    await hass.services.async_call(
        "media_player", "volume_up", {"entity_id": main}, blocking=True
    )
    await hass.services.async_call(
        "media_player", "volume_down", {"entity_id": main}, blocking=True
    )
    await hass.services.async_call(
        "media_player", "volume_down", {"entity_id": zone2}, blocking=True
    )
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
            "media_player",
            "select_source",
            {"entity_id": main, "source": "CD"},
            blocking=True,
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
        "media_player",
        "select_source",
        {"entity_id": main, "source": "Radio"},
        blocking=True,
    )
    assert telnet.sent == ["MSQUICK3"]


async def test_an_input_shows_its_signal_below(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(
        hass, "PWON", "ZMON", "SIDVD", "SYSDA DOLBY TRUEHD   ", "SSINFAISFSV48K"
    )
    main = entity_id(hass, loaded_entry, "media_player_main")
    assert (
        hass.states.get(main).attributes["media_album_name"] == "DOLBY TRUEHD · 48 kHz"
    )
    await _push(hass, "SSINFAISFSVNON")
    assert hass.states.get(main).attributes["media_album_name"] == "DOLBY TRUEHD"
    await _push(hass, "SSINFAISFSV2.8M")
    assert hass.states.get(main).attributes["media_album_name"] == "DOLBY TRUEHD · 2.8M"


@pytest.mark.assumed(
    "an AVR-X2600H answered SSINFAISFSV 441 to a PCM signal; that bare digits are "
    "the rate (44.1 kHz) is read from marantz-denon-mqtt, settled by a 48 kHz signal"
)
async def test_a_sample_rate_in_bare_digits(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "SIDVD", "SYSDA PCM   ", "SSINFAISFSV 441")
    main = entity_id(hass, loaded_entry, "media_player_main")
    assert hass.states.get(main).attributes["media_album_name"] == "PCM · 44.1 kHz"


@pytest.mark.assumed(
    "PWSTANDBY puts the whole receiver in standby per the documents; not yet sent "
    "from the option on a receiver"
)
async def test_main_off_can_put_the_whole_receiver_in_standby(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "Z2ON")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": main}, blocking=True
    )
    assert telnet.sent == ["ZMOFF"]

    hass.config_entries.async_update_entry(
        loaded_entry, options={"main_off_standby": True}
    )
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMON", "Z2ON")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": main}, blocking=True
    )
    assert telnet.sent == ["PWSTANDBY"]


async def test_heos_music_and_favorites_on_a_heos_receiver(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """HEOS sources in the browser, favorites in the source menu, enqueue options."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    from .test_init import FakeHeos

    heos = FakeHeos.instances[-1]
    heos.connected = True
    heos.favorites = ["Bayern 3", "CD"]
    await _push(hass, "PWON", "ZMON", "SICD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    state = hass.states.get(main)
    assert MediaPlayerEntityFeature(state.attributes["supported_features"]) & (
        MediaPlayerEntityFeature.MEDIA_ENQUEUE
    )
    # A favorite named like an input stays out of the menu.
    assert state.attributes["source_list"][-1] == "Bayern 3"
    assert state.attributes["source_list"].count("CD") == 1

    entity = _player(hass, main)
    with patch(
        "custom_components.denonavr_plus.media_player.media_source.async_browse_media",
        AsyncMock(side_effect=BrowseError),
    ):
        root = await entity.async_browse_media()
    assert "heos" in [child.media_content_id for child in root.children or []]
    sources = await entity.async_browse_media(None, "heos")
    assert [child.title for child in sources.children or []] == ["Favorites", "Queue"]

    await hass.services.async_call(
        "media_player",
        "select_source",
        {"entity_id": main, "source": "Bayern 3"},
        blocking=True,
    )
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": main,
            "media_content_type": "music",
            "media_content_id": "heos_container/1024/a%261/Album",
            "enqueue": "next",
        },
        blocking=True,
    )
    assert heos.calls[-2:] == [
        ("play_preset", 1),
        ("add_to_queue", (1024, "a&1", 2, None)),
    ]

    # Without the HEOS link there is nothing to browse or play from it.
    heos.connected = False
    with pytest.raises(BrowseError):
        await entity.async_browse_media(None, "heos")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "heos_favorite/1",
            },
            blocking=True,
        )


def _heos_state(state: str | None) -> dict[str, Any]:
    return {
        "state": state,
        "media_type": "station" if state else None,
        "title": "Announcement" if state else None,
        "artist": None,
        "album": None,
        "image_url": None,
        "position": None,
        "duration": None,
        "position_at": None,
        "repeat": None,
        "shuffle": None,
    }


async def _eventually(condition: Any, timeout: float = 3.0) -> None:
    """Wait until a condition holds (fails the test after `timeout` seconds)."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_an_announcement_puts_the_zone_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Input, volume and mute are kept, the announcement plays loud enough, then all is back."""
    from .test_init import FakeHeos

    hass.config_entries.async_update_entry(
        loaded_entry, options={"main": {volume.OPT_ANNOUNCE_VOLUME: -10.0}}
    )
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMON", "SITV", "MV50", "MUON")
    heos = FakeHeos.instances[-1]
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]

    def restored() -> list[str]:
        # The receiver's own queries may run in between (a resync after the input change).
        return [c for c in telnet.sent if c[:2] in ("SI", "MV", "MU", "ZM", "PW")]

    async def announce(extra: dict[str, Any] | None = None) -> None:
        data = {
            "entity_id": main,
            "media_content_type": "music",
            "media_content_id": "http://example.com/tts.mp3",
            "announce": True,
        }
        if extra:
            data["extra"] = extra
        await hass.services.async_call(
            "media_player", "play_media", data, blocking=True
        )

    telnet.sent.clear()
    with (
        patch("custom_components.denonavr_plus.media_player.ANNOUNCE_POLL", 0.01),
        patch("custom_components.denonavr_plus.media_player.ANNOUNCE_STOP_GRACE", 0.5),
        patch("custom_components.denonavr_plus.media_player.ANNOUNCE_SETTLE", 0.05),
    ):
        await announce()
        # Loud enough, and a muted zone is unmuted for it.
        assert telnet.sent == ["MV70", "MUOFF"]
        assert heos.calls[-1] == ("url", "http://example.com/tts.mp3")
        await _push(hass, "SINET", "MV70", "MUOFF")
        heos.on_update(_heos_state("play"))
        # A second announcement while the first plays keeps the first snapshot.
        await announce({"volume": 0.5})
        # HEOS stops the first stream, then plays the second: a play before
        # that stop is still the first one.
        heos.on_update(_heos_state("stop"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.05)
        telnet.sent.clear()
        # A short stop while HEOS switches is no end.
        heos.on_update(_heos_state("stop"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.6)
        assert restored() == []
        # Meanwhile its input changes are no one's choice (linked players).
        assert loaded_entry.runtime_data.announcing == {"main"}
        heos.on_update(_heos_state("stop"))
        await _eventually(lambda: restored() == ["SITV", "MV50", "MUON"])
        # A moment after the restore, the input is someone's choice again.
        await _eventually(lambda: not loaded_entry.runtime_data.announcing)


async def test_an_announcement_that_does_not_start_puts_the_zone_back_at_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """HEOS refuses the URL: the zone is put back before the error is reported."""
    from .test_init import FakeHeos

    await _push(hass, "PWON", "ZMOFF", "SITV", "MV50")
    heos = FakeHeos.instances[-1]
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    async def answer() -> None:
        await _eventually(lambda: "ZMON" in telnet.sent)
        telnet.push("ZMON")

    task = asyncio.create_task(answer())
    with (
        patch.object(heos, "async_play_url", AsyncMock(return_value=False)),
        patch("custom_components.denonavr_plus.media_player.TURN_ON_SETTLE", 0),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
    await task
    assert telnet.sent[0] == "ZMON" and telnet.sent[-1] == "ZMOFF"

    # Without a HEOS link nothing is switched at all.
    heos.connected = False
    telnet.sent.clear()
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
    assert telnet.sent == []


async def test_snapshot_and_restore_actions(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.exceptions import ServiceValidationError

    from custom_components.denonavr_plus.const import DOMAIN

    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "restore", {"entity_id": zone2}, blocking=True
        )
    await _push(hass, "PWON", "Z2OFF")
    await hass.services.async_call(
        DOMAIN, "snapshot", {"entity_id": zone2}, blocking=True
    )
    await _push(hass, "Z2ON", "Z2CD", "Z240")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        DOMAIN, "restore", {"entity_id": zone2}, blocking=True
    )
    assert telnet.sent == ["Z2OFF"]

    await hass.services.async_call(
        DOMAIN, "snapshot", {"entity_id": zone2}, blocking=True
    )
    await _push(hass, "Z2OFF")
    telnet.sent.clear()
    await hass.services.async_call(
        DOMAIN, "restore", {"entity_id": zone2}, blocking=True
    )
    assert telnet.sent == ["Z2ON"]


async def test_an_announcement_that_never_starts_still_puts_the_zone_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """No volume option: the volume stays; HEOS never reports playing; a failing restore is logged."""
    await _push(hass, "PWON", "ZMON", "SITV", "MV50")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    device = loaded_entry.runtime_data.device
    with patch(
        "custom_components.denonavr_plus.media_player.ANNOUNCE_START_WAIT", 0.05
    ):
        telnet.sent.clear()
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
        assert telnet.sent == []
        await _push(hass, "SINET")
        await asyncio.sleep(0.3)
        await hass.async_block_till_done()
        assert "SITV" in telnet.sent

        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
        with patch.object(
            type(device), "async_restore", AsyncMock(side_effect=CommandNotSent("gone"))
        ) as restore:
            await asyncio.sleep(0.3)
            await hass.async_block_till_done()
        assert restore.await_count == 1

    from homeassistant.exceptions import HomeAssistantError as _Error

    from custom_components.denonavr_plus.const import DOMAIN

    await hass.services.async_call(
        DOMAIN, "snapshot", {"entity_id": main}, blocking=True
    )
    with (
        patch.object(
            type(device), "async_restore", AsyncMock(side_effect=CommandNotSent("gone"))
        ),
        pytest.raises(_Error),
    ):
        await hass.services.async_call(
            DOMAIN, "restore", {"entity_id": main}, blocking=True
        )


async def test_search_finds_by_name_for_voice(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Assist's "play Kino on the receiver": search, then the first result plays."""
    from homeassistant.components.media_player import MediaClass, SearchMediaQuery

    from .test_init import FakeHeos

    device = loaded_entry.runtime_data.device
    device.discovery.quick_select_names = {1: "Kino", 2: "Kinoabend Spezial"}
    heos = FakeHeos.instances[-1]
    heos.connected = True
    heos.favorites = ["Radio Kino"]
    entity = _player(hass, entity_id(hass, loaded_entry, "media_player_main"))
    found = await entity.async_search_media(SearchMediaQuery(search_query=" kino "))
    assert [item.media_content_id for item in found.result] == [
        "quick_select/1",
        "quick_select/2",
        "heos_favorite/1",
    ]
    found = await entity.async_search_media(
        SearchMediaQuery(search_query="kino", media_filter_classes=[MediaClass.CHANNEL])
    )
    assert [item.title for item in found.result] == ["Radio Kino"]
    found = await entity.async_search_media(SearchMediaQuery(search_query="cd"))
    assert [item.media_content_id for item in found.result] == ["source/CD"]
    assert (
        await entity.async_search_media(SearchMediaQuery(search_query=""))
    ).result == []


async def test_an_input_gets_its_own_artwork(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_client: Any
) -> None:
    """The media control card paints the picture as background: a TV input gets one too."""
    main = entity_id(hass, loaded_entry, "media_player_main")
    await _push(hass, "PWON", "ZMOFF")
    assert "entity_picture" not in hass.states.get(main).attributes
    await _push(hass, "ZMON", "SITV")
    picture = hass.states.get(main).attributes["entity_picture"]
    client = await hass_client()
    response = await client.get(picture)
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("image/svg+xml")
    assert (await response.text()).startswith("<svg")
    await _push(hass, "SICD")
    assert hass.states.get(main).attributes["entity_picture"] != picture


async def test_favorites_and_quick_selects_play_by_name(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """As the HEOS integration takes them: media_content_type favorite or quick_select."""
    from homeassistant.exceptions import ServiceValidationError

    from .test_init import FakeHeos

    device = loaded_entry.runtime_data.device
    device.discovery.quick_select_names = {1: "Kino", 2: "Radio"}
    heos = FakeHeos.instances[-1]
    heos.connected = True
    heos.favorites = ["Bayern 3", "Klassik"]
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    for media_type, media_id in (
        ("favorite", "Klassik"),
        ("favorite", "1"),
        ("quick_select", "Radio"),
        ("quick_select", "1"),
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": media_type,
                "media_content_id": media_id,
            },
            blocking=True,
        )
    assert heos.calls[-2:] == [("play_preset", 2), ("play_preset", 1)]
    assert telnet.sent == ["MSQUICK2", "MSQUICK1"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "favorite",
                "media_content_id": "Nope",
            },
            blocking=True,
        )


async def test_the_heos_queue_and_what_plays_from_where(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Service and station of HEOS, the queue in the browser and as actions, clearing it."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    from custom_components.denonavr_plus.const import DOMAIN

    from .test_init import FakeHeos

    heos = FakeHeos.instances[-1]
    heos.connected = True
    await _push(hass, "PWON", "ZMON", "SINET")
    station = _heos_state("play") | {"service": "TuneIn", "station": "Bayern 3"}
    heos.on_update(station)
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    main = entity_id(hass, loaded_entry, "media_player_main")
    state = hass.states.get(main)
    assert state.attributes["app_name"] == "TuneIn"
    assert state.attributes["media_channel"] == "Bayern 3"
    assert state.attributes["media_content_type"] == "channel"
    assert MediaPlayerEntityFeature(state.attributes["supported_features"]) & (
        MediaPlayerEntityFeature.CLEAR_PLAYLIST
    )

    entity = _player(hass, main)
    queue = await entity.async_browse_media(None, "heos_queue")
    assert [(c.title, c.media_content_id) for c in queue.children or []] == [
        ("Song – Band", "heos_queue/1"),
        ("Other", "heos_queue/2"),
    ]
    await hass.services.async_call(
        "media_player",
        "play_media",
        {"entity_id": main, "media_content_type": "queue", "media_content_id": "2"},
        blocking=True,
    )
    await hass.services.async_call(
        "media_player", "clear_playlist", {"entity_id": main}, blocking=True
    )
    response = await hass.services.async_call(
        DOMAIN, "get_queue", {"entity_id": main}, blocking=True, return_response=True
    )
    assert [item["queue_id"] for item in response[main]["items"]] == [1, 2]
    await hass.services.async_call(
        DOMAIN,
        "remove_from_queue",
        {"entity_id": main, "queue_ids": [2]},
        blocking=True,
    )
    await hass.services.async_call(
        DOMAIN,
        "move_queue_item",
        {"entity_id": main, "queue_ids": [1], "destination": 2},
        blocking=True,
    )
    assert heos.calls[-4:] == [
        ("play_queue", 2),
        ("clear_queue", None),
        ("remove_from_queue", [2]),
        ("move_queue_item", ([1], 2)),
    ]

    # A failing HEOS command is reported, and zone 2 has no queue.
    with (
        patch.object(heos, "async_clear_queue", AsyncMock(side_effect=TimeoutError)),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            "media_player", "clear_playlist", {"entity_id": main}, blocking=True
        )
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "get_queue",
            {"entity_id": zone2},
            blocking=True,
            return_response=True,
        )


async def test_the_source_menu_can_leave_out_quick_selects_and_favorites(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    device.discovery.quick_select_names = {1: "Kino"}
    hass.config_entries.async_update_entry(
        loaded_entry, options={"source_shortcuts": False}
    )
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMON")
    main = entity_id(hass, loaded_entry, "media_player_main")
    assert "Kino" not in hass.states.get(main).attributes["source_list"]


async def test_the_turn_on_volume_applies_however_a_zone_is_turned_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Not only turn_on: picking a source in standby turns on at the turn-on volume too."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={"main": {volume.OPT_TURN_ON_VOLUME: -30.0}}
    )
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMOFF")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    async def answer() -> None:
        await _eventually(lambda: "ZMON" in telnet.sent)
        telnet.push("ZMON")

    task = asyncio.create_task(answer())
    with patch("custom_components.denonavr_plus.media_player.TURN_ON_SETTLE", 0):
        await hass.services.async_call(
            "media_player",
            "select_source",
            {"entity_id": main, "source": "CD"},
            blocking=True,
        )
    await task
    assert telnet.sent == ["ZMON", "MV50", "SICD"]


async def test_heos_features_wait_for_the_heos_link(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A receiver whose HEOS does not answer (a 2015 X-series has none) offers no HEOS features."""
    from homeassistant.components.media_player import MediaPlayerEntityFeature

    from custom_components.denonavr_plus.const import DOMAIN

    from .test_init import FakeHeos

    heos = FakeHeos.instances[-1]
    heos.connected = False
    await _push(hass, "PWON", "ZMON", "SINET")
    main = entity_id(hass, loaded_entry, "media_player_main")
    features = MediaPlayerEntityFeature(
        hass.states.get(main).attributes["supported_features"]
    )
    for feature in (
        MediaPlayerEntityFeature.MEDIA_ANNOUNCE,
        MediaPlayerEntityFeature.MEDIA_ENQUEUE,
        MediaPlayerEntityFeature.CLEAR_PLAYLIST,
        MediaPlayerEntityFeature.PAUSE,
    ):
        assert not features & feature, feature
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/a.mp3",
            },
            blocking=True,
        )
    assert err.value.translation_key == "heos_unavailable"
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            DOMAIN,
            "get_queue",
            {"entity_id": main},
            blocking=True,
            return_response=True,
        )
    assert err.value.translation_key == "heos_unavailable"


async def test_a_source_zone_2_does_not_take_leaves_its_list(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone 2 of an AVR-X2600H plays no HDMI input: it says so and drops it."""
    await _push(hass, "PWON", "Z2ON", "Z2NET")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    source = "DVD"
    assert source in hass.states.get(zone2).attributes["source_list"]
    with (
        patch("custom_components.denonavr_plus.avr.device.ZONE_SOURCE_WAIT", 0.05),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            "media_player", "select_source", {"entity_id": zone2, "source": source}, blocking=True
        )
    assert err.value.translation_key == "source_not_taken"
    assert err.value.translation_placeholders["source"] == source
    await _push(hass)
    assert source not in hass.states.get(zone2).attributes["source_list"]
    # For good: out of the zone's list in the options, where it can come back.
    chosen = loaded_entry.options["sources"]["zone2"]
    assert "DVD" not in chosen and "AUX1" in chosen


async def test_a_source_or_sound_mode_is_found_in_any_case(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A typo gets an error that names the choices, instead of a silent no."""
    from homeassistant.exceptions import ServiceValidationError

    await _push(hass, "PWON", "ZMON", "SIDVD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "select_source", {"entity_id": main, "source": " cd "}, blocking=True
    )
    assert "SICD" in telnet.sent
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "media_player", "select_source", {"entity_id": main, "source": "Kassette"}, blocking=True
        )
    assert err.value.translation_key == "unknown_source"
    assert "CD" in err.value.translation_placeholders["options"]

    telnet.sent.clear()
    await hass.services.async_call(
        "media_player", "select_sound_mode", {"entity_id": main, "sound_mode": "stereo"}, blocking=True
    )
    assert "MSSTEREO" in telnet.sent
    # In capitals it is the receiver's own token, as automations sent it before.
    await hass.services.async_call(
        "media_player", "select_sound_mode", {"entity_id": main, "sound_mode": "DOLBY ATMOS"}, blocking=True
    )
    assert "MSDOLBY ATMOS" in telnet.sent
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            "media_player", "select_sound_mode", {"entity_id": main, "sound_mode": "Stero"}, blocking=True
        )
    assert err.value.translation_key == "unknown_sound_mode"


# Linked player (linked_players.py) ----------------------------------------

_APPLE_TV = "media_player.apple_tv"
_APPLE_TV_FEATURES = 1 | 2 | 16 | 32 | 4096  # pause, seek, previous, next, play


def _link(
    hass: HomeAssistant, entry: MockConfigEntry, code: str = "DVD", **options: Any
) -> None:
    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            "linked_players": {code: {"player": _APPLE_TV, "follow": False, "full_volume": False}},
            **options,
        },
    )


async def test_a_linked_player_shows_on_the_zone_that_plays_its_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "SIDVD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    hass.states.async_set(
        _APPLE_TV,
        "playing",
        {
            "media_title": "Severance",
            "media_artist": "Apple TV+",
            "media_series_title": "Severance",
            "media_duration": 3000,
            "media_position": 120,
            "app_name": "TV",
            "entity_picture": "/api/media_player_proxy/media_player.apple_tv?token=x",
            "supported_features": _APPLE_TV_FEATURES,
        },
    )
    _link(hass, loaded_entry)
    await hass.async_block_till_done()
    state = hass.states.get(main)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "Severance"
    assert state.attributes["media_artist"] == "Apple TV+"
    assert state.attributes["media_series_title"] == "Severance"
    assert state.attributes["media_duration"] == 3000
    assert state.attributes["app_name"] == "TV"
    assert state.attributes["entity_picture"].startswith("/api/media_player_proxy/media_player.apple_tv")
    assert state.attributes["linked_player"] == _APPLE_TV
    # The player's transport, the receiver's power, volume and source.
    features = state.attributes["supported_features"]
    assert features & 2 and features & 4096 and features & 4  # seek, play, volume set
    assert not features & 1 << 15  # no shuffle: the player has none

    # A change on the player shows at once.
    hass.states.async_set(_APPLE_TV, "paused", {**hass.states.get(_APPLE_TV).attributes})
    await hass.async_block_till_done()
    assert hass.states.get(main).state == "paused"

    # Idle: the input shows again, as without a player.
    hass.states.async_set(_APPLE_TV, "idle", {"supported_features": _APPLE_TV_FEATURES})
    await hass.async_block_till_done()
    state = hass.states.get(main)
    assert state.state == "idle"
    assert state.attributes["media_title"] == "DVD"

    # Another input: the receiver's own information.
    await _push(hass, "SICD")
    state = hass.states.get(main)
    assert state.state == "on"
    assert "linked_player" not in state.attributes


async def test_an_unreachable_linked_player_leaves_the_zone_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "SIDVD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    hass.states.async_set(_APPLE_TV, STATE_UNAVAILABLE)
    _link(hass, loaded_entry)
    await hass.async_block_till_done()
    state = hass.states.get(main)
    assert state.state == "on"
    assert state.attributes["linked_player"] == _APPLE_TV


async def test_transport_goes_to_the_linked_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import DATA_COMPONENT, RepeatMode
    from pytest_homeassistant_custom_component.common import async_mock_service

    await _push(hass, "PWON", "ZMON", "SIDVD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    hass.states.async_set(_APPLE_TV, "playing", {"supported_features": _APPLE_TV_FEATURES})
    _link(hass, loaded_entry)
    await hass.async_block_till_done()
    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None
    calls = {
        service: async_mock_service(hass, "media_player", service)
        for service in (
            "media_play", "media_pause", "media_stop", "media_next_track",
            "media_previous_track", "media_seek", "shuffle_set", "repeat_set",
        )
    }
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await player.async_media_play()
    await player.async_media_pause()
    await player.async_media_stop()
    await player.async_media_next_track()
    await player.async_media_previous_track()
    await player.async_media_seek(42)
    await player.async_set_shuffle(True)
    await player.async_set_repeat(RepeatMode.ALL)
    for service, received in calls.items():
        assert [call.data["entity_id"] for call in received] == [_APPLE_TV], service
    assert calls["media_seek"][0].data["seek_position"] == 42
    assert calls["repeat_set"][0].data["repeat"] == RepeatMode.ALL
    # Nothing went to the receiver.
    assert telnet.sent == []


async def test_seek_needs_a_linked_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import DATA_COMPONENT
    from homeassistant.exceptions import ServiceValidationError

    await _push(hass, "PWON", "ZMON", "SIDVD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None
    with pytest.raises(ServiceValidationError) as raised:
        await player.async_media_seek(10)
    assert raised.value.translation_key == "no_linked_player"


async def test_zone2_shows_the_linked_player_of_its_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "Z2ON", "Z2DVD")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    hass.states.async_set(_APPLE_TV, "playing", {"media_title": "Severance"})
    _link(hass, loaded_entry)
    await hass.async_block_till_done()
    assert hass.states.get(zone2).attributes["media_title"] == "Severance"


@pytest.mark.assumed(
    "that the receiver reports SSINFAISFSV NON when an input's signal stops is read "
    "from the protocol notes, not seen on a receiver; a stopped player on an input settles it"
)
async def test_the_input_signal_tells_playing_from_idle(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    await _push(hass, "PWON", "ZMON", "SIDVD", "SSINFAISFSV48K", "Z2ON", "Z2CD")
    # Off by default: the zone is simply on.
    assert hass.states.get(main).state == "on"
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "signal_state": True}
    )
    await hass.async_block_till_done()
    assert hass.states.get(main).state == "playing"
    await _push(hass, "SSINFAISFSVNON")
    assert hass.states.get(main).state == "idle"
    # Zone 2 plays another input, whose signal the receiver does not report.
    assert hass.states.get(zone2).state == "on"
    # The same input as the main zone: the main zone's signal.
    await _push(hass, "Z2DVD")
    assert hass.states.get(zone2).state == "idle"
    # The tuner has no signal to go by.
    await _push(hass, "SITUNER")
    assert hass.states.get(main).state == "on"


async def test_without_a_signal_report_the_zone_stays_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "signal_state": True}
    )
    await _push(hass, "PWON", "ZMON", "SIDVD")
    loaded_entry.runtime_data.data.readonly.pop("sample_rate", None)
    loaded_entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(main).state == "on"


async def test_a_linked_player_decides_over_the_signal(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    main = entity_id(hass, loaded_entry, "media_player_main")
    await _push(hass, "PWON", "ZMON", "SIDVD", "SSINFAISFSV48K")
    hass.states.async_set(_APPLE_TV, "paused", {"media_title": "Severance"})
    # A paused streamer often keeps its signal.
    _link(hass, loaded_entry, signal_state=True)
    await hass.async_block_till_done()
    assert hass.states.get(main).state == "paused"


async def test_network_details_stay_off_an_input_with_a_linked_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A paused network session lends the linked input no title or cover."""
    from custom_components.denonavr_plus.avr.models import NowPlaying

    await _push(hass, "PWON", "ZMON", "SIDVD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    loaded_entry.runtime_data.data.now_playing = NowPlaying(
        state="pause", title="Old song", image_url="http://192.0.2.1/cover.jpg"
    )
    # The linked player plays, but has no title or picture.
    hass.states.async_set(_APPLE_TV, "playing", {"supported_features": _APPLE_TV_FEATURES})
    _link(hass, loaded_entry)
    await hass.async_block_till_done()
    state = hass.states.get(main)
    assert state.state == "playing"
    assert state.attributes["media_title"] == "DVD"
    from homeassistant.components.media_player import DATA_COMPONENT

    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None and player.media_image_url is None


class _BrowsingPlayer(MediaPlayerEntity):
    """A linked player with a media browser, as an Apple TV or a Chromecast has."""

    _attr_name = "Apple TV"
    _attr_should_poll = False
    _attr_state = MediaPlayerState.IDLE
    _attr_supported_features = (
        MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.PLAY
    )

    def __init__(self) -> None:
        self.entity_id = _APPLE_TV
        self.played: list[tuple[str, str, dict[str, Any]]] = []

    async def async_browse_media(
        self, media_content_type: Any = None, media_content_id: str | None = None
    ) -> Any:
        from homeassistant.components.media_player import BrowseMedia, MediaClass

        if media_content_id == "apps":
            return BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id="apps",
                media_content_type="apps",
                title="Apps",
                can_play=False,
                can_expand=True,
                children=[
                    BrowseMedia(
                        media_class=MediaClass.APP,
                        media_content_id="com.example.tv",
                        media_content_type="app",
                        title="TV",
                        can_play=True,
                        can_expand=False,
                        thumbnail="/api/media_player_proxy/media_player.apple_tv/browse_media/app/1",
                    )
                ],
            )
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id="",
            media_content_type="",
            title="Apple TV",
            can_play=False,
            can_expand=True,
            children=[
                BrowseMedia(
                    media_class=MediaClass.DIRECTORY,
                    media_content_id="apps",
                    media_content_type="apps",
                    title="Apps",
                    can_play=False,
                    can_expand=True,
                )
            ],
        )

    async def async_play_media(self, media_type: Any, media_id: str, **kwargs: Any) -> None:
        self.played.append((str(media_type), media_id, kwargs))


async def _browsing_player(hass: HomeAssistant) -> _BrowsingPlayer:
    from homeassistant.components.media_player import DATA_COMPONENT

    player = _BrowsingPlayer()
    await hass.data[DATA_COMPONENT].async_add_entities([player])
    await hass.async_block_till_done()
    return player


async def test_the_browser_has_the_linked_players_media(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import DATA_COMPONENT

    await _browsing_player(hass)
    # A linked player without a browser gets no folder.
    hass.states.async_set("media_player.streamer", "idle", {"supported_features": 1})
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "linked_players": {
                "DVD": {"player": _APPLE_TV},
                "CD": {"player": "media_player.streamer"},
                # The same player on a second input: one folder.
                "GAME": {"player": _APPLE_TV},
            },
        },
    )
    await hass.async_block_till_done()
    main = entity_id(hass, loaded_entry, "media_player_main")
    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None
    root = await player.async_browse_media()
    linked = [child for child in root.children or [] if child.media_content_id.startswith("linked:")]
    assert [(child.title, child.media_content_id) for child in linked] == [
        ("Apple TV (DVD)", "linked:DVD|")
    ]
    top = await player.async_browse_media("", "linked:DVD|")
    assert [child.media_content_id for child in top.children or []] == ["linked:DVD|apps"]
    apps = await player.async_browse_media("apps", "linked:DVD|apps")
    app = (apps.children or [])[0]
    assert app.media_content_id == "linked:DVD|com.example.tv"
    assert app.media_content_type == "app" and app.can_play
    assert app.thumbnail.startswith("/api/media_player_proxy/media_player.apple_tv/")


async def test_playing_a_linked_players_media_switches_to_its_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    linked = await _browsing_player(hass)
    _link(hass, loaded_entry)
    await _push(hass, "PWON", "ZMON", "SICD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": main,
            "media_content_type": "app",
            "media_content_id": "linked:DVD|com.example.tv",
            "enqueue": "add",
        },
        blocking=True,
    )
    assert "SIDVD" in telnet.sent
    assert [played[:2] for played in linked.played] == [("app", "com.example.tv")]
    assert linked.played[0][2]["enqueue"] == "add"


async def test_a_gone_linked_player_is_a_clear_error(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import DATA_COMPONENT, BrowseError

    main = entity_id(hass, loaded_entry, "media_player_main")
    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None
    with pytest.raises(BrowseError) as raised:
        await player.async_browse_media("", "linked:DVD|")
    assert raised.value.translation_key == "linked_player_gone"
    with pytest.raises(ServiceValidationError) as failed:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": main, "media_content_type": "app", "media_content_id": "linked:DVD|x"},
            blocking=True,
        )
    assert failed.value.translation_key == "linked_player_gone"


async def test_a_linked_player_that_is_gone_is_an_error_before_switching(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    _link(hass, loaded_entry)
    await _push(hass, "PWON", "ZMON", "SICD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with pytest.raises(ServiceValidationError) as failed:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": main, "media_content_type": "app", "media_content_id": "linked:DVD|x"},
            blocking=True,
        )
    assert failed.value.translation_key == "linked_player_gone"
    assert telnet.sent == []


async def test_a_failing_linked_browser_is_a_browse_error(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import DATA_COMPONENT

    linked = await _browsing_player(hass)
    _link(hass, loaded_entry)
    await hass.async_block_till_done()
    main = entity_id(hass, loaded_entry, "media_player_main")
    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None
    with patch.object(type(linked), "async_browse_media", side_effect=NotImplementedError):
        with pytest.raises(BrowseError):
            await player.async_browse_media("", "linked:DVD|")
    with patch.object(
        type(linked), "async_browse_media", side_effect=HomeAssistantError("offline")
    ):
        with pytest.raises(BrowseError, match="offline"):
            await player.async_browse_media("", "linked:DVD|")
    # A player without a browser of its own.
    linked._attr_supported_features = MediaPlayerEntityFeature.PLAY
    with pytest.raises(BrowseError) as gone:
        await player.async_browse_media("", "linked:DVD|")
    assert gone.value.translation_key == "linked_player_gone"


async def test_zone_2_gets_no_folder_for_an_input_it_does_not_offer(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components.media_player import DATA_COMPONENT

    await _browsing_player(hass)
    _link(hass, loaded_entry)
    await hass.async_block_till_done()
    zone2 = hass.data[DATA_COMPONENT].get_entity(entity_id(hass, loaded_entry, "media_player_zone2"))
    assert zone2 is not None
    with patch.object(type(zone2), "source_list", new_callable=PropertyMock, return_value=["CD"]):
        root = await zone2.async_browse_media()
    assert not [c for c in root.children or [] if c.media_content_id.startswith("linked:")]


# Music Assistant on the network input -----------------------------------------

_MASS = "media_player.living_room_music_assistant"


async def test_music_assistant_on_the_network_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Its title and queue on the receiver's player; HEOS's own details step aside."""
    from homeassistant.components.media_player import DATA_COMPONENT

    from .test_init import FakeHeos

    await _push(hass, "PWON", "ZMON", "SINET")
    main = entity_id(hass, loaded_entry, "media_player_main")
    FakeHeos.instances[-1].on_update(_heos_state("play"))
    hass.states.async_set(
        _MASS,
        "playing",
        {"media_title": "Track from the queue", "supported_features": _APPLE_TV_FEATURES},
    )
    # The receiver lists the input as NETWORK and reports it as NET.
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "linked_players": {"NETWORK": {"player": _MASS, "follow": False, "full_volume": False}},
        },
    )
    await hass.async_block_till_done()
    state = hass.states.get(main)
    assert state.attributes["media_title"] == "Track from the queue"
    assert state.attributes["linked_player"] == _MASS
    calls = async_mock_service(hass, "media_player", "media_next_track")
    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None
    await player.async_media_next_track()
    # Skip goes to Music Assistant's queue, not to HEOS.
    assert [call.data["entity_id"] for call in calls] == [_MASS]
