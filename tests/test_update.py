"""Tests for update.py."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.device import DenonAvrDevice
from custom_components.denonavr_plus.avr.transport import UpnpClient
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .common import entity_id
from .fakes import HOST, FakeHeos

#: The receiver's UPnP description, with the firmware it runs.
_DESCRIPTION = (
    '<root xmlns="urn:schemas-upnp-org:device-1-0" xmlns:d="urn:x-denon">'
    "<device><manufacturer>Denon</manufacturer>"
    "<d:firmware_version>3.139.175</d:firmware_version>"
    "</device></root>"
)


@pytest.fixture
async def described_entry(
    hass: HomeAssistant, receiver_network: Any
) -> AsyncIterator[MockConfigEntry]:
    with patch.object(UpnpClient, "async_get_description", AsyncMock(return_value=_DESCRIPTION)):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


@pytest.mark.assumed(
    "player/check_update and its update_exist / update_none answer follow the HEOS "
    "CLI specification; an AVR-X2600H with an update pending would settle it"
)
async def test_the_firmware_entity_shows_an_update_without_inventing_a_version(
    hass: HomeAssistant, described_entry: MockConfigEntry
) -> None:
    coordinator = described_entry.runtime_data
    installed = "3.139.175"
    update = entity_id(hass, described_entry, "update_firmware")
    heos = FakeHeos.instances[-1]

    heos.update = False
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(update)
    assert state is not None and state.state == STATE_OFF
    assert state.attributes["installed_version"] == installed
    assert state.attributes["latest_version"] == installed

    heos.update = True
    with patch(
        "custom_components.denonavr_plus.avr.device.FIRMWARE_CHECK_INTERVAL", 0
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    state = hass.states.get(update)
    assert state.state == STATE_ON
    assert state.attributes["latest_version"] == "Newer version"
    # Nothing to install from Home Assistant.
    assert state.attributes["supported_features"] == 0


async def test_the_firmware_entity_is_unknown_until_heos_answers(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    update = entity_id(hass, loaded_entry, "update_firmware")
    with patch.object(
        DenonAvrDevice, "firmware_update", property(lambda device: None)
    ):
        loaded_entry.runtime_data.async_update_listeners()
        await hass.async_block_till_done()
        assert hass.states.get(update).state == STATE_UNKNOWN
