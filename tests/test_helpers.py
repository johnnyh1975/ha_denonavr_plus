"""Tests for helpers.py."""

from __future__ import annotations

import re

import pytest

from custom_components.denonavr_plus import helpers
from custom_components.denonavr_plus.helpers import option_key
from custom_components.denonavr_plus.avr.models import ChannelDescriptor, Discovery
from custom_components.denonavr_plus.avr.profile import ControlSpec, load_profile


def test_everyday_controls_exist_in_the_profile() -> None:
    # A typo here would silently leave an everyday control disabled.
    profile = load_profile()
    for control_id in helpers._EVERYDAY_CONTROLS:
        assert profile.control(control_id) is not None, control_id


def test_setup_values_start_disabled() -> None:
    profile = load_profile()
    for control_id in ("dynamic_eq", "bass", "sleep_timer", "all_zone_stereo"):
        spec = profile.control(control_id)
        assert spec is not None
        assert helpers.control_enabled_by_default(spec), control_id
    for control_id in ("main_power", "volume_limit", "lpf_for_lfe", "aspect_ratio"):
        spec = profile.control(control_id)
        assert spec is not None
        assert not helpers.control_enabled_by_default(spec), control_id


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Medium Small", "medium_small"),
        ("-20 dB", "minus_20_db"),
        ("0 dB", "0_db"),
        ("A + B", "a_b"),
        ("480p/576p", "480p_576p"),
        ("4K (60/50)", "4k_60_50"),
        ("Height None / Dolby 2ch", "height_none_dolby_2ch"),
        ("eARC", "earc"),
    ],
)
def test_option_keys_are_valid_state_keys(label: str, key: str) -> None:
    assert option_key(label) == key
    assert re.fullmatch(r"[a-z0-9]([a-z0-9_-]*[a-z0-9])?", key)


def test_without_a_label_for_every_value_the_tokens_stand_in() -> None:
    spec = ControlSpec("example", {"kind": "enum", "values": ["LOW", "HI"], "labels": ["Low"]})
    keys, key_to_value, value_to_key = helpers.enum_options(spec)
    assert keys == ["low", "hi"]
    assert key_to_value == {"low": "LOW", "hi": "HI"}
    assert value_to_key == {"LOW": "low", "HI": "hi"}


_MULTEQ = ControlSpec(
    "multeq",
    {
        "kind": "enum",
        "values": ["AUDYSSEY", "BYP.LR", "FLAT", "MANUAL", "OFF"],
        "labels": ["Reference", "L/R Bypass", "Flat", "Manual", "Off"],
    },
)


def test_only_the_options_the_receiver_lists() -> None:
    """An AVR-X2600H (3.139.175) lists four MultEq options, without Manual."""
    keys, key_to_value, value_to_key = helpers.enum_options(
        _MULTEQ, ["Reference", "L/R Bypass", "Flat", "Off"]
    )
    assert keys == ["reference", "l_r_bypass", "flat", "off"]
    assert "MANUAL" not in key_to_value.values()
    assert "MANUAL" not in value_to_key


@pytest.mark.parametrize(
    "published",
    [
        None,
        # As many as the profile: the profile's labels stay.
        ["Reference", "L/R Bypass", "Flat", "Manual", "Off"],
        # A name the profile does not know: nothing is dropped on a guess.
        ["Reference", "Bypass L/R", "Off"],
    ],
)
def test_the_profiles_options_otherwise(published: list[str] | None) -> None:
    keys, _, _ = helpers.enum_options(_MULTEQ, published)
    assert len(keys) == 5


def test_published_names_are_compared_without_case_and_signs() -> None:
    spec = ControlSpec(
        "ref", {"kind": "enum", "values": ["0", "5", "10"], "labels": ["0 dB", "5 dB", "10 dB"]}
    )
    keys, _, _ = helpers.enum_options(spec, ["0dB", "+5dB"])
    assert keys == ["0_db", "5_db"]


def _discovery(*channels: tuple[str, str]) -> Discovery:
    return Discovery(channels=[ChannelDescriptor(code, name) for code, name in channels])


@pytest.mark.parametrize(
    ("channels", "known", "name"),
    [
        (["FL", "FR"], [("FL", "Front L"), ("FR", "Front R")], "Front"),
        (["C"], [("C", "Center")], "Center"),
        # A pair without a common base is named after its first channel.
        (["SW", "C"], [("SW", "Subwoofer"), ("C", "Center")], "Subwoofer"),
        # Not configured: the protocol codes stand in.
        (["SBL", "SBR"], [], "SBL"),
        ([], [], ""),
    ],
)
def test_speaker_group_names(channels: list[str], known: list[tuple[str, str]], name: str) -> None:
    assert helpers.group_name(_discovery(*known), channels) == name


def test_zone_2_settings_go_with_zone_2() -> None:
    spec = ControlSpec("zone2_sleep_timer", {"kind": "minutes", "subdevice": "zone2"})
    assert helpers.control_zone_shown(spec, {})
    assert helpers.control_zone_shown(spec, {"zones": ["zone2"]})
    assert not helpers.control_zone_shown(spec, {"zones": []})
    other = ControlSpec("bass", {"kind": "level", "subdevice": "audio"})
    assert helpers.control_zone_shown(other, {"zones": []})


@pytest.mark.assumed(
    "VSVPMBYP comes from Denon's 2020 command list; no receiver has taken it yet"
)
def test_a_value_only_some_receivers_have() -> None:
    """Video Mode Bypass (VSVPMBYP): the AVR-X2600H and AVC-X3700H list it,
    the AVR-X4300H does not; a receiver that lists nothing does not get it."""
    from custom_components.denonavr_plus.avr.profile import load_profile

    spec = load_profile().control("video_mode")
    assert spec is not None
    assert helpers.enum_options(spec, ["Auto", "Game", "Movie", "Bypass"])[0] == [
        "auto", "game", "movie", "bypass",
    ]
    assert helpers.enum_options(spec, ["Auto", "Game", "Movie"])[0] == ["auto", "game", "movie"]
    assert helpers.enum_options(spec, None)[0] == ["auto", "game", "movie"]


def test_the_receivers_own_names_for_the_options() -> None:
    """Input Mode: an AVC-8500H lists no eARC, under its own names (No Input)."""
    from custom_components.denonavr_plus.avr.profile import load_profile

    spec = load_profile().control("input_mode")
    assert spec is not None
    keys = helpers.enum_options(
        spec, ["No Input", "Auto", "HDMI", "Digital", "Analog", "7.1CH IN", "ARC"]
    )[0]
    assert "earc" not in keys and "none" in keys
