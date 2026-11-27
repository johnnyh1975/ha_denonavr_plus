"""Tests for avr/profile.py: the protocol grammar file."""

from __future__ import annotations

import re

import pytest

from custom_components.denonavr_plus.avr.profile import load_profile

from .conftest import all_deviceinfo_files


def test_profile_loads_and_is_cached() -> None:
    assert load_profile() is load_profile()


def test_core_controls_present() -> None:
    profile = load_profile()
    for control_id in ("main_power", "main_volume", "main_mute", "main_source", "sound_mode"):
        assert profile.control(control_id) is not None, control_id
    assert profile.control("does_not_exist") is None


def test_control_spec_fields() -> None:
    volume_limit = load_profile().control("volume_limit")
    assert volume_limit is not None
    assert volume_limit.kind == "enum"
    assert volume_limit.prefix == "SSVCTZMALIM "
    assert volume_limit.values == ["OFF", "060", "070", "080"]
    assert volume_limit.get("missing", "fallback") == "fallback"


def test_scales_and_references() -> None:
    profile = load_profile()
    assert profile.volume_reference == 80.0
    assert profile.volume_max_fallback == 98.0
    assert profile.level_reference == 50.0


def test_scopes_partition_controls() -> None:
    profile = load_profile()
    core = {s.id for s in profile.controls_by_scope("core")}
    feature = {s.id for s in profile.feature_controls()}
    assert core and feature
    assert not core & feature


#: Feature gates that no recorded Deviceinfo document contains, and why.
GATES_NOT_IN_RECORDINGS = {
    # Set by video_config.feature_flags() from the setup API's menu
    # availability, never read from Deviceinfo.
    "PictureAdjust",
}


def test_feature_gates_are_real_function_names() -> None:
    # A misspelt gate never matches, so its control silently never appears.
    # Every gate must be a FuncName that some real receiver reports, or be
    # listed above with the reason it is not.
    names: set[str] = set()
    for path in all_deviceinfo_files():
        names |= set(re.findall(r"<FuncName>([^<]+)</FuncName>", path.read_text()))
    gates = {spec.feature for spec in load_profile().feature_controls()}
    assert gates - names == GATES_NOT_IN_RECORDINGS


def test_refresh_groups_have_triggers_and_queries() -> None:
    profile = load_profile()
    for group_id, group in profile.refresh_groups.items():
        # A trigger: a line prefix, or a value whose change it follows.
        assert group.get("on") or group.get("on_change") in profile.controls, group_id
        assert group.get("queries"), group_id


