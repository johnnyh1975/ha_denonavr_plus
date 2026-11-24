"""Test the receiver: one sequence in the options and in the repair.

1. Check capabilities (read only), with a progress bar.
2. Test commands quietly, straight after.
3. Ask whether to test what is heard and seen: Start or Skip. Asked here,
   not at the start: it needs someone in front of the TV, and whoever has
   watched the first two parts is most likely still there.
4. One result page for all of it, with the link to the device-support issue.

The parts themselves are capabilities.py and command_test.py.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .avr.command_test import AUDIBLE, QUIET, summary
from .avr.errors import CommandNotSent
from .capabilities import issue_url
from .capabilities import summary as check_summary

if TYPE_CHECKING:
    from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator

_LOGGER = logging.getLogger(__name__)

NOT_CONNECTED = "test_not_connected"


class ReceiverTestSteps:
    """The steps of the sequence, mixed into the options and the repair flow.

    The flow provides the entry and how to finish (`_check_entry`,
    `_check_finished`).
    """

    hass: HomeAssistant
    _test_tasks: dict[str, asyncio.Task[dict[str, Any] | None]]
    _test_results: dict[str, dict[str, Any]]

    def _check_entry(self) -> DenonAvrConfigEntry | None:
        raise NotImplementedError

    def _check_finished(self) -> Any:
        """The flow's own end (a FlowResult of its kind)."""
        raise NotImplementedError

    async def async_step_tests_start(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """What the sequence does, before it starts (the repair)."""

        if user_input is not None:
            return await self.async_step_tests()
        return self.async_show_form(step_id="tests_start")  # type: ignore[attr-defined, no-any-return]

    async def async_step_tests(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._progress(
            "tests",
            "capability_check",
            lambda coordinator, progress: coordinator.capabilities.async_run(
                coordinator, progress
            ),
            "tests_quiet",
        )

    async def async_step_tests_quiet(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._progress(
            "tests_quiet",
            "command_test",
            lambda coordinator, progress: coordinator.command_test.async_run(
                coordinator, [QUIET], progress
            ),
            "tests_ask",
        )

    async def async_step_tests_ask(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_show_menu(  # type: ignore[attr-defined, no-any-return]
            step_id="tests_ask", menu_options=["tests_audible", "tests_done"]
        )

    async def async_step_tests_audible(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._progress(
            "tests_audible",
            "command_test",
            lambda coordinator, progress: coordinator.command_test.async_run(
                coordinator, [AUDIBLE], progress
            ),
            "tests_done",
        )

    async def async_step_tests_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_abort(reason=NOT_CONNECTED)  # type: ignore[attr-defined, no-any-return]

    async def async_step_tests_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return cast(FlowResult, self._check_finished())
        check = self._results().get("tests", {})
        answers = check_summary(check)
        tests: dict[str, Any] = {}
        for step, group in (("tests_quiet", QUIET), ("tests_audible", AUDIBLE)):
            # Only this run: the kept result also holds the other group's last one.
            ran = self._results().get(step, {}).get("tests", {})
            tests |= {key: item for key, item in ran.items() if item.get("group") == group}
        counts = summary(tests)
        confirmed = counts.get("confirmed", 0) + counts.get("query_only", 0)
        skipped = counts.get("skipped", 0) + counts.get("no_value", 0)
        return self.async_show_form(  # type: ignore[attr-defined, no-any-return]
            step_id="tests_done",
            description_placeholders={
                "model": check.get("model") or "Denon AVR",
                "answered": str(answers.get("answered", 0)),
                "total": str(sum(answers.values())),
                "confirmed": str(confirmed),
                "unconfirmed": str(len(tests) - confirmed - skipped),
                "skipped": str(skipped),
                "not_restored": str(counts.get("not_restored", 0)),
                "url": issue_url(check.get("model"), check.get("firmware")),
            },
        )

    # Helpers -------------------------------------------------------------

    def _results(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_test_results"):
            self._test_results = {}
        return self._test_results

    async def _progress(
        self,
        step_id: str,
        action: str,
        run: Callable[
            [DenonAvrCoordinator, Callable[[float], None]], Awaitable[dict[str, Any]]
        ],
        next_step: str,
    ) -> FlowResult:
        """Start the part, show its progress, go on when it is done."""

        if not hasattr(self, "_test_tasks"):
            self._test_tasks = {}
        task = self._test_tasks.get(step_id)
        started = task is None
        if task is None:
            entry = self._check_entry()
            if entry is None or entry.state is not ConfigEntryState.LOADED:
                return self.async_abort(reason=NOT_CONNECTED)  # type: ignore[attr-defined, no-any-return]
            task = self.hass.async_create_task(self._run(entry.runtime_data, run))
            self._test_tasks[step_id] = task
        # Show the progress at least once, even for a part that finished at
        # once: the next step must not receive this step's input.
        if started or not task.done():
            return self.async_show_progress(  # type: ignore[attr-defined, no-any-return]
                step_id=step_id, progress_action=action, progress_task=task
            )
        result = task.result()
        if result is None:
            return self.async_show_progress_done(next_step_id="tests_failed")  # type: ignore[attr-defined, no-any-return]
        self._results()[step_id] = result
        return self.async_show_progress_done(next_step_id=next_step)  # type: ignore[attr-defined, no-any-return]

    async def _run(
        self,
        coordinator: DenonAvrCoordinator,
        run: Callable[
            [DenonAvrCoordinator, Callable[[float], None]], Awaitable[dict[str, Any]]
        ],
    ) -> dict[str, Any] | None:
        try:
            return await run(coordinator, self.async_update_progress)  # type: ignore[attr-defined]
        except CommandNotSent:
            return None
        except Exception:  # the flow shows "not connected"; the log has the cause
            _LOGGER.exception("Receiver test failed")
            return None
