"""Tests for avr/command_test.py: write, confirm, put back."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr import command_test
from custom_components.denonavr_plus.avr.command_test import (
    AUDIBLE,
    NEVER,
    QUIET,
    Probe,
    alternative,
    same,
    summary,
)
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.avr.profile import ControlSpec, load_profile

from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


class Receiver:
    """Answers queries and writes as a receiver would, with its quirks.

    settings: reply prefix -> value. queries: query -> the prefixes it answers.
    A write is the longest prefix it starts with, followed by the new value.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        settings: dict[str, str],
        queries: dict[str, list[str]],
        *,
        silent: tuple[str, ...] = (),
        ignored: tuple[str, ...] = (),
        rejected: tuple[str, ...] = (),
        echo_only: tuple[str, ...] = (),
        once: tuple[str, ...] = (),
        links: dict[str, dict[str, str]] | None = None,
    ) -> None:
        self.hass = hass
        self.telnet = FakeTelnet.instances[-1]
        self.settings = settings
        self.queries = queries
        self.silent, self.ignored, self.rejected = silent, ignored, rejected
        self.echo_only, self.once = echo_only, once
        self.links = links or {}
        self.written: set[str] = set()

    async def send(self, command: str) -> None:
        self.telnet.sent.append(command)
        if command in self.queries:
            lines = [
                f"{prefix}{self.settings[prefix]}"
                for prefix in self.queries[command]
                if prefix in self.settings
            ]
            self._push(*lines)
            return
        prefixes = [p for p in self.settings if command.startswith(p)]
        if not prefixes:
            return
        prefix = max(prefixes, key=len)
        value = command[len(prefix) :]
        if prefix in self.ignored or (prefix in self.once and prefix in self.written):
            return
        if prefix in self.rejected:
            self._push("?")
            return
        self.written.add(prefix)
        if prefix not in self.echo_only:
            self.settings[prefix] = value
            self.settings.update(self.links.get(prefix, {}))
        if prefix not in self.silent:
            self._push(f"{prefix}{value}")

    def _push(self, *lines: str) -> None:
        if lines:
            self.hass.loop.call_soon(self.telnet.push, *lines)


@pytest.fixture
def fast() -> Iterator[None]:
    with (
        patch.object(command_test, "CONFIRM_WAIT", 0.05),
        patch.object(command_test, "QUERY_WAIT", 0.05),
        patch.object(command_test, "ZONE_SETTLE", 0.3),
        patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_QUIET", 0.02),
    ):
        yield


def _only(*keys: str) -> Any:
    """Run only these of the receiver's tests, in this order."""

    real = command_test.probes_for

    def probes(device: Any, groups: Any) -> list[Probe]:
        found = {probe.key: probe for probe in real(device, command_test.GROUPS)}
        return [found[key] for key in keys]

    return patch.object(command_test, "probes_for", probes)


async def _run(
    hass: HomeAssistant, entry: MockConfigEntry, receiver: Receiver, *keys: str
) -> dict[str, Any]:
    device = entry.runtime_data.device
    with patch.object(receiver.telnet, "async_send", receiver.send), _only(*keys):
        return await device.async_command_test([QUIET, AUDIBLE])


# Choosing a value -------------------------------------------------------


@pytest.fixture(scope="module")
def profile() -> Any:
    return load_profile()


def test_a_different_value_for_each_kind(profile: Any) -> None:
    control = profile.control
    assert alternative(control("tone_control"), True) is False
    # Levels move towards 0 dB, which every range contains.
    assert alternative(control("bass"), 0.0) == 0.5
    assert alternative(control("bass"), 3.0) == 2.5
    assert alternative(control("bass"), -3.0) == -2.5
    # Enums take the next value, the one before at the end of the list.
    assert alternative(control("dimmer"), "BRI") == "DIM"
    assert alternative(control("dimmer"), "OFF") == "DAR"
    assert alternative(control("dimmer"), "XYZ") == "BRI"
    # Not 15 minutes: the receiver would switch off if it were not put back.
    assert alternative(control("auto_standby"), "OFF") == "8H"
    assert alternative(control("auto_standby"), "8H") == "4H"
    assert alternative(control("effect_level"), 3) == 4
    assert alternative(control("effect_level"), 15) == 14
    assert alternative(control("lfe"), 0) == -1
    # The longest timer when none runs.
    assert alternative(control("sleep_timer"), 0) == 120
    assert alternative(control("sleep_timer"), 60) == 55
    assert alternative(control("sleep_timer"), 5) == 10


