"""Why the receiver may keep a setting (avr/refusals.py)."""

from __future__ import annotations

from custom_components.denonavr_plus.avr import refusals
from custom_components.denonavr_plus.avr.profile import load_profile


def test_standby_comes_before_the_setting_s_own_condition() -> None:
    assert refusals.refusal_reason("subwoofer_switch", "speaker", False) == refusals.STANDBY
    assert refusals.refusal_reason("subwoofer_switch", "speaker", True) == "two_channel"
    # Power not known yet counts as on.
    assert refusals.refusal_reason("graphic_eq", "audyssey", None) == "multeq_off"


def test_a_setting_taken_anywhere_has_no_reason() -> None:
    assert refusals.refusal_reason("dynamic_eq", "audyssey", True) is None
    # Not a main-zone setting: standby does not stop it.
    assert refusals.refusal_reason("zone2_sleep_timer", "zone", False) is None


def test_every_reason_names_a_control_of_the_profile() -> None:
    profile = load_profile()
    for control_id in refusals.REASONS:
        assert profile.control(control_id) is not None, control_id
    assert refusals.ALL_REASONS == {refusals.STANDBY, *refusals.REASONS.values()}


def test_every_reason_has_its_text() -> None:
    import json
    from pathlib import Path

    strings = json.loads(
        (Path(__file__).parents[1] / "custom_components/denonavr_plus/strings.json").read_text()
    )
    for reason in refusals.ALL_REASONS:
        assert f"setting_not_taken_{reason}" in strings["exceptions"], reason
