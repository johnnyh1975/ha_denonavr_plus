"""Tests for button.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.transport import WebConfigClient
from custom_components.denonavr_plus.button import DenonAvrQuickSelectSave
from custom_components.denonavr_plus.const import DOMAIN

from .common import async_enable, entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

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


@pytest.mark.assumed(
    "CVZRL is documented only in the 2015 X-series document and NSRBT only in the "
    "denonavr library; pressing both on a receiver would settle them"
)
async def test_command_buttons_send_their_commands(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await async_enable(hass, loaded_entry, "button_channel_level_reset", "button_network_restart")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    for key in ("button_channel_level_reset", "button_network_restart"):
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id(hass, loaded_entry, key)}, blocking=True
        )
    assert telnet.sent == ["CVZRL", "NSRBT"]


@pytest.mark.assumed(
    "MSQUICK<n> MEMORY comes from the denonavr library; saving a preset on a "
    "receiver would settle it"
)
async def test_a_quick_select_preset_can_be_saved(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    coordinator = loaded_entry.runtime_data
    coordinator.device.discovery.quick_select_names[2] = "Movie night"
    button = DenonAvrQuickSelectSave(coordinator, 2)
    assert button.translation_placeholders == {"name": "Movie night"}
    assert button.entity_registry_enabled_default is False
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await button.async_press()
    assert telnet.sent == ["MSQUICK2 MEMORY"]
