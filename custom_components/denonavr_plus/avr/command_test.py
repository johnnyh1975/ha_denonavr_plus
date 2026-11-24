"""The command test: write a setting, see the receiver confirm it, put it back.

The capability check only asks. This test changes settings, one at a time:
it reads the current value, writes a different valid one, waits for the
receiver's echo, asks again, then writes the original back and checks that
it is back. What it finds tells which command formats a model takes, which
the protocol documents do not settle (see the `assumed` markers in the
tests).

Two groups. Quiet: settings whose change is not noticed in a few seconds
(tone, Audyssey and surround settings, picture adjustments, timers, the
display, Zone 2 levels, the volume by 0.5 dB). Audible: what is heard or
seen (mute, speaker and output selection, MultEQ, the tuner, Zone 2
following the main zone, a Zone 2 quick select, the setup menu on the TV).

Never tested: power, the 12 V triggers, the Bluetooth transmitter, the
input, the sound mode, and every command that stores or resets something
(tuner preset and quick select memory, channel level reset, network
restart).

A receiver in standby ignores the settings (an AVR-X2000 did: every write
had no effect). So a main zone that is off is turned on for the test and off
again afterwards; settings of the main zone that is still not on are skipped.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .capability_check import reply_prefix
from .errors import CommandNotSent

if TYPE_CHECKING:
    from .device import DenonAvrDevice
    from .profile import ControlSpec

#: Bump when the tests or the result format change.
TEST_VERSION = 1

QUIET = "quiet"
AUDIBLE = "audible"
GROUPS = (QUIET, AUDIBLE)

#: How long to wait for the receiver's echo of a write, and for the first
#: answer to a query.
CONFIRM_WAIT = 2.0
QUERY_WAIT = 1.0
#: How long a restored zone may take to report its power and input.
ZONE_SETTLE = 3.0
#: A main zone in standby is turned on for the test: how long it may take to
#: report on, and the pause before the first setting (the receiver ignores
#: settings while it starts up).
POWER_WAIT = 15.0
POWER_SETTLE = 3.0
#: How long to wait for a receiver that stopped answering, before the
#: settings it did not take back are written again (seconds). The telnet
#: client opens a new session after SILENCE_TIMEOUT, so this covers that.
RECOVER_WAIT = 60.0
RECOVER_POLL = 1.0
#: After a test that reloads the sound path (the audible ones, the EQs), a
#: pause and then an answer to the power query before anything else is
#: written; up to SETTLE_WAIT for that answer.
SETTLE = 1.0
SETTLE_WAIT = 10.0

#: Controls that are never written by the test.
NEVER = frozenset(
    {
        "system_power",
        "main_power",
        "main_source",
        "sound_mode",
        "video_select",
        "trigger_1",
        "trigger_2",
        "bluetooth_transmitter",
        "channel_level",
    }
)

#: Controls whose change is heard or seen.
AUDIBLE_CONTROLS = frozenset(
    {
        "main_mute",
        "all_zone_stereo",
        "front_speaker",
        "speaker_preset",
        "hdmi_video_output",
        "hdmi_audio_out",
        "resolution_hdmi",
        "input_mode",
        "multeq",
        "dirac_filter",
        "volume_limit",
        "zone2_quick_select",
    }
)

#: Value kinds the test can write.
KINDS = frozenset({"onoff", "enum", "level", "centered", "integer", "signed_int", "minutes"})

#: The value written instead of the neighbour, where a neighbour would do harm
#: if it were not put back (auto standby after 15 minutes).
PREFERRED = {
    "auto_standby": "60M",
    "zone2_auto_standby": "8H",
    # Every input takes Auto; an AVR-X2600H kept HDMI on an HDMI-only input
    # when the test chose Digital.
    "input_mode": "AUTO",
}

#: Control groups that act on the main zone's sound or picture.
_MAIN_ZONE_GROUPS = frozenset(
    {"volume", "tone", "audio", "audyssey", "surround", "speaker", "video", "source"}
)

#: Queries that answer for a control whose own query does not.
_CONFIRM_QUERY = {"all_zone_stereo": "MS?"}

#: Zones put back after a control's test: the zones All Zone Stereo turns on,
#: the volume a lower volume limit pulls down, and a recalled quick select
#: (put back through the zone only: recalling the old one would apply it again).
_ZONE_RESTORE = {
    "zone2_quick_select": ("zone2",),
    "all_zone_stereo": ("zone2", "zone3"),
    "volume_limit": ("main",),
}
_NO_WRITE_BACK = frozenset({"zone2_quick_select"})

#: Settings a control only takes while another one is set: the test sets it
#: for the moment and back. An AVR-X2000 ignored bass and treble with tone
#: control off, and the picture adjustments outside the custom picture mode.
#: An AVR-X2600H with Audyssey on did not take tone control while Dynamic EQ
#: was on, so tone control (and bass and treble with it) need Dynamic EQ off.
_TONE = (("dynamic_eq", False), ("tone_control", True))
_CUSTOM_PICTURE = (("picture_mode", "CTM"),)
REQUIRES: dict[str, tuple[tuple[str, Any], ...]] = {
    "bass": _TONE,
    "treble": _TONE,
    "tone_control": (("dynamic_eq", False),),
    "contrast": _CUSTOM_PICTURE,
    "brightness": _CUSTOM_PICTURE,
    "saturation": _CUSTOM_PICTURE,
    "enhancer": _CUSTOM_PICTURE,
    "noise_reduction": _CUSTOM_PICTURE,
}

#: A requirement that only matters in some states: Dynamic EQ does nothing
#: with MultEQ off (an AVR-X2000 took tone control with Dynamic EQ "on"
#: and MultEQ off, and ignored a Dynamic EQ change there).
_ONLY_WHEN: dict[str, Callable[[dict[str, Any]], bool]] = {
    "dynamic_eq": lambda values: values.get("multeq") not in (None, "OFF"),
}

#: Settings that need what the test cannot set quietly: Audyssey on (MultEQ
#: needs a calibration), Zone 2 on (it would play). Reported as a missing
#: precondition instead of "no effect".
_AUDYSSEY_SETTINGS = frozenset(
    {
        "dynamic_eq",
        "dynamic_volume",
        "reference_level_offset",
        "low_frequency_containment",
        "containment_amount",
    }
)
#: The graphic EQ works only with MultEQ off; switching MultEQ is heard.
_NEEDS_AUDYSSEY_OFF = frozenset({"graphic_eq"})

#: The subwoofer can only be switched off in two-channel modes (2015
#: protocol document: "SWR OFF: DIRECT, STEREO (2ch) mode"); an AVR-X2600H
#: in Multi Ch Stereo kept PSSWR ON.
_TWO_CHANNEL_MODES = frozenset({"DIRECT", "PURE DIRECT", "STEREO"})
_NEEDS_TWO_CHANNEL = frozenset({"subwoofer_switch"})

#: Sources Zone 2 plays on every receiver. An AVR-X2600H plays no HDMI
#: input in Zone 2 and kept Z2SOURCE when the test chose AUX2 (HDMI).
_ZONE2_SOURCES = ("NET", "TUNER")
_ZONE2_SETTINGS = frozenset(
    {
        "zone2_sleep_timer",
        "zone2_auto_standby",
        "zone2_channel_level_fl",
        "zone2_channel_level_fr",
        "zone2_quick_select",
    }
)

#: What the receiver was doing when the test started, for reading the results.
CONTEXT = ("sound_mode", "multeq", "tone_control", "picture_mode", "input_mode")
SIGNAL = ("decoder", "audio_format", "sample_rate")

#: The parts of the receiver the tests are ordered and announced by, in
#: this order; a control's command group decides its part.
AREAS = ("audio", "speakers", "video", "receiver", "zone2", "tuner")
_AREA_OF_GROUP = {
    "tone": "audio",
    "audio": "audio",
    "audyssey": "audio",
    "surround": "audio",
    "speaker": "speakers",
    "video": "video",
    "zone": "zone2",
}
#: Quiet tests that also reload the sound path.
_HEAVY = frozenset({"dynamic_eq", "graphic_eq"})

#: Controls that belong to another part than their command group: All Zone
#: Stereo switches Zone 2 on, and right after a MultEQ change an AVR-X2600H
#: took it and then stopped answering.
_AREA_OF_CONTROL = {"all_zone_stereo": "zone2"}

#: FM band in the tuner's unit (MHz x 100) and its step.
_FM_LOW, _FM_HIGH, _FM_STEP = 8750, 10800, 5

_ERRORS = ("?", "ERR", "ERROR")


@dataclass
class Probe:
    """One test: how to read, change and put back one setting."""

    key: str
    group: str
    query: str
    #: Lines that belong to this setting start with one of these.
    prefixes: tuple[str, ...]
    #: The current value: from the state, or from the lines just received.
    read: Callable[[list[str]], Any]
    write: Callable[[Any], Awaitable[Any]]
    alternative: Callable[[Any], Any]
    #: Why the test cannot run now, or None.
    condition: Callable[[], str | None] = lambda: None
    #: A missing precondition the test cannot set quietly, or None.
    precondition: Callable[[], str | None] = lambda: None
    #: (control, value) set for the moment before the test and put back.
    requires: tuple[tuple[str, Any], ...] = ()
    #: Put back another way than by writing the old value.
    restore: Callable[[Any], Awaitable[Any]] | None = None
    #: Zones whose power, input, volume and mute are put back afterwards.
    zones: tuple[str, ...] = ()
    write_back: bool = True
    #: The value assumed when the query is not answered.
    default: Any = None
    #: The part of the receiver it belongs to (AREAS); the progress names it.
    area: str = "receiver"
    extra: dict[str, Any] = field(default_factory=dict)

    def wanted(self, line: str) -> bool:
        return line.startswith(self.prefixes) or line in _ERRORS


def same(first: Any, second: Any) -> bool:
    """Equal values; numbers within a hundredth (dB values are floats)."""

    if first is None or second is None:
        return False
    if isinstance(first, bool) or isinstance(second, bool):
        return first is second
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return math.isclose(first, second, abs_tol=0.01)
    return bool(first == second)


def alternative(spec: ControlSpec, value: Any, step: float = 0.5) -> Any:
    """A different valid value for a control, or None when there is none.

    `step` is the receiver's step for a level: an AVR-X2600H takes bass and
    treble in whole dB only and ignores PSBAS 505.
    """

    kind = spec.kind
    if kind == "onoff":
        return not value
    if kind == "enum":
        values = spec.values
        preferred = PREFERRED.get(spec.id)
        if preferred in values and preferred != value:
            return preferred
        if value in values:
            index = values.index(value)
            if index + 1 < len(values):
                return values[index + 1]
            return values[index - 1] if index > 0 else None
        return values[0] if values else None
    if kind == "level":
        # Towards 0 dB: always inside the range of a valid value.
        return float(value) - step if float(value) > 0 else float(value) + step
    if kind in ("centered", "integer", "signed_int"):
        step = int(spec.get("step", 1) or 1)
        low, high = spec.get("min"), spec.get("max")
        current = int(value)
        if high is None or current + step <= high:
            return current + step
        if low is None or current - step >= low:
            return current - step
        return None
    if kind == "minutes":
        step = int(spec.get("step", 10) or 10)
        high = int(spec.get("max", 120) or 120)
        current = int(value)
        if current == 0:
            # The longest timer: the least harm if it were not put back.
            return high
        return current - step if current - step > 0 else min(current + step, high)
    return None


def control_probes(device: DenonAvrDevice) -> list[Probe]:
    """A test for every profile control this receiver has and the test may write."""

    probes = []
    discovery = device.discovery
    for spec in device.profile.controls.values():
        if spec.id in NEVER or spec.kind not in KINDS or not spec.query:
            continue
        if not discovery.has_control(spec.scope, spec.feature, spec.id):
            continue
        probes.append(_control_probe(device, spec))
    return probes


def _control_probe(device: DenonAvrDevice, spec: ControlSpec) -> Probe:
    query = _CONFIRM_QUERY.get(spec.id, spec.query or "")
    prefixes = tuple(
        dict.fromkeys(
            prefix
            for prefix in (spec.prefix.strip(), reply_prefix(spec.query or ""), reply_prefix(query))
            if prefix
        )
    )

    def read(_lines: list[str]) -> Any:
        return device.state.values.get(spec.id)

    async def write(value: Any) -> None:
        await device.async_set_control(spec.id, value)

    condition: Callable[[], str | None] = lambda: None
    if spec.group in _MAIN_ZONE_GROUPS:
        condition = _main_zone_on(device)
    requires = tuple(
        (control_id, value)
        for control_id, value in REQUIRES.get(spec.id, ())
        if _applies(device, control_id)
    )
    return Probe(
        key=spec.id,
        group=AUDIBLE if spec.id in AUDIBLE_CONTROLS else QUIET,
        area=_AREA_OF_CONTROL.get(spec.id) or _AREA_OF_GROUP.get(spec.group or "", "receiver"),
        query=query,
        prefixes=prefixes,
        read=read,
        write=write,
        alternative=lambda value: alternative(spec, value, _level_step(device, spec)),
        condition=condition,
        zones=tuple(zone for zone in _ZONE_RESTORE.get(spec.id, ()) if _has_zone(device, zone)),
        write_back=spec.id not in _NO_WRITE_BACK,
        precondition=_precondition(device, spec.id),
        requires=requires,
    )


def _applies(device: DenonAvrDevice, control_id: str) -> bool:
    spec = device.profile.control(control_id)
    return spec is not None and device.discovery.has_control(spec.scope, spec.feature, spec.id)


def _precondition(device: DenonAvrDevice, control_id: str) -> Callable[[], str | None]:
    def missing() -> str | None:
        values = device.state.values
        if control_id in _AUDYSSEY_SETTINGS and values.get("multeq") == "OFF":
            return "audyssey_off"
        if control_id in _NEEDS_AUDYSSEY_OFF and values.get("multeq") not in (None, "OFF"):
            return "audyssey_on"
        if control_id in _ZONE2_SETTINGS and not device.state.zone("zone2").power:
            return "zone2_off"
        if control_id in _NEEDS_TWO_CHANNEL and values.get("sound_mode") not in _TWO_CHANNEL_MODES:
            return "not_two_channel"
        return None

    return missing


def _level_step(device: DenonAvrDevice, spec: ControlSpec) -> float:
    """The receiver's own step for a level, where it publishes one."""

    meta = device.discovery.numeric_meta.get(spec.feature or "")
    return float(meta["step"]) if meta and meta.get("step") else 0.5


