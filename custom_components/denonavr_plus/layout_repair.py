"""The repair after a speaker layout change: what the receiver reset, and putting it back.

Applying a speaker layout (avr/speaker_layout.py) makes the receiver reset
the choices of its speaker positions and front speaker A/B. The repair says
how many it reset; fixing it puts them back. The restore point is kept in the
repair's data, so it survives a restart of Home Assistant.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.repairs import ConfirmRepairFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .avr.errors import CommandNotSent, SetupWriteError
from .avr.speaker_layout import RestoreRefused, changes
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import DenonAvrCoordinator

ISSUE = "speaker_layout_changed"


def issue_id(entry_id: str) -> str:
    return f"{ISSUE}_{entry_id}"


def open_point(hass: HomeAssistant, entry_id: str) -> dict[str, Any] | None:
    """The restore point of a repair still open, to keep going back to its state."""

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry_id))
    if issue is None or not issue.active or not issue.data or "point" not in issue.data:
        return None
    return cast(dict[str, Any], json.loads(str(issue.data["point"])))


def async_report(hass: HomeAssistant, coordinator: DenonAvrCoordinator, point: dict[str, Any]) -> None:
    """The repair for a layout change that reset more; none when nothing else changed."""

    entry = coordinator.config_entry
    assert entry is not None
    entry_id = entry.entry_id
    if not point.get("unsettled") and not changes(point):
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry_id))
        return
    model = coordinator.device.discovery.device.model_name or "Denon AVR"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(entry_id),
        is_fixable=True,
        # Kept over a restart with its data: the restore point lives here.
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE,
        translation_placeholders={"model": model, "count": str(len(changes(point)))},
        # As text: a repair's data holds plain values only.
        data={"entry_id": entry_id, "point": json.dumps(point)},
    )


class SpeakerLayoutRepairFlow(ConfirmRepairFlow):
    """Put back the speaker positions and front speaker A/B a layout change reset.

    Putting back can take up to a minute (each write waits for the receiver),
    so it runs as a task with a progress step, not inside the confirm step.
    """

    def __init__(self, entry_id: str, point: str) -> None:
        self._entry_id = entry_id
        self._point: dict[str, Any] = json.loads(point)
        self._task: asyncio.Task[tuple[str | None, list[str]]] | None = None

    async def async_step_confirm(self, user_input: dict[str, str] | None = None) -> FlowResult:
        if self._task is None:
            if user_input is None:
                return await super().async_step_confirm()
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            coordinator = getattr(entry, "runtime_data", None) if entry is not None else None
            if coordinator is None:
                return self.async_abort(reason="receiver_unavailable")
            self._task = self.hass.async_create_task(
                self._restore(coordinator), "denonavr_plus speaker layout restore"
            )
        if not self._task.done():
            return self.async_show_progress(
                step_id="confirm", progress_action="restoring", progress_task=self._task
            )
        return self.async_show_progress_done(next_step_id="finish")

    async def _restore(self, coordinator: DenonAvrCoordinator) -> tuple[str | None, list[str]]:
        """The reason it stopped (None when done) and what is still different."""

        try:
            left = await coordinator.device.speaker_layout.async_restore(self._point)
        except RestoreRefused as err:
            return f"speaker_layout_{err.reason}", []
        except TimeoutError:
            # The receiver answered, but slowly: part may be back already.
            return "speaker_layout_timeout", []
        except (SetupWriteError, CommandNotSent):
            return "receiver_unavailable", []
        return None, left

    async def async_step_finish(self, user_input: dict[str, str] | None = None) -> FlowResult:
        assert self._task is not None
        reason, left = self._task.result()
        if reason is not None:
            # The repair stays, for another try.
            return self.async_abort(reason=reason)
        ir.async_delete_issue(self.hass, DOMAIN, issue_id(self._entry_id))
        if left:
            return self.async_abort(
                reason="speaker_layout_left", description_placeholders={"count": str(len(left))}
            )
        return self.async_create_entry(data={})
