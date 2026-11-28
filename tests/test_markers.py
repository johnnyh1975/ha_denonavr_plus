"""Tests for markers.py: the integration's own bookkeeping, apart from the options."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import markers
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .fakes import HOST


async def test_a_marker_change_does_not_touch_the_options(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Before 1.7 the speakers followed were an option: every change ran the options listener."""
    from custom_components.denonavr_plus import _apply_channel_defaults

    coordinator = loaded_entry.runtime_data
    coordinator.device.discovery.configured_channels = {"FL", "FR", "C"}
    with patch.object(hass.config_entries, "async_update_entry") as update:
        _apply_channel_defaults(hass, loaded_entry, coordinator)
        # The receiver reports a speaker less: the defaults follow.
        coordinator.device.discovery.configured_channels = {"FL", "FR"}
        _apply_channel_defaults(hass, loaded_entry, coordinator)
    update.assert_not_called()
    assert coordinator.markers.get(markers.CHANNELS_APPLIED) == ["FL", "FR"]


async def test_markers_outlast_a_reload(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> None:
    loaded_entry.runtime_data.markers.set(markers.ZONE2_SOURCES_ASKED, "kept")
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data.markers.get(markers.ZONE2_SOURCES_ASKED) == "kept"


async def test_the_setup_flow_hands_over_through_the_options(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Decision E3: the flow has no entry id to store under; the first start takes it over."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        minor_version=CONFIG_MINOR_VERSION,
        options={markers.MOVE_PENDING: ["other"], "volume_step": 1.0},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.denonavr_plus.move.async_run_pending"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.markers.get(markers.MOVE_PENDING) == ["other"]
    assert markers.MOVE_PENDING not in entry.options
    assert entry.options["volume_step"] == 1.0
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_options_are_read_when_they_are_written(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A change made between reading and writing elsewhere is not lost."""
    before = dict(loaded_entry.options)
    markers.update_options(hass, loaded_entry, changes={"volume_step": 2.0})
    markers.update_options(hass, loaded_entry, changes={"heos": False})
    assert loaded_entry.options == {**before, "volume_step": 2.0, "heos": False}
    markers.update_options(hass, loaded_entry, remove=("heos",))
    assert "heos" not in loaded_entry.options
    with patch.object(hass.config_entries, "async_update_entry") as update:
        markers.update_options(hass, loaded_entry, changes={"volume_step": 2.0})
    update.assert_not_called()


async def test_the_markers_go_with_the_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, hass_storage: dict[str, Any]
) -> None:
    key = f"{DOMAIN}.{loaded_entry.entry_id}.markers"
    loaded_entry.runtime_data.markers.set(markers.ZONE2_SOURCES_ASKED, True)
    loaded_entry.runtime_data.markers.remove("not there")
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    assert hass_storage[key]["data"][markers.ZONE2_SOURCES_ASKED] is True
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
