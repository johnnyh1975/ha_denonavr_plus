"""Tests for coordinator.py: the connection diagnosis for a lost telnet link."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.denonavr_plus.avr.transport import GoformClient
from custom_components.denonavr_plus.const import DOMAIN

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
