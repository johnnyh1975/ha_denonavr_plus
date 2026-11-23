"""Repair flows: run the capability check for a model not checked before."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .capabilities import CapabilityCheckSteps
from .coordinator import DenonAvrConfigEntry


class CapabilityCheckRepairFlow(CapabilityCheckSteps, RepairsFlow):
    """The repair "<model> has not been checked yet": runs the check."""

    def __init__(self, entry_id: str) -> None:
        self._entry_id = entry_id

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self.async_step_check()

    def _check_entry(self) -> DenonAvrConfigEntry | None:
        return self.hass.config_entries.async_get_entry(self._entry_id)

    def _check_finished(self) -> FlowResult:
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """The only fixable issue is the capability check."""

    return CapabilityCheckRepairFlow(str((data or {})["entry_id"]))
