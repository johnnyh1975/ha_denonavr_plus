"""Repair flows: test a model not checked before."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

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


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """The only fixable issue is the capability check."""

    return CapabilityCheckRepairFlow(str((data or {})["entry_id"]))
