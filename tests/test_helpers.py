"""Tests for helpers.py."""

from __future__ import annotations

from custom_components.denonavr_plus import helpers
from custom_components.denonavr_plus.avr.profile import load_profile


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
