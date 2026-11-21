"""Tests for diagnostics.py."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_diagnostics_redact_the_mac(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    loaded_entry: MockConfigEntry,
) -> None:
    FakeTelnet.instances[-1].push("PWON", "ZMON", "MV40")
    result: dict[str, Any] = await get_diagnostics_for_config_entry(
        hass, hass_client, loaded_entry
    )
    assert result["device"]["model_name"] == "AVC-X3700H"
    assert result["device"]["mac_address"] == "**REDACTED**"
    assert result["available"] is True
    assert [zone["id"] for zone in result["zones"]] == ["main", "zone2"]
    assert result["state"]["zones"]["main"]["volume_raw"] == 40.0
