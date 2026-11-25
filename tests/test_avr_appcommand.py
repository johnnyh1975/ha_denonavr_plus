"""Tests for avr/appcommand.py: the sound state over AppCommand.xml."""

from __future__ import annotations

import pytest

from custom_components.denonavr_plus.avr import appcommand

from .conftest import deviceinfo


def test_tone_control_as_an_avr_x4100w_answers() -> None:
    values = appcommand.parse_status(deviceinfo("AVR-X4100W-AppCommand-update-tonecontrol.xml"))
    assert values == {"tone_control": True, "bass": 0.0, "treble": 0.0}


def test_no_tone_values_where_the_receiver_offers_no_tone_control() -> None:
    """An AVC-X3700H answers status 0 and empty values (tone control not available)."""
    text = deviceinfo("AVC-X3700H-AppCommand-update-tonecontrol-8080.xml")
    assert appcommand.parse_status(text) == {}


@pytest.mark.parametrize(
    ("recording", "mode"),
    [
        ("AVC-X3700H-AppCommand-update-soundmode-8080.xml", "Stereo"),
        ("AVR-X4300H-AppCommand-update-soundmode-8080.xml", "DTS Neo:X Cinema"),
    ],
)
def test_the_sound_mode_by_its_name(recording: str, mode: str) -> None:
    assert appcommand.parse_status(deviceinfo(recording)) == {"sound_mode_display": mode}


def test_levels_other_than_zero() -> None:
    text = (
        "<rx><cmd><status>1</status><adjust>0</adjust><basslevel>-2dB</basslevel>"
        "<treblelevel>+6dB</treblelevel></cmd></rx>"
    )
    assert appcommand.parse_status(text) == {"tone_control": False, "bass": -2.0, "treble": 6.0}


@pytest.mark.parametrize(
    "text",
    [None, "", "<broken", "<rx><error>2</error></rx>", "<html><body>Form not defined</body></html>"],
)
def test_no_answer_gives_nothing(text: str | None) -> None:
    assert appcommand.parse_status(text) == {}
    assert appcommand.parse_audyssey(text) == {}


@pytest.mark.assumed(
    "the GetAudyssey answer (list/param by name) and its numbers follow the denonavr "
    "library (appcommand.py, const.py); a recording from a receiver would settle it"
)
def test_audyssey_settings_as_telnet_tokens() -> None:
    text = (
        '<rx><cmd><list><param name="multeq" control="1">2</param>'
        '<param name="dynamiceq" control="1">1</param>'
        '<param name="dynamicvol" control="1">3</param></list></cmd></rx>'
    )
    assert appcommand.parse_audyssey(text) == {
        "multeq": "BYP.LR",
        "dynamic_eq": True,
        "dynamic_volume": "HEV",
    }
    unknown = '<rx><cmd><list><param name="multeq">9</param></list></cmd></rx>'
    assert appcommand.parse_audyssey(unknown) == {}


def test_the_requests_ask_for_what_is_parsed() -> None:
    assert "GetSurroundModeStatus" in appcommand.STATUS_REQUEST
    assert "GetToneControl" in appcommand.STATUS_REQUEST
    for name in ("multeq", "dynamiceq", "dynamicvol"):
        assert f'name="{name}"' in appcommand.AUDYSSEY_REQUEST
