"""Tests for linked_players.py: renamed and missing players, following, full volume."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_mock_service,
)

from custom_components.denonavr_plus import linked_players
from custom_components.denonavr_plus.const import DOMAIN
from custom_components.denonavr_plus.linked_players import Link

from .fakes import FakeTelnet

APPLE_TV = "media_player.apple_tv"
STREAMER = "media_player.streamer"
#: pause, volume set, volume mute, stop, play
FEATURES = 1 | 4 | 8 | 4096 | 16384


async def _settle(hass: HomeAssistant) -> None:
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


def _options(entry: MockConfigEntry, **links: dict[str, Any]) -> None:
    entry_options = {**entry.options, "linked_players": links}
    hass = entry.runtime_data.hass
    hass.config_entries.async_update_entry(entry, options=entry_options)


def _player(
    code_player: str, *, follow: bool = False, full_volume: bool = False, power: bool = False
) -> dict[str, Any]:
    return {"player": code_player, "follow": follow, "full_volume": full_volume, "power": power}


def test_links_skip_entries_without_a_player() -> None:
    options = {
        "linked_players": {
            "DVD": {"player": APPLE_TV, "follow": True},
            "CD": {"player": ""},
            "BD": "media_player.wrong_shape",
        }
    }
    assert linked_players.links(options) == {"DVD": Link(APPLE_TV, True, False)}
    assert linked_players.links({"linked_players": ["DVD"]}) == {}
    assert linked_players.with_link(options, "CD", Link(STREAMER)) == {
        "DVD": _player(APPLE_TV, follow=True),
        "CD": _player(STREAMER),
    }
    assert linked_players.with_link(options, "DVD", None) == {}


# Following a player --------------------------------------------------------


async def test_the_receiver_follows_a_player_that_starts(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True))
    await hass.async_block_till_done()
    telnet.sent.clear()
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert telnet.sent[-1] == "SIDVD"

    # Playing again after a short drop is no new start.
    telnet.push("SICD")
    await _settle(hass)
    telnet.sent.clear()
    hass.states.async_set(APPLE_TV, "unavailable")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert telnet.sent == []


async def test_a_player_that_only_appears_playing_is_not_followed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Home Assistant starting: the player comes with its state, it did not start."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True))
    await hass.async_block_till_done()
    telnet.sent.clear()
    hass.states.async_set(APPLE_TV, "playing")
    await hass.async_block_till_done()
    assert telnet.sent == []


async def test_a_player_that_comes_up_playing_is_not_followed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """After a restart the player is first unavailable (restored), then playing."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "unavailable")
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True))
    await hass.async_block_till_done()
    telnet.sent.clear()
    hass.states.async_set(APPLE_TV, "playing")
    await hass.async_block_till_done()
    assert telnet.sent == []
    # Its next start is followed.
    hass.states.async_set(APPLE_TV, "paused")
    hass.states.async_set(APPLE_TV, "playing")
    await hass.async_block_till_done()
    assert telnet.sent[-1] == "SIDVD"


