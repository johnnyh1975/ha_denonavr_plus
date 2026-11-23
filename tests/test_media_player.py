"""Tests for media_player.py: the zones stay usable over HTTP without telnet."""

from __future__ import annotations

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
