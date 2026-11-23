"""Tests for avr/models.py."""

from __future__ import annotations

from custom_components.denonavr_plus.avr.models import DeviceInfo, KnownAnswers

X2000 = DeviceInfo(model_name="AVR-X2000")


def _known(**changes: object) -> KnownAnswers:
    data: dict[str, object] = {
        "model": "AVR-X2000",
        "firmware": None,
        "asked": ["PSDYNEQ ?", "STBY?"],
        "answered": ["PSDYNEQ ?"],
    }
    data.update(changes)
    known = KnownAnswers.from_dict(data)
    assert known is not None
    return known


def test_answers_cover_the_receiver_they_came_from() -> None:
    assert _known().covers(X2000, {"PSDYNEQ ?", "STBY?"})


def test_another_model_or_firmware_is_asked_again() -> None:
    assert not _known(model="AVR-X2600H").covers(X2000, {"PSDYNEQ ?"})
    assert not _known(firmware="1.0").covers(X2000, {"PSDYNEQ ?"})


def test_a_query_not_asked_then_is_asked_again() -> None:
    """A control added in a later release."""
    assert not _known().covers(X2000, {"PSDYNEQ ?", "PSLFC ?"})


def test_a_round_trip_through_storage() -> None:
    known = _known()
    assert KnownAnswers.from_dict(known.as_dict()) == known


def test_anything_else_in_storage_is_ignored() -> None:
    assert KnownAnswers.from_dict(None) is None
    assert KnownAnswers.from_dict({"asked": "PSDYNEQ ?"}) is None
    assert KnownAnswers.from_check(None) is None
    assert KnownAnswers.from_check({"queries": []}) is None


def test_the_answers_of_a_capability_check() -> None:
    known = KnownAnswers.from_check(
        {
            "model": "AVR-X2000",
            "firmware": None,
            "queries": {
                "PSDYNEQ ?": {"result": "answered", "lines": ["PSDYNEQ ON"]},
                "STBY?": {"result": "no reply", "lines": []},
            },
        }
    )
    assert known == _known()
