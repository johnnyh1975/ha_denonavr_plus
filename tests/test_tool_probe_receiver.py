"""Tests for tools/probe_receiver.py: it only reads, and it hides private data."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "probe_receiver.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("probe_receiver", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load()


def test_every_telnet_command_is_a_query() -> None:
    for query in probe.CATALOGUE:
        assert query.command.endswith("?") or query.command in probe.READ_ONLY_REQUESTS
    assert all(command.endswith("?") for command in probe.BURST)


def test_the_integration_forms_are_asked() -> None:
    commands = {query.command for query in probe.CATALOGUE}
    # Denon AVR Plus sends the sleep timer with a space; both forms are asked.
    assert {"SLP?", "SLP ?"} <= commands
    assert {"SSTTR ?", "BTTX ?", "PSIMAXAUD ?"} <= commands


def test_only_get_appcommands_are_built() -> None:
    body = probe.appcommand_body(["GetAllZoneSource", "GetRenameSource"])
    assert b"<cmd id=\"1\">GetAllZoneSource</cmd>" in body
    with pytest.raises(ValueError):
        probe.appcommand_body(["SetToneControl"])
    with pytest.raises(ValueError):
        probe.appcommand0300_body("SetAudyssey", ["dynamiceq"])


def test_heos_asks_only_for_information() -> None:
    for command in probe.HEOS_PER_PLAYER + probe.HEOS_GLOBAL:
        name = command.split("/")[1]
        assert name.startswith(("get_", "check_")) or name == "heart_beat", command


def test_private_data_is_replaced() -> None:
    host = "192.0.2.77"
    heos = {
        "player/get_players": {"payload": [
            {"name": "Living Room Cinema", "ip": host, "serial": "BBK29190825114"},
            {"name": "Kitchen", "ip": "192.0.2.31", "serial": "ACJ0000000123"},
        ]},
    }
    texts = [json.dumps(heos)]
    macs, serials, names, _ = probe.find_identity(texts, host)
    raw = json.dumps(heos) + (
        "<MacWifi>02-00-00-00-00-7a</MacWifi><SSID>Home Wifi</SSID>"
        '{"service_username": "someone@mail.invalid"} fe80::1a2b:3c4d:5e6f:7a8b'
        f" NSFRN Living Room Cinema {host}"
    )
    text = probe.anonymise(raw, host, macs, serials, names)
    for private in ("Living Room Cinema", "Kitchen", "BBK29190825114", "ACJ0000000123",
                    "02-00-00-00-00-7a", "Home Wifi", "someone@", "fe80::1a2b", host):
        assert private not in text, private
    assert "HEOS speaker 2" in text
    assert probe.PLACEHOLDER_WIFI in text


def test_the_setup_scan_only_reads() -> None:
    # Every page of the scan and of the fixed list is a get_config request.
    assert all("/get_config?" in page for page in probe.SETUP_PAGES)
    assert list(probe.SETUP_TYPES) == list(range(1, 21))


def test_zone_states_come_from_the_power_answers() -> None:
    telnet = {
        "ZM?": {"lines": ["ZMON"]},
        "Z2?": {"lines": ["Z2OFF", "Z2TUNER", "Z250"]},
        "Z3?": {"lines": ["?"]},
    }
    assert probe.zone_states(telnet) == {"main": "on", "zone 2": "off"}
