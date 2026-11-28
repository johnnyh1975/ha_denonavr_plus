"""Tests for avr/command_test.py: write, confirm, put back."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import PropertyMock, patch

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
from custom_components.denonavr_plus.avr.models import AvrState, Discovery
from custom_components.denonavr_plus.avr.parser import TelnetParser
from custom_components.denonavr_plus.avr.profile import (
    ControlSpec,
    load_profile,
    profile_for_receiver,
)

from .fakes import FakeTelnet


class Receiver:
    """Answers queries and writes as a receiver would, with its quirks.

    settings: reply prefix -> value. queries: query -> the prefixes it answers.
    A write is the longest prefix it starts with, followed by the new value.
    It answers its power query; `stop_after` (a command) makes it take that
    command and then answer nothing, until `wake` is called.
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
        stop_after: str | None = None,
    ) -> None:
        self.hass = hass
        self.telnet = FakeTelnet.instances[-1]
        self.settings = settings
        self.queries = queries
        self.silent, self.ignored, self.rejected = silent, ignored, rejected
        self.echo_only, self.once = echo_only, once
        self.links = links or {}
        self.written: set[str] = set()
        self.stop_after = stop_after
        self.asleep = False

    def wake(self) -> None:
        self.asleep = False

    async def send(self, command: str) -> None:
        self.telnet.sent.append(command)
        if self.asleep:
            return
        if command == self.stop_after:
            self.asleep = True
        if command == "PW?":
            self._push("PWON")
            return
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
        patch.object(command_test, "POWER_WAIT", 0.2),
        patch.object(command_test, "POWER_SETTLE", 0.01),
        patch.object(command_test, "RECOVER_WAIT", 2.0),
        patch.object(command_test, "RECOVER_POLL", 0.05),
        patch.object(command_test, "SETTLE", 0.01),
        patch.object(command_test, "SETTLE_WAIT", 0.3),
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
    # 60 minutes, not 8 hours: an AVR-X2600H ignored STBY8H.
    assert alternative(control("auto_standby"), "OFF") == "60M"
    assert alternative(control("auto_standby"), "60M") == "2H"
    # Auto exists for every input; Digital does not on one with HDMI only.
    assert alternative(control("input_mode"), "HDMI") == "AUTO"
    assert alternative(control("effect_level"), 3) == 4
    assert alternative(control("effect_level"), 15) == 14
    assert alternative(control("lfe"), 0) == -1
    # The longest timer when none runs.
    assert alternative(control("sleep_timer"), 0) == 120
    assert alternative(control("sleep_timer"), 60) == 55
    assert alternative(control("sleep_timer"), 5) == 10


