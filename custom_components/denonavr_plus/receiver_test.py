"""Test the receiver: one sequence in the options and in the repair.

0. A start page: the three steps, how long, what changes; Start.
1. Read capabilities (read only).
2. Test commands quietly, straight after.
3. Ask whether to test what is heard and seen: Test now or Skip. Asked here,
   not at the start: it needs someone in front of the TV, and whoever has
   watched the first two steps is most likely still there.
4. One result page, titled by whether everything came back, with the link
   to the device-support issue.

One progress runs from 0 to 100 % over the three steps; each step has its
own title (step x of 3) and a checklist. Closing the window cancels the
running step, which puts back what it changed.

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
from homeassistant.helpers import translation

from .avr.capability_check import QUERIES
from .avr.command_test import AUDIBLE, QUIET, summary
from .avr.errors import CommandNotSent
from .capabilities import issue_url
from .capabilities import summary as check_summary
from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator

_LOGGER = logging.getLogger(__name__)

NOT_CONNECTED = "test_not_connected"


#: A part: (coordinator, progress, current) -> its result.
Run = Callable[
    ["DenonAvrCoordinator", Callable[[float], None], Callable[[str], None]],
    Awaitable[dict[str, Any]],
]

#: Names shown for what runs that are no entity's name: the parts of the
#: capability check (slugs of avr.capability_check.QUERIES groups) and the
#: tests that are no profile control. Translated under selector.receiver_test_item.
ITEM_SELECTOR = "receiver_test_item"
#: A test whose entity has another translation key.
_ENTITY_KEY = {"main_volume": "volume"}

#: The share of the whole test each part covers, so the progress runs once
#: from 0 to 100 % instead of starting again for every part.
SHARES = {"tests_check": (0.0, 0.25), "tests_quiet": (0.25, 0.75), "tests_audible": (0.75, 1.0)}


class ReceiverTestSteps:
    """The steps of the sequence, mixed into the options and the repair flow.

    tests_start (what happens, Start) -> tests_check -> tests_quiet ->
    tests_ask (Test now or Skip) -> tests_audible -> tests_done, or
    tests_done_check when a setting did not come back. The flow provides the
    entry and how to finish (`_check_entry`, `_check_finished`).
    """

    hass: HomeAssistant
    _test_tasks: dict[str, asyncio.Task[dict[str, Any] | None]]
    _test_results: dict[str, dict[str, Any]]
    _test_current: str = ""
    _test_names: dict[str, str] | None = None

    def _check_entry(self) -> DenonAvrConfigEntry | None:
        raise NotImplementedError

    def _check_finished(self) -> Any:
        """The flow's own end (a FlowResult of its kind)."""
        raise NotImplementedError

    async def async_step_tests_start(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """What the test does and how long it takes; it starts with Start."""

        if user_input is not None:
            return await self.async_step_tests_check()
        return self.async_show_form(step_id="tests_start")  # type: ignore[attr-defined, no-any-return]

    async def async_step_tests_check(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._progress(
            "tests_check",
            "test_part_1",
            lambda coordinator, progress, current: coordinator.capabilities.async_run(
                coordinator, progress, on_current=current
            ),
            "tests_quiet",
        )

    async def async_step_tests_quiet(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self._progress(
            "tests_quiet",
            "test_part_2",
            lambda coordinator, progress, current: coordinator.command_test.async_run(
                coordinator, [QUIET], progress, current
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
            "test_part_3",
            lambda coordinator, progress, current: coordinator.command_test.async_run(
                coordinator, [AUDIBLE], progress, current
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
        """The result: "Everything put back", or what to check on the receiver."""

        if user_input is not None:
            return cast(FlowResult, self._check_finished())
        placeholders = self._summary()
        step_id = "tests_done_check" if placeholders["not_restored"] != "0" else "tests_done"
        return self.async_show_form(  # type: ignore[attr-defined, no-any-return]
            step_id=step_id, description_placeholders=placeholders
        )

    async def async_step_tests_done_check(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return await self.async_step_tests_done(user_input)

    # Helpers -------------------------------------------------------------

    def _summary(self) -> dict[str, str]:
        check = self._results().get("tests_check", {})
        answers = check_summary(check)
        tests: dict[str, Any] = {}
        for step, group in (("tests_quiet", QUIET), ("tests_audible", AUDIBLE)):
            # Only this run: the kept result also holds the other group's last one.
            ran = self._results().get(step, {}).get("tests", {})
            tests |= {key: item for key, item in ran.items() if item.get("group") == group}
        counts = summary(tests)
        confirmed = counts.get("confirmed", 0) + counts.get("query_only", 0)
        precondition = counts.get("precondition", 0)
        skipped = sum(
            counts.get(result, 0) for result in ("skipped", "no_value", "interrupted")
        )
        not_restored = sorted(key for key, item in tests.items() if item.get("restored") is False)
        return {
            "model": check.get("model") or "Denon AVR",
            "answered": str(answers.get("answered", 0)),
            "total": str(sum(answers.values())),
            "confirmed": str(confirmed),
            "precondition": str(precondition),
            "no_effect": str(len(tests) - confirmed - precondition - skipped),
            "skipped": str(skipped),
            "not_restored": str(len(not_restored)),
            "not_restored_list": ", ".join(not_restored) or "-",
            "url": issue_url(check.get("model"), check.get("firmware")),
        }

    async def _names(self) -> dict[str, str]:
        """What runs, by key, in the language of Home Assistant."""

        if self._test_names is None:
            texts = await translation.async_get_translations(
                self.hass, self.hass.config.language, "entity", {DOMAIN}
            )
            texts |= await translation.async_get_translations(
                self.hass, self.hass.config.language, "selector", {DOMAIN}
            )
            names: dict[str, str] = {}
            items = f"component.{DOMAIN}.selector.{ITEM_SELECTOR}.options."
            for key, text in texts.items():
                if key.startswith(items):
                    names[key[len(items) :]] = text
                elif key.startswith(f"component.{DOMAIN}.entity.") and key.endswith(".name"):
                    names.setdefault(key.split(".")[-2], text)
            for test, entity in _ENTITY_KEY.items():
                if entity in names:
                    names.setdefault(test, names[entity])
            for group in {group for _, group in QUERIES}:
                slug = _slug(group)
                if slug in names:
                    names[group] = names[slug]
            self._test_names = names
        return self._test_names

    def _results(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_test_results"):
            self._test_results = {}
        return self._test_results

    async def _progress(
        self,
        step_id: str,
        action: str,
        run: Run,
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
            low, high = SHARES[step_id]

            def progress(share: float) -> None:
                self.async_update_progress(low + (high - low) * share)  # type: ignore[attr-defined]

            progress(0.0)
            names = await self._names()
            self._test_current = ""

            def current(key: str) -> None:
                # The frontend reloads the step and shows the new name.
                self._test_current = names.get(key) or _readable(key)
                self.async_notify_flow_changed()  # type: ignore[attr-defined]

            task = self.hass.async_create_task(
                self._run(entry.runtime_data, run, progress, current)
            )
            self._test_tasks[step_id] = task
        # Show the progress at least once, even for a part that finished at
        # once: the next step must not receive this step's input.
        if started or not task.done():
            return self.async_show_progress(  # type: ignore[attr-defined, no-any-return]
                step_id=step_id,
                progress_action=action,
                progress_task=task,
                description_placeholders={"current": self._test_current or "…"},
            )
        result = task.result()
        if result is None:
            return self.async_show_progress_done(next_step_id="tests_failed")  # type: ignore[attr-defined, no-any-return]
        self._results()[step_id] = result
        return self.async_show_progress_done(next_step_id=next_step)  # type: ignore[attr-defined, no-any-return]

    async def _run(
        self,
        coordinator: DenonAvrCoordinator,
        run: Run,
        progress: Callable[[float], None],
        current: Callable[[str], None],
    ) -> dict[str, Any] | None:
        try:
            return await run(coordinator, progress, current)
        except CommandNotSent:
            return None
        except Exception:  # the flow shows "not connected"; the log has the cause
            _LOGGER.exception("Receiver test failed")
            return None


def _slug(text: str) -> str:
    return "_".join(text.lower().split())


def _readable(key: str) -> str:
    return key.replace("_", " ").capitalize()