def test_no_different_value() -> None:
    assert alternative(ControlSpec("x", {"kind": "enum", "values": ["A"]}), "A") is None
    assert alternative(ControlSpec("x", {"kind": "enum", "values": []}), "A") is None
    assert alternative(ControlSpec("x", {"kind": "integer", "min": 0, "max": 0}), 0) is None
    assert alternative(ControlSpec("x", {"kind": "token"}), "A") is None
    assert alternative(ControlSpec("x", {"kind": "integer"}), 4) == 5


def test_values_are_compared_as_the_parser_decodes_them() -> None:
    assert same(0.5, 0.5000001)
    assert not same(None, None)
    assert not same(True, 1)
    assert same(False, False)
    assert same("ON", "ON")


def test_the_summary_counts_what_was_not_put_back() -> None:
    results = {
        "a": {"result": "confirmed", "restored": True},
        "b": {"result": "confirmed", "restored": False},
        "c": {"result": "skipped"},
    }
    assert summary(results) == {"confirmed": 2, "not_restored": 1, "skipped": 1}
    assert summary(None) == {}


# Which tests run --------------------------------------------------------


async def test_the_tests_of_a_receiver(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    device = loaded_entry.runtime_data.device
    probes = command_test.probes_for(device, [QUIET, AUDIBLE])
    keys = [probe.key for probe in probes]
    groups = [probe.group for probe in probes]
    # Quiet ones first; nothing that powers, triggers or stores.
    assert groups == sorted(groups, key=[QUIET, AUDIBLE].index)
    assert not set(keys) & NEVER
    assert {"bass", "dimmer", "main_volume"} <= {p.key for p in probes if p.group == QUIET}
    assert {"main_mute", "multeq", "setup_menu", "zone2_follow_main", "tuner_preset"} <= {
        p.key for p in probes if p.group == AUDIBLE
    }
    by_key = {probe.key: probe for probe in probes}
    # A lower volume limit can pull the volume down: the main zone is put back.
    assert by_key["volume_limit"].zones == ("main",)
    assert by_key["all_zone_stereo"].zones == ("zone2",)
    audible = command_test.probes_for(device, [AUDIBLE])
    assert {probe.group for probe in audible} == {AUDIBLE}


# Running them -----------------------------------------------------------


async def test_each_outcome(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    settings = {
        "ZM": "ON",
        "PSBAS ": "50",
        "PSTRE ": "50",
        "PSTONE CTRL ": "OFF",
        "PSDYNEQ ": "ON",
        "PSLOM ": "OFF",
        "DIM ": "BRI",
        "MV": "50",
    }
    queries = {
        "ZM?": ["ZM"],
        "PSBAS ?": ["PSBAS "],
        "PSTRE ?": ["PSTRE "],
        "PSTONE CTRL ?": ["PSTONE CTRL "],
        "PSDYNEQ ?": ["PSDYNEQ "],
        "PSLOM ?": ["PSLOM "],
        "DIM ?": ["DIM "],
        "MV?": ["MV"],
        "PSLFE ?": [],
    }
    receiver = Receiver(
        hass,
        settings,
        queries,
        silent=("PSTRE ",),
        ignored=("PSTONE CTRL ",),
        rejected=("PSDYNEQ ",),
        echo_only=("PSLOM ",),
        once=("DIM ",),
    )
    receiver.telnet.push("ZMON")
    progress: list[float] = []
    device = loaded_entry.runtime_data.device
    keys = ("bass", "treble", "tone_control", "dynamic_eq", "loudness_management")
    keys += ("dimmer", "lfe", "main_volume")
    with patch.object(receiver.telnet, "async_send", receiver.send), _only(*keys):
        result = await device.async_command_test([QUIET], progress.append)

    tests = result["tests"]
    assert {key: item["result"] for key, item in tests.items()} == {
        "bass": "confirmed",
        "treble": "query_only",
        "tone_control": "no_effect",
        "dynamic_eq": "rejected",
        "loudness_management": "echo_only",
        "dimmer": "confirmed",
        "lfe": "no_value",
        "main_volume": "confirmed",
    }
    assert tests["bass"] | {"lines": []} == {
        "group": QUIET,
        "result": "confirmed",
        "before": 0.0,
        "written": 0.5,
        "after": 0.5,
        "restored": True,
        "lines": [],
    }
    assert tests["bass"]["lines"] == ["PSBAS 50", "PSBAS 505", "PSBAS 505", "PSBAS 50", "PSBAS 50"]
    # The dimmer took the change but not the way back.
    assert tests["dimmer"]["restored"] is False
    assert tests["tone_control"]["restored"] is True
    assert receiver.settings["MV"] == "50"
    assert tests["main_volume"]["written"] == 49.5
    assert progress[-1] == 1.0 and len(progress) == len(keys)
    assert result["groups"] == [QUIET]
    assert result["main_zone_on"] is True
    assert result["test_version"] == command_test.TEST_VERSION


async def test_the_main_zone_must_be_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"PSBAS ": "50", "SLP": "OFF"}, {"PSBAS ?": ["PSBAS "], "SLP?": ["SLP"]})
    receiver.telnet.push("ZMOFF")
    result = await _run(hass, loaded_entry, receiver, "bass", "sleep_timer")
    assert result["tests"]["bass"] == {"group": QUIET, "result": "skipped", "reason": "main_zone_off"}
    # A timer is a setting of the receiver, not of the main zone's sound.
    assert result["tests"]["sleep_timer"]["result"] == "confirmed"
    assert result["tests"]["sleep_timer"]["written"] == 120
    assert receiver.settings["SLP"] == "OFF"
    assert "PSBAS 505" not in receiver.telnet.sent