def _wanted(device: DenonAvrDevice, control_id: str) -> Callable[[str], bool]:
    spec = device.profile.control(control_id)
    prefixes = tuple(
        prefix
        for prefix in ((spec.prefix.strip() if spec else ""), reply_prefix((spec and spec.query) or ""))
        if prefix
    )
    return lambda line: bool(prefixes) and line.startswith(prefixes)


def context(device: DenonAvrDevice) -> dict[str, Any]:
    """What the receiver was doing, to read the results by."""

    found: dict[str, Any] = {key: device.state.values.get(key) for key in CONTEXT}
    found["source"] = device.state.zone("main").source
    found["zone2_power"] = device.state.zone("zone2").power
    # The signal decides what some settings do (Dynamic Compression and
    # Loudness Management act on Dolby and DTS, Dialog Control on DTS:X).
    readonly = device.state.readonly
    found["signal"] = {key: readonly.get(key) for key in SIGNAL}
    return found


def special_probes(device: DenonAvrDevice) -> list[Probe]:
    """The tests that are not a profile control: volume, tuner, zones, menu."""

    probes = [_volume_probe(device), _setup_menu_probe(device)]
    if _has_zone(device, "zone2"):
        probes.append(_zone2_follow_probe(device))
    if device.has_tuner:
        probes += _tuner_probes(device)
    return probes


