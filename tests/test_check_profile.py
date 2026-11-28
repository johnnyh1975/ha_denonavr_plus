"""Tests for scripts/check_profile.py: the profile and its variants fit the schema."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "check_profile.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_profile", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_bundled_profile_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert _script().main() == 0
    assert "fits its schema" in capsys.readouterr().out


def test_a_misspelt_key_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _script()
    avr = tmp_path / "avr"
    avr.mkdir()
    for name in ("profile.py", "profile_schema.py"):
        (avr / name).write_text((module.AVR / name).read_text(encoding="utf-8"), encoding="utf-8")
    raw = json.loads((module.AVR / "protocol_profile.json").read_text(encoding="utf-8"))
    raw["controls"]["dynamic_eq"]["of"] = "OFF"
    (avr / "protocol_profile.json").write_text(json.dumps(raw), encoding="utf-8")
    module.AVR = avr
    assert module.main() == 1
    assert "unknown key 'of'" in capsys.readouterr().out
