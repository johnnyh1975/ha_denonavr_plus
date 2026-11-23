"""Tests for sensor.py."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .common import entity_id, registry_entry
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_only_the_decoder_starts_enabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert registry_entry(hass, loaded_entry, "sensor_decoder").disabled_by is None
    # Values that change with every track, and the sound mode the media player
    # already shows, start disabled.
    for key in (
        "sensor_sample_rate",
        "sensor_input_signal",
        "sensor_audio_format",
        "sensor_mode_info",
        "sensor_sound_mode",
    ):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key


async def test_volume_keeps_no_long_term_statistics(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    state = hass.states.get(entity_id(hass, loaded_entry, "sensor_volume_db"))
    assert state is not None
    assert "state_class" not in state.attributes
