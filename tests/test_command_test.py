"""Tests for command_test.py: the test kept, chosen at setup, and its message."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import capabilities
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.const import DOMAIN
from custom_components.denonavr_plus.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .test_capabilities import RESULT as CHECK_RESULT


RESULT: dict[str, Any] = {
    "test_version": 1,
    "model": "AVC-X3700H",
    "firmware": "1.2.3",
    "groups": ["quiet"],
    "main_zone_on": True,
    "tests": {
        "bass": {"group": "quiet", "result": "confirmed", "restored": True},
        "dimmer": {"group": "quiet", "result": "confirmed", "restored": False},
        "lfe": {"group": "quiet", "result": "no_value"},
    },
}
from .common import report_notification, set_up_and_settle


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any] | None:
    """The pointer to the prefilled issue (the recording is not a known model)."""
    return report_notification(hass, entry)


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    return await set_up_and_settle(hass, **options)


async def test_a_test_chosen_at_setup_runs_once(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    run = AsyncMock(return_value=dict(RESULT))
    with patch.object(DenonAvrDevice, "async_command_test", run):
        entry = await _setup(hass, command_test_pending=["quiet", "unknown"])
    assert run.await_args.args[0] == ["quiet"]
    assert entry.state is ConfigEntryState.LOADED
    assert "command_test_pending" not in entry.options
    assert entry.runtime_data.markers.get("command_test_pending") is None
    # A notification, not a repair: nothing to fix.
    issue = _issue(hass, entry)
    assert issue is not None and "AVC-X3700H" in issue["title"]
    assert capabilities.issue_url("AVC-X3700H", None) in issue["message"]
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["command_test"]["tests"] == RESULT["tests"]


async def test_the_check_runs_before_the_test(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    order: list[str] = []

    async def check(self: DenonAvrDevice, on_progress: Any = None, on_current: Any = None) -> dict[str, Any]:
        order.append("check")
        return dict(CHECK_RESULT)

    async def test(self: DenonAvrDevice, groups: Any, on_progress: Any = None, on_current: Any = None) -> dict[str, Any]:
        # One message for both: the check has not sent its own yet.
        order.append("message" if _issue(hass, entries()[0]) else "test")
        return dict(RESULT)

    def entries() -> list[Any]:
        return hass.config_entries.async_entries(DOMAIN)
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", check),
        patch.object(DenonAvrDevice, "async_command_test", test),
    ):
        entry = await _setup(
            hass, capability_check_pending=True, command_test_pending=["quiet"]
        )
    assert order == ["check", "test"]
    assert "command_test_pending" not in entry.options
    assert entry.runtime_data.markers.get("command_test_pending") is None
    assert _issue(hass, entry) is not None


async def test_a_postponed_check_postpones_the_test(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    run = AsyncMock(return_value=dict(RESULT))
    with (
        patch.object(
            DenonAvrDevice,
            "async_check_capabilities",
            AsyncMock(side_effect=CommandNotSent("query_needs_telnet")),
        ),
        patch.object(DenonAvrDevice, "async_command_test", run),
    ):
        entry = await _setup(
            hass, capability_check_pending=True, command_test_pending=["quiet"]
        )
    run.assert_not_awaited()
    assert entry.runtime_data.markers.get("command_test_pending") == ["quiet"]


async def test_a_test_chosen_waits_for_the_connection(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    failing = AsyncMock(side_effect=CommandNotSent("query_needs_telnet"))
    with patch.object(DenonAvrDevice, "async_command_test", failing):
        entry = await _setup(hass, command_test_pending=["audible"])
    assert entry.runtime_data.markers.get("command_test_pending") == ["audible"]
    assert _issue(hass, entry) is None


async def test_a_failing_test_after_setup_is_only_logged(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    failing = AsyncMock(side_effect=ValueError("broken"))
    with patch.object(DenonAvrDevice, "async_command_test", failing):
        entry = await _setup(hass, command_test_pending=["quiet"])
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.markers.get("command_test_pending") == ["quiet"]


async def test_no_group_left_only_clears_the_option(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    run = AsyncMock(return_value=dict(RESULT))
    with patch.object(DenonAvrDevice, "async_command_test", run):
        entry = await _setup(hass, command_test_pending=[])
        # An empty list is not "pending": nothing runs at setup.
        assert entry.runtime_data.markers.get("command_test_pending") == []
        from custom_components.denonavr_plus import command_test

        await command_test.async_run_pending(hass, entry)
    run.assert_not_awaited()
    assert "command_test_pending" not in entry.options
    assert entry.runtime_data.markers.get("command_test_pending") is None


async def test_removing_the_entry_removes_the_test(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    coordinator = loaded_entry.runtime_data
    with patch.object(DenonAvrDevice, "async_command_test", AsyncMock(return_value=dict(RESULT))):
        await coordinator.command_test.async_run(coordinator, ["quiet"])
    key = f"{DOMAIN}.{loaded_entry.entry_id}.command_test"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_the_test_and_the_check_run_one_at_a_time(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    running = 0
    most = 0

    async def busy() -> None:
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.02)
        running -= 1

    async def check(self: DenonAvrDevice, on_progress: Any = None, on_current: Any = None) -> dict[str, Any]:
        await busy()
        return dict(CHECK_RESULT)

    async def test(self: DenonAvrDevice, groups: Any, on_progress: Any = None, on_current: Any = None) -> dict[str, Any]:
        await busy()
        return dict(RESULT)

    coordinator = loaded_entry.runtime_data
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", check),
        patch.object(DenonAvrDevice, "async_command_test", test),
    ):
        await asyncio.gather(
            coordinator.capabilities.async_run(coordinator),
            coordinator.command_test.async_run(coordinator, ["quiet"]),
        )
    assert most == 1


async def test_a_test_ending_after_removal_keeps_nothing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    coordinator = loaded_entry.runtime_data

    async def test(self: DenonAvrDevice, groups: Any, on_progress: Any = None, on_current: Any = None) -> dict[str, Any]:
        assert await hass.config_entries.async_remove(loaded_entry.entry_id)
        return dict(RESULT)

    with patch.object(DenonAvrDevice, "async_command_test", test):
        await coordinator.command_test.async_run(coordinator, ["quiet"])
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{loaded_entry.entry_id}.command_test" not in hass_storage


async def test_the_result_is_kept_across_a_reload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    coordinator = loaded_entry.runtime_data
    with patch.object(DenonAvrDevice, "async_command_test", AsyncMock(return_value=dict(RESULT))):
        await coordinator.command_test.async_run(coordinator, ["quiet"])
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.command_test.result["tests"] == RESULT["tests"]


async def test_the_other_groups_last_result_is_kept(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Quiet, then audible: the diagnostics hold both."""
    coordinator = loaded_entry.runtime_data
    audible = {
        "model": "AVC-X3700H",
        "groups": ["audible"],
        "tests": {"main_mute": {"group": "audible", "result": "confirmed"}},
    }
    run = AsyncMock(side_effect=[dict(RESULT), dict(audible), dict(RESULT)])
    with patch.object(DenonAvrDevice, "async_command_test", run):
        await coordinator.command_test.async_run(coordinator, ["quiet"])
        result = await coordinator.command_test.async_run(coordinator, ["audible"])
        assert set(result["tests"]) == {"bass", "dimmer", "lfe", "main_mute"}
        assert result["groups"] == ["quiet", "audible"]
        # The quiet part again replaces only the quiet results.
        result = await coordinator.command_test.async_run(coordinator, ["quiet"])
    assert set(result["tests"]) == {"bass", "dimmer", "lfe", "main_mute"}
