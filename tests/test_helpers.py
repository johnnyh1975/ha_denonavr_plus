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
