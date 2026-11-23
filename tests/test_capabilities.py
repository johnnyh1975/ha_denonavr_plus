"""Tests for capabilities.py and repairs.py: the check in Home Assistant."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import capabilities
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]

RESULT: dict[str, Any] = {
    "check_version": 1,
    "model": "AVC-X3700H",
    "firmware": "1.2.3",
    "queries": {
        "PW?": {"group": "Power", "result": "answered", "lines": ["PWON"]},
        "PSFOO ?": {"group": "Test", "result": "no reply", "lines": []},
    },
}


def _issue(hass: HomeAssistant, entry: MockConfigEntry, kind: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{entry.entry_id}")


def test_the_issue_link_is_prefilled() -> None:
    url = urlparse(capabilities.issue_url("SR6015", "4.1.0"))
    query = parse_qs(url.query)
    assert url.path == "/johnnyh1975/ha_denonavr_plus/issues/new"
    assert query["template"] == ["device_support.yml"]
    assert query["model"] == ["SR6015"]
    assert query["firmware"] == ["4.1.0"]
    assert query["title"] == ["Device support: SR6015"]


async def test_an_unchecked_model_gets_a_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The recording (AVC-X3700H) is not in KNOWN_MODELS."""
    issue = _issue(hass, loaded_entry, "capability_check")
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders == {"model": "AVC-X3700H"}


async def test_a_known_model_gets_none(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    with patch.object(capabilities, "KNOWN_MODELS", frozenset({"AVC-X3700H"})):
        loaded_entry.runtime_data.capabilities.async_update_issues("AVC-X3700H")
    assert _issue(hass, loaded_entry, "capability_check") is None


async def test_the_repair_runs_the_check_and_points_to_github(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    with patch.object(
        DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(RESULT))
    ):
        result = await manager.async_init(
            DOMAIN, data={"issue_id": f"capability_check_{loaded_entry.entry_id}"}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "check"
        result = await manager.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()
        result = await manager.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "check_done"
    placeholders = result["description_placeholders"]
    assert placeholders["answered"] == "1" and placeholders["total"] == "2"
    assert "template=device_support.yml" in placeholders["url"]
    result = await manager.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY

    # Kept, in the diagnostics, and the repairs changed.
    key = f"{DOMAIN}.{loaded_entry.entry_id}.capabilities"
    assert hass_storage[key]["data"]["queries"]["PW?"]["result"] == "answered"
    assert _issue(hass, loaded_entry, "capability_check") is None
    report = _issue(hass, loaded_entry, "capability_report")
    assert report is not None and not report.is_fixable
    assert report.learn_more_url == capabilities.issue_url("AVC-X3700H", "1.2.3")


async def test_the_repair_without_a_connection(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    failing = AsyncMock(side_effect=CommandNotSent("query_needs_telnet"))
    with patch.object(DenonAvrDevice, "async_check_capabilities", failing):
        result = await manager.async_init(
            DOMAIN, data={"issue_id": f"capability_check_{loaded_entry.entry_id}"}
        )
        result = await manager.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        result = await manager.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connected"
    assert _issue(hass, loaded_entry, "capability_check") is not None


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10"},
        options=options,
        minor_version=CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_check_asked_for_at_setup_runs_once(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    check = AsyncMock(return_value=dict(RESULT))
    with patch.object(DenonAvrDevice, "async_check_capabilities", check):
        entry = await _setup(hass, capability_check_pending=True)
    check.assert_awaited_once()
    assert entry.state is ConfigEntryState.LOADED
    assert "capability_check_pending" not in entry.options
    assert _issue(hass, entry, "capability_report") is not None


async def test_a_check_asked_for_waits_for_the_connection(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    failing = AsyncMock(side_effect=CommandNotSent("query_needs_telnet"))
    with patch.object(DenonAvrDevice, "async_check_capabilities", failing):
        entry = await _setup(hass, capability_check_pending=True)
    assert entry.options["capability_check_pending"] is True


async def test_a_failing_check_after_setup_is_only_logged(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    failing = AsyncMock(side_effect=ValueError("broken"))
    with patch.object(DenonAvrDevice, "async_check_capabilities", failing):
        entry = await _setup(hass, capability_check_pending=True)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options["capability_check_pending"] is True


async def test_removing_the_entry_removes_the_check(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    with patch.object(
        DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(RESULT))
    ):
        coordinator = loaded_entry.runtime_data
        await coordinator.capabilities.async_run(coordinator)
    key = f"{DOMAIN}.{loaded_entry.entry_id}.capabilities"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
    assert _issue(hass, loaded_entry, "capability_report") is None
