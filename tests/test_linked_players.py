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
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.denonavr_plus import linked_players
from custom_components.denonavr_plus.const import DOMAIN
from custom_components.denonavr_plus.linked_players import Link

from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]

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


def _player(code_player: str, *, follow: bool = False, full_volume: bool = False) -> dict[str, Any]:
    return {"player": code_player, "follow": follow, "full_volume": full_volume}


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


async def test_following_pauses_the_player_of_the_input_left(
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
    pauses = async_mock_service(hass, "media_player", "media_pause")
    hass.states.async_set(APPLE_TV, "playing", {"supported_features": FEATURES})
    await hass.async_block_till_done()
    assert telnet.sent[-1] == "SIDVD"
    assert [call.data["entity_id"] for call in pauses] == [STREAMER]
    # The logbook names the player as the cause: its context goes along.
    assert pauses[0].context.id == hass.states.get(APPLE_TV).context.id


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


async def test_a_player_is_kept_at_full_volume(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    volume = async_mock_service(hass, "media_player", "volume_set")
    mute = async_mock_service(hass, "media_player", "volume_mute")
    hass.states.async_set(
        APPLE_TV,
        "idle",
        {"supported_features": FEATURES, "volume_level": 0.4, "is_volume_muted": True},
    )
    # At once when linked.
    _options(loaded_entry, DVD=_player(APPLE_TV, full_volume=True))
    await hass.async_block_till_done()
    assert [call.data["volume_level"] for call in volume] == [1.0]
    assert [call.data["is_volume_muted"] for call in mute] == [False]
    # Not again while the volume stays (a player that refuses).
    hass.states.async_set(
        APPLE_TV,
        "playing",
        {"supported_features": FEATURES, "volume_level": 0.4, "is_volume_muted": True},
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


async def test_full_volume_is_only_for_players_that_have_it(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
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
    hass: HomeAssistant, loaded_entry: MockConfigEntry
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
    assert pauses == []
