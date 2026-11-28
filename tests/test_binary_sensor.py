"""Tests for binary_sensor.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import async_enable, entity_id
from .fakes import FakeHeos


@pytest.mark.assumed(
    "player/check_update and its update_exist / update_none answer follow the HEOS "
    "CLI specification; an AVR-X2600H with an update pending would settle it"
)
async def test_the_firmware_sensor_asks_heos_twice_a_day(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await async_enable(hass, loaded_entry, "binary_sensor_firmware_update")
    coordinator = loaded_entry.runtime_data
    sensor = entity_id(hass, loaded_entry, "binary_sensor_firmware_update")
    heos = FakeHeos.instances[-1]
    heos.update = True
    check = AsyncMock(wraps=heos.async_check_update)
    with patch.object(heos, "async_check_update", check):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(sensor).state == "on"
        # Asked again only after 12 hours.
        await coordinator.async_refresh()
    assert check.await_count == 1
