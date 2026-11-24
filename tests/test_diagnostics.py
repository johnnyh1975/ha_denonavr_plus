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
    FakeHeos,
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
    assert result["started_offline"] is False
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


async def test_diagnostics_show_how_media_reaches_the_receiver(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    loaded_entry: MockConfigEntry,
) -> None:
    """Which now-playing fields are filled and how HEOS answered, not what plays."""
    heos = FakeHeos.instances[-1]
    heos.connected = True
    heos.command_results = {"browse/play_stream": {"result": "success"}}
    heos.on_update(
        {
            "state": "play",
            "media_type": "station",
            "title": "Private Title",
            "artist": "Someone",
            "album": None,
            "image_url": f"http://{HOST}/art.jpg",
            "position": 3.0,
            "duration": None,
            "position_at": 1000.0,
            "repeat": "off",
            "shuffle": False,
        }
    )
    result = await get_diagnostics_for_config_entry(hass, hass_client, loaded_entry)

    media = result["media"]
    assert media["heos_connected"] is True
    assert media["telnet_media"] is False
    assert media["can_play_url"] is True
    assert media["heos_command_results"] == {"browse/play_stream": {"result": "success"}}
    assert media["now_playing"] == {
        "state": "play",
        "media_type": "station",
        "repeat": "off",
        "shuffle": False,
        "duration": None,
        "has_position": True,
        "has": ["title", "artist", "image_url"],
    }
    dump = json.dumps(result)
    assert "Private Title" not in dump and HOST not in dump


async def test_diagnostics_hold_the_telnet_lines_and_refused_sources(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    loaded_entry: MockConfigEntry,
) -> None:
    """What went over telnet explains a result the state alone does not."""
    FakeTelnet.instances[-1].log = ["12:00:00.000 > MS?", "12:00:00.060 < MSSTEREO"]
    result = await get_diagnostics_for_config_entry(hass, hass_client, loaded_entry)
    assert result["communication_log"] == ["12:00:00.000 > MS?", "12:00:00.060 < MSSTEREO"]
    assert result["refused_sources"] == {"main": [], "zone2": []}