async def test_a_level_moves_by_the_receivers_own_step(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An AVR-X2600H publishes whole dB for bass and ignored PSBAS 505."""
    device = loaded_entry.runtime_data.device
    device.discovery.numeric_meta["Bass"] = {"min": -6.0, "max": 6.0, "step": 1.0}
    probes = {p.key: p for p in command_test.probes_for(device, [QUIET])}
    assert probes["bass"].alternative(0.0) == 1.0
    assert probes["bass"].alternative(3.0) == 2.0


async def test_a_level_without_a_published_step_moves_by_whole_db(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An AVR-X2000 publishes no range for bass and ignored PSBAS 505."""
    device = loaded_entry.runtime_data.device
    device.discovery.numeric_meta.pop("Bass", None)
    probes = {p.key: p for p in command_test.probes_for(device, [QUIET])}
    assert probes["bass"].alternative(0.0) == 1.0


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


async def test_the_tests_go_through_the_receiver_part_by_part(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    probes = command_test.probes_for(device, [QUIET, AUDIBLE])
    areas = {probe.key: probe.area for probe in probes}
    assert areas["bass"] == areas["graphic_eq"] == areas["cinema_eq"] == "audio"
    assert areas["subwoofer_level"] == "speakers"
    assert areas["picture_mode"] == "video"
    assert areas["dimmer"] == areas["main_volume"] == areas["setup_menu"] == "receiver"
    # All Zone Stereo switches Zone 2 on; an AVR-X2600H stopped answering
    # when it came right after MultEQ.
    assert areas["zone2_follow_main"] == areas["all_zone_stereo"] == "zone2"
    assert areas["tuner_preset"] == "tuner"
    # Within each group the parts come one after the other, in their order,
    # so the progress names each part once.
    for group in (QUIET, AUDIBLE):
        order = [probe.area for probe in probes if probe.group == group]
        assert order == sorted(order, key=command_test.AREAS.index)


async def test_the_progress_learns_each_part_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    parts: list[str] = []
    with (
        _only("bass", "treble", "dimmer", "main_volume", "picture_mode"),
        patch.object(type(device), "telnet_connected", new_callable=PropertyMock, return_value=False),
    ):
        results = await command_test.async_run(device, [QUIET], on_current=parts.append)
    assert parts == ["audio", "receiver", "video"]
    assert {item["result"] for item in results.values()} == {"interrupted"}


# Running them -----------------------------------------------------------


async def test_each_outcome(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    settings = {
        "ZM": "ON",
        "PSBAS ": "50",
        "PSTRE ": "50",
        "PSTONE CTRL ": "OFF",
        "PSDRC ": "OFF",
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
        "PSDRC ?": ["PSDRC "],
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
        ignored=("PSDRC ",),
        rejected=("PSDYNEQ ",),
        echo_only=("PSLOM ",),
        once=("DIM ",),
    )
    receiver.telnet.push("ZMON", "PSTONE CTRL OFF")
    progress: list[float] = []
    device = loaded_entry.runtime_data.device
    keys = ("bass", "treble", "dynamic_compression", "dynamic_eq", "loudness_management")
    keys += ("dimmer", "lfe", "main_volume")
    with patch.object(receiver.telnet, "async_send", receiver.send), _only(*keys):
        result = await device.async_command_test([QUIET], progress.append)

    tests = result["tests"]
    assert {key: item["result"] for key, item in tests.items()} == {
        "bass": "confirmed",
        "treble": "query_only",
        "dynamic_compression": "no_effect",
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
        "written": 1.0,
        "after": 1.0,
        "restored": True,
        # Bass and treble are only taken with tone control on (and with it
        # Dynamic EQ off, which the recording does not report: left alone).
        "requires": {"dynamic_eq": False, "tone_control": True},
        "lines": [],
    }
    assert tests["bass"]["lines"] == ["PSBAS 50", "PSBAS 51", "PSBAS 51", "PSBAS 50", "PSBAS 50"]
    # The dimmer took the change but not the way back.
    assert tests["dimmer"]["restored"] is False
    assert tests["dynamic_compression"]["restored"] is True
    # Tone control on for bass, then off again; on again for treble, off again.
    assert receiver.telnet.sent.count("PSTONE CTRL ON") == 2
    assert receiver.settings["PSTONE CTRL "] == "OFF"
    assert result["context"]["tone_control"] is False
    # The signal is recorded: it decides what Dolby and DTS settings do.
    assert set(result["context"]["signal"]) == set(command_test.SIGNAL)
    assert receiver.settings["MV"] == "50"
    assert tests["main_volume"]["written"] == 49.5
    assert progress[-1] == 1.0 and len(progress) == len(keys)
    assert result["groups"] == [QUIET]
    assert result["main_zone_on"] is True
    assert result["test_version"] == command_test.TEST_VERSION


async def test_a_main_zone_that_is_off_is_turned_on_for_the_test(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """In standby an AVR-X2000 ignored every setting (its first quiet test)."""
    receiver = Receiver(hass, {"ZM": "OFF", "DIM ": "BRI"}, {"DIM ?": ["DIM "]})
    receiver.telnet.push("ZMOFF")
    result = await _run(hass, loaded_entry, receiver, "dimmer", "lfe")
    assert result["tests"]["dimmer"]["result"] == "confirmed"
    assert (result["main_zone_on"], result["turned_on_for_test"]) == (False, True)
    sent = receiver.telnet.sent
    assert sent.index("ZMON") < sent.index("DIM DIM")
    # Off again as it was.
    assert sent[-1] == "ZMOFF"
    assert receiver.settings["ZM"] == "OFF"


async def test_a_main_zone_already_on_is_left_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {"ZM": "ON", "DIM ": "BRI"}, {"DIM ?": ["DIM "]})
    receiver.telnet.push("ZMON")
    result = await _run(hass, loaded_entry, receiver, "dimmer")
    assert result["turned_on_for_test"] is False
    assert not {"ZMON", "ZMOFF"} & set(receiver.telnet.sent)


async def test_the_main_zone_must_be_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """A main zone that does not come on: its settings are skipped."""
    receiver = Receiver(hass, {"PSBAS ": "50", "SLP": "OFF"}, {"PSBAS ?": ["PSBAS "], "SLP?": ["SLP"]})
    receiver.telnet.push("ZMOFF")
    result = await _run(hass, loaded_entry, receiver, "bass", "sleep_timer")
    assert result["tests"]["bass"] == {"group": QUIET, "result": "skipped", "reason": "main_zone_off"}
    # A timer is a setting of the receiver, not of the main zone's sound.
    assert result["tests"]["sleep_timer"]["result"] == "confirmed"
    assert result["tests"]["sleep_timer"]["written"] == 120
    assert receiver.settings["SLP"] == "OFF"
    assert "PSBAS 51" not in receiver.telnet.sent


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
        patch.object(command_test, "POWER_WAIT", 0.05),
        patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_QUIET", 0.02),
    ):
        receiver.telnet.push("ZMON")
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


# A receiver that stops answering ----------------------------------------


async def test_nothing_is_written_while_the_receiver_does_not_answer(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """From an AVR-X2600H run: after All Zone Stereo it answered nothing."""
    receiver = Receiver(
        hass,
        {"ZM": "ON", "DIM ": "BRI", "MV": "50"},
        {"ZM?": ["ZM"], "DIM ?": ["DIM "], "MV?": ["MV"]},
        stop_after="DIM DIM",
    )
    receiver.telnet.push("ZMON")
    hass.loop.call_later(0.5, receiver.wake)
    result = await _run(hass, loaded_entry, receiver, "dimmer", "main_volume")
    tests = result["tests"]
    # The volume was not tested: nothing went out while nobody listened.
    assert tests["main_volume"] == {
        "group": QUIET,
        "result": "interrupted",
        "reason": command_test.SILENT,
    }
    assert [command for command in receiver.telnet.sent if command.startswith("MV")] in ([], ["MV?"])
    # Once it answered again, the dimmer was written back.
    assert tests["dimmer"]["restored"] is True
    assert tests["dimmer"]["restored_later"] is True
    assert receiver.settings["DIM "] == "BRI"


async def test_a_receiver_that_does_not_come_back_leaves_it_to_the_user(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(
        hass, {"ZM": "ON", "DIM ": "BRI"}, {"ZM?": ["ZM"], "DIM ?": ["DIM "]}, stop_after="DIM DIM"
    )
    receiver.telnet.push("ZMON")
    with patch.object(command_test, "RECOVER_WAIT", 0.2):
        result = await _run(hass, loaded_entry, receiver, "dimmer")
    assert result["tests"]["dimmer"]["restored"] is False
    assert "restored_later" not in result["tests"]["dimmer"]


async def test_a_value_the_receiver_does_not_report_now_is_not_written(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """What it said earlier may be stale: no write without a fresh answer."""
    receiver = Receiver(hass, {"ZM": "ON", "DIM ": "BRI"}, {"ZM?": ["ZM"], "DIM ?": []})
    receiver.telnet.push("ZMON", "DIM BRI")
    result = await _run(hass, loaded_entry, receiver, "dimmer", "main_volume")
    assert result["tests"]["dimmer"]["result"] == "no_value"
    assert "DIM DIM" not in receiver.telnet.sent
    # It still answers its power query: the next test runs.
    assert result["tests"]["main_volume"]["result"] != "interrupted"


async def test_after_a_change_of_the_sound_path_the_receiver_is_given_time(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """MultEQ and the like reload the sound path: nothing more until it answers."""
    receiver = Receiver(
        hass,
        {"ZM": "ON", "PSMULTEQ:": "AUDYSSEY", "DIM ": "BRI"},
        {"ZM?": ["ZM"], "PSMULTEQ: ?": ["PSMULTEQ:"], "DIM ?": ["DIM "]},
    )
    receiver.telnet.push("ZMON", "PSMULTEQ:AUDYSSEY")
    result = await _run(hass, loaded_entry, receiver, "multeq", "dimmer")
    assert result["tests"]["multeq"]["restored"] is True
    sent = receiver.telnet.sent
    changed, back, dimmer = (
        sent.index("PSMULTEQ:BYP.LR"),
        sent.index("PSMULTEQ:AUDYSSEY"),
        sent.index("DIM DIM"),
    )
    # A power query before the way back, and again before the next setting.
    assert "PW?" in sent[changed:back]
    assert "PW?" in sent[back:dimmer]


async def test_what_does_not_come_back_goes_out_over_http_when_telnet_stays_away() -> None:
    written: list[Any] = []

    async def write(value: Any) -> None:
        written.append(value)

    class Device:
        telnet_connected = False

        async def async_send_command(self, command: str) -> list[str]:
            raise CommandNotSent("query_needs_telnet")

        async def async_collect(self, wanted: Any, action: Any, first: float) -> list[str]:
            await action()
            return []

    probe = Probe(
        key="dimmer",
        group=QUIET,
        query="DIM ?",
        prefixes=("DIM ",),
        read=lambda lines: None,
        write=write,
        alternative=lambda value: value,
    )
    results: dict[str, dict[str, Any]] = {"dimmer": {"result": "confirmed", "restored": False}}
    with patch.object(command_test, "RECOVER_WAIT", 0.0):
        await command_test._recover(Device(), results, {"dimmer": (probe, "BRI", {})})  # type: ignore[arg-type]
    assert written == ["BRI"]
    # Nothing confirms it over HTTP: still to be checked on the receiver.
    assert results["dimmer"] == {"result": "confirmed", "restored": False, "put_back_over_http": True}


# The tests that are not a profile control -------------------------------


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
    # Not the main zone's input: an AVR-X2600H plays no HDMI input in Zone 2.
    assert result["tests"]["zone2_follow_main"]["written"] == "TUNER"
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



# Settings that depend on others --------------------------------------------


async def test_a_setting_the_receiver_needs_does_not_come(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """Tone control is not taken: bass is not tested, and reported so."""
    settings = {"PSTONE CTRL ": "OFF", "PSBAS ": "50"}
    receiver = Receiver(hass, settings, {"PSBAS ?": ["PSBAS "]}, ignored=("PSTONE CTRL ",))
    receiver.telnet.push("ZMON", "PSTONE CTRL OFF")
    result = await _run(hass, loaded_entry, receiver, "bass")
    assert result["tests"]["bass"] == {
        "group": QUIET,
        "result": "precondition",
        "reason": "tone_control_not_set",
    }
    assert "PSBAS 51" not in receiver.telnet.sent
    # Put back even so: the command went out.
    assert receiver.telnet.sent[-1] == "PSTONE CTRL OFF"


async def test_a_setting_already_as_needed_is_left_alone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    settings = {"PSTONE CTRL ": "ON", "PSBAS ": "50"}
    receiver = Receiver(hass, settings, {"PSBAS ?": ["PSBAS "]})
    receiver.telnet.push("ZMON", "PSTONE CTRL ON")
    result = await _run(hass, loaded_entry, receiver, "bass")
    assert result["tests"]["bass"]["result"] == "confirmed"
    assert "requires" not in result["tests"]["bass"]
    assert not any(command.startswith("PSTONE CTRL O") for command in receiver.telnet.sent)


@pytest.mark.parametrize(
    ("key", "state", "reason"),
    [
        ("dynamic_eq", ("PSMULTEQ:OFF",), "audyssey_off"),
        ("zone2_channel_level_fl", ("Z2OFF",), "zone2_off"),
        # An AVR-X2600H with Zone 2 off did not take Z2QUICK1.
        ("zone2_quick_select", ("Z2OFF",), "zone2_off"),
        # The subwoofer switch acts in two-channel modes only.
        ("subwoofer_switch", ("MSDOLBY SURROUND",), "not_two_channel"),
    ],
)
async def test_what_cannot_be_set_quietly_is_a_missing_precondition(
    hass: HomeAssistant,
    loaded_entry: MockConfigEntry,
    fast: None,
    key: str,
    state: tuple[str, ...],
    reason: str,
) -> None:
    """MultEQ needs a calibration; Zone 2 on would play. Neither is set."""
    receiver = Receiver(hass, {}, {})
    receiver.telnet.push("ZMON", *state)
    device = loaded_entry.runtime_data.device
    # The recording does not answer the Zone 2 settings; as one that does.
    device.discovery.answered.add(key)
    probe = next(p for p in command_test.probes_for(device, [QUIET, AUDIBLE]) if p.key == key)
    with patch.object(receiver.telnet, "async_send", receiver.send):
        result = await command_test._run_probe(device, probe)
    assert result == {"group": probe.group, "result": "precondition", "reason": reason}


async def test_a_cancelled_test_puts_back_what_it_needed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    settings = {"PSTONE CTRL ": "OFF", "PSBAS ": "50"}
    receiver = Receiver(hass, settings, {"PSBAS ?": ["PSBAS "]}, silent=("PSBAS ",))
    device = loaded_entry.runtime_data.device
    with (
        patch.object(receiver.telnet, "async_send", receiver.send),
        _only("bass"),
        patch.object(command_test, "QUERY_WAIT", 0.05),
        patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_QUIET", 0.02),
    ):
        receiver.telnet.push("ZMON", "PSTONE CTRL OFF")
        task = hass.async_create_task(device.async_command_test([QUIET]))
        for _ in range(100):
            if "PSBAS 51" in receiver.telnet.sent:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert receiver.telnet.sent[-2:] == ["PSBAS 50", "PSTONE CTRL OFF"]
    assert receiver.settings["PSTONE CTRL "] == "OFF"


async def test_the_picture_adjustments_need_the_custom_picture_mode(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    picture = {"contrast", "brightness", "saturation", "enhancer", "noise_reduction"}
    for key in picture:
        assert command_test.REQUIRES[key] == (("picture_mode", "CTM"),)
    probes = {p.key: p for p in command_test.probes_for(device, [QUIET])}
    # Only where the receiver has the setting the other one needs.
    assert probes["bass"].requires == (("dynamic_eq", False), ("tone_control", True))


async def test_tone_control_needs_dynamic_eq_off_with_audyssey_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """An AVR-X2600H with Audyssey on ignored tone control while Dynamic EQ was on."""
    settings = {"PSDYNEQ ": "ON", "PSTONE CTRL ": "OFF", "PSBAS ": "50"}
    receiver = Receiver(hass, settings, {"PSBAS ?": ["PSBAS "]})
    receiver.telnet.push("ZMON", "PSMULTEQ:AUDYSSEY", "PSDYNEQ ON", "PSTONE CTRL OFF")
    result = await _run(hass, loaded_entry, receiver, "bass")
    assert result["tests"]["bass"]["result"] == "confirmed"
    assert result["tests"]["bass"]["requires"] == {"dynamic_eq": False, "tone_control": True}
    sent = receiver.telnet.sent
    assert sent.index("PSDYNEQ OFF") < sent.index("PSTONE CTRL ON") < sent.index("PSBAS 51")
    # Put back in the other order.
    assert sent[-2:] == ["PSTONE CTRL OFF", "PSDYNEQ ON"]
    assert (receiver.settings["PSDYNEQ "], receiver.settings["PSTONE CTRL "]) == ("ON", "OFF")


async def test_dynamic_eq_is_left_alone_with_audyssey_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    """With MultEQ off, Dynamic EQ does nothing; an AVR-X2000 took tone control so."""
    settings = {"PSDYNEQ ": "ON", "PSTONE CTRL ": "OFF", "PSBAS ": "50"}
    receiver = Receiver(hass, settings, {"PSBAS ?": ["PSBAS "]})
    receiver.telnet.push("ZMON", "PSMULTEQ:OFF", "PSDYNEQ ON", "PSTONE CTRL OFF")
    result = await _run(hass, loaded_entry, receiver, "bass")
    assert result["tests"]["bass"]["requires"] == {"dynamic_eq": False, "tone_control": True}
    assert "PSDYNEQ OFF" not in receiver.telnet.sent


@pytest.mark.assumed(
    "the graphic EQ works only with MultEQ off, per the receivers' menus; an "
    "AVR-X2600H with Audyssey on ignored PSGEQ ON, a test with MultEQ off "
    "would settle it"
)
async def test_the_graphic_eq_needs_audyssey_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, fast: None
) -> None:
    receiver = Receiver(hass, {}, {})
    receiver.telnet.push("ZMON", "PSMULTEQ:AUDYSSEY")
    device = loaded_entry.runtime_data.device
    device.discovery.answered.add("graphic_eq")
    probe = next(p for p in command_test.probes_for(device, [QUIET]) if p.key == "graphic_eq")
    with patch.object(receiver.telnet, "async_send", receiver.send):
        result = await command_test._run_probe(device, probe)
    assert result == {"group": QUIET, "result": "precondition", "reason": "audyssey_on"}


# What receivers confirmed --------------------------------------------------

RECORDED = Path(__file__).parent / "fixtures" / "command_test"


@pytest.mark.parametrize("model", ["AVR-X2600H", "AVR-X2000"])
async def test_what_receivers_confirmed_is_what_the_integration_sends(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, model: str
) -> None:
    """Each setting a receiver confirmed in its command test.

    The integration writes it in the form the receiver echoed, and reads that
    echo back as the value written. The recordings are the command tests of
    the maintainer's receivers (tests/fixtures/command_test).
    """
    run = json.loads((RECORDED / f"{model}.json").read_text(encoding="utf-8"))
    device = loaded_entry.runtime_data.device
    # The AVR-X2000 answers on port 80 and speaks the older variant.
    profile = profile_for_receiver(load_profile(), 80, 210) if model == "AVR-X2000" else load_profile()
    parser, state = TelnetParser(profile, Discovery()), AvrState()
    checked = []
    for key, item in run["tests"].items():
        spec = profile.control(key)
        if item["result"] != "confirmed" or spec is None or spec.kind not in command_test.KINDS:
            continue
        sent = f"{spec.prefix}{device._encode_control_value(spec, item['written'])}"
        assert sent in item["lines"], key
        parser.feed(sent, state)
        assert same(state.values[key], item["written"]), key
        checked.append(key)
    assert len(checked) >= 10
