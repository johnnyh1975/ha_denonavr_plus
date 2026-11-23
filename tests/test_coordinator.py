"""Tests for coordinator.py: the diagnosis of a lost link and a start without the receiver."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.denonavr_plus.avr.transport import GoformClient
from custom_components.denonavr_plus.const import (
    DOMAIN,
    HTTP_FALLBACK_INTERVAL,
    RECONCILE_INTERVAL,
)

from .common import entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]

HTTP_OK: dict[str, Any] = {"power": True, "volume_db": -40.0}


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"telnet_unavailable_{entry.entry_id}")


async def _pass(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: int) -> None:
    """Let time pass minute by minute so every reconciliation poll runs."""
    for _ in range(minutes):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


def _drop_link() -> FakeTelnet:
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    return telnet


async def test_telnet_down_while_http_answers_raises_a_repair(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=HTTP_OK)):
        telnet = _drop_link()
        await _pass(hass, freezer, 4)
        assert _issue(hass, loaded_entry) is None

        await _pass(hass, freezer, 2)
        issue = _issue(hass, loaded_entry)
        assert issue is not None
        assert issue.translation_key == "telnet_unavailable"
        assert issue.translation_placeholders == {"model": "AVC-X3700H"}
        assert issue.severity is ir.IssueSeverity.WARNING
        assert "telnet" in caplog.text.lower()

        assert telnet._on_availability is not None
        telnet.connected = True
        telnet._on_availability(True)
        # Pushed updates are debounced on the event loop clock; with the clock
        # frozen, fire the timer instead of sleeping.
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert _issue(hass, loaded_entry) is None


async def test_receiver_fully_unreachable_only_warns(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Off without network standby is a normal state, so no repair, one warning."""
    _drop_link()
    await _pass(hass, freezer, 12)
    assert _issue(hass, loaded_entry) is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "AVC-X3700H" in r.message]
    assert len(warnings) == 1


async def test_unload_removes_the_repair(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=HTTP_OK)):
        _drop_link()
        await _pass(hass, freezer, 6)
    assert _issue(hass, loaded_entry) is not None
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, loaded_entry) is None


async def test_poll_runs_more_often_while_telnet_is_down(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    coordinator = loaded_entry.runtime_data
    assert coordinator.update_interval == RECONCILE_INTERVAL
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=HTTP_OK)):
        telnet = _drop_link()
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        # The media players now depend on the HTTP poll alone.
        assert coordinator.update_interval == HTTP_FALLBACK_INTERVAL

        assert telnet._on_availability is not None
        telnet.connected = True
        telnet._on_availability(True)
        freezer.tick(timedelta(seconds=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert coordinator.update_interval == RECONCILE_INTERVAL


async def _settle(hass: HomeAssistant) -> None:
    # Device updates are debounced by 0.1 s before they reach the coordinator.
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def test_a_silent_receiver_starts_from_the_last_discovery(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    """Off without network standby at a restart: the media players still come up."""
    key = f"{DOMAIN}.{loaded_entry.entry_id}.discovery"
    assert [zone["id"] for zone in hass_storage[key]["data"]["zones"]] == ["main", "zone2"]
    main = entity_id(hass, loaded_entry, "media_player_main")
    zone2 = entity_id(hass, loaded_entry, "media_player_zone2")
    dynamic_eq = entity_id(hass, loaded_entry, "switch_dynamic_eq")
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)

    with (
        patch.object(GoformClient, "async_get_device_info", AsyncMock(return_value=None)),
        patch.object(FakeTelnet, "async_start", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(loaded_entry.entry_id)
        await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert loaded_entry.runtime_data.started_offline
    # The media players are there (unavailable without a turn-on automation),
    # the settings wait for the receiver.
    for player in (main, zone2):
        state = hass.states.get(player)
        assert state is not None and state.state == STATE_UNAVAILABLE
    eq_state = hass.states.get(dynamic_eq)
    assert eq_state is not None and eq_state.attributes.get("restored") is True

    # The receiver answers: the entry is set up in full.
    telnet = FakeTelnet.instances[-1]
    telnet.connected = True
    assert telnet._on_availability is not None
    telnet._on_availability(True)
    await _settle(hass)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert not loaded_entry.runtime_data.started_offline
    eq_state = hass.states.get(dynamic_eq)
    assert eq_state is not None and eq_state.state != STATE_UNAVAILABLE
    assert "restored" not in eq_state.attributes


async def test_without_a_kept_discovery_setup_waits(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: "192.0.2.10"})
    entry.add_to_hass(hass)
    with patch.object(GoformClient, "async_get_device_info", AsyncMock(return_value=None)):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_removing_the_entry_removes_the_kept_discovery(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    key = f"{DOMAIN}.{loaded_entry.entry_id}.discovery"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
