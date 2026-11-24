"""Tests for avr/parser.py: Deviceinfo, UPnP description and telnet lines."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from custom_components.denonavr_plus.avr.models import AvrState, Discovery
from custom_components.denonavr_plus.avr.parser import (
    TelnetParser,
    parse_device_info,
    parse_upnp_description,
)
from custom_components.denonavr_plus.avr.profile import load_profile, profile_variant

from .conftest import all_deviceinfo_files, deviceinfo


def _parse(name: str) -> Discovery:
    profile = load_profile()
    features = {s.feature for s in profile.controls.values() if s.feature}
    enums = {
        s.feature for s in profile.controls.values() if s.kind == "enum" and s.feature
    }
    return parse_device_info(
        deviceinfo(name), features, enums, receiver_type=profile.receiver_type
    )


# ── Deviceinfo ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "model", "zones", "sources"),
    [
        ("AVC-X3700H-Deviceinfo-8080.xml", "AVC-X3700H", 2, 13),
        ("AVR-X4300H-Deviceinfo-8080.xml", "AVR-X4500H", 3, 13),
        ("SR6012-Deviceinfo-8080.xml", "SR6012", 2, 13),
        ("NR1609-Deviceinfo-8080.xml", "NR1609", 2, 13),
        ("AVR-X2100W-2-Deviceinfo.xml", "AVR-X2100W", 2, 18),
        ("AVR-X2000-Deviceinfo.xml", "AVR-X2000", 2, 18),
    ],
)
def test_parse_device_info_real_receivers(
    name: str, model: str, zones: int, sources: int
) -> None:
    discovery = _parse(name)
    assert discovery.device.model_name == model
    assert len(discovery.zones) == zones
    assert discovery.zones[0].is_main
    assert [z.id for z in discovery.zones[1:]] == [
        f"zone{i}" for i in range(2, zones + 1)
    ]
    assert len(discovery.sources) == sources


def test_parse_device_info_formats_the_mac() -> None:
    discovery = _parse("AVC-X3700H-Deviceinfo-8080.xml")
    # The recorded documents carry the placeholder 0123456789AB.
    assert discovery.device.mac_address == "01:23:45:67:89:ab"


def test_parse_device_info_classifies_avr_x() -> None:
    assert _parse("AVC-X3700H-Deviceinfo-8080.xml").device.hardware_type == "avr-x-2016"


def test_parse_device_info_reads_volume_scale() -> None:
    volume = _parse("AVC-X3700H-Deviceinfo-8080.xml").volume
    assert volume["absolute_max"] > 0
    assert volume["step"] == 0.5


@pytest.mark.parametrize("path", all_deviceinfo_files(), ids=lambda p: p.name)
def test_parse_device_info_never_raises(path: Path) -> None:
    # Every recorded receiver parses without an exception, including the
    # pre-AVR-X models whose document has a different shape.
    discovery = _parse(path.name)
    assert discovery.zones


def test_parse_device_info_tolerates_garbage() -> None:
    discovery = parse_device_info("<not xml")
    assert discovery.device.model_name is None
    assert discovery.zones == []


def test_parse_device_info_strips_region_marker() -> None:
    xml = "<Device_Info><ModelName>*AVR-X2600H</ModelName><DeviceZones>2</DeviceZones></Device_Info>"
    discovery = parse_device_info(xml)
    assert discovery.device.model_name == "AVR-X2600H"
    assert len(discovery.zones) == 2


# ── UPnP description ──────────────────────────────────────────────────────


def test_parse_upnp_description_reads_identity() -> None:
    xml = (
        '<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:d="urn:x-denon">'
        "<device><manufacturer>Denon</manufacturer>"
        "<serialNumber>SN0000000000</serialNumber>"
        "<d:firmware_version>3.88.614</d:firmware_version>"
        "<deviceList><device><manufacturer>Other</manufacturer></device></deviceList>"
        "</device></root>"
    )
    result = parse_upnp_description(xml)
    assert result == {
        "manufacturer": "Denon",
        "serial_number": "SN0000000000",
        "firmware_version": "3.88.614",
    }


def test_parse_upnp_description_garbage() -> None:
    assert parse_upnp_description("<broken") == {}


# ── Telnet lines ──────────────────────────────────────────────────────────


@pytest.fixture
def parser_and_state() -> tuple[TelnetParser, AvrState]:
    discovery = _parse("AVC-X3700H-Deviceinfo-8080.xml")
    return TelnetParser(load_profile(), discovery), AvrState()


def test_feed_core_main_zone_lines(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    parser, state = parser_and_state
    for line in ("PWON", "ZMON", "MV505", "MVMAX 98", "MUOFF", "SITV"):
        assert parser.feed(line, state)
    main = state.zone("main")
    assert state.system_power is True
    assert main.power is True
    assert main.volume_raw == 50.5
    assert main.volume_max_raw == 98.0
    assert main.muted is False
    assert main.source == "TV"


def test_feed_standby_and_mute(parser_and_state: tuple[TelnetParser, AvrState]) -> None:
    parser, state = parser_and_state
    parser.feed("PWSTANDBY", state)
    parser.feed("MUON", state)
    assert state.system_power is False
    assert state.zone("main").muted is True


def test_feed_quick_select(parser_and_state: tuple[TelnetParser, AvrState]) -> None:
    parser, state = parser_and_state
    assert parser.feed("MSQUICK2", state)
    assert state.values["quick_select"] == 2
    assert not parser.feed("MSQUICK", state)


def test_feed_zone2(parser_and_state: tuple[TelnetParser, AvrState]) -> None:
    parser, state = parser_and_state
    for line in ("Z2ON", "Z240", "Z2MUON", "Z2CD"):
        parser.feed(line, state)
    zone = state.zone("zone2")
    assert zone.power is True
    assert zone.volume_raw == 40.0
    assert zone.muted is True
    assert zone.source == "CD"


def test_zone2_replies_that_are_no_source(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    """Seen on an AVR-X2600H (3.139.175): zone 2 showed the source "QUICK0"."""
    parser, state = parser_and_state
    parser.feed("Z2CD", state)
    for line in (
        "Z2QUICK0",
        "Z2SMART1",
        "Z2SLP060",
        "Z2HPFOFF",
        "Z2STBYOFF",
        "Z2CVFL 50",
    ):
        parser.feed(line, state)
        assert state.zone("zone2").source == "CD", line
    # SOURCE: zone 2 follows the main zone.
    assert parser.feed("Z2SOURCE", state)
    assert state.zone("zone2").source == "SOURCE"


def test_feed_ignores_empty_and_unknown(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    parser, state = parser_and_state
    assert not parser.feed("", state)
    assert not parser.feed("\r", state)
    assert not parser.feed("XYZZY", state)


def test_feed_volume_not_a_number(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    parser, state = parser_and_state
    assert not parser.feed("MVOFF", state)
    assert state.zone("main").volume_raw is None


#: One reply per model-dependent ("answered") control and the value it means,
#: in the formats the denonavr library sends and reads.
ANSWERED_REPLIES = [
    ("TR1 ON", "trigger_1", True),
    ("TR2 OFF", "trigger_2", False),
    ("SPPR 2", "speaker_preset", "2"),
    ("PSDEH MED", "dialog_enhancer", "MED"),
    ("PSIMAX AUTO", "imax", "AUTO"),
    ("PSIMAXAUD MANUAL", "imax_audio", "MANUAL"),
    ("PSIMAXHPF 080", "imax_hpf", "080"),
    ("PSIMAXLPF 120", "imax_lpf", "120"),
    ("PSIMAXSWM ON", "imax_subwoofer", True),
    ("PSIMAXSWO L+M", "imax_subwoofer_output", "L+M"),
    ("PSAUROPR SPE", "auro_matic_preset", "SPE"),
    ("PSAUROST 10", "auro_matic_strength", 10),
    ("PSAUROMODE EXP", "auro_3d_mode", "EXP"),
    ("PSDIRAC 2", "dirac_filter", "2"),
    ("PSMDAX LOW", "mdax", "LOW"),
    ("PSDACFIL MODE2", "dac_filter", "MODE2"),
    ("ILB DIM", "illumination", "DIM"),
    ("SSTTR ON", "tactile_transducer", True),
    ("SSTTRLEV 47", "tactile_transducer_level", -3.0),
    ("SSTTRLPF 080", "tactile_transducer_lpf", "080"),
    ("PSSP:FH", "effect_speaker_selection", "FH"),
]


def _recording(name: str) -> dict[str, Any]:
    path = Path(__file__).parent / "fixtures" / "capability_check" / name
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def test_replies_recorded_on_an_avr_x2600h(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    """Every answer of an AVR-X2600H (3.139.175) to the capability check."""
    parser, state = parser_and_state
    recording = _recording("AVR-X2600H-3.139.175.json")
    for answer in recording["queries"].values():
        for line in answer["lines"]:
            parser.feed(line, state)
    assert state.values["auto_standby"] == "15M"
    assert state.values["bluetooth_transmitter"] is False
    assert state.values["multeq"] == "BYP.LR"
    assert state.values["quick_select"] == 0
    zone2 = state.zone("zone2")
    assert (zone2.power, zone2.volume_raw, zone2.muted) == (False, 32.0, False)
    # Zone 2 settings: decoded as controls, never taken for a source.
    assert state.values["zone2_sleep_timer"] == 0
    assert state.values["zone2_auto_standby"] == "OFF"
    assert state.values["zone2_quick_select"] == "0"  # none recalled
    assert state.values["zone2_channel_level_fl"] == 0.0
    assert state.values["zone2_channel_level_fr"] == 0.0
    assert state.values["headphone_eq"] is False
    # Z2NET, not Z2QUICK0 that comes later.
    assert zone2.source == "NET"


def test_replies_recorded_on_an_avr_x2000() -> None:
    """Every answer of an AVR-X2000 to the capability check."""
    parser = TelnetParser(load_profile(), _parse("AVR-X2000-Deviceinfo.xml"))
    state = AvrState()
    for answer in _recording("AVR-X2000.json")["queries"].values():
        for line in answer["lines"]:
            parser.feed(line, state)
    assert state.values["multeq"] == "OFF"
    assert state.values["dynamic_eq"] is True
    assert state.values["dynamic_volume"] == "MED"
    assert state.values["sound_mode"] == "MCH STEREO"
    zone2 = state.zone("zone2")
    assert (zone2.power, zone2.source, zone2.volume_raw) == (False, "NET", 30.0)
    assert state.values["zone2_sleep_timer"] == 0
    assert state.values["zone2_channel_level_fl"] == 0.0
    # It does not answer Z2STBY?.
    assert "zone2_auto_standby" not in state.values
    # CV? lists eleven channels and no CVEND.
    assert len(state.channel_trims) == 11


@pytest.mark.assumed(
    "the NSE line layout (flag byte, NUL end, lines 1/2/4) comes from the 2015 "
    "X-series document and the denonavr library; an AVR-X2000 playing from the "
    "network would confirm it"
)
def test_now_playing_from_the_on_screen_list(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    """Receivers without HEOS (AVR-X2000) report what plays in NSE0-NSE8."""
    parser, state = parser_and_state
    for line in (
        "NSE0Now Playing Internet Radio",
        "NSE1\x01Song title\x00junk",
        "NSE2\x01Artist",
        "NSE3\x01128kbps",
        "NSE4\x01Album",
    ):
        parser.feed(line, state)
    now = state.now_playing
    assert (now.state, now.title, now.artist, now.album) == (
        "play",
        "Song title",
        "Artist",
        "Album",
    )
    # Line 0 without "Now Playing": a menu, nothing plays.
    parser.feed("NSE0Internet Radio", state)
    assert state.now_playing.state is None and state.now_playing.title is None


def test_tuner_frequency_and_station(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    """TFAN009290 is FM 92.90 MHz (both receivers answer TFAN?)."""
    parser, state = parser_and_state
    parser.feed("TFAN009290", state)
    parser.feed("TFANNAMEBayern 3", state)
    assert state.values["tuner_frequency"] == 9290
    assert state.values["tuner_station"] == "Bayern 3"


def test_the_recalled_tuner_preset(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    """TPAN01 (an AVR-X2600H) and TPANOFF (an AVR-X2000), from their capability checks."""
    parser, state = parser_and_state
    parser.feed("TPAN01", state)
    assert state.values["tuner_preset"] == "01"
    # Storing a preset is no recall.
    parser.feed("TPANMEM05", state)
    assert state.values["tuner_preset"] == "01"
    parser.feed("TPANOFF", state)
    assert state.values["tuner_preset"] is None


@pytest.mark.assumed(
    "every format here comes from the denonavr library (const.py and its event "
    "callbacks), none from a recording; replies from a receiver with these "
    "features would settle it"
)
@pytest.mark.parametrize(("line", "control_id", "value"), ANSWERED_REPLIES)
def test_model_dependent_controls_read_their_reply(
    parser_and_state: tuple[TelnetParser, AvrState],
    line: str,
    control_id: str,
    value: object,
) -> None:
    parser, state = parser_and_state
    assert parser.feed(line, state)
    assert state.values[control_id] == value


def test_every_model_dependent_control_has_a_reply_test() -> None:
    profile = load_profile()
    answered = {spec.id for spec in profile.controls_by_scope("answered")}
    # Confirmed by the AVR-X2600H recording (test_replies_recorded_on_an_avr_x2600h).
    recorded = {
        "auto_standby",
        "bluetooth_transmitter",
        "zone2_sleep_timer",
        "zone2_auto_standby",
        "zone2_quick_select",
        "zone2_channel_level_fl",
        "zone2_channel_level_fr",
        "headphone_eq",
    }
    assert answered == {control_id for _, control_id, _ in ANSWERED_REPLIES} | recorded


@pytest.mark.parametrize("line", ["BTTX SP", "BTTX BT", "SSTTR END"])
def test_a_shared_prefix_leaves_the_switch_alone(
    parser_and_state: tuple[TelnetParser, AvrState], line: str
) -> None:
    """BTTX also reports the output mode; SSTTR ends its list with END."""
    parser, state = parser_and_state
    assert parser.feed("BTTX ON", state) and parser.feed("SSTTR ON", state)
    assert not parser.feed(line, state)
    assert state.values["bluetooth_transmitter"] is True
    assert state.values["tactile_transducer"] is True


@pytest.mark.parametrize(
    "line",
    [
        "",
        "MVMAXxx",
        "MSQUICKx",
        "MSQUICK1 SMART",
        "MSSMART1",
        "SSLEVFL",
        "SSLEVFL xx",
        "SSLEV END",
        "SSSDEFL",
        "SSSDESTP 12",
        "SSSDE END",
        "SSCFR",
        "SSCFR XYZ",
        "SSCFRFRO 80 90",
        "SSCFRFRO xx",
        "SSSMG",
        "SSSMG END",
        "SSQSNZMAQS1",
        "SSQSNZMAQSx Name",
        "SSSPCFRO",
        "OPSMLALL",
        "OPSMLALLMOVx",
        "OPSMLALLMOV1",
        "OPSML x",
        "OPSML 1",
        "Z2",
        "Z2SLPxx",
        "PSBAS xx",
        "PVCN xx",
        "PSLFE xx",
        "PSLFE -5",
    ],
)
def test_lines_that_do_not_fit_are_ignored(
    parser_and_state: tuple[TelnetParser, AvrState], line: str
) -> None:
    parser, state = parser_and_state
    assert parser.feed(line, state) is False
    assert state.values == {} or set(state.values) <= {"quick_select"}


def test_settings_of_every_kind_are_decoded(
    parser_and_state: tuple[TelnetParser, AvrState],
) -> None:
    parser, state = parser_and_state
    # LFE comes as digits without a sign: 05 is -5 dB.
    for line in ("Z2SLPOFF", "PSLFE 05", "PVCN 52", "MVMAX 855"):
        assert parser.feed(line, state), line
    assert state.values["zone2_sleep_timer"] == 0
    assert state.values["lfe"] == -5
    assert state.values["contrast"] == 2
    assert state.zone("main").volume_max_raw == 85.5


def test_sound_mode_lists_are_published_at_their_end() -> None:
    discovery = Discovery()
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    for line in (
        "OPSMLALL MOV071Virtual",
        "OPSMLALL MUS011Stereo",
        "OPSMLALL MOV071Virtual",
        "OPSMLALL END",
        "OPSML 011Direct",
        "OPSML 020Stereo",
        "OPSML END",
    ):
        assert parser.feed(line, state), line

    assert discovery.all_sound_modes == ["Virtual", "Stereo"]
    assert discovery.sound_mode_groups == {"MOV": ["Virtual"], "MUS": ["Stereo"]}
    assert discovery.current_sound_modes == ["Direct", "Stereo"]
    assert state.values["sound_mode_display"] == "Direct"


def test_auto_lip_sync_by_receiver_generation() -> None:
    """Newer receivers report it as SSALSSET, the AVR-X2000 inside SSHOS."""
    newer, state = TelnetParser(load_profile(), Discovery()), AvrState()
    assert newer.feed("SSALSSET ON", state)
    assert state.values["auto_lip_sync"] is True
    older = TelnetParser(profile_variant(load_profile(), "older"), Discovery())
    state = AvrState()
    older.feed("SSHOSALS OFF", state)
    assert state.values["auto_lip_sync"] is False
    assert load_profile().control("auto_lip_sync").query == "SSALS ?"


def test_a_sound_mode_listed_twice_is_offered_once() -> None:
    """An AVR-X2600H left Stereo four times in its list of current modes."""
    discovery, state = Discovery(), AvrState()
    parser = TelnetParser(load_profile(), discovery)
    for line in ("OPSML 010Stereo", "OPSML 020Stereo", "OPSML 031Virtual", "OPSML END"):
        parser.feed(line, state)
    assert discovery.current_sound_modes == ["Stereo", "Virtual"]
