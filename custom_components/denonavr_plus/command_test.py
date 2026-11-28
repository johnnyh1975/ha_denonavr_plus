"""The command test in Home Assistant: running it and keeping it.

The test itself is avr/command_test.py; it runs as part of the receiver test
(receiver_test.py) and, chosen when a model not checked yet is added, after
the capability check. The result is kept per entry and goes into the
diagnostics.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .avr.command_test import GROUPS
from .avr.errors import CommandNotSent
from . import markers
from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator

_LOGGER = logging.getLogger(__name__)

#: Entry option: the groups to test once after setup (chosen when adding).
OPT_TEST_PENDING = markers.TEST_PENDING
_STORE_VERSION = 1


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
        on_current: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Run the test and keep the result.

        Raises CommandNotSent when the receiver is not connected over telnet.
        """

        async with self._lock:
            result = await coordinator.device.async_command_test(groups, on_progress, on_current)
        result["tested_at"] = dt_util.utcnow().isoformat()
        # The other group's last results stay: the quiet and the audible part
        # run one after the other, and the diagnostics hold both.
        previous = self.result or {}
        kept = {
            key: item
            for key, item in previous.get("tests", {}).items()
            if item.get("group") not in groups
        }
        result["tests"] = kept | result["tests"]
        result["groups"] = [
            group
            for group in GROUPS
            if group in groups or group in previous.get("groups", [])
        ]
        if self._hass.config_entries.async_get_entry(self._entry_id) is None:
            return result
        await self._store.async_save(result)
        self.result = result
        return result


async def async_run_pending(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """The test chosen when the receiver was added; a background task.

    Runs after the capability check (when that was chosen too), then clears
    the option. When the receiver is not reachable, it stays pending.
    """

    coordinator = entry.runtime_data
    groups = [group for group in coordinator.markers.get(OPT_TEST_PENDING, []) if group in GROUPS]
    if groups:
        try:
            result = await coordinator.command_test.async_run(coordinator, groups)
        except CommandNotSent:
            _LOGGER.info("Command test postponed: the receiver is not connected")
            return
        except Exception:  # a background task must not raise
            _LOGGER.exception("Command test failed")
            return
        # One message for the check and the test (the check did not send its own).
        coordinator.capabilities.async_report(coordinator.capabilities.result or result)
    coordinator.markers.remove(OPT_TEST_PENDING)
