"""Tests for receiver_test.py: the receiver test as a flow, from the options."""

from __future__ import annotations

import json
from pathlib import Path

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import receiver_test
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.errors import CommandNotSent


from .common import options_step

HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X3600H"

pytestmark = pytest.mark.usefixtures("flow_without_setup")

MANIFEST_VERSION = json.loads(
    (Path(__file__).parents[1] / "custom_components/denonavr_plus/manifest.json").read_text()
)["version"]

CHECK = {"model": "AVC-X3700H", "firmware": "1.2.3", "queries": {
    "PW?": {"group": "Power", "result": "answered", "lines": ["PWON"]},
    "SR?": {"group": "Power", "result": "no reply", "lines": []},
}}


def _command_test(groups: list[str]) -> dict[str, Any]:
    tests = {
        "quiet": {
            "bass": {"group": "quiet", "result": "confirmed", "restored": True},
            "treble": {"group": "quiet", "result": "query_only", "restored": True},
            "dimmer": {"group": "quiet", "result": "no_effect", "restored": False},
            "lfe": {"group": "quiet", "result": "no_value"},
            "zone2_channel_level_fl": {"group": "quiet", "result": "confirmed", "restored": False},
        },
        "audible": {
            "main_mute": {"group": "audible", "result": "confirmed", "restored": True},
            "setup_menu": {"group": "audible", "result": "skipped", "reason": "no_alternative"},
            "volume_limit": {"group": "audible", "result": "interrupted", "reason": "receiver_silent"},
        },
    }
    found: dict[str, Any] = {}
    for group in groups:
        found |= tests[group]
    return {"model": "AVC-X3700H", "firmware": "1.2.3", "groups": groups, "tests": found}


async def _through_progress(hass: HomeAssistant, result: Any) -> Any:
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    return await hass.config_entries.options.async_configure(result["flow_id"])


@pytest.mark.parametrize("audible", [True, False])
async def test_the_receiver_test_runs_its_parts_in_sequence(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, audible: bool
) -> None:
    async def command_test(self: Any, groups: list[str], on_progress: Any = None, on_current: Any = None) -> Any:
        on_progress(0.5)
        return _command_test(groups)

    check = AsyncMock(return_value=dict(CHECK))
    options_before = dict(loaded_entry.options)
    shown: list[float] = []
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", check),
        patch.object(DenonAvrDevice, "async_command_test", command_test),
        patch(
            "homeassistant.config_entries.OptionsFlow.async_update_progress",
            lambda self, progress: shown.append(progress),
        ),
    ):
        # A start page says what happens; Start begins.
        result = await options_step(hass, loaded_entry, "tests_start")
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "tests_start"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        assert (result["step_id"], result["progress_action"]) == ("tests_check", "test_part_1")
        result = await _through_progress(hass, result)
        # The quiet part follows by itself.
        assert (result["step_id"], result["progress_action"]) == ("tests_quiet", "test_part_2")
        result = await _through_progress(hass, result)
        # Then the question, with a button each.
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "tests_ask"
        assert result["menu_options"] == ["tests_audible", "tests_done"]
        choice = "tests_audible" if audible else "tests_done"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": choice}
        )
        if audible:
            assert result["progress_action"] == "test_part_3"
            result = await _through_progress(hass, result)
    check.assert_awaited_once()
    # One progress over the whole test, not starting again for every part.
    expected_shares = [0.0, 0.25, 0.5] + ([0.75, 0.875] if audible else [])
    assert shown == expected_shares
    # A setting did not come back: the result says so in its title.
    assert result["step_id"] == "tests_done_check"
    placeholders = result["description_placeholders"]
    assert (placeholders["answered"], placeholders["total"]) == ("1", "2")
    expected = ("4", "1", "2", "1", "2") if audible else ("3", "1", "1", "0", "2")
    assert (
        placeholders["confirmed"],
        placeholders["no_effect"],
        placeholders["skipped"],
        placeholders["interrupted"],
        placeholders["not_restored"],
    ) == expected
    # Named as their entities are, with the device where the name says little.
    assert placeholders["not_restored_list"] == "Dimmer, Zone 2: Front Left Level"
    # A receiver report in Discussions, with this integration's version.
    assert "/discussions/new?category=receiver-reports" in placeholders["url"]
    assert "version=" + MANIFEST_VERSION in placeholders["url"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Changes no option; both parts are kept for the diagnostics.
    assert dict(loaded_entry.options) == options_before
    kept = loaded_entry.runtime_data.command_test.result
    assert ("main_mute" in kept["tests"]) is audible
    assert "bass" in kept["tests"]


async def test_everything_put_back_is_the_result_title(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    tested = {"model": "AVC-X3700H", "groups": ["quiet"], "tests": {
        "bass": {"group": "quiet", "result": "confirmed", "restored": True},
        "dynamic_eq": {"group": "quiet", "result": "precondition", "reason": "audyssey_off"},
    }}
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(CHECK))),
        patch.object(DenonAvrDevice, "async_command_test", AsyncMock(return_value=tested)),
    ):
        result = await options_step(hass, loaded_entry, "tests_start")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        result = await _through_progress(hass, result)
        result = await _through_progress(hass, result)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "tests_done"}
        )
    assert result["step_id"] == "tests_done"
    placeholders = result["description_placeholders"]
    assert (placeholders["confirmed"], placeholders["precondition"]) == ("1", "1")
    assert (placeholders["not_restored"], placeholders["not_restored_list"]) == ("0", "-")


