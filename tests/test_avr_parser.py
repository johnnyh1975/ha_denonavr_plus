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


def test_parse_device_info_reads_the_interface_level() -> None:
    assert _parse("AVR-X2000-Deviceinfo.xml").device.comm_api_version == 210
    assert _parse("AVR-X2100W-2-Deviceinfo.xml").device.comm_api_version == 300
    assert _parse("AVC-X3700H-Deviceinfo-8080.xml").device.comm_api_version == 301
    assert _parse("AVR-1912-Deviceinfo.xml").device.comm_api_version is None


def test_parse_device_info_reads_the_brand() -> None:
    assert _parse("SR6012-Deviceinfo-8080.xml").device.brand_code == 1
    assert _parse("AVR-X2000-Deviceinfo.xml").device.brand_code == 0


def test_parse_device_info_reads_the_product_category() -> None:
    assert _parse("AVR-X2000-Deviceinfo.xml").device.product_category == 1
    assert _parse("M-CR510-Deviceinfo.xml").device.product_category == 2
    assert _parse("AVR-1912-Deviceinfo.xml").device.product_category is None


def test_a_marantz_reports_its_smart_select() -> None:
    from custom_components.denonavr_plus.avr.profile import profile_for_receiver

    parser = TelnetParser(profile_for_receiver(load_profile(), 8080, 301, 1), Discovery())
    state = AvrState()
    assert parser.feed("MSSMART2", state)
    assert state.values["quick_select"] == 2
    # MSSMART without a number is a surround mode on some Marantz models,
    # not a preset and not a sound mode this integration lists.
    assert not parser.feed("MSSMART", state)
    assert state.values["quick_select"] == 2


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
    ("PSSWL2 59", "subwoofer_2_level", 9.0),
    ("PSSWL3 48", "subwoofer_3_level", -2.0),
    ("PSSWL4 515", "subwoofer_4_level", 1.5),
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
    # As an AVR-X2000 answered (tests/fixtures/capability_check/AVR-X2000.json).
    ("Z2HDA THR", "zone2_hdmi_audio", "THR"),
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


# ── Receivers without Deviceinfo.xml (2010-2012) ──────────────────────────────


def test_the_main_zone_document_gives_inputs_names_and_sound_modes() -> None:
    """Recorded AVR-1912 (denonavr library): codes, the user's names, in use or not."""
    from custom_components.denonavr_plus.avr.parser import parse_main_zone

    profile = load_profile()
    found = parse_main_zone(
        deviceinfo("AVR-1912-formMainZone_MainZoneXml.xml"), profile.listed_sound_modes
    )
    sources = {source.code: (source.name, source.visible) for source in found.sources}
    assert "SOURCE" not in sources
    assert sources["DVD"] == ("Chrome", True)
    assert sources["TUNER"] == ("TUNER", True)
    assert sources["CD"] == ("CD", False)
    assert sources["GAME"] == ("GAME1", False)
    # MCH STEREO is the wire token of the listed name MULTI CH STEREO.
    assert {"DOLBY DIGITAL", "PURE DIRECT", "MULTI CH STEREO", "MATRIX"} <= found.features
    assert [zone.id for zone in found.zones] == ["main"]


@pytest.mark.parametrize(
    "recording",
    ["AVR-2312CI", "AVR-3311CI", "AVR-3312", "AVR-4810", "M-CR603"],
)
def test_every_recorded_main_zone_document_parses(recording: str) -> None:
    from custom_components.denonavr_plus.avr.parser import parse_main_zone

    found = parse_main_zone(
        deviceinfo(f"{recording}-formMainZone_MainZoneXml.xml"),
        load_profile().listed_sound_modes,
    )
    codes = [source.code for source in found.sources]
    assert codes and len(codes) == len(set(codes))
    assert "SOURCE" not in codes


def test_a_broken_main_zone_document_gives_the_main_zone_only() -> None:
    from custom_components.denonavr_plus.avr.parser import parse_main_zone

    found = parse_main_zone("<broken", {})
    assert not found.sources and [zone.id for zone in found.zones] == ["main"]


def test_a_2011_receiver_reports_volume_99_as_the_minimum() -> None:
    parser = TelnetParser(load_profile(), Discovery())
    state = AvrState()
    assert parser.feed("MV99", state)
    assert state.zone("main").volume_raw == 0.0
    assert parser.feed("Z299", state)
    assert state.zone("zone2").volume_raw == 0.0