async def test_nothing_is_followed_while_the_main_zone_is_unavailable(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Home Assistant would skip the switch; the player left must keep playing."""
    from homeassistant.components.media_player import DATA_COMPONENT

    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    hass.states.async_set(STREAMER, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    main = next(
        item.entity_id
        for item in er.async_entries_for_config_entry(er.async_get(hass), loaded_entry.entry_id)
        if item.unique_id.endswith("_media_player_main")
    )
    player = hass.data[DATA_COMPONENT].get_entity(main)
    assert player is not None
    pauses = async_mock_service(hass, "media_player", "media_pause")
    telnet.sent.clear()
    with patch.object(type(player), "available", False):
        player.async_write_ha_state()
        hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert telnet.sent == []
    assert pauses == []


async def test_a_player_without_follow_is_not_followed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle")
    _options(loaded_entry, DVD=_player(APPLE_TV))
    await hass.async_block_till_done()
    telnet.sent.clear()
    hass.states.async_set(APPLE_TV, "playing")
    await hass.async_block_till_done()
    assert telnet.sent == []


async def test_an_input_that_plays_already_is_left_alone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMOFF", "Z2ON", "Z2DVD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle")
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True))
    await hass.async_block_till_done()
    telnet.sent.clear()
    hass.states.async_set(APPLE_TV, "playing")
    await hass.async_block_till_done()
    assert telnet.sent == []


async def test_following_pauses_the_players_no_zone_hears(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    hass.states.async_set(STREAMER, "playing", {"supported_features": FEATURES})
    _options(
        loaded_entry,
        DVD=_player(APPLE_TV, follow=True),
        CD=_player(STREAMER),
    )
    await hass.async_block_till_done()
    events = async_capture_events(hass, linked_players.EVENT_INPUT_FOLLOWED)
    pauses = async_mock_service(hass, "media_player", "media_pause")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert telnet.sent[-1] == "SIDVD"
    # The receiver reports the new input: the other players pause now.
    telnet.push("SIDVD")
    await _settle(hass)
    assert [call.data["entity_id"] for call in pauses] == [STREAMER]
    # The logbook names the player as the cause: its context goes along.
    assert pauses[0].context.id == hass.states.get(APPLE_TV).context.id
    assert [event.data for event in events] == [
        {
            "entity_id": main_player(hass, loaded_entry),
            "source": "DVD",
            "previous_source": "CD",
            "player": APPLE_TV,
        }
    ]
    assert events[0].context.id == pauses[0].context.id


async def test_a_second_player_right_after_is_paused_instead(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    # A player that cannot pause is stopped.
    hass.states.async_set(STREAMER, "idle", {"supported_features": 4096})
    _options(
        loaded_entry,
        DVD=_player(APPLE_TV, follow=True),
        GAME=_player(STREAMER, follow=True),
    )
    await hass.async_block_till_done()
    stops = async_mock_service(hass, "media_player", "media_stop")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    telnet.push("SIDVD")
    await _settle(hass)
    telnet.sent.clear()
    hass.states.async_set(STREAMER, "playing", {"supported_features": 4096})
    await hass.async_block_till_done()
    assert telnet.sent == []
    assert [call.data["entity_id"] for call in stops] == [STREAMER]

    # Later, the second player is followed.
    with patch.object(linked_players, "FOLLOW_COOLDOWN", 0):
        hass.states.async_set(STREAMER, "idle", {"supported_features": 4096})
        hass.states.async_set(STREAMER, "playing", {"supported_features": 4096})
        await hass.async_block_till_done(wait_background_tasks=True)
    assert telnet.sent[-1] == "SIGAME"


async def test_a_refused_switch_is_logged(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle")
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True))
    await hass.async_block_till_done()

    async def refuse(call: ServiceCall) -> None:
        raise HomeAssistantError("receiver gone")

    hass.services.async_register("media_player", "select_source", refuse)
    with caplog.at_level(logging.WARNING):
        hass.states.async_set(APPLE_TV, "playing")
        await hass.async_block_till_done()
    assert "receiver gone" in caplog.text


# Full volume -----------------------------------------------------------------


async def test_a_player_is_kept_at_full_volume_while_its_input_plays(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    volume = async_mock_service(hass, "media_player", "volume_set")
    mute = async_mock_service(hass, "media_player", "volume_mute")
    hass.states.async_set(
        APPLE_TV,
        "idle",
        {"supported_features": FEATURES, "volume_level": 0.4, "is_volume_muted": True},
    )
    _options(loaded_entry, DVD=_player(APPLE_TV, full_volume=True))
    await hass.async_block_till_done()
    # Another input plays: the player's volume is its own business.
    hass.states.async_set(
        APPLE_TV,
        "idle",
        {"supported_features": FEATURES, "volume_level": 0.3, "is_volume_muted": True},
    )
    await hass.async_block_till_done()
    assert volume == [] and mute == []

    # The zone switches to its input: full volume at once.
    telnet.push("SIDVD")
    await _settle(hass)
    assert [call.data["volume_level"] for call in volume] == [1.0]
    assert [call.data["is_volume_muted"] for call in mute] == [False]
    # Not again while the volume stays (a player that refuses).
    hass.states.async_set(
        APPLE_TV,
        "playing",
        {"supported_features": FEATURES, "volume_level": 0.3, "is_volume_muted": True},
    )
    await hass.async_block_till_done()
    assert len(volume) == 1
    # Again when someone turns it down.
    hass.states.async_set(
        APPLE_TV,
        "playing",
        {"supported_features": FEATURES, "volume_level": 0.2, "is_volume_muted": False},
    )
    await hass.async_block_till_done()
    assert len(volume) == 2 and len(mute) == 1
    # A player that is off is left alone.
    hass.states.async_set(APPLE_TV, "off", {"supported_features": FEATURES, "volume_level": 0.1})
    await hass.async_block_till_done()
    assert len(volume) == 2

    # The receiver in standby: a television playing on its own speakers.
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES, "volume_level": 0.5})
    telnet.push("PWSTANDBY", "ZMOFF")
    await _settle(hass)
    calls = len(volume)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES, "volume_level": 0.2})
    await hass.async_block_till_done()
    assert len(volume) == calls


async def test_switching_full_volume_on_applies_it_at_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    volume = async_mock_service(hass, "media_player", "volume_set")
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES, "volume_level": 0.4})
    _options(loaded_entry, DVD=_player(APPLE_TV))
    await hass.async_block_till_done()
    assert volume == []
    _options(loaded_entry, DVD=_player(APPLE_TV, full_volume=True))
    await hass.async_block_till_done()
    assert [call.data["volume_level"] for call in volume] == [1.0]


async def test_full_volume_is_only_for_players_that_have_it(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD", "Z2ON", "Z2CD")
    await _settle(hass)
    volume = async_mock_service(hass, "media_player", "volume_set")
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": 4096, "volume_level": 0.4})
    hass.states.async_set(STREAMER, "idle", {"supported_features": FEATURES, "volume_level": 0.4})
    _options(loaded_entry, DVD=_player(APPLE_TV, full_volume=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    assert volume == []


# Renamed and missing players ----------------------------------------------------


async def test_a_renamed_player_stays_linked(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    item = registry.async_get_or_create(
        "media_player", "test", "apple-tv", suggested_object_id="apple_tv"
    )
    assert item.entity_id == APPLE_TV
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    registry.async_update_entity(APPLE_TV, new_entity_id="media_player.living_room_tv")
    await hass.async_block_till_done()
    assert loaded_entry.options["linked_players"]["DVD"] == _player(
        "media_player.living_room_tv", follow=True
    )
    assert loaded_entry.options["linked_players"]["CD"] == _player(STREAMER)


async def test_a_missing_player_is_a_repair_that_unlinks_it(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    issue = linked_players.issue_id(loaded_entry.entry_id)
    hass.states.async_set(STREAMER, "idle")
    _options(loaded_entry, DVD=_player(APPLE_TV), CD=_player(STREAMER))
    await hass.async_block_till_done()
    found = ir.async_get(hass).async_get_issue(DOMAIN, issue)
    assert found is not None and found.is_fixable
    assert found.translation_placeholders["players"] == f"DVD → {APPLE_TV}"

    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["linked_players"] == {"CD": _player(STREAMER)}
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None


async def test_a_removed_player_raises_the_repair_and_a_new_one_ends_it(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create("media_player", "test", "apple-tv", suggested_object_id="apple_tv")
    issue = linked_players.issue_id(loaded_entry.entry_id)
    _options(loaded_entry, DVD=_player(APPLE_TV))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None
    registry.async_remove(APPLE_TV)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    registry.async_get_or_create("media_player", "test", "apple-tv", suggested_object_id="apple_tv")
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None


async def test_missing_players_are_looked_for_once_home_assistant_has_started(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, caplog: pytest.LogCaptureFixture
) -> None:
    """Other integrations add their players while Home Assistant starts."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "linked_players": {"DVD": _player(APPLE_TV)}}
    )
    await hass.async_block_till_done()
    issue = linked_players.issue_id(loaded_entry.entry_id)
    ir.async_delete_issue(hass, DOMAIN, issue)
    hass.set_state(CoreState.starting)
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    # A reload after the start stops cleanly.
    caplog.clear()
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert "Unable to remove unknown" not in caplog.text


