"""Tests for diagnostics.py."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.denonavr_plus.avr.transport import UpnpClient
from custom_components.denonavr_plus.const import DOMAIN

from .test_init import (  # noqa: F401 -- fixtures
    HOST,
    FakeTelnet,
    loaded_entry,
    receiver_network,
)

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
    assert result["telnet_response_ms"] == 42
    # The setup interface was not read in this test.
    assert result["setup_port"] is None
    assert [zone["id"] for zone in result["zones"]] == ["main", "zone2"]
    assert result["state"]["zones"]["main"]["volume_raw"] == 40.0


SERIAL = "SERIAL0000000"
UPNP_DESCRIPTION = (
    '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
    "<manufacturer>Denon</manufacturer>"
    f"<serialNumber>{SERIAL}</serialNumber>"
    "</device></root>"
)


async def test_diagnostics_contain_no_address_mac_or_serial(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    receiver_network: Any,
) -> None:
    """AGENTS.md rule 6: no address, MAC or serial number anywhere in the dump."""
    with patch.object(
        UpnpClient, "async_get_description", AsyncMock(return_value=UPNP_DESCRIPTION)
    ):
        entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.device.discovery.device.serial_number == SERIAL

    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    dump = json.dumps(result).lower()
    assert result["device"]["serial_number"] == "**REDACTED**"
    for private in (SERIAL, HOST, "0123456789ab", "01:23:45:67:89:ab"):
        assert private.lower() not in dump
