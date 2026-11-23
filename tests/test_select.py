"""Tests for select.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import registry_entry
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_setup_selects_start_disabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for key in ("select_dynamic_volume", "select_dimmer", "select_sound_mode"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is None, key
    for key in (
        "select_volume_limit",
        "select_lpf_for_lfe",
        "select_aspect_ratio",
        "select_eq_channel",
        "select_eq_speaker_selection",
    ):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key