async def test_the_test_needs_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    FakeTelnet.instances[-1].connected = False
    with pytest.raises(CommandNotSent):
        await loaded_entry.runtime_data.device.async_command_test([QUIET])


async def test_the_tests_after_a_lost_connection_are_interrupted(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"DIM ": "BRI", "ECO": "ON"}, {"DIM ?": ["DIM "], "ECO?": ["ECO"]})
    device = loaded_entry.runtime_data.device

    def drop(_progress: float) -> None:
        receiver.telnet.connected = False

    with patch.object(receiver.telnet, "async_send", receiver.send), _only("dimmer", "eco_mode"):
        result = await device.async_command_test([QUIET], drop)
    assert result["tests"]["dimmer"]["result"] == "confirmed"
    assert result["tests"]["eco_mode"] == {"group": QUIET, "result": "interrupted"}


async def test_a_write_that_cannot_go_out(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    device = loaded_entry.runtime_data.device
    receiver = Receiver(hass, {"DIM ": "BRI"}, {"DIM ?": ["DIM "]})

    async def unreachable(value: Any) -> None:
        raise CommandNotSent("unreachable")

    probe = Probe(
        key="dimmer",
        group=QUIET,
        query="DIM ?",
        prefixes=("DIM",),
        read=lambda lines: device.state.values.get("dimmer"),
        write=unreachable,
        alternative=lambda value: "DIM",
    )
    with patch.object(receiver.telnet, "async_send", receiver.send):
        result = await command_test._run_probe(device, probe)
    assert result["result"] == "interrupted"
    assert result["restored"] is False


async def test_a_cancelled_test_puts_the_value_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    receiver = Receiver(hass, {"DIM ": "BRI"}, {"DIM ?": ["DIM "]}, silent=("DIM ",))
    device = loaded_entry.runtime_data.device
    with (
        patch.object(receiver.telnet, "async_send", receiver.send),
        _only("dimmer"),
        patch.object(command_test, "QUERY_WAIT", 0.05),
        patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_QUIET", 0.02),
    ):
        task = hass.async_create_task(device.async_command_test([QUIET]))
        for _ in range(100):
            if "DIM DIM" in receiver.telnet.sent:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert receiver.telnet.sent[-1] == "DIM BRI"
    assert receiver.settings["DIM "] == "BRI"


# The tests that are not a profile control -------------------------------


@pytest.mark.assumed(
    "MNMEN ON / MNMEN OFF with a space, from the protocol documents; "
    "settled by the command test on a receiver"
)
async def test_the_setup_menu_is_opened_and_closed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"MNMEN ": "OFF"}, {"MNMEN?": ["MNMEN "]})
    receiver.telnet.push("ZMON")
    result = await _run(hass, loaded_entry, receiver, "setup_menu")
    assert result["tests"]["setup_menu"]["result"] == "confirmed"
    assert receiver.telnet.sent.count("MNMEN ON") == 1
    assert receiver.settings["MNMEN "] == "OFF"


