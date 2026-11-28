"""Tests for layout_repair.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component

from custom_components.denonavr_plus import layout_repair
from custom_components.denonavr_plus.avr.errors import SetupWriteError
from custom_components.denonavr_plus.avr.speaker_layout import (
    PRESET_CHANGED,
    RestoreRefused,
)
from custom_components.denonavr_plus.const import DOMAIN

from .test_init import receiver_network  # noqa: F401 -- fixtures

__all__ = ["receiver_network"]

POINT = {
    "before": {
        "mode": "6",
        "positions": {"3": "4", "5": "5"},
        "front_speaker": "A",
        "speaker_preset": None,
    },
    "after": {
        "mode": "17",
        "positions": {"3": "5", "5": "6"},
        "front_speaker": "B",
        "speaker_preset": None,
    },
    "unsettled": False,
}
SPEAKER_LAYOUT = "custom_components.denonavr_plus.avr.speaker_layout.SpeakerLayout"


def _report(hass: HomeAssistant, web_control: Any, point: dict[str, Any]) -> str:
    entry = web_control.entry
    layout_repair.async_report(hass, entry.runtime_data, point)
    return layout_repair.issue_id(entry.entry_id)


async def _fix(hass: HomeAssistant, issue: str) -> Any:
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, data={"issue_id": issue})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "confirm"
    return await manager.async_configure(result["flow_id"], {})


async def test_what_a_layout_reset_becomes_a_repair_that_puts_it_back(
    hass: HomeAssistant, web_control: Any
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    issue = _report(hass, web_control, POINT)
    found = ir.async_get(hass).async_get_issue(DOMAIN, issue)
    assert found is not None and found.is_fixable
    # Two positions and front speaker A/B.
    assert found.translation_placeholders["count"] == "3"
    with patch(
        f"{SPEAKER_LAYOUT}.async_restore", AsyncMock(return_value=[])
    ) as restore:
        result = await _fix(hass, issue)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    restore.assert_awaited_once_with(POINT)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None


async def test_the_repair_keeps_its_way_back_over_a_restart(
    hass: HomeAssistant, web_control: Any
) -> None:
    issue = _report(hass, web_control, POINT)
    found = ir.async_get(hass).async_get_issue(DOMAIN, issue)
    assert found is not None and found.is_persistent
    assert layout_repair.open_point(hass, web_control.entry.entry_id) == POINT


async def test_a_layout_that_reset_nothing_else_leaves_no_repair(
    hass: HomeAssistant, web_control: Any
) -> None:
    issue = _report(hass, web_control, POINT)
    same = {**POINT, "after": POINT["before"]}
    _report(hass, web_control, same)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None
    assert layout_repair.open_point(hass, web_control.entry.entry_id) is None
    # Not seen in time: what changed is not known, the way back stays.
    _report(hass, web_control, {**same, "unsettled": True})
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is not None


@pytest.mark.parametrize(
    ("raised", "left", "reason"),
    [
        (RestoreRefused(PRESET_CHANGED), None, "speaker_layout_preset_changed"),
        (SetupWriteError("speaker_layout"), None, "receiver_unavailable"),
        (TimeoutError(), None, "receiver_unavailable"),
        (None, ["5"], "speaker_layout_left"),
    ],
)
async def test_what_could_not_be_put_back_is_said(
    hass: HomeAssistant,
    web_control: Any,
    raised: Exception | None,
    left: list[str] | None,
    reason: str,
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    issue = _report(hass, web_control, POINT)
    restore = AsyncMock(side_effect=raised) if raised else AsyncMock(return_value=left)
    with patch(f"{SPEAKER_LAYOUT}.async_restore", restore):
        result = await _fix(hass, issue)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == reason
    # A refusal keeps the repair, for another try; what was done ends it.
    assert (ir.async_get(hass).async_get_issue(DOMAIN, issue) is None) is (
        raised is None
    )


async def test_without_the_receiver_nothing_is_written(
    hass: HomeAssistant, web_control: Any
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    issue = _report(hass, web_control, POINT)
    await hass.config_entries.async_unload(web_control.entry.entry_id)
    result = await _fix(hass, issue)
    assert (
        result["type"] is FlowResultType.ABORT
        and result["reason"] == "receiver_unavailable"
    )