def _main_zone_on(device: DenonAvrDevice) -> Callable[[], str | None]:
    return lambda: None if device.state.zone("main").power else "main_zone_off"


def _has_zone(device: DenonAvrDevice, zone_id: str) -> bool:
    return any(zone.id == zone_id for zone in device.discovery.zones)


def _volume_probe(device: DenonAvrDevice) -> Probe:
    """The main zone's volume, 0.5 dB down and back."""

    def read(_lines: list[str]) -> Any:
        return device.state.zone("main").volume_raw

    async def write(value: Any) -> None:
        await device.async_set_volume_raw("main", float(value))

    return Probe(
        key="main_volume",
        group=QUIET,
        query="MV?",
        prefixes=("MV",),
        read=read,
        write=write,
        alternative=lambda raw: raw - 0.5 if raw >= 0.5 else raw + 0.5,
        condition=_main_zone_on(device),
    )


def _setup_menu_probe(device: DenonAvrDevice) -> Probe:
    """The setup menu on the TV, opened and closed (MNMEN ON / OFF)."""

    def read(lines: list[str]) -> Any:
        values = [line[len("MNMEN") :].strip() for line in lines if line.startswith("MNMEN")]
        return values[-1] if values else None

    async def write(value: Any) -> None:
        await device.async_send_command(f"MNMEN {value}")

    return Probe(
        key="setup_menu",
        group=AUDIBLE,
        query="MNMEN?",
        prefixes=("MNMEN",),
        read=read,
        write=write,
        # Someone who has the menu open is using it: leave it alone.
        alternative=lambda value: "ON" if value == "OFF" else None,
        condition=_main_zone_on(device),
        # The remote commands get no reply on some models; the menu is closed
        # unless someone opened it.
        default="OFF",
    )