def test_every_query_is_in_the_protocol_docs() -> None:
    """A query the integration sends must be described in docs/protocol."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    raw = json.loads(
        (root / "custom_components/denonavr_plus/avr/protocol_profile.json").read_text()
    )
    queries = {s["query"] for s in raw["controls"].values() if s.get("query")}
    queries |= {v["query"] for v in raw["readonly"].values()}
    queries |= {
        v["query"] for v in raw["introspection"].values()
        if isinstance(v, dict) and v.get("query")
    }
    for v in raw["introspection"].values():
        if isinstance(v, dict):
            queries |= set(v.get("queries", []))
    for control in raw["controls"].values():
        queries |= {variant["query"] for variant in control.get("variants", [])}
    for group in raw["refresh"]["groups"].values():
        queries |= set(group.get("queries", []))
    docs = "\n".join(f.read_text() for f in sorted((root / "docs/protocol").rglob("*.md")))
    missing = sorted(
        q for q in queries
        if not re.search(r"(?<![A-Z])" + re.escape(q.rstrip("?").strip()) + r"(?![A-Z])", docs)
    )
    assert not missing, f"queries not described in docs/protocol: {missing}"


def test_the_variants_follow_the_receivers_generation() -> None:
    """2013 (AVR-X2000), 2014-2015 (port 80) and 2016 on, as the documents give them.

    The 2015 X-series document has three-digit picture levels and presets
    01-56, like the newer receivers, but dialog level as PSDIL.
    """
    from custom_components.denonavr_plus.avr.profile import profile_for_receiver

    profile = load_profile()
    x2000 = profile_for_receiver(profile, 80, 210)
    x2100w = profile_for_receiver(profile, 80, 300)
    x2600h = profile_for_receiver(profile, 8080, 301)
    assert (x2000.variant, x2100w.variant, x2600h.variant) == ("older", "older", None)
    assert x2000.control("brightness").get("width") == 2
    assert x2000.control("brightness").get("max") == 12
    assert x2000.control("brightness").get("2013") is None
    assert x2100w.control("brightness").get("width") == 3
    assert x2100w.control("dialog_level").query == "PSDIL ?"
    assert x2600h.control("dialog_level").query == "PSCLV ?"
    # Without a CommApiVers (2010-2012) the 2013 encodings apply too.
    assert profile_for_receiver(profile, 80, None).control("contrast").get("width") == 2
    # The cached profile stays as it is for other receivers.
    assert profile.control("brightness").get("width") == 3


@pytest.mark.assumed(
    "the AVR-X2000/E400 document names the tuner presets A1-G8; the AVR-X2000 "
    "answered TPANOFF, so its own form is unconfirmed: a TPAN reply while a "
    "preset plays would settle it"
)
def test_the_2013_variant_replaces_the_network_audio_entries() -> None:
    from custom_components.denonavr_plus.avr.profile import profile_for_receiver

    profile = load_profile()
    x2000 = profile_for_receiver(profile, 80, 210)
    assert x2000.netaudio["tuner_presets"]["format"] == "letter"
    assert "2013" not in x2000.netaudio
    # 2015 X-series document: TPAN01-56.
    assert profile_for_receiver(profile, 80, 300).netaudio["tuner_presets"]["format"] == "number"
    assert profile.netaudio["tuner_presets"]["format"] == "number"


def test_grammar_reference_points() -> None:
    profile = load_profile()
    assert profile.line_terminator == "\r"
    assert profile.list_terminator == "END"
    assert profile.volume_reference == 80.0


def test_group_channels_name_the_members() -> None:
    profile = load_profile()
    assert profile.group_channels("SWF") == ["SW", "SW2"]
    assert profile.group_channels("SBK") == ["SB", "SBL", "SBR"]
    assert profile.group_channels("NOPE") == []


def test_controls_by_scope_and_group() -> None:
    profile = load_profile()
    assert all(spec.scope == "zone" for spec in profile.controls_by_scope("zone"))
    assert profile.controls_by_scope("nowhere") == []
    assert all(spec.group == "tone" for spec in profile.controls_by_group("tone"))
    assert profile.controls_by_group("tone")


def test_center_level_by_receiver_generation() -> None:
    """An AVR-X2600H on 0.9.2 reported center level -2 dB, through PSCLV only."""
    from custom_components.denonavr_plus.avr.profile import profile_variant

    profile = load_profile()
    assert profile.control("dialog_level").query == "PSCLV ?"
    assert profile_variant(profile, "older").control("dialog_level").query == "PSDIL ?"


@pytest.mark.assumed(
    "Denon's 2014 and 2020 documents write MNZST? without a space; an AVR-X2600H "
    "and an AVR-X3600H left MNZST ? unanswered. An answer to MNZST? would settle it"
)
def test_all_zone_stereo_is_asked_without_a_space() -> None:
    assert load_profile().control("all_zone_stereo").query == "MNZST?"


@pytest.mark.assumed(
    "values from Denon's FY21 AVR protocol table (2020); no receiver with IMAX "
    "or LFC has been tested"
)
def test_values_from_the_2020_table() -> None:
    profile = load_profile()
    assert profile.control("imax").get("values") == ["AUTO", "ON", "OFF"]
    assert profile.control("containment_amount").get("min") == 1


def test_audio_delay_and_zone_2_levels_follow_denons_tables() -> None:
    from custom_components.denonavr_plus.avr.profile import profile_variant

    def field(profile: object, control_id: str, key: str) -> object:
        spec = profile.control(control_id)  # type: ignore[attr-defined]
        assert spec is not None
        return spec.get(key)

    profile = load_profile()
    assert (field(profile, "audio_delay", "max"), field(profile, "audio_delay", "step")) == (500, 1)
    assert field(profile_variant(profile, "older"), "audio_delay", "max") == 200
    assert field(profile, "zone2_channel_level_fl", "step") == 1
    assert field(profile, "zone2_channel_level_fr", "step") == 1


def test_mdax_sends_the_values_of_marantzs_table() -> None:
    """Marantz FY20 AVR control protocol (2019): PSMDAX OFF/LOW/MID/HI, no MED."""
    spec = load_profile().control("mdax")
    assert spec is not None
    assert spec.values == ["OFF", "LOW", "MID", "HI"]


def test_marantz_receivers_get_smart_select() -> None:
    """Marantz FY20 AVR control protocol (2019): MSSMART, Z2SMART; BrandCode 1."""
    from custom_components.denonavr_plus.avr.profile import profile_for_receiver

    profile = load_profile()
    marantz = profile_for_receiver(profile, 8080, 301, 1)
    assert marantz.introspection["quick_select"]["query"] == "MSSMART ?"
    assert marantz.introspection["quick_select"]["prefix"] == "MSSMART"
    zone2 = marantz.control("zone2_quick_select")
    assert zone2 is not None and (zone2.query, zone2.prefix) == ("Z2SMART ?", "Z2SMART")
    # A Marantz on port 80 (SR6011) gets both variants and stays "older".
    sr6011 = profile_for_receiver(profile, 80, 300, 1)
    assert sr6011.variant == "older" and sr6011.variants == ("older", "marantz")
    assert sr6011.introspection["quick_select"]["prefix"] == "MSSMART"
    # Marantz below level 0300 (NR1604, SR5008): the manufacturer's apps send
    # MSQUICK<n> to them, so they keep Quick Select.
    nr1604 = profile_for_receiver(profile, 80, 210, 1)
    assert nr1604.introspection["quick_select"]["prefix"] == "MSQUICK"
    assert "marantz" not in nr1604.variants
    # Denon keeps Quick Select.
    denon = profile_for_receiver(profile, 8080, 301, 0)
    assert denon is profile
    assert denon.introspection["quick_select"]["prefix"] == "MSQUICK"
    # M-DAX is gated as Restorer on Marantz; only a Denon keeps the Restorer gate.
    restorer = marantz.control("restorer")
    assert restorer is not None and restorer.scope == "answered"
    denon_restorer = denon.control("restorer")
    assert denon_restorer is not None and denon_restorer.scope == "feature"


@pytest.mark.parametrize(
    ("model", "level", "values"),
    [
        # Documents of AVR-1713 and AVR-2113CI; Deviceinfo.xml of the AVR-1713.
        ("AVR-1713", 210, ["MODE1", "MODE2", "MODE3", "OFF"]),
        ("AVR-3313", 210, ["MODE1", "MODE2", "MODE3", "OFF"]),
        # AVR-X2000/E400 document; the AVR-X2000 answers PSRSTR LOW.
        ("AVR-X2000", 210, ["HI", "MED", "LOW", "OFF"]),
        ("AVR-E400", 210, ["HI", "MED", "LOW", "OFF"]),
        ("AVR-X1100W", 250, ["HI", "MED", "LOW", "OFF"]),
        # AVR-1912/2112CI document (2011), no Deviceinfo.xml.
        ("AVR-1912", None, ["MODE1", "MODE2", "MODE3", "OFF"]),
    ],
)
def test_the_restorer_follows_the_series(
    model: str, level: int | None, values: list[str]
) -> None:
    from custom_components.denonavr_plus.avr.profile import profile_for_receiver

    profile = profile_for_receiver(load_profile(), 80, level, 0, model)
    restorer = profile.control("restorer")
    assert restorer is not None
    assert restorer.values == values
    assert restorer.get("labels") == ["High", "Medium", "Low", "Off"]


def test_dynamic_volume_of_2011() -> None:
    """AVR-1912/2112CI document: NGT, EVE, DAY; from 2012 OFF, LIT, MED, HEV."""
    from custom_components.denonavr_plus.avr.profile import profile_for_receiver

    legacy = profile_for_receiver(load_profile(), 80, None, None, "AVR-1912").control(
        "dynamic_volume"
    )
    assert legacy is not None and legacy.values == ["NGT", "EVE", "DAY"]
    newer = profile_for_receiver(load_profile(), 80, 210, 0, "AVR-1713").control("dynamic_volume")
    assert newer is not None and newer.values == ["OFF", "LIT", "MED", "HEV"]
