"""The capability check in Home Assistant: running it, keeping it, asking for it.

The check itself (the queries) is avr/capability_check.py. Here: the result
is kept per entry, goes into the diagnostics, and a model that has not been
checked gets a repair that runs the receiver test (receiver_test.py), which
starts with the check. When a check ran, a second repair
points to a prefilled device-support issue on GitHub, where the user attaches
the diagnostics file. Nothing is sent anywhere by the integration itself.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .avr.capability_check import KNOWN_MODELS, RELATED_MODELS
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
    """Checked, or a sibling of a checked model: no check is asked for."""
    return model in KNOWN_MODELS or model in RELATED_MODELS


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
        # One check at a time: the pending one after setup, the repair and the
        # options step share the telnet replies.
        self.lock = asyncio.Lock()

    async def async_load(self) -> None:
        self.result = await self._store.async_load()

    async def async_remove(self) -> None:
        await self._store.async_remove()

    async def async_run(
        self,
        coordinator: DenonAvrCoordinator,
        on_progress: Callable[[float], None] | None = None,
        *,
        report: bool = True,
        on_current: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Run the check, keep the result and point to the GitHub issue.

        Without `report`, the pointer waits for what runs after the check
        (the command test after setup), so there is one message for both.
        Raises CommandNotSent when the receiver is not connected over telnet.
        """

        async with self.lock:
            result = await coordinator.device.async_check_capabilities(on_progress, on_current)
        result["checked_at"] = dt_util.utcnow().isoformat()
        if self._hass.config_entries.async_get_entry(self._entry_id) is None:
            # Removed while the check ran: keep nothing behind.
            return result
        await self._store.async_save(result)
        self.result = result
        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(ISSUE_CHECK))
        if report:
            self.async_report(result)
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

    def async_delete_check_issue(self) -> None:
        """At unload: the request for a check is raised again at the next setup.

        The pointer to GitHub stays until the user ignores it or the entry is
        removed; a reload must not take it away before it was read.
        """

        ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(ISSUE_CHECK))

    def async_delete_issues(self) -> None:
        for issue in (ISSUE_CHECK, ISSUE_REPORT):
            ir.async_delete_issue(self._hass, DOMAIN, self._issue_id(issue))

    def async_report(self, result: dict[str, Any]) -> None:
        """The pointer to the prefilled issue, for a model not known yet."""

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


async def async_run_pending(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, *, report: bool = True
) -> None:
    """The check asked for when the receiver was added; a background task.

    Runs once the integration is connected, then clears the option. When the
    receiver is not reachable, it stays pending for the next start.
    """

    coordinator = entry.runtime_data
    try:
        await coordinator.capabilities.async_run(coordinator, report=report)
    except CommandNotSent:
        _LOGGER.info("Capability check postponed: the receiver is not connected")
        return
    except Exception:  # a background task must not raise
        _LOGGER.exception("Capability check failed")
        return
    options = {k: v for k, v in entry.options.items() if k != OPT_CHECK_PENDING}
    hass.config_entries.async_update_entry(entry, options=options)