def _zone2_follow_probe(device: DenonAvrDevice) -> Probe:
    """Zone 2 follows the main zone's input (Z2SOURCE), then its own again."""

    from .device import FOLLOW_MAIN

    def read(_lines: list[str]) -> Any:
        return device.state.zone("zone2").source

    async def write(value: Any) -> None:
        await device.async_select_source("zone2", str(value))

    def other(source: Any) -> Any:
        if source != FOLLOW_MAIN:
            return FOLLOW_MAIN
        codes = {item.code for item in device.discovery.sources}
        return next((code for code in _ZONE2_SOURCES if code in codes), device.state.zone("main").source)

    return Probe(
        key="zone2_follow_main",
        group=AUDIBLE,
        area="zone2",
        query="Z2?",
        prefixes=("Z2",),
        read=read,
        write=write,
        alternative=other,
        zones=("zone2",),
    )


def _tuner_probes(device: DenonAvrDevice) -> list[Probe]:
    """The tuner's frequency and preset; only while a zone plays the tuner."""

    def playing() -> str | None:
        return None if device.tuner_zone is not None else "tuner_not_playing"

    presets = device.profile.netaudio.get("tuner_presets", {})
    preset_prefix = str(presets.get("prefix", "TPAN"))
    # What the tuner played before: a preset, or a frequency without one.
    before: dict[str, Any] = {}

    def read_frequency(_lines: list[str]) -> Any:
        # Only the first read is what it played before the test.
        before.setdefault("preset", device.state.values.get("tuner_preset"))
        return device.state.values.get("tuner_frequency")

    async def tune(value: Any) -> None:
        await device.async_tune(int(value))

    def other_frequency(value: Any) -> Any:
        if not _FM_LOW <= int(value) <= _FM_HIGH:
            return None  # AM: not tested
        up = int(value) + _FM_STEP
        return up if up <= _FM_HIGH else int(value) - _FM_STEP

    async def back_to(value: Any) -> None:
        # Tuning drops the preset; recall it when there was one.
        if before.get("preset"):
            await device.async_send_command(f"{preset_prefix}{before['preset']}")
        else:
            await tune(value)

    def read_preset(lines: list[str]) -> Any:
        before.setdefault("frequency", device.state.values.get("tuner_frequency"))
        values = [
            line[len(preset_prefix) :].strip()
            for line in lines
            if line.startswith(preset_prefix) and not line.startswith(f"{preset_prefix}MEM")
        ]
        return values[-1] if values else None

    async def recall(value: Any) -> None:
        await device.async_send_command(f"{preset_prefix}{value}")

    def other_preset(value: Any) -> Any:
        codes = device.tuner_preset_codes
        if value in codes:
            return codes[(codes.index(value) + 1) % len(codes)]
        return codes[0]

    async def preset_back(value: Any) -> None:
        if value == "OFF":
            # No preset before: the frequency it played.
            if before.get("frequency") is not None:
                await tune(before["frequency"])
            return
        await recall(value)

    return [
        Probe(
            key="tuner_frequency",
            group=AUDIBLE,
            area="tuner",
            query="TFAN?",
            prefixes=("TFAN",),
            read=read_frequency,
            write=tune,
            alternative=other_frequency,
            condition=playing,
            restore=back_to,
        ),
        Probe(
            key="tuner_preset",
            group=AUDIBLE,
            area="tuner",
            query=f"{preset_prefix}?",
            prefixes=(preset_prefix,),
            read=read_preset,
            write=recall,
            alternative=other_preset,
            condition=playing,
            restore=preset_back,
        ),
    ]


