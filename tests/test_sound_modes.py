"""Tests for sound_modes.py: readable sound mode names (package 55)."""

from __future__ import annotations

import pytest

from custom_components.denonavr_plus import choices, sound_modes

#: Names an AVR-X2600H reported (OPSML/OPSMLALL in its diagnostics).
X2600H = {
    "Dolby Audio - DD + DSur": "Dolby Surround",
    "Dolby Audio - DD + Neural:X": "DTS Neural:X",
    "Dolby Audio - Dolby Surround": "Dolby Surround",
    "Dolby Audio - Dolby Digital": "Dolby Digital",
    "Multi In + DSur": "Dolby Surround",
    "Multi In + Neural:X": "DTS Neural:X",
    "Multi In + Virtual:X": "DTS Virtual:X",
    "DTS Neural:X": "DTS Neural:X",
    "Multi Ch Stereo": "Multichannel stereo",
    "Multi Ch In": "Multichannel input",
    "Stereo": "Stereo",
    "Pure Direct": "Pure Direct",
    "Rock Arena": "Rock Arena",
}


@pytest.mark.parametrize(("name", "shown"), X2600H.items())
def test_the_receiver_s_names_read_short(name: str, shown: str) -> None:
    assert sound_modes.readable(name) == shown


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("Dolby Audio - DD+", "Dolby Digital Plus"),
        ("Dolby Audio - TrueHD", "Dolby TrueHD"),
        ("Dolby Audio - Dolby Atmos", "Dolby Atmos"),
        ("Dolby Audio - Something New", "Dolby Audio - Something New"),
        ("Multi Ch In 7.1", "Multichannel input 7.1"),
        ("DTS-HD + Unknown", "DTS-HD + Unknown"),
    ],
)
def test_other_names_follow_the_same_rules_or_stay(name: str, shown: str) -> None:
    assert sound_modes.readable(name) == shown


def test_the_words_come_in_the_user_s_language() -> None:
    german = sound_modes.readable_with(
        {"multichannel_stereo": "Mehrkanal-Stereo", "multichannel_input": "Mehrkanal-Eingang"}
    )
    assert german("Multi Ch Stereo") == "Mehrkanal-Stereo"


def test_two_modes_with_the_same_short_name_keep_the_receiver_s_names() -> None:
    modes = ["Dolby Audio - DD + DSur", "Dolby Audio - Dolby Surround", "Stereo"]
    names = choices.sound_mode_names({}, modes, sound_modes.readable)
    assert names == {
        "Dolby Audio - DD + DSur": "Dolby Audio - DD + DSur",
        "Dolby Audio - Dolby Surround": "Dolby Audio - Dolby Surround",
        "Stereo": "Stereo",
    }
    # A name of the user's own always wins.
    options = {"sound_mode_names": {"Dolby Audio - DD + DSur": "Film"}}
    assert choices.sound_mode_names(options, modes, sound_modes.readable)[
        "Dolby Audio - DD + DSur"
    ] == "Film"


def test_a_mode_is_chosen_by_its_short_or_its_receiver_name() -> None:
    known = ["Dolby Audio - DD + DSur", "Multi Ch Stereo"]
    readable = sound_modes.readable
    assert choices.sound_mode_match({}, "dolby surround", known, readable) == known[0]
    assert choices.sound_mode_match({}, "Dolby Audio - DD + DSur", known, readable) == known[0]
    assert choices.sound_mode_match({}, "Multichannel stereo", known, readable) == known[1]
    assert choices.sound_mode_match({}, "Dolby Surround", known) is None


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("PURE DIRECT", "Pure Direct"),
        ("DOLBY DIGITAL", "Dolby Digital"),
        ("MCH STEREO", "Multichannel stereo"),
        ("MULTI CH STEREO", "Multichannel stereo"),
        ("MULTI CH IN 7.1", "Multichannel input 7.1"),
        ("DTS SURROUND", "DTS Surround"),
        ("DOLBY TRUEHD", "Dolby TrueHD"),
        ("DTS HD MSTR", "DTS HD MSTR"),
        ("AUDYSSEY DSX", "Audyssey DSX"),
        ("DOLBY D EX", "Dolby D EX"),
        # A sign or a digit: an MS token, left as the receiver writes it.
        ("DTS:X", "DTS:X"),
        ("AURO-3D", "AURO-3D"),
        ("7CH STEREO", "7CH STEREO"),
        ("M CH IN+DOLBY EX", "M CH IN+DOLBY EX"),
        ("DOLBY AUDIO-DSUR", "DOLBY AUDIO-DSUR"),
    ],
)
def test_names_in_capitals_are_written_as_words(name: str, shown: str) -> None:
    """Older receivers (AVR-X2000) and the MS tokens give every name in capitals."""
    assert sound_modes.readable(name) == shown


def test_a_current_token_reads_as_the_listed_mode() -> None:
    """Before the receiver names the mode, its token is the listed mode, not a clash."""
    readable = sound_modes.readable_with(sound_modes.WORDS)
    shown = choices.shown_sound_modes({}, ["Multi Ch Stereo", "Stereo"], "MCH STEREO", readable)
    assert shown["MCH STEREO"] == shown["Multi Ch Stereo"] == "Multichannel stereo"
    assert choices.sound_mode_list({}, ["Multi Ch Stereo", "Stereo"], "MCH STEREO", readable) == [
        "Multichannel stereo",
        "Stereo",
    ]
