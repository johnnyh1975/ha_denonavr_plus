"""The command test in Home Assistant: running it, keeping it, offering it.

The test itself is avr/command_test.py. It is offered in the options and when
the receiver is added (skippable, both groups off unless chosen). The result
is kept per entry and goes into the diagnostics; a test chosen at setup ends
with a message that points to the device-support issue on GitHub, as the
capability check does.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.selector import BooleanSelector
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .avr.command_test import AUDIBLE, GROUPS, QUIET, summary
from .avr.errors import CommandNotSent
from .capabilities import issue_url
from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator

_LOGGER = logging.getLogger(__name__)

#: Entry option: the groups to test once after setup (chosen when adding).
OPT_TEST_PENDING = "command_test_pending"
ISSUE_DONE = "command_test_done"
_STORE_VERSION = 1


def groups_schema(defaults: dict[str, bool] | None = None) -> dict[Any, Any]:
    """The two checkboxes: quiet and audible tests."""

    defaults = defaults or {}
    return {
        vol.Required(group, default=defaults.get(group, False)): BooleanSelector()
        for group in GROUPS
    }


def chosen(user_input: dict[str, Any]) -> list[str]:
    return [group for group in GROUPS if user_input.get(group)]


class CommandTestResults:
    """The last command test of one entry, kept in .storage."""

    def __init__(self, hass: HomeAssistant, entry_id: str, lock: asyncio.Lock) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass, _STORE_VERSION, f"{DOMAIN}.{entry_id}.command_test"
        )
        self.result: dict[str, Any] | None = None
        # Shared with the capability check: both read the telnet replies.
        self._lock = lock

    async def async_load(self) -> None:
        self.result = await self._store.async_load()

    async def async_remove(self) -> None:
        await self._store.async_remove()

    async def async_run(
        self,
        coordinator: DenonAvrCoordinator,
        groups: list[str],
        on_progress: Callable[[float], None] | None = None,
    ) -> dict[str, Any]:
        """Run the test and keep the result.

        Raises CommandNotSent when the receiver is not connected over telnet.
        """

        async with self._lock:
            result = await coordinator.device.async_command_test(groups, on_progress)
        result["tested_at"] = dt_util.utcnow().isoformat()
        if self._hass.config_entries.async_get_entry(self._entry_id) is None:
            return result
        await self._store.async_save(result)
        self.result = result
        return result

    def async_report(self, result: dict[str, Any]) -> None:
        """The message after a test chosen at setup: what came out, where it is."""

        counts = summary(result.get("tests"))
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            f"{ISSUE_DONE}_{self._entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_DONE,
            translation_placeholders={
                "model": result.get("model") or "Denon AVR",
                "confirmed": str(counts.get("confirmed", 0) + counts.get("query_only", 0)),
                "total": str(len(result.get("tests", {}))),
                "not_restored": str(counts.get("not_restored", 0)),
            },
            learn_more_url=issue_url(result.get("model"), result.get("firmware")),
        )

    def async_delete_issues(self) -> None:
        ir.async_delete_issue(self._hass, DOMAIN, f"{ISSUE_DONE}_{self._entry_id}")


async def async_run_pending(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """The test chosen when the receiver was added; a background task.

    Runs after the capability check (when that was chosen too), then clears
    the option. When the receiver is not reachable, it stays pending.
    """

    groups = [group for group in entry.options.get(OPT_TEST_PENDING, []) if group in GROUPS]
    coordinator = entry.runtime_data
    if groups:
        try:
            result = await coordinator.command_test.async_run(coordinator, groups)
        except CommandNotSent:
            _LOGGER.info("Command test postponed: the receiver is not connected")
            return
        except Exception:  # a background task must not raise
            _LOGGER.exception("Command test failed")
            return
        coordinator.command_test.async_report(result)
    options = {k: v for k, v in entry.options.items() if k != OPT_TEST_PENDING}
    hass.config_entries.async_update_entry(entry, options=options)


class CommandTestSteps:
    """Options steps: test_quiet or test_audible (menu entries that start at
    once) -> command_test_run (progress) -> command_test_done (the result)."""

    _test_task: asyncio.Task[dict[str, Any] | None] | None = None
    _test_result: dict[str, Any] | None = None
    _test_groups: list[str] | None = None

    hass: HomeAssistant

    def _check_entry(self) -> DenonAvrConfigEntry | None:
        raise NotImplementedError

    def _check_finished(self) -> Any:
        raise NotImplementedError

    async def async_step_test_quiet(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._test_groups = [QUIET]
        return await self.async_step_command_test_run()

    async def async_step_test_audible(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        self._test_groups = [AUDIBLE]
        return await self.async_step_command_test_run()

    async def async_step_command_test_run(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if self._test_task is None:
            entry = self._check_entry()
            if entry is None or entry.state is not ConfigEntryState.LOADED:
                return self.async_abort(reason="test_not_connected")  # type: ignore[attr-defined, no-any-return]
            self._test_task = self.hass.async_create_task(
                self._run_test(entry.runtime_data, self._test_groups or [])
            )
            started = True
        else:
            started = False
        if started or not self._test_task.done():
            return self.async_show_progress(  # type: ignore[attr-defined, no-any-return]
                step_id="command_test_run",
                progress_action="command_test",
                progress_task=self._test_task,
            )
        self._test_result = self._test_task.result()
        next_step = "command_test_done" if self._test_result is not None else "command_test_failed"
        return self.async_show_progress_done(next_step_id=next_step)  # type: ignore[attr-defined, no-any-return]

    async def _run_test(
        self, coordinator: DenonAvrCoordinator, groups: list[str]
    ) -> dict[str, Any] | None:
        try:
            return await coordinator.command_test.async_run(
                coordinator,
                groups,
                self.async_update_progress,  # type: ignore[attr-defined]
            )
        except CommandNotSent:
            return None
        except Exception:  # the flow shows "not connected"; the log has the cause
            _LOGGER.exception("Command test failed")
            return None

    async def async_step_command_test_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_abort(reason="test_not_connected")  # type: ignore[attr-defined, no-any-return]

    async def async_step_command_test_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return cast(FlowResult, self._check_finished())
        result = self._test_result or {}
        counts = summary(result.get("tests"))
        confirmed = counts.get("confirmed", 0) + counts.get("query_only", 0)
        skipped = counts.get("skipped", 0) + counts.get("no_value", 0)
        total = len(result.get("tests", {}))
        return self.async_show_form(  # type: ignore[attr-defined, no-any-return]
            step_id="command_test_done",
            description_placeholders={
                "model": result.get("model") or "Denon AVR",
                "confirmed": str(confirmed),
                "unconfirmed": str(total - confirmed - skipped),
                "skipped": str(skipped),
                "not_restored": str(counts.get("not_restored", 0)),
                "url": issue_url(result.get("model"), result.get("firmware")),
            },
        )
