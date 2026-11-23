"""Tests for media_player.py: the zones stay usable over HTTP without telnet."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
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