@pytest.mark.assumed(
    "NSARA/NSHEA ON|OFF as Denon's AVR Remote app reads them; no receiver has answered yet"
)
def test_control_by_apps_is_read(parser_and_state: tuple[TelnetParser, AvrState]) -> None:
    parser, state = parser_and_state
    assert parser.feed("NSARA OFF", state)
    assert parser.feed("NSHEA ON", state)
    assert state.values["remote_app_control"] is False
    assert state.values["heos_app_control"] is True
    assert not parser.feed("NSARA ?", state)


@pytest.mark.parametrize(
    ("recording", "path", "expected"),
    [
        ("AVC-X3700H-Deviceinfo-8080.xml", "AudioDelay/Adjust", (0.0, 500.0)),
        # The older layout: the range on AudioDelay itself, with StepValue.
        ("AVR-X2100W-2-Deviceinfo.xml", "AudioDelay", (0.0, 200.0)),
        ("AVC-X3700H-Deviceinfo-8080.xml", "ContainmentAmount", (1.0, 7.0)),
    ],
)
def test_published_value_ranges_are_read(recording: str, path: str, expected: tuple[float, float]) -> None:
    from custom_components.denonavr_plus.avr.profile import range_paths

    profile = load_profile()
    paths = {path for spec in profile.controls.values() for path in range_paths(spec)}
    discovery = parse_device_info(
        (Path(__file__).parent / "fixtures" / "denonavr" / recording).read_text(encoding="utf-8"),
        value_ranges=paths,
    )
    meta = discovery.numeric_meta[path]
    assert (meta["min"], meta["max"], meta["step"]) == (*expected, 1.0)
    # Effect Level stays the profile's 0-15: receivers report PSEFF 00 although
    # their Deviceinfo says 1-15. Auro-Matic Strength stays 1-16 for the same
    # reason: the AVC-A10H describes 0-15, the AVR-X4300H and the 2015 document 1-16.
    assert "EffectLevel" not in paths and "AuroMatic3DStrength" not in paths


def _capability_lines(model: str, query: str) -> list[str]:
    path = Path(__file__).parent / "fixtures" / "capability_check" / f"{model}.json"
    return cast(list[str], json.loads(path.read_text(encoding="utf-8"))["queries"][query]["lines"])


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # Front, center and surround small, one subwoofer answered as YES.
        ("AVR-X2000", {"FL", "FR", "C", "SL", "SR", "SW"}),
        ("AVR-X2600H-3.139.175", {"FL", "FR", "C", "SL", "SR", "SW"}),
    ],
)
def test_a_subwoofer_answered_yes_is_set_up(model: str, expected: set[str]) -> None:
    discovery = Discovery()
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    for line in _capability_lines(model, "SSSPC ?"):
        parser.feed(line, state)
    assert discovery.configured_channels == expected


def test_a_speaker_removed_since_the_last_answer_no_longer_counts() -> None:
    """The AVR-X2000 after its owner set center, surround and subwoofer to None."""

    discovery = Discovery()
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    for line in _capability_lines("AVR-X2000", "SSSPC ?"):
        parser.feed(line, state)
    for line in (
        "SSSPCFRO LAR",
        "SSSPCCEN NON",
        "SSSPCSUA NON",
        "SSSPCSWF NO",
        "SSSPCSBK NON",
        "SSSPCSBK 2SP",
        "SSSPCFRH NON",
    ):
        parser.feed(line, state)
    assert discovery.configured_channels == {"FL", "FR"}

    # Set up again later, the center counts again.
    for line in ("SSSPCFRO SMA", "SSSPCCEN SMA", "SSSPCSWF YES", "SSSPCSBK NON", "SSSPCSBK 2SP"):
        parser.feed(line, state)
    assert discovery.configured_channels == {"FL", "FR", "C", "SW"}


@pytest.mark.assumed(
    "that the flagged OPSMLALL entry marks the current genre rests on one "
    "AVR-X2600H log (MOV031 flagged, MS on Dolby Audio, SSSMG from the start "
    "MUS); asking SSSMG ? right after a genre switch would settle it"
)
def test_the_current_genre_follows_the_flagged_sound_mode() -> None:
    """An AVR-X2600H (3.139.175): SSSMG at the start, later only OPSMLALL pushes."""

    parser, state = TelnetParser(load_profile(), Discovery()), AvrState()
    parser.feed("SSSMG MUS", state)
    for line in (
        "OPSMLALL MOV010Stereo",
        "OPSMLALL MOV020Dolby Audio - Dolby Digital",
        "OPSMLALL MOV031Dolby Audio - DD + DSur",
        "OPSMLALL MOV040Dolby Audio - DD + Neural:X",
        "OPSMLALL MUS080Stereo",
        "OPSMLALL MUS090Dolby Audio - Dolby Digital",
        "OPSMLALL PUR250Pure Direct",
        "OPSMLALL END",
    ):
        parser.feed(line, state)
    assert state.values["sound_mode_genre"] == "MOV"


