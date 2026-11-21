"""Tests for scripts/check_private_data.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_private_data.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_private_data", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up in sys.modules while it executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo, check=True, capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("start\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "start")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_categories_flags_private_values() -> None:
    module = _load()
    assert module.categories("host 192.168.1.20") == ["private IPv4 address"]
    assert module.categories("mac 3c:22:fb:01:02:03") == ["MAC address"]
    assert module.categories("mail someone@gmail.com") == ["e-mail address"]


def test_categories_accepts_placeholders() -> None:
    module = _load()
    assert module.categories("192.0.2.10 aa:bb:cc:dd:ee:ff user@example.com") == []


def test_non_utf8_file_in_the_diff_does_not_crash(repo: Path) -> None:
    # The MIT licence of the recorded receiver data is Latin-1 ("Götz").
    # A diff containing it once crashed this check with UnicodeDecodeError
    # instead of checking anything.
    (repo / "LICENSE").write_bytes("Copyright (c) 2016 Oliver G\xf6tz\n".encode("latin-1"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "latin-1 file")
    assert _load().main(["--base", "HEAD~1"]) == 0


def test_private_address_in_the_diff_fails(repo: Path) -> None:
    (repo / "notes.txt").write_text("receiver at 192.168.1.20\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "leak")
    assert _load().main(["--base", "HEAD~1"]) == 1
