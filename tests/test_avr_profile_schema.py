"""Tests for avr/profile_schema.py: the shape of protocol_profile.json."""

from __future__ import annotations

import copy
import json

import pytest

from custom_components.denonavr_plus.avr import profile as profile_module
from custom_components.denonavr_plus.avr import profile_schema
from custom_components.denonavr_plus.avr.profile_schema import (
    BOOL,
    DOC,
    INT,
    NUMBER,
    STR,
    ListOf,
    MapOf,
    Spec,
    Struct,
)


def _raw() -> dict:
    return json.loads(profile_module._PROFILE_PATH.read_text(encoding="utf-8"))


def test_the_bundled_profile_fits() -> None:
    assert profile_schema.problems(_raw()) == []


def test_an_unknown_key_a_wrong_type_or_a_missing_key_is_found() -> None:
    """A misspelt key would read as "this receiver has no such function"."""
    raw = _raw()
    typo = copy.deepcopy(raw)
    typo["controls"]["dynamic_eq"]["of"] = typo["controls"]["dynamic_eq"].pop("off", "OFF")
    assert profile_schema.problems(typo) == ["profile.controls.dynamic_eq: unknown key 'of'"]
    wrong = copy.deepcopy(raw)
    wrong["grammar"]["speakers_web"]["layout_attempts"] = "4"
    assert profile_schema.problems(wrong) == [
        "profile.grammar.speakers_web.layout_attempts: expected an integer, got str"
    ]
    missing = copy.deepcopy(raw)
    del missing["controls"]["dynamic_eq"]["kind"]
    assert profile_schema.problems(missing) == ["profile.controls.dynamic_eq: missing kind"]
    variant = copy.deepcopy(raw)
    variant["controls"]["brightness"]["2013"]["widht"] = 2
    assert profile_schema.problems(variant) == [
        "profile.controls.brightness.2013: unknown key 'widht'"
    ]


@pytest.mark.parametrize(
    ("spec", "value", "problem"),
    [
        (INT, True, "x: expected an integer, got bool"),
        (BOOL, True, None),
        (NUMBER, 1, None),
        (NUMBER, "1", "x: expected a number, got str"),
        (ListOf(STR), "a", "x: expected a list, got str"),
        (ListOf(STR), ["a", 1], "x[1]: expected a string, got int"),
        (MapOf(STR), [], "x: expected an object, got list"),
        (MapOf(STR), {"doc": 1}, "x.doc: none of the allowed shapes fits"),
        (Struct({}), "a", "x: expected an object, got str"),
        (DOC, ["a", "b"], None),
        (DOC, 1, "x: none of the allowed shapes fits"),
    ],
)
def test_each_shape(spec: Spec, value: object, problem: str | None) -> None:
    found: list[str] = []
    spec.check(value, "x", found)
    assert found == ([problem] if problem else [])


def test_a_shape_must_say_how_it_checks() -> None:
    with pytest.raises(NotImplementedError):
        Spec().check(1, "x", [])


def test_every_key_the_setup_page_modules_read_is_known() -> None:
    """A key the code reads but the schema rejects would fail every setup once set."""
    import re
    from pathlib import Path

    avr = Path(profile_module.__file__).parent
    sections = {
        "video_config.py": ("video_web", "speakers_web"),
        "speaker_layout.py": ("speakers_web",),
        "graphic_eq.py": ("graphic_eq",),
    }
    grammar = profile_schema.GRAMMAR.keys
    for module, names in sections.items():
        read = set(re.findall(r"grammar\.get\(\"(\w+)\"", (avr / module).read_text(encoding="utf-8")))
        # video_config.py reads both setup-page sections; each key in one of them.
        known = set().union(*(grammar[name].keys for name in names))  # type: ignore[attr-defined]
        assert read and not read - known, (module, read - known)
