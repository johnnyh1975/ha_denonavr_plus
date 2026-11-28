"""Repair flows, one per fixable repair: test a model not checked before, take
another integration over, confirm the list after a move, unlink a missing
player, put a speaker layout back."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from . import capabilities, layout_repair, linked_players, move
from .const import DOMAIN
from .coordinator import DenonAvrConfigEntry
from .receiver_test import ReceiverTestSteps


class CapabilityCheckRepairFlow(ReceiverTestSteps, RepairsFlow):
    """The repair "<model> has not been checked yet": runs the receiver test."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_tests_start()

    def _check_entry(self) -> DenonAvrConfigEntry | None:
        return self.hass.config_entries.async_get_entry(self._entry_id)

    def _check_finished(self) -> FlowResult:
        return self.async_create_entry(data={})


class MoveRepairFlow(ConfirmRepairFlow):
    """The repair "<receiver> is also set up in <integration>": take it over."""

    def __init__(self, entry_id: str, old_entry_id: str) -> None:
        self._entry_id = entry_id
        self._old_entry_id = old_entry_id

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if user_input is None:
            return await super().async_step_confirm()
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        old = self.hass.config_entries.async_get_entry(self._old_entry_id)
        if entry is None or old is None or entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="move_not_possible")
        await move.async_move(self.hass, entry, old)
        return self.async_create_entry(data={})


#: The flow for each fixable repair, by its translation key. Every repair this
#: integration raises with is_fixable=True has an entry (tests/test_repairs.py).
FIX_FLOWS: dict[str, Callable[[dict[str, Any]], RepairsFlow]] = {
    capabilities.ISSUE_CHECK: lambda data: CapabilityCheckRepairFlow(str(data["entry_id"])),
    move.ISSUE_AVAILABLE: lambda data: MoveRepairFlow(
        str(data["entry_id"]), str(data["old_entry_id"])
    ),
    move.ISSUE_CHECK: lambda data: ConfirmRepairFlow(),
    linked_players.ISSUE: lambda data: linked_players.LinkedPlayerRepairFlow(
        str(data["entry_id"])
    ),
    layout_repair.ISSUE: lambda data: layout_repair.SpeakerLayoutRepairFlow(
        str(data["entry_id"]), str(data["point"])
    ),
}


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """The flow of a fixable repair, chosen by its translation key.

    A repair without a flow is an error, not some other flow.
    """

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    if issue is None or issue.translation_key not in FIX_FLOWS:
        raise ValueError(f"no repair flow for {issue_id}")
    return FIX_FLOWS[issue.translation_key](data or {})
