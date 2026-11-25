"""Why the receiver may keep a setting at its old value.

A receiver takes some settings only in a context: a signal, a sound mode,
another setting. Asked for one outside it, it answers with the old value or
not at all. Where that is known, a setting is confirmed after it is sent
(DenonAvrDevice.async_set_control_confirmed), and one the receiver kept is
reported with its reason instead of the old value silently coming back.

Sources: the receiver's command test (an AVR-X2000 and an AVR-X2600H; see
command_test.py for what each did), the protocol documents (PSSWR OFF only
in Direct and Stereo, 2015 document) and the profile's notes (resolution
only with the IP scaler on HDMI).
"""

from __future__ import annotations

#: The main zone is in standby: an AVR-X2000 took no setting then.
STANDBY = "standby"

#: Control groups that act on the main zone's sound or picture.
MAIN_ZONE_GROUPS = frozenset(
    {"volume", "tone", "audio", "audyssey", "surround", "speaker", "video", "source"}
)

#: The reason for each setting the receiver takes only in a context.
REASONS: dict[str, str] = {
    "dynamic_compression": "dolby_dts",
    "loudness_management": "dolby_dts",
    "lfe": "dolby_dts",
    "dialog_control": "dts_x",
    "effect_level": "dsp_mode",
    "subwoofer_switch": "two_channel",
    "front_speaker": "front_b",
    "resolution_hdmi": "scaler",
    "graphic_eq": "multeq_off",
    "bass": "tone_control",
    "treble": "tone_control",
    "tone_control": "dynamic_eq_off",
    "contrast": "custom_picture",
    "brightness": "custom_picture",
    "saturation": "custom_picture",
    "enhancer": "custom_picture",
    "noise_reduction": "custom_picture",
}

#: Every reason, for the texts (translation keys setting_not_taken_<reason>).
ALL_REASONS = frozenset({STANDBY, *REASONS.values()})


def refusal_reason(control_id: str, group: str | None, main_zone_on: bool | None) -> str | None:
    """Why the receiver may keep the setting now, or None when it takes it anywhere.

    Standby comes first: in standby no condition can be met. A main zone
    whose power is not known yet counts as on.
    """

    if main_zone_on is False and group in MAIN_ZONE_GROUPS:
        return STANDBY
    return REASONS.get(control_id)
