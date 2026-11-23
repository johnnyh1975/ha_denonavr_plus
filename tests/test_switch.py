"""Tests for switch.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import registry_entry
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_switches_that_repeat_the_media_player_start_disabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for key in ("switch_dynamic_eq", "switch_cinema_eq", "switch_all_zone_stereo"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is None, key
    # Power and mute are on the media player; the rest is set once.
    for key in ("switch_main_power", "switch_main_mute", "switch_auto_lip_sync"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key
