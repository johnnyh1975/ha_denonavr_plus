"""Tests for scripts/check_icons.py."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_icons.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_icons", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard(tmp_path: Path) -> tuple[ModuleType, Path]:
    """The check, and a copy of the integration a test may break."""
    module = _load()
    copy = tmp_path / "denonavr_plus"
    shutil.copytree(module.PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    return module, copy


def _icons(copy: Path) -> dict:
    return json.loads((copy / "icons.json").read_text(encoding="utf-8"))


def _write_icons(copy: Path, icons: dict) -> None:
    (copy / "icons.json").write_text(json.dumps(icons), encoding="utf-8")


def test_the_integration_passes() -> None:
    module = _load()
    assert module.check(module.PACKAGE) == []
    assert module.main() == 0


def test_mute_shows_its_state() -> None:
    icons = _icons(_load().PACKAGE)
    assert icons["entity"]["switch"]["main_mute"]["state"]["on"] == "mdi:volume-off"


def test_a_missing_icon_is_found(guard: tuple[ModuleType, Path]) -> None:
    module, copy = guard
    icons = _icons(copy)
    del icons["entity"]["select"]["dimmer"]
    _write_icons(copy, icons)
    assert module.check(copy) == ["entity.select.dimmer: no icon in icons.json"]


def test_an_icon_without_an_entity_is_found(guard: tuple[ModuleType, Path]) -> None:
    module, copy = guard
    icons = _icons(copy)
    icons["entity"]["select"]["gone"] = {"default": "mdi:speaker"}
    _write_icons(copy, icons)
    assert module.check(copy) == ["icons.json entity.select.gone: strings.json has no such entity"]


def test_a_malformed_icon_is_found(guard: tuple[ModuleType, Path]) -> None:
    module, copy = guard
    icons = _icons(copy)
    icons["entity"]["switch"]["main_mute"]["state"]["on"] = "volume-off"
    icons["entity"]["number"]["bass"] = "mdi:music-clef-bass"
    _write_icons(copy, icons)
    assert module.check(copy) == [
        "icons.json entity.number.bass: needs a default icon",
        "icons.json entity.switch.main_mute.state.on: 'volume-off' is not an mdi:<name> icon",
    ]


def test_an_icon_in_code_is_found(guard: tuple[ModuleType, Path]) -> None:
    module, copy = guard
    button = copy / "button.py"
    text = button.read_text(encoding="utf-8")
    button.write_text(
        text.replace(
            "    _attr_entity_category = EntityCategory.CONFIG\n",
            '    _attr_entity_category = EntityCategory.CONFIG\n    _attr_icon = "mdi:restore"\n',
            1,
        ),
        encoding="utf-8",
    )
    assert module.check(copy) == ["button.py: sets _attr_icon; name the icon in icons.json"]


def test_an_icon_in_the_profile_is_found(guard: tuple[ModuleType, Path]) -> None:
    module, copy = guard
    profile = copy / "avr" / "protocol_profile.json"
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["controls"]["main_mute"]["icon"] = "mdi:volume-mute"
    profile.write_text(json.dumps(data), encoding="utf-8")
    assert module.check(copy) == [
        "protocol_profile.json main_mute: names an icon; use icons.json"
    ]


def test_missing_icons_file(guard: tuple[ModuleType, Path], capsys: pytest.CaptureFixture[str]) -> None:
    module, copy = guard
    (copy / "icons.json").unlink()
    assert module.check(copy) == ["icons.json is missing"]
    module.PACKAGE = copy
    assert module.main() == 1
    assert "icons.json is missing" in capsys.readouterr().out