@pytest.mark.assumed(
    "MNMEN? may get no answer (the remote commands are documented without a reply); "
    "settled by the command test on a receiver"
)
async def test_an_unanswered_setup_menu_is_taken_as_closed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"MNMEN ": "OFF"}, {}, silent=("MNMEN ",))
    receiver.telnet.push("ZMON")
    result = await _run(hass, loaded_entry, receiver, "setup_menu")
    item = result["tests"]["setup_menu"]
    assert (item["before"], item["result"], item["restored"]) == ("OFF", "no_effect", True)


async def test_an_open_setup_menu_is_left_alone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"MNMEN ": "ON"}, {"MNMEN?": ["MNMEN "]})
    receiver.telnet.push("ZMON")
    result = await _run(hass, loaded_entry, receiver, "setup_menu")
    assert result["tests"]["setup_menu"]["reason"] == "no_alternative"
    assert not any(command.startswith("MNMEN O") for command in receiver.telnet.sent)


async def test_zone2_follows_the_main_zone_and_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"Z2": "CD"}, {"Z2?": ["Z2"]})
    result = await _run(hass, loaded_entry, receiver, "zone2_follow_main")
    item = result["tests"]["zone2_follow_main"]
    assert (item["before"], item["written"], item["result"]) == ("CD", "SOURCE", "confirmed")
    assert item["restored"] is True
    assert receiver.settings["Z2"] == "CD"


async def test_zone2_that_follows_takes_the_main_input_and_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"Z2": "SOURCE"}, {"Z2?": ["Z2"]})
    receiver.telnet.push("SIDVD")
    result = await _run(hass, loaded_entry, receiver, "zone2_follow_main")
    assert result["tests"]["zone2_follow_main"]["written"] == "DVD"
    assert receiver.settings["Z2"] == "SOURCE"


