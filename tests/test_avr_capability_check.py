"""Tests for avr/capability_check.py: the queries match the probe script."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from custom_components.denonavr_plus.avr import capability_check

PROBE = Path(__file__).resolve().parent.parent / "tools" / "probe_receiver.py"
CHECKS = Path(__file__).resolve().parent / "fixtures" / "capability_check"


def _probe_catalogue() -> list[tuple[str, str]]:
    spec = importlib.util.spec_from_file_location("probe_receiver_for_check", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return [(q.command, q.group) for q in module.CATALOGUE]


def test_the_check_asks_what_the_probe_asks() -> None:
    """The guard: change one list and this fails until the other follows."""
    probe = [item for item in _probe_catalogue() if item[0] != "NSA"]
    assert list(capability_check.QUERIES) == probe


def test_a_model_with_a_recorded_check_is_known() -> None:
    """A reviewed check in the fixtures ends the repair that asks for one."""
    models = {json.loads(path.read_text())["model"] for path in CHECKS.glob("*.json")}
    assert models == {"AVR-X2000", "AVR-X2600H"}
    assert models <= capability_check.KNOWN_MODELS


def test_a_related_model_points_to_a_checked_one() -> None:
    related = capability_check.RELATED_MODELS
    assert set(related.values()) <= capability_check.KNOWN_MODELS
    assert not set(related) & capability_check.KNOWN_MODELS


def test_only_queries() -> None:
    assert all(
        command.endswith("?") or command in capability_check.SCREEN_REQUESTS
        for command, _ in capability_check.QUERIES
    )
    assert capability_check.SCREEN_REQUESTS <= {command for command, _ in capability_check.QUERIES}


def test_classify() -> None:
    classify = capability_check.classify
    assert classify("MV?", ["MV50", "MVMAX 80"]) == "answered"
    assert classify("PSFOO ?", ["?"]) == "rejected"
    assert classify("PSFOO ?", ["PWON"]) == "other lines only"
    assert classify("PSFOO ?", []) == "no reply"


def test_what_identifies_the_receiver_is_replaced() -> None:
    lines = [
        "NSFRN Living Room of Someone",
        "SSINFIPA 198.51.100.7 MAC 02:00:00:00:00:7A owner someone@example.org",
        "SSINFMAC 0200000000AB SERIAL ABC123456789",
        "SSINFIP6 fe80::1c2b:3a4d:5e6f:7081",
        "NSE1 Now playing on Living Room of Someone",
    ]
    assert capability_check.anonymise(
        lines, macs=("02:00:00:00:00:ab",), serials=("ABC123456789",)
    ) == [
        "NSFRN Denon AVR",
        "SSINFIPA 192.0.2.10 MAC aa:bb:cc:dd:ee:ff owner user@example.com",
        "SSINFMAC aa:bb:cc:dd:ee:ff SERIAL SERIAL0000000",
        "SSINFIP6 2001:db8::1",
        "NSE1 Now playing on Denon AVR",
    ]