async def test_the_repair_ends_when_the_entry_is_gone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    flow = linked_players.LinkedPlayerRepairFlow("no-such-entry")
    flow.hass = hass
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_unloading_stops_following(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    hass.states.async_set(APPLE_TV, "idle")
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True))
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    telnet.sent.clear()
    hass.states.async_set(APPLE_TV, "playing")
    await hass.async_block_till_done()
    assert telnet.sent == []


async def test_zone2_on_the_main_zone_source_moves_along(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone 2 following the main zone does not keep the input left."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD", "Z2ON", "Z2SOURCE")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    hass.states.async_set(STREAMER, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert telnet.sent[-1] == "SIDVD"
    # The receiver reports the new input: the other players pause now.
    telnet.push("SIDVD")
    await _settle(hass)
    assert [call.data["entity_id"] for call in pauses] == [STREAMER]


async def test_one_player_on_two_inputs_is_not_paused(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A streamer on an analog and a digital input: switching keeps it playing."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(APPLE_TV))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert telnet.sent[-1] == "SIDVD"
    # The receiver reports the new input: the other players pause now.
    telnet.push("SIDVD")
    await _settle(hass)
    assert pauses == []


async def test_the_player_left_keeps_playing_in_another_zone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD", "Z2ON", "Z2CD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    hass.states.async_set(STREAMER, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert telnet.sent[-1] == "SIDVD"
    # The receiver reports the new input: the other players pause now.
    telnet.push("SIDVD")
    await _settle(hass)
    assert pauses == []


async def test_removing_the_entry_removes_the_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    issue = linked_players.issue_id(loaded_entry.entry_id)
    _options(loaded_entry, DVD=_player(APPLE_TV))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None
    assert await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None


def main_player(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    return next(
        item.entity_id
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.unique_id.endswith("_media_player_main")
    )


async def test_switching_by_hand_pauses_the_players_no_zone_hears(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """With a followed player, switching to another linked input pauses the rest."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    # By remote to the streamer's input.
    telnet.push("SICD")
    await _settle(hass)
    assert [call.data["entity_id"] for call in pauses] == [APPLE_TV]


async def test_an_input_without_a_player_pauses_nothing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The TV, the tuner, or the network for an announcement (as Rotel)."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    for line in ("SITV", "SINET", "SIDVD"):
        telnet.push(line)
        await _settle(hass)
    assert pauses == []


async def test_following_from_standby_keeps_the_player_that_started(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The zone turns on with its old input first: no pause until it is there."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWSTANDBY", "ZMOFF", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "idle", {"supported_features": FEATURES})
    hass.states.async_set(STREAMER, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    # The receiver wakes up on its old input, then takes the new one.
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    assert pauses == []
    await hass.async_block_till_done(wait_background_tasks=True)
    telnet.push("SIDVD")
    await _settle(hass)
    assert "SIDVD" in telnet.sent
    assert [call.data["entity_id"] for call in pauses] == [STREAMER]
    assert pauses[0].context.id == hass.states.get(APPLE_TV).context.id


async def test_a_reload_or_turning_on_pauses_nothing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    hass.states.async_set(STREAMER, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV, follow=True), CD=_player(STREAMER))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    # A reload: the receiver reports its state afresh.
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    # Off, and on again by remote: a player on the TV's own speakers plays on.
    telnet.push("ZMOFF")
    await _settle(hass)
    telnet.push("ZMON", "SIDVD")
    await _settle(hass)
    assert pauses == []


async def test_a_hidden_zone_2_still_hears_its_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SITV", "Z2ON", "Z2CD")
    await _settle(hass)
    hass.states.async_set(STREAMER, "playing", {"supported_features": FEATURES})
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "zones": ["main"],
            "linked_players": {
                "DVD": _player(APPLE_TV, follow=True),
                "CD": _player(STREAMER),
            },
        },
    )
    await hass.async_block_till_done()
    # Hiding a zone reloads the entry: a new connection reports afresh.
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SITV", "Z2ON", "Z2CD")
    await _settle(hass)
    assert loaded_entry.runtime_data.data.zone("zone2").power
    pauses = async_mock_service(hass, "media_player", "media_pause")
    telnet.push("SIDVD")
    await _settle(hass)
    assert pauses == []
    # Let the receiver's follow-up queries finish.
    await asyncio.sleep(1.2)
    await hass.async_block_till_done()


async def test_without_a_followed_player_every_player_keeps_playing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV), CD=_player(STREAMER))
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    telnet.push("SITV")
    await _settle(hass)
    # Off and on again: no change of input is no reason either.
    telnet.push("ZMOFF")
    await _settle(hass)
    assert pauses == []


async def test_an_announcement_pauses_nothing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Over to the network and back, also with Music Assistant linked there."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    _options(
        loaded_entry,
        DVD=_player(APPLE_TV, follow=True),
        NETWORK=_player("media_player.music_assistant"),
    )
    await hass.async_block_till_done()
    pauses = async_mock_service(hass, "media_player", "media_pause")
    loaded_entry.runtime_data.announcing.add("main")
    telnet.push("SINET")
    await _settle(hass)
    telnet.push("SIDVD")
    await _settle(hass)
    loaded_entry.runtime_data.announcing.clear()
    assert pauses == []
    # Not an announcement: switching to Music Assistant's input pauses the Apple TV.
    telnet.push("SINET")
    await _settle(hass)
    assert [call.data["entity_id"] for call in pauses] == [APPLE_TV]


async def test_full_volume_never_on_the_network_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Music Assistant's player may have the receiver as its volume control."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SINET")
    await _settle(hass)
    volume = async_mock_service(hass, "media_player", "volume_set")
    hass.states.async_set(
        "media_player.music_assistant",
        "playing",
        {"supported_features": FEATURES, "volume_level": 0.3},
    )
    _options(loaded_entry, NETWORK=_player("media_player.music_assistant", full_volume=True))
    await hass.async_block_till_done()
    assert volume == []


async def test_the_receiver_follows_music_assistant_to_the_network(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set("media_player.music_assistant", "idle", {"supported_features": FEATURES})
    _options(loaded_entry, NETWORK=_player("media_player.music_assistant", follow=True))
    await hass.async_block_till_done()
    telnet.sent.clear()
    hass.states.async_set("media_player.music_assistant", "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    # The recording lists the input as NETWORK (its Deviceinfo list, before
    # the telnet list); on the wire it is SINET.
    switched = [line for line in telnet.sent if line.startswith("SI")]
    assert switched == ["SINET"]
    # The receiver reports NET: that is the linked input, no second switch.
    telnet.push("SINET")
    await _settle(hass)
    hass.states.async_set("media_player.music_assistant", "paused", {"supported_features": FEATURES})
    hass.states.async_set("media_player.music_assistant", "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert [line for line in telnet.sent if line.startswith("SI")] == switched


async def test_an_announcement_stream_is_not_followed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Music Assistant's HEOS player may show the announcement as playing."""
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set("media_player.music_assistant", "idle", {"supported_features": FEATURES})
    _options(loaded_entry, NETWORK=_player("media_player.music_assistant", follow=True))
    await hass.async_block_till_done()
    events = async_capture_events(hass, linked_players.EVENT_INPUT_FOLLOWED)
    telnet.sent.clear()
    loaded_entry.runtime_data.announcing.add("main")
    hass.states.async_set("media_player.music_assistant", "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert [line for line in telnet.sent if line.startswith("SI")] == []
    assert events == []



# Switched with the input -----------------------------------------------------


async def test_a_player_is_switched_on_with_its_input_and_off_with_its_zones(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    turned_on = async_mock_service(hass, "media_player", "turn_on")
    turned_off = async_mock_service(hass, "media_player", "turn_off")
    on_off = FEATURES | 128 | 256  # turn on, turn off
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "off", {"supported_features": on_off})
    _options(loaded_entry, DVD=_player(APPLE_TV, power=True))
    await _settle(hass)
    assert not turned_on

    telnet.push("SIDVD")
    await _settle(hass)
    assert [call.data["entity_id"] for call in turned_on] == [APPLE_TV]
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": on_off})

    # Another input: the player stays on.
    telnet.push("SICD")
    await _settle(hass)
    assert not turned_off
    # Back, then the zone off: off with it.
    telnet.push("SIDVD")
    await _settle(hass)
    telnet.push("ZMOFF")
    await _settle(hass)
    assert [call.data["entity_id"] for call in turned_off] == [APPLE_TV]


async def test_without_the_option_or_the_feature_nothing_is_switched(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    turned_on = async_mock_service(hass, "media_player", "turn_on")
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SICD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "off", {"supported_features": FEATURES | 128})
    hass.states.async_set(STREAMER, "off", {"supported_features": FEATURES})
    _options(loaded_entry, DVD=_player(APPLE_TV), BD=_player(STREAMER, power=True))
    await _settle(hass)
    telnet.push("SIDVD")
    await _settle(hass)
    telnet.push("SIBD")
    await _settle(hass)
    assert not turned_on


async def test_a_player_is_suggested_only_when_one_fits(hass: HomeAssistant) -> None:
    hass.states.async_set("media_player.shield", "idle", {"friendly_name": "Nvidia Shield"})
    hass.states.async_set("media_player.shield_2", "idle", {"friendly_name": "Shield Bedroom"})
    hass.states.async_set("media_player.sonos", "idle", {"friendly_name": "Sonos Arc"})

    def suggest(name: str, exclude: list[str] | None = None) -> str | None:
        return linked_players.suggest_player(
            hass, name, exclude=exclude or [], network=False, receiver_names=[]
        )

    assert suggest("Sonos") == "media_player.sonos"
    assert suggest("Shield") is None  # two fit
    assert suggest("Shield", ["media_player.shield_2"]) == "media_player.shield"
    assert suggest("Blu-ray") is None  # a default name
    assert suggest("Apple TV") is None


async def test_what_plays_when_the_receiver_first_reports_switches_nothing_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A reload with the zone already on the input: the resync is no switch."""
    turned_on = async_mock_service(hass, "media_player", "turn_on")
    hass.states.async_set(APPLE_TV, "off", {"supported_features": FEATURES | 128 | 256})
    _options(loaded_entry, DVD=_player(APPLE_TV, power=True))
    await _settle(hass)
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    FakeTelnet.instances[-1].push("PWON", "ZMON", "SIDVD")
    await _settle(hass)
    assert not turned_on


async def test_an_announcement_switches_no_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    turned_on = async_mock_service(hass, "media_player", "turn_on")
    turned_off = async_mock_service(hass, "media_player", "turn_off")
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMOFF", "SIDVD")
    await _settle(hass)
    hass.states.async_set(APPLE_TV, "off", {"supported_features": FEATURES | 128 | 256})
    _options(loaded_entry, DVD=_player(APPLE_TV, power=True))
    await _settle(hass)
    coordinator = loaded_entry.runtime_data
    coordinator.announcing.add("main")
    telnet.push("ZMON")  # the announcement wakes the zone on its last input
    await _settle(hass)
    telnet.push("SINET", "ZMOFF", "SIDVD")
    await _settle(hass)
    coordinator.announcing.discard("main")
    coordinator.async_update_listeners()
    await _settle(hass)
    assert not turned_on and not turned_off
    # Afterwards the zone switched on by hand wakes the player as usual.
    telnet.push("ZMON")
    await _settle(hass)
    assert [call.data["entity_id"] for call in turned_on] == [APPLE_TV]
