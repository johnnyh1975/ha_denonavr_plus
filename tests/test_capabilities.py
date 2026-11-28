"""Tests for capabilities.py: the check kept, its repairs, the check after setup."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import capabilities
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.avr.models import DeviceInfo
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN



RESULT: dict[str, Any] = {
    "check_version": 1,
    "model": "AVC-X3700H",
    "firmware": "1.2.3",
    "queries": {
        "PW?": {"group": "Power", "result": "answered", "lines": ["PWON"]},
        "PSFOO ?": {"group": "Test", "result": "no reply", "lines": []},
    },
}
from .common import report_notification


def _issue(hass: HomeAssistant, entry: MockConfigEntry, kind: str) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{kind}_{entry.entry_id}")


def test_the_receiver_report_link_is_prefilled() -> None:
    url = urlparse(capabilities.report_url("AVR-X2600H", "3.139.175", "1.1.0"))
    query = parse_qs(url.query)
    assert url.path == "/johnnyh1975/ha_denonavr_plus/discussions/new"
    assert query["category"] == ["receiver-reports"]
    assert query["title"] == ["[Receiver] AVR-X2600H"]
    # The field ids of .github/DISCUSSION_TEMPLATE/receiver-reports.yml.
    assert (query["model"], query["firmware"], query["version"]) == (
        ["AVR-X2600H"], ["3.139.175"], ["1.1.0"]
    )
    form = (Path(__file__).parents[1] / ".github/DISCUSSION_TEMPLATE/receiver-reports.yml").read_text()
    for field in ("model", "firmware", "version"):
        assert f"id: {field}" in form


def test_the_issue_link_is_prefilled() -> None:
    url = urlparse(capabilities.issue_url("SR6015", "4.1.0"))
    query = parse_qs(url.query)
    assert url.path == "/johnnyh1975/ha_denonavr_plus/issues/new"
    assert query["template"] == ["device_support.yml"]
    assert query["model"] == ["SR6015"]
    assert query["firmware"] == ["4.1.0"]
    assert query["title"] == ["Device support: SR6015"]


async def test_a_described_generation_gets_no_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The recording (AVC-X3700H) is not in KNOWN_MODELS, but of level 0301."""
    assert _issue(hass, loaded_entry, "capability_check") is None


async def test_a_device_of_no_described_generation_gets_a_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    system = DeviceInfo(model_name="M-CR510", comm_api_version=220, product_category=2)
    loaded_entry.runtime_data.capabilities.async_update_issues(system)
    issue = _issue(hass, loaded_entry, "capability_check")
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders == {"model": "M-CR510"}


def test_a_sibling_of_a_checked_model_counts_as_known() -> None:
    assert capabilities.known_model("AVR-S750H")
    assert capabilities.known_model("AVR-E400")
    assert capabilities.known_model("SR6014")
    assert not capabilities.known_model("NR1200")  # stereo, same document
    assert not capabilities.known_model("AVR-X2700H")
    assert not capabilities.known_model(None)


async def test_a_known_model_gets_none(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    with patch.object(capabilities, "KNOWN_MODELS", frozenset({"AVC-X3700H"})):
        loaded_entry.runtime_data.capabilities.async_update_issues(DeviceInfo(model_name="AVC-X3700H"))
    assert _issue(hass, loaded_entry, "capability_check") is None


async def _setup(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10"},
        options=options,
        minor_version=CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_a_check_asked_for_at_setup_runs_once(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    check = AsyncMock(return_value=dict(RESULT))
    with patch.object(DenonAvrDevice, "async_check_capabilities", check):
        entry = await _setup(hass, capability_check_pending=True)
    check.assert_awaited_once()
    assert entry.state is ConfigEntryState.LOADED
    assert "capability_check_pending" not in entry.options
    assert report_notification(hass, entry) is not None


async def test_a_check_asked_for_waits_for_the_connection(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    failing = AsyncMock(side_effect=CommandNotSent("query_needs_telnet"))
    with patch.object(DenonAvrDevice, "async_check_capabilities", failing):
        entry = await _setup(hass, capability_check_pending=True)
    assert entry.options["capability_check_pending"] is True


async def test_a_failing_check_after_setup_is_only_logged(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    failing = AsyncMock(side_effect=ValueError("broken"))
    with patch.object(DenonAvrDevice, "async_check_capabilities", failing):
        entry = await _setup(hass, capability_check_pending=True)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options["capability_check_pending"] is True


async def test_removing_the_entry_removes_the_check(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    with patch.object(
        DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(RESULT))
    ):
        coordinator = loaded_entry.runtime_data
        await coordinator.capabilities.async_run(coordinator)
    key = f"{DOMAIN}.{loaded_entry.entry_id}.capabilities"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
    assert report_notification(hass, loaded_entry) is None


async def test_checks_run_one_at_a_time(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    running = 0
    most = 0

    async def check(self: DenonAvrDevice, on_progress: Any = None, on_current: Any = None) -> dict[str, Any]:
        nonlocal running, most
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.02)
        running -= 1
        return dict(RESULT)

    coordinator = loaded_entry.runtime_data
    with patch.object(DenonAvrDevice, "async_check_capabilities", check):
        await asyncio.gather(
            coordinator.capabilities.async_run(coordinator),
            coordinator.capabilities.async_run(coordinator),
        )
    assert most == 1


async def test_a_check_ending_after_removal_keeps_nothing(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    coordinator = loaded_entry.runtime_data
    key = f"{DOMAIN}.{loaded_entry.entry_id}.capabilities"

    async def check(self: DenonAvrDevice, on_progress: Any = None, on_current: Any = None) -> dict[str, Any]:
        assert await hass.config_entries.async_remove(loaded_entry.entry_id)
        return dict(RESULT)

    with patch.object(DenonAvrDevice, "async_check_capabilities", check):
        await coordinator.capabilities.async_run(coordinator)
    await hass.async_block_till_done()
    assert key not in hass_storage
    assert report_notification(hass, loaded_entry) is None


async def test_the_report_survives_a_reload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    coordinator = loaded_entry.runtime_data
    with patch.object(
        DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(RESULT))
    ):
        await coordinator.capabilities.async_run(coordinator)
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert report_notification(hass, loaded_entry) is not None