async def test_the_receiver_test_needs_the_receiver(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Opened, then the receiver went away: Start says why instead of running."""
    result = await options_step(hass, loaded_entry, "tests_start")
    await hass.config_entries.async_unload(loaded_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "test_not_connected"


@pytest.mark.parametrize("error", [CommandNotSent("query_needs_telnet"), RuntimeError("bug")])
async def test_the_receiver_test_ends_when_a_part_fails(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, error: Exception
) -> None:
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(CHECK))),
        patch.object(DenonAvrDevice, "async_command_test", AsyncMock(side_effect=error)),
    ):
        result = await options_step(hass, loaded_entry, "tests_start")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        result = await _through_progress(hass, result)
        result = await _through_progress(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "test_not_connected"


async def test_the_progress_names_what_runs(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The step is reloaded with the name of each part of the receiver."""
    keys = ("audio", "speakers", "zone2", "receiver", "something_new")
    gates = {key: asyncio.Event() for key in ("check", *keys)}

    async def check(self: Any, on_progress: Any = None, on_current: Any = None) -> Any:
        on_current("Signal and setup")
        await gates["check"].wait()
        return dict(CHECK)

    async def command_test(
        self: Any, groups: list[str], on_progress: Any = None, on_current: Any = None
    ) -> Any:
        for index, key in enumerate(keys):
            on_current(key)
            await gates[key].wait()
            on_progress((index + 1) / len(keys))
        return _command_test(groups)

    changed: list[Any] = []
    hass.bus.async_listen("data_entry_flow_progressed", changed.append)

    async def shown(flow_id: str) -> str:
        # What the frontend does on the event: load the step again.
        for _ in range(5):
            await asyncio.sleep(0)
        result = await hass.config_entries.options.async_configure(flow_id)
        return str(result["description_placeholders"]["current"])

    names: list[str] = []
    sent: list[float] = []
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", check),
        patch.object(DenonAvrDevice, "async_command_test", command_test),
        patch.object(receiver_test, "PROGRESS_AGAIN", 0),
        patch(
            "homeassistant.config_entries.OptionsFlow.async_update_progress",
            lambda self, progress: sent.append(round(progress, 2)),
        ),
    ):
        result = await options_step(hass, loaded_entry, "tests_start")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        flow_id = result["flow_id"]
        names.append(await shown(flow_id))
        gates["check"].set()
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(flow_id)
        assert result["step_id"] == "tests_quiet"
        for key in keys:
            names.append(await shown(flow_id))
            gates[key].set()
    # The part's name, and a readable key for anything new.
    assert names == [
        "Signal and setup",
        "Audio",
        "Speakers",
        "Zone 2",
        "Receiver",
        "Something new",
    ]
    # After each reload the percentage comes again, so the circle keeps it.
    assert sent[:3] == [0.0, 0.0, 0.25]
    assert sent.count(0.35) == 2
    assert changed and all(event.data["refresh"] for event in changed)