async def async_power_on(device: DenonAvrDevice) -> bool:
    """Turn a main zone that is off on for the test; True when it was."""

    zone = device.state.zone("main")
    if zone.power:
        return False
    await device.async_set_power("main", True)
    loop = asyncio.get_running_loop()
    until = loop.time() + POWER_WAIT
    while not zone.power and loop.time() < until:
        await asyncio.sleep(0.1)
    if zone.power:
        await asyncio.sleep(POWER_SETTLE)
    return True


def probes_for(device: DenonAvrDevice, groups: tuple[str, ...] | list[str]) -> list[Probe]:
    """The tests of the chosen groups: quiet ones first, each by part of the receiver."""

    every = control_probes(device) + special_probes(device)
    return [
        probe
        for group in GROUPS
        if group in groups
        for area in AREAS
        for probe in every
        if probe.group == group and probe.area == area
    ]


async def async_run(
    device: DenonAvrDevice,
    groups: tuple[str, ...] | list[str],
    on_progress: Callable[[float], None] | None = None,
    on_current: Callable[[str], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the tests one after the other; each puts back what it changed.

    `on_current` learns the part of the receiver (AREAS) when the tests
    move on to the next one.

    When the telnet connection drops, the remaining tests are marked
    interrupted; the one that ran is still put back (over HTTP if need be).
    When the receiver stops answering, no further setting is written: the
    remaining tests are marked interrupted, and once it answers again, what
    did not come back is written again.
    """

    probes = probes_for(device, groups)
    results: dict[str, dict[str, Any]] = {}
    undo: dict[str, tuple[Probe, Any, dict[str, Any]]] = {}
    silent = False
    for index, probe in enumerate(probes):
        if on_current is not None and probe.area != (probes[index - 1].area if index else None):
            on_current(probe.area)
        if silent:
            results[probe.key] = {"group": probe.group, "result": "interrupted", "reason": SILENT}
        elif not device.telnet_connected:
            results[probe.key] = {"group": probe.group, "result": "interrupted"}
        else:
            entry = await _run_probe(device, probe, undo)
            if _heavy(probe) and "written" in entry:
                answering = await _settled(device)
            else:
                answering = not _unanswered(entry) or await _answers(device)
            if not answering:
                silent = True
                if entry["result"] == "no_value":
                    entry = {"group": probe.group, "result": "interrupted", "reason": SILENT}
            results[probe.key] = entry
        if on_progress is not None:
            on_progress((index + 1) / len(probes))
    if undo:
        await _recover(device, results, undo)
    return results


#: Why tests did not run: the receiver no longer answered.
SILENT = "receiver_silent"


def _heavy(probe: Probe) -> bool:
    """Whether the test makes the receiver reload its sound path."""

    return probe.group == AUDIBLE or probe.key in _HEAVY


async def _settled(device: DenonAvrDevice) -> bool:
    """Give the receiver a moment, then wait until it answers again."""

    await asyncio.sleep(SETTLE)
    loop = asyncio.get_running_loop()
    until = loop.time() + SETTLE_WAIT
    while not await _answers(device):
        if loop.time() >= until:
            return False
        await asyncio.sleep(RECOVER_POLL)
    return True


def _unanswered(entry: dict[str, Any]) -> bool:
    """Nothing came back where something should have: is the receiver there?"""

    if entry.get("restored") is False:
        return True
    return entry["result"] == "no_value" and not entry.get("lines")


async def _answers(device: DenonAvrDevice) -> bool:
    """Whether the receiver answers its power query, which it always does."""

    try:
        lines = await device.async_collect(
            lambda line: line.startswith("PW"),
            lambda: device.async_send_command("PW?"),
            QUERY_WAIT,
        )
    except CommandNotSent:
        return False
    return bool(lines)


async def _recover(
    device: DenonAvrDevice,
    results: dict[str, dict[str, Any]],
    undo: dict[str, tuple[Probe, Any, dict[str, Any]]],
) -> None:
    """Write what did not come back again, once the receiver answers.

    When it does not answer over telnet in time and the link is down, the
    old values go out over HTTP; nothing confirms them there, so they still
    count as not put back.
    """

    loop = asyncio.get_running_loop()
    until = loop.time() + RECOVER_WAIT
    while not await _answers(device):
        if loop.time() >= until:
            if not device.telnet_connected:
                for key, (probe, before, snapshots) in undo.items():
                    with contextlib.suppress(CommandNotSent):
                        await _put_back(device, probe, before, snapshots)
                        results[key]["put_back_over_http"] = True
            return
        await asyncio.sleep(RECOVER_POLL)
    for key, (probe, before, snapshots) in undo.items():
        log: list[str] = []
        with contextlib.suppress(CommandNotSent):
            if await _restore(device, probe, before, snapshots, log):
                results[key] |= {"restored": True, "restored_later": True}
        results[key]["lines"] = [*results[key].get("lines", []), *log]


async def _run_probe(
    device: DenonAvrDevice,
    probe: Probe,
    undo: dict[str, tuple[Probe, Any, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {"group": probe.group}
    reason = probe.condition()
    if reason is not None:
        return entry | {"result": "skipped", "reason": reason}
    reason = probe.precondition()
    if reason is not None:
        return entry | {"result": "precondition", "reason": reason}
    changed: list[tuple[str, Any]] = []
    try:
        reason = await _prepare(device, probe, changed)
        if reason is not None:
            return entry | {"result": "precondition", "reason": reason}
        if changed:
            entry["requires"] = dict(probe.requires)
        return await _test(device, probe, entry, {} if undo is None else undo)
    finally:
        # Also when the test is stopped: the setting it needed goes back.
        with contextlib.suppress(CommandNotSent):
            for control_id, value in reversed(changed):
                await _set(device, control_id, value)


async def _prepare(
    device: DenonAvrDevice, probe: Probe, changed: list[tuple[str, Any]]
) -> str | None:
    """Set what the probe needs; the reason when the receiver does not take it."""

    for control_id, value in probe.requires:
        old = device.state.values.get(control_id)
        when = _ONLY_WHEN.get(control_id)
        if same(old, value) or (when is not None and not when(device.state.values)):
            continue
        changed.append((control_id, old))
        await _set(device, control_id, value)
        if not same(device.state.values.get(control_id), value):
            return f"{control_id}_not_set"
    return None


async def _set(device: DenonAvrDevice, control_id: str, value: Any) -> None:
    """Set a control and wait for its echo."""

    await device.async_collect(
        _wanted(device, control_id),
        lambda: device.async_set_control(control_id, value),
        CONFIRM_WAIT,
    )


async def _test(
    device: DenonAvrDevice,
    probe: Probe,
    entry: dict[str, Any],
    undo: dict[str, tuple[Probe, Any, dict[str, Any]]],
) -> dict[str, Any]:
    """Write, confirm, put back. What did not come back goes into `undo`."""

    snapshots: dict[str, Any] = {}
    log: list[str] = []
    written = False
    before: Any = None
    try:
        lines = await _query(device, probe)
        log += lines
        # After the query, so the zones are as fresh as the value.
        snapshots = {zone: device.zone_snapshot(zone) for zone in probe.zones}
        # Nothing is written blind: a setting the receiver does not report
        # now is not taken from what it said earlier.
        before = probe.read(lines) if lines else None
        if before is None:
            before = probe.default
        if before is None:
            return entry | {"result": "no_value", "lines": log}
        target = probe.alternative(before)
        if target is None:
            return entry | {"result": "skipped", "reason": "no_alternative", "before": before}
        entry |= {"before": before, "written": target}
        written = True
        echo = await device.async_collect(probe.wanted, lambda: probe.write(target), CONFIRM_WAIT)
        log += echo
        echoed = same(probe.read(echo), target)
        answer = await _query(device, probe)
        log += answer
        after = probe.read(answer)
        entry["after"] = after
        entry["result"] = _classify(echoed, same(after, target), echo + answer)
        if _heavy(probe):
            # Not straight back: the receiver may still be switching.
            await _settled(device)
        entry["restored"] = await _restore(device, probe, before, snapshots, log)
        written = False
    except CommandNotSent:
        entry["result"] = "interrupted"
    except asyncio.CancelledError:
        if written:
            with contextlib.suppress(CommandNotSent):
                await _put_back(device, probe, before, snapshots)
        raise
    finally:
        entry["lines"] = log
    if written:
        try:
            entry["restored"] = await _restore(device, probe, before, snapshots, log)
        except CommandNotSent:
            entry["restored"] = False
    if entry.get("restored") is False:
        undo[probe.key] = (probe, before, snapshots)
    return entry


def _classify(echoed: bool, changed: bool, lines: list[str]) -> str:
    if echoed and changed:
        return "confirmed"
    if changed:
        return "query_only"
    if echoed:
        return "echo_only"
    if any(line in _ERRORS for line in lines):
        return "rejected"
    return "no_effect"


async def _query(device: DenonAvrDevice, probe: Probe) -> list[str]:
    return await device.async_collect(
        probe.wanted, lambda: device.async_send_command(probe.query), QUERY_WAIT
    )


async def _put_back(
    device: DenonAvrDevice, probe: Probe, before: Any, snapshots: dict[str, Any]
) -> None:
    if probe.write_back:
        await (probe.restore or probe.write)(before)
    for zone, snapshot in snapshots.items():
        await device.async_restore(zone, snapshot)


async def _restore(
    device: DenonAvrDevice,
    probe: Probe,
    before: Any,
    snapshots: dict[str, Any],
    log: list[str],
) -> bool:
    """Put the old value back; True when the receiver reports it again.

    The lines that come back are added to `log`.
    """

    if probe.write_back:
        log += await device.async_collect(
            probe.wanted, lambda: (probe.restore or probe.write)(before), CONFIRM_WAIT
        )
    for zone, snapshot in snapshots.items():
        await device.async_restore(zone, snapshot)
    zones_back = await _zones_settled(device, snapshots)
    if not probe.write_back:
        return zones_back
    answer = await _query(device, probe)
    log += answer
    now = probe.read(answer)
    if now is None:
        now = probe.default
    return zones_back and same(now, before)


async def _zones_settled(device: DenonAvrDevice, snapshots: dict[str, Any]) -> bool:
    """Wait until every zone reports the power and input it had."""

    def settled() -> bool:
        return all(
            (now := device.zone_snapshot(zone)).power == snapshot.power
            and (snapshot.power is False or now.source == snapshot.source)
            for zone, snapshot in snapshots.items()
        )

    loop = asyncio.get_running_loop()
    until = loop.time() + ZONE_SETTLE
    while not settled():
        if loop.time() >= until:
            return False
        await asyncio.sleep(0.1)
    return True


def summary(results: dict[str, dict[str, Any]] | None) -> dict[str, int]:
    """How many tests ended how, and how many settings were not put back."""

    counts: dict[str, int] = {}
    for item in (results or {}).values():
        counts[item["result"]] = counts.get(item["result"], 0) + 1
        if item.get("restored") is False:
            counts["not_restored"] = counts.get("not_restored", 0) + 1
    return counts
