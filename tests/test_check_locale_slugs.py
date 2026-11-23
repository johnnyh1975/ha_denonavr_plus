"""Tests for scripts/check_locale_slugs.py."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_locale_slugs.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_locale_slugs", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def guard(tmp_path: Path) -> tuple[ModuleType, Path]:
    """The guard, pointed at a copy of the integration that a test may break."""
    module = _load()
    copy = tmp_path / "denonavr_plus"
    shutil.copytree(module.PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    module.PACKAGE = copy
    return module, copy


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, old
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _edit_json(path: Path, change: Callable[[dict], None]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_the_integration_keeps_the_rules() -> None:
    assert _load().check() == []


def test_a_hard_coded_name_is_caught(guard: tuple[ModuleType, Path]) -> None:
    module, package = guard
    _edit(
        package / "number.py",
        '        self._attr_translation_key = "channel_trim"\n',
        '        self._attr_name = "Trim"\n',
    )
    assert any("DenonAvrChannelTrim" in p and "_attr_name" in p for p in module.check())


def test_an_entity_outside_the_base_class_is_caught(
    guard: tuple[ModuleType, Path],
) -> None:
    module, package = guard
    (package / "extra.py").write_text(
        "class Stray(SensorEntity):\n    _attr_translation_key = 'decoder'\n",
        encoding="utf-8",
    )
    assert any("extra.py:Stray" in p for p in module.check())


def test_a_missing_override_is_caught(guard: tuple[ModuleType, Path]) -> None:
    module, package = guard
    _edit(package / "entity.py", "def suggested_object_id(", "def english_object_id(")
    assert any("must define suggested_object_id" in p for p in module.check())


def test_a_translated_device_name_is_caught(guard: tuple[ModuleType, Path]) -> None:
    module, package = guard
    _edit(
        package / "entity.py",
        "identifiers={(DOMAIN, f\"{identifier}_{sub_device}\")},",
        "identifiers={(DOMAIN, f\"{identifier}_{sub_device}\")},\n"
        "                translation_key=sub_device,",
    )
    assert any("DeviceInfo has a translation_key" in p for p in module.check())


def test_a_missing_translation_is_caught(guard: tuple[ModuleType, Path]) -> None:
    module, package = guard
    _edit_json(
        package / "translations" / "de.json",
        lambda data: data["entity"]["select"].pop("dynamic_volume"),
    )
    assert "translations/de.json: entity.select.dynamic_volume.name is missing" in (
        module.check()
    )


def test_a_lost_placeholder_is_caught(guard: tuple[ModuleType, Path]) -> None:
    module, package = guard

    def _drop(data: dict) -> None:
        data["entity"]["number"]["channel_trim"]["name"] = "Pegel"

    _edit_json(package / "translations" / "pl.json", _drop)
    assert any(
        "pl.json: entity.number.channel_trim.name has other placeholders" in p
        for p in module.check()
    )
