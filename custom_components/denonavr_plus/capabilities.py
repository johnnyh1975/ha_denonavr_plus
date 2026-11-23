"""The capability check in Home Assistant: running it, keeping it, asking for it.

The check itself (the queries) is avr/capability_check.py. Here: the result
is kept per entry, goes into the diagnostics, and a model that has not been
checked gets a repair that runs the check. When a check ran, a second repair
points to a prefilled device-support issue on GitHub, where the user attaches
the diagnostics file. Nothing is sent anywhere by the integration itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .avr.capability_check import KNOWN_MODELS
from .avr.errors import CommandNotSent
from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator

_LOGGER = logging.getLogger(__name__)

#: Entry option: run the check once after setup (asked when adding a model
#: that has not been checked).
OPT_CHECK_PENDING = "capability_check_pending"
ISSUE_CHECK = "capability_check"
ISSUE_REPORT = "capability_report"
_STORE_VERSION = 1
_NEW_ISSUE = "https://github.com/johnnyh1975/ha_denonavr_plus/issues/new"


def known_model(model: str | None) -> bool:
    return model in KNOWN_MODELS


def issue_url(model: str | None, firmware: str | None) -> str:
    """A device-support issue on GitHub with model and firmware filled in."""

    query = {
        "template": "device_support.yml",
        "title": f"Device support: {model or 'unknown model'}",
        "model": model or "",
        "firmware": firmware or "",
    }
    return f"{_NEW_ISSUE}?{urlencode(query)}"


class CapabilityResults:
    """The last check result of one entry, kept in .storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORE_VERSION, f"{DOMAIN}.{entry_id}.capabilities"
        )
        self.result: dict[str, Any] | None = None

    async def async_load(self) -> None:
        self.result = await self._store.async_load()

    async def async_remove(self) -> None:
        await self._store.async_remove()

    async def async_run(
        self,
        coordinator: DenonAvrCoordinator,
        on_progress: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        """Run the check, keep the result and point to the GitHub issue.

        Raises CommandNotSent when the receiver is not connected over telnet.
        """

        result = await coordinator.device.async_check_capabilities(on_progress)
        result["checked_at"] = dt_util.utcnow().isoformat()
        await self._store.async_save(result)
        self.result = result
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(ISSUE_CHECK))
        self._report_issue(result)
        return result

    def async_update_issues(self, model: str | None) -> None:
        """Ask for a check when the model has none and is not known."""

        if known_model(model) or self.result is not None:
            ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(ISSUE_CHECK))
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self._issue_id(ISSUE_CHECK),
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_CHECK,
            translation_placeholders={"model": model or "Denon AVR"},
            data={"entry_id": self._entry_id},
        )

    def async_delete_issues(self) -> None:
        for issue in (ISSUE_CHECK, ISSUE_REPORT):
            ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(issue))

    def _report_issue(self, result: dict[str, Any]) -> None:
        if known_model(result.get("model")):
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self._issue_id(ISSUE_REPORT),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_REPORT,
            translation_placeholders={"model": result.get("model") or "Denon AVR"},
            learn_more_url=issue_url(result.get("model"), result.get("firmware")),
        )

    def _issue_id(self, issue: str) -> str:
        return f"{issue}_{self._entry_id}"


def summary(result: dict[str, Any] | None) -> dict[str, int]:
    """How many queries were answered, rejected or unanswered."""

    counts: dict[str, int] = {}
    for item in (result or {}).get("queries", {}).values():
        counts[item["result"]] = counts.get(item["result"], 0) + 1
    return counts


async def async_run_pending(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """The check asked for when the receiver was added; a background task.

    Runs once the integration is connected, then clears the option. When the
    receiver is not reachable, it stays pending for the next start.
    """

    coordinator = entry.runtime_data
    try:
        await coordinator.capabilities.async_run(coordinator)
    except CommandNotSent:
        _LOGGER.info("Capability check postponed: the receiver is not connected")
        return
    except Exception:  # a background task must not raise
        _LOGGER.exception("Capability check failed")
        return
    options = {k: v for k, v in entry.options.items() if k != OPT_CHECK_PENDING}
    hass.config_entries.async_update_entry(entry, options=options)


class CapabilityCheckSteps:
    """The steps that run the check with a progress bar and show the result.

    Shared by the options flow and the repair flow; each provides the entry
    and how to finish. Steps: check (a confirmation) -> check_run (progress)
    -> check_done (the result and the GitHub link).
    """

    _check_task: asyncio.Task[dict[str, Any] | None] | None = None
    _check_result: dict[str, Any] | None = None

    # Provided by the flow handler this is mixed into.
    hass: HomeAssistant

    def _check_entry(self) -> DenonAvrConfigEntry | None:
        raise NotImplementedError

    def _check_finished(self) -> Any:
        """The flow's own end (a FlowResult of its kind)."""
        raise NotImplementedError

    async def async_step_check(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return await self.async_step_check_run()
        return self.async_show_form(step_id="check")  # type: ignore[attr-defined, no-any-return]

    async def async_step_check_run(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._check_task is None:
            entry = self._check_entry()
            if entry is None or entry.state is not ConfigEntryState.LOADED:
                return self.async_abort(reason="not_connected")  # type: ignore[attr-defined, no-any-return]
            self._check_task = self.hass.async_create_task(self._run(entry.runtime_data))
            started = True
        else:
            started = False
        # Show the progress at least once, even for a task that finished at
        # once: the next step must not receive this step's input.
        if started or not self._check_task.done():
            return self.async_show_progress(  # type: ignore[attr-defined, no-any-return]
                step_id="check_run",
                progress_action="capability_check",
                progress_task=self._check_task,
            )
        self._check_result = self._check_task.result()
        next_step = "check_done" if self._check_result is not None else "check_failed"
        return self.async_show_progress_done(next_step_id=next_step)  # type: ignore[attr-defined, no-any-return]

    async def _run(self, coordinator: DenonAvrCoordinator) -> dict[str, Any] | None:
        try:
            return await coordinator.capabilities.async_run(
                coordinator,
                self.async_update_progress,  # type: ignore[attr-defined]
            )
        except CommandNotSent:
            return None

    async def async_step_check_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_abort(reason="not_connected")  # type: ignore[attr-defined, no-any-return]

    async def async_step_check_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return cast(FlowResult, self._check_finished())
        result = self._check_result or {}
        counts = summary(result)
        return self.async_show_form(  # type: ignore[attr-defined, no-any-return]
            step_id="check_done",
            description_placeholders={
                "model": result.get("model") or "Denon AVR",
                "answered": str(counts.get("answered", 0)),
                "total": str(sum(counts.values())),
                "url": issue_url(result.get("model"), result.get("firmware")),
            },
        )