def test_a_sound_mode_name_may_start_with_a_digit() -> None:
    discovery = Discovery()
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    for line in ("OPSMLALL MUS0517ch Stereo", "OPSMLALL END", "OPSML 0517ch Stereo", "OPSML END"):
        assert parser.feed(line, state), line
    assert discovery.all_sound_modes == ["7ch Stereo"]
    assert state.values["sound_mode_genre"] == "MUS"
    assert state.values["sound_mode_display"] == "7ch Stereo"


def test_inputs_the_receiver_does_not_name_get_readable_names() -> None:
    """An AVR-X2000 names its inputs in SSFUN and lists its services in SSSOD only."""

    discovery = Discovery()
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    for query in ("SSFUN ?", "SSSOD ?"):
        for line in _capability_lines("AVR-X2000", query):
            parser.feed(line, state)
    names = {source.code: source.name for source in discovery.sources}
    assert names["TV"] == "TV AUDIO"  # the receiver's own name stays
    assert names["IRADIO"] == "Internet Radio"
    assert names["SERVER"] == "Media Server"
    assert names["USB/IPOD"] == "iPod/USB"
    assert names["LASTFM"] == "Last.fm"
    assert names["TUNER"] == "Tuner"
    # A name the receiver sends later wins.
    parser.feed("SSFUNTUNER Radio", state)
    assert {source.code: source.name for source in discovery.sources}["TUNER"] == "Radio"


def _older_parser() -> tuple[TelnetParser, AvrState]:
    return TelnetParser(profile_variant(load_profile(), "older"), Discovery()), AvrState()


def test_the_elapsed_time_of_a_track_is_its_position() -> None:
    """An AVR-X2000 playing over AirPlay: line 5 counts up, NUL padded."""

    parser, state = _older_parser()
    pad = "\x00" * 40
    parser.feed("NSE0Now Playing" + pad, state)
    parser.feed("NSE1\x01Yoshi City" + pad, state)
    assert parser.feed("NSE5\x00   03:36 " + pad, state)
    assert state.now_playing.position == 216.0
    assert state.now_playing.position_at is not None
    assert state.now_playing.duration is None
    # The same time again (paused): the time it was read stays.
    read_at = state.now_playing.position_at
    assert not parser.feed("NSE5\x00   03:36 " + pad, state)
    assert state.now_playing.position_at == read_at
    # At a track change the line is empty for a moment: no position then.
    assert parser.feed("NSE5\x00\x00  00:00 " + pad, state)
    assert state.now_playing.position is None
    assert parser.feed("NSE5\x00   1:02:03 " + pad, state)
    assert state.now_playing.position == 3723.0
    # Another title: the old position goes with the old track.
    parser.feed("NSE1\x01Another Song" + pad, state)
    assert state.now_playing.position is None


@pytest.mark.assumed(
    "the percentage after the time is in the AVR-X2000/E400 document (v03) "
    "only; an AVR-X2000 sent the time alone"
)
def test_the_percentage_after_the_time_gives_the_duration() -> None:
    parser, state = _older_parser()
    parser.feed("NSE0Now Playing", state)
    parser.feed("NSE5\x0001:00 25%", state)
    assert (state.now_playing.position, state.now_playing.duration) == (60.0, 240)


def test_a_time_on_a_menu_page_is_no_position() -> None:
    parser, state = _older_parser()
    parser.feed("NSE0Internet Radio", state)
    assert not parser.feed("NSE5\x0003:36", state)
    assert state.now_playing.position is None



def test_a_speaker_value_replaces_only_its_own_kind() -> None:
    """Presence and count of a count group are separate; quick changes do not mix."""

    discovery = Discovery()
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    for line in ("SSSPCFRO LAR", "SSSPCSBK NON", "SSSPCSBK 2SP", "SSSPCSWF NO", "SSSPCSWF YES"):
        parser.feed(line, state)
    assert discovery.configured_channels == {"FL", "FR", "SW"}
    # A count line alone keeps the surround back absent.
    parser.feed("SSSPCSBK 1SP", state)
    assert "SB" not in discovery.configured_channels
    # Set up as small: two speakers, the count kept.
    parser.feed("SSSPCSBK SMA", state)
    assert discovery.configured_channels == {"FL", "FR", "SW", "SB"}
    parser.feed("SSSPCSBK 2SP", state)
    assert discovery.configured_channels == {"FL", "FR", "SW", "SBL", "SBR"}



