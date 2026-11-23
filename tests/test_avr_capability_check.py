"""Tests for avr/capability_check.py: the queries match the probe script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from custom_components.denonavr_plus.avr import capability_check

PROBE = Path(__file__).resolve().parent.parent / "tools" / "probe_receiver.py"


def _probe_catalogue() -> list[tuple[str, str]]:
    spec = importlib.util.spec_from_file_location("probe_receiver_for_check", PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return [(q.command, q.group) for q in module.CATALOGUE]


def test_the_check_asks_what_the_probe_asks() -> None:
    """The guard: change one list and this fails until the other follows."""
    probe = [item for item in _probe_catalogue() if item[0] not in ("NSE", "NSA", "NSH")]
    assert list(capability_check.QUERIES) == probe


def test_only_queries() -> None:
    assert all(command.endswith("?") for command, _ in capability_check.QUERIES)


def test_classify() -> None:
    classify = capability_check.classify
    assert classify("MV?", ["MV50", "MVMAX 80"]) == "answered"
    assert classify("PSFOO ?", ["?"]) == "rejected"
    assert classify("PSFOO ?", ["PWON"]) == "other lines only"
    assert classify("PSFOO ?", []) == "no reply"


def test_addresses_are_replaced() -> None:
    line = "SSINFIPA 198.51.100.7 MAC 02:00:00:00:00:7A owner someone@example.org"
    assert capability_check.anonymise(line) == (
        "SSINFIPA 192.0.2.10 MAC aa:bb:cc:dd:ee:ff owner user@example.com"
    )
