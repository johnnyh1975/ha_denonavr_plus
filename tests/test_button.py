"""Tests for button.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.transport import WebConfigClient
from custom_components.denonavr_plus.const import DOMAIN

from .common import async_enable, entity_id
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_rejected_eq_write_is_reported(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await async_enable(hass, loaded_entry, "button_eq_curve_copy")
    button = entity_id(hass, loaded_entry, "button_eq_curve_copy")
    with (
        patch.object(WebConfigClient, "async_set", AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "setup_write_failed"
    assert err.value.translation_placeholders == {"model": "AVC-X3700H"}


async def test_accepted_eq_write_passes(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await async_enable(hass, loaded_entry, "button_eq_curve_copy")
    button = entity_id(hass, loaded_entry, "button_eq_curve_copy")
    write = AsyncMock(return_value=True)
    with patch.object(WebConfigClient, "async_set", write):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    write.assert_awaited_once()
