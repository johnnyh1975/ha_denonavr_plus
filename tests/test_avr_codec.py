"""Tests for avr/codec.py: the Denon number encodings."""

from __future__ import annotations

import pytest

from custom_components.denonavr_plus.avr.codec import (
    decode_centered,
    decode_half_step,
    encode_centered,
    encode_half_step,
)


@pytest.mark.parametrize(
    ("token", "value"),
    [("50", 50.0), ("05", 5.0), ("455", 45.5), ("450", 45.0), (" 80 ", 80.0), ("0", 0.0)],
)
def test_decode_half_step(token: str, value: float) -> None:
    assert decode_half_step(token) == value


@pytest.mark.parametrize("token", ["OFF", "NON", "", "4a"])
def test_decode_half_step_rejects_non_numbers(token: str) -> None:
    assert decode_half_step(token) is None


@pytest.mark.parametrize(
    ("value", "token"),
    [(50.0, "50"), (5.0, "05"), (45.5, "455"), (45.2, "45"), (45.3, "455"), (-3.0, "00")],
)
def test_encode_half_step(value: float, token: str) -> None:
    assert encode_half_step(value) == token


@pytest.mark.parametrize("value", [0.0, 0.5, 12.0, 45.5, 79.5, 98.0])
def test_half_step_round_trip(value: float) -> None:
    assert decode_half_step(encode_half_step(value)) == value


def test_decode_centered() -> None:
    assert decode_centered("050", 50) == 0
    assert decode_centered("000", 50) == -50
    assert decode_centered("100", 50) == 50
    assert decode_centered("OFF", 50) is None


def test_encode_centered_pads_to_width() -> None:
    assert encode_centered(0, 50, 3) == "050"
    assert encode_centered(-50, 50, 3) == "000"
    assert encode_centered(4.6, 50, 3) == "055"


@pytest.mark.parametrize(
    ("token", "value"),
    [("99", 0.0), ("995", 0.0), ("00", 0.0), ("50", 50.0), ("455", 45.5), ("98", 98.0), ("9X", None)],
)
def test_decode_volume_reads_99_as_the_minimum(token: str, value: float | None) -> None:
    """AVR-1912/2112CI document (2011): MV99 is the minimum, MV995 -80.5 dB."""
    from custom_components.denonavr_plus.avr.codec import decode_volume

    assert decode_volume(token) == value
