"""Tests for repairs.py: the repair that runs the capability check."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import capabilities
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.const import DOMAIN

from .test_capabilities import RESULT
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


def _issue(hass: HomeAssistant, entry: MockConfigEntry, kind: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{entry.entry_id}")


async def test_the_repair_runs_the_check_and_points_to_github(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    tested = {"model": "AVC-X3700H", "groups": ["quiet"], "tests": {
        "bass": {"group": "quiet", "result": "confirmed", "restored": True},
    }}
    with (
        patch.object(
            DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(RESULT))
        ),
        patch.object(DenonAvrDevice, "async_command_test", AsyncMock(return_value=tested)),
    ):
        result = await manager.async_init(
            DOMAIN, data={"issue_id": f"capability_check_{loaded_entry.entry_id}"}
        )
        # The repair first says what the receiver test does.
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "tests_start"
        result = await manager.async_configure(result["flow_id"], {})
        for _part in ("check", "quiet"):
            assert result["type"] is FlowResultType.SHOW_PROGRESS
            await hass.async_block_till_done()
            result = await manager.async_configure(result["flow_id"])
        assert result["step_id"] == "tests_ask"
        result = await manager.async_configure(result["flow_id"], {"next_step_id": "tests_done"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "tests_done"
    placeholders = result["description_placeholders"]
    assert placeholders["answered"] == "1" and placeholders["total"] == "2"
    assert placeholders["confirmed"] == "1"
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
    assert result["reason"] == "test_not_connected"
    assert _issue(hass, loaded_entry, "capability_check") is not None


async def test_a_failing_check_in_the_repair_aborts(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Any error, not only a lost connection, ends the flow cleanly."""
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    failing = AsyncMock(side_effect=ValueError("broken reply"))
    with patch.object(DenonAvrDevice, "async_check_capabilities", failing):
        result = await manager.async_init(
            DOMAIN, data={"issue_id": f"capability_check_{loaded_entry.entry_id}"}
        )
        result = await manager.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
        result = await manager.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "test_not_connected"