def test_an_input_named_by_its_code_earlier_gets_its_readable_name() -> None:
    """Sources from the stored snapshot of 1.2.x, then the receiver's SSSOD."""
    from custom_components.denonavr_plus.avr.models import SourceDescriptor

    discovery = Discovery()
    discovery.sources = [SourceDescriptor(code="IRADIO", name="IRADIO")]
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    parser.feed("SSSODIRADIO USE", state)
    assert discovery.sources[0].name == "Internet Radio"


# ── Recordings of newer receivers (tests/fixtures/recordings) ────────────

_RECORDINGS = Path(__file__).parent / "fixtures" / "recordings"


def _replay(model: str) -> tuple[TelnetParser, AvrState, Discovery]:
    """Every telnet line a recorded receiver sent, through the parser."""
    folder = _RECORDINGS / model
    profile = load_profile()
    features = {s.feature for s in profile.controls.values() if s.feature}
    discovery = parse_device_info(
        (folder / "goform_Deviceinfo.xml").read_text(),
        features,
        receiver_type=profile.receiver_type,
    )
    parser, state = TelnetParser(profile, discovery), AvrState()
    for entry in json.loads((folder / "telnet_corpus.json").read_text()):
        parser.feed(entry["line"], state)
    return parser, state, discovery


def test_a_recorded_avc_x3900h_is_read_as_it_reports() -> None:
    """AVC-X3900H (2026, 3.139.174): YES/NO speakers, four subwoofers, OPALS."""
    parser, state, discovery = _replay("AVC-X3900H")
    assert discovery.device.model_name == "AVC-X3900H"
    assert discovery.configured_channels == {"FL", "FR", "C", "SL", "SR", "SW"}
    # Auto lip sync as OPALSSET (no SSALS); the answered prefix is the one written.
    assert state.values["auto_lip_sync"] is True
    assert parser.variant_prefixes["auto_lip_sync"] == "OPALSSET "
    # The volume limit in two digits: the profile's value, the receiver's spelling.
    assert state.values["volume_limit"] == "070"
    assert parser.digits["volume_limit"] == 2
    assert state.channel_levels["SW2"] == 9.0
    assert state.values["input_mode"] == "EARC"


def test_a_recorded_avr_x3600h_keeps_the_older_spellings() -> None:
    parser, state, discovery = _replay("AVR-X3600H")
    assert discovery.quick_select_names == {
        1: "Quick Select 1", 2: "Quick Select 2", 3: "Quick Select 3", 4: "Quick Select 4"
    }
    assert state.values["volume_limit"] == "060"
    assert parser.digits == {}
    assert "auto_lip_sync" not in parser.variant_prefixes


@pytest.mark.assumed(
    "the block lines come from LaserGuruGuy's tests ('SSQS2QSN Movie night', "
    "'SSQS2SRC ON', 'SSQS2 END'), not from his recorded telnet corpus; an "
    "AVC-X3900H answering SSQS2 ? would settle it"
)
def test_quick_selects_named_in_blocks() -> None:
    discovery = Discovery()
    parser, state = TelnetParser(load_profile(), discovery), AvrState()
    for line in ("SSQS2QSN Movie night  ", "SSQS2SRC ON", "SSQS2 END"):
        assert parser.feed(line, state)
    assert discovery.quick_select_names == {2: "Movie night"}


@pytest.mark.assumed(
    "PSSWL2 next to PSSWL comes from LaserGuruGuy/denon_avr ('PSSWL2 59'); the "
    "recorded AVC-X3900H answers PSSWL ? with PSSWL alone (its levels via SSLEV)"
)
def test_the_level_of_a_second_subwoofer() -> None:
    parser, state = TelnetParser(load_profile(), Discovery()), AvrState()
    assert parser.feed("PSSWL2 59", state)
    assert state.values["subwoofer_2_level"] == 9.0


@pytest.mark.assumed(
    "SSAUDSTS and the SSHOS item lines follow LaserGuruGuy/denon_avr (AVR-X3600H, "
    "AVC-X3900H, after his recordings were made); an answer to SSAUDSTS ? and SSHOS ? "
    "from one of them would settle it"
)
def test_the_audio_menu_status_and_the_stored_hdmi_setup() -> None:
    parser, state = TelnetParser(load_profile(), Discovery()), AvrState()
    assert parser.feed("SSAUDSTS 222121222122000", state)
    assert state.values["audio_menu_status"] == "222121222122000"
    assert not parser.feed("SSAUDSTS ???", state)
    assert parser.feed("SSHOSPAS OFF", state)
    assert state.values["hdmi_setup_PAS"] == "OFF"
    assert parser.feed("SSHOS END", state)
    # An item this profile does not know is not claimed.
    assert not parser.feed("SSHOSIPM 1", state)
