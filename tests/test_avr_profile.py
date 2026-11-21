"""Tests for avr/profile.py: the protocol grammar file."""

from __future__ import annotations

import re

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
    # Taken over from denon_avr 1.1.2; none of the 28 recordings has it.
    # A Deviceinfo from a receiver with a headphone EQ would confirm it.
    "HeadphoneEq",
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
    for group_id, group in load_profile().refresh_groups.items():
        assert group.get("on"), group_id
        assert group.get("queries"), group_id
