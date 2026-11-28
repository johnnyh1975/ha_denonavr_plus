"""Repair flows: test a model not checked before, take another integration over,
choose the inputs Zone 2 plays."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from . import layout_repair, linked_players, move, zone_sources
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


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """The receiver test, taking another integration over, Zone 2's inputs,
    unlinking a missing player, or a list to confirm."""

    data = data or {}
    if issue_id.startswith(zone_sources.ISSUE):
        return zone_sources.ZoneSourcesRepairFlow(str(data["entry_id"]))
    if issue_id.startswith(move.ISSUE_AVAILABLE):
        return MoveRepairFlow(str(data["entry_id"]), str(data["old_entry_id"]))
    if issue_id.startswith(linked_players.ISSUE):
        return linked_players.LinkedPlayerRepairFlow(str(data["entry_id"]))
    if issue_id.startswith(move.ISSUE_CHECK):
        return ConfirmRepairFlow()
    if issue_id.startswith(layout_repair.ISSUE):
        return layout_repair.SpeakerLayoutRepairFlow(str(data["entry_id"]), str(data["point"]))
    return CapabilityCheckRepairFlow(str(data["entry_id"]))