async def test_a_zone_put_back_through_its_snapshot(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """Recalling a quick select is not undone by recalling the old one."""
    device = loaded_entry.runtime_data.device
    receiver = Receiver(hass, {"Z2": "OFF", "Z2QUICK": "1"}, {"Z2QUICK ?": ["Z2QUICK"]})
    receiver.telnet.push("Z2OFF", "Z2QUICK1")

    async def recall(value: Any) -> None:
        await device.async_send_command(f"Z2QUICK{value}")
        receiver._push("Z2ON")

    probe = Probe(
        key="zone2_quick_select",
        group=AUDIBLE,
        query="Z2QUICK ?",
        prefixes=("Z2QUICK",),
        read=lambda lines: device.state.values.get("zone2_quick_select"),
        write=recall,
        alternative=lambda value: "2",
        zones=("zone2",),
        write_back=False,
    )
    with patch.object(receiver.telnet, "async_send", receiver.send):
        result = await command_test._run_probe(device, probe)
    assert result["result"] == "confirmed"
    assert result["restored"] is True
    assert receiver.telnet.sent.count("Z2QUICK1") == 0
    assert receiver.telnet.sent[-1] == "Z2OFF"


async def test_a_zone_that_does_not_come_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    device = loaded_entry.runtime_data.device
    receiver = Receiver(hass, {"Z2": "OFF"}, {"Z2?": ["Z2"]}, ignored=("Z2",))
    receiver.telnet.push("Z2OFF")

    async def turn_on(value: Any) -> None:
        receiver.settings["Z2"] = "ON"
        await device.async_send_command("Z2ON")
        receiver._push("Z2ON")

    probe = Probe(
        key="zone2_power",
        group=AUDIBLE,
        query="Z2?",
        prefixes=("Z2",),
        read=lambda lines: device.state.zone("zone2").power,
        write=turn_on,
        alternative=lambda value: True,
        zones=("zone2",),
        write_back=False,
    )
    with patch.object(receiver.telnet, "async_send", receiver.send):
        result = await command_test._run_probe(device, probe)
    assert result["restored"] is False


TUNER = {"ZM": "ON", "SI": "TUNER", "TFAN": "010570", "TPAN": "OFF"}
TUNER_QUERIES = {"TFAN?": ["TFAN"], "TPAN?": ["TPAN"]}


@pytest.mark.assumed(
    "the tuner takes FM frequencies in 50 kHz steps (TFAN010575); receivers for "
    "the Americas may tune in 200 kHz steps; settled by the command test there"
)
async def test_the_tuner_frequency_is_tuned_and_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, dict(TUNER), TUNER_QUERIES, links={"TFAN": {"TPAN": "OFF"}})
    receiver.telnet.push("ZMON", "SITUNER", "TFAN010570", "TPANOFF")
    result = await _run(hass, loaded_entry, receiver, "tuner_frequency")
    item = result["tests"]["tuner_frequency"]
    assert (item["before"], item["written"], item["result"]) == (10570, 10575, "confirmed")
    assert item["restored"] is True
    assert receiver.settings["TFAN"] == "010570"


async def test_the_tuner_preset_it_played_is_recalled_again(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    settings = dict(TUNER, TPAN="03")
    links = {"TFAN": {"TPAN": "OFF"}, "TPAN": {"TFAN": "010570"}}
    receiver = Receiver(hass, settings, TUNER_QUERIES, links=links)
    receiver.telnet.push("ZMON", "SITUNER", "TFAN010570", "TPAN03")
    result = await _run(hass, loaded_entry, receiver, "tuner_frequency", "tuner_preset")
    assert result["tests"]["tuner_frequency"]["restored"] is True
    assert "TPAN03" in receiver.telnet.sent
    preset = result["tests"]["tuner_preset"]
    assert (preset["before"], preset["written"], preset["restored"]) == ("03", "04", True)
    assert receiver.settings["TPAN"] == "03"


async def test_a_tuner_without_a_preset_gets_its_frequency_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    links = {"TFAN": {"TPAN": "OFF"}, "TPAN": {"TFAN": "008750"}}
    receiver = Receiver(hass, dict(TUNER, TFAN="010800"), TUNER_QUERIES, links=links)
    receiver.telnet.push("ZMON", "SITUNER", "TFAN010800", "TPANOFF")
    result = await _run(hass, loaded_entry, receiver, "tuner_frequency", "tuner_preset")
    # The top of the band: one step down.
    assert result["tests"]["tuner_frequency"]["written"] == 10795
    preset = result["tests"]["tuner_preset"]
    assert (preset["before"], preset["written"], preset["restored"]) == ("OFF", "01", True)
    assert receiver.settings["TFAN"] == "010800"


async def test_the_tuner_is_tested_only_while_it_plays(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, dict(TUNER, TFAN="000522"), TUNER_QUERIES)
    receiver.telnet.push("ZMON", "SICD")
    result = await _run(hass, loaded_entry, receiver, "tuner_frequency")
    assert result["tests"]["tuner_frequency"]["reason"] == "tuner_not_playing"
    receiver.telnet.push("SITUNER", "TFAN000522")
    result = await _run(hass, loaded_entry, receiver, "tuner_frequency")
    # AM is not tested.
    assert result["tests"]["tuner_frequency"]["reason"] == "no_alternative"
