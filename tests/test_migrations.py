"""Tests for migrations.py: config entries are brought to the current version."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import migrations
from custom_components.denonavr_plus.config_flow import DenonAvrConfigFlow
from custom_components.denonavr_plus.const import (
    CONFIG_MINOR_VERSION,
    CONFIG_VERSION,
    DOMAIN,
)

from .fakes import HOST


async def _set_up(hass: HomeAssistant, version: int, minor: int, **options: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        options=options,
        version=version,
        minor_version=minor,
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def test_the_flow_creates_the_current_version() -> None:
    assert (DenonAvrConfigFlow.VERSION, DenonAvrConfigFlow.MINOR_VERSION) == (
        CONFIG_VERSION,
        CONFIG_MINOR_VERSION,
    )


def test_every_step_leads_to_the_current_version() -> None:
    # Following the steps from any registered version must end at the current
    # one; a gap would strand entries of that version.
    current = (CONFIG_VERSION, CONFIG_MINOR_VERSION)
    for start in migrations.STEPS:
        assert start < current, start


async def test_a_current_entry_loads_unchanged(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    entry = await _set_up(hass, CONFIG_VERSION, CONFIG_MINOR_VERSION)
    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (CONFIG_VERSION, CONFIG_MINOR_VERSION)


async def test_an_older_entry_runs_its_step(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    async def _add_option(hass: HomeAssistant, entry: Any) -> tuple[int, int]:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "new_option": True}
        )
        return (CONFIG_VERSION, CONFIG_MINOR_VERSION + 1)

    newer = CONFIG_MINOR_VERSION + 1
    with (
        patch.object(migrations, "CONFIG_MINOR_VERSION", newer),
        patch.object(DenonAvrConfigFlow, "MINOR_VERSION", newer),
        patch.dict(migrations.STEPS, {(CONFIG_VERSION, CONFIG_MINOR_VERSION): _add_option}),
    ):
        entry = await _set_up(hass, CONFIG_VERSION, CONFIG_MINOR_VERSION)
    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (CONFIG_VERSION, newer)
    assert entry.options["new_option"] is True


async def test_an_entry_without_a_step_is_not_loaded(
    hass: HomeAssistant, receiver_network: Any, caplog: pytest.LogCaptureFixture
) -> None:
    entry = await _set_up(hass, 1, 0)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert "Cannot migrate" in caplog.text


async def test_an_entry_from_a_newer_minor_version_still_loads(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    # After a downgrade within the same major version.
    entry = await _set_up(hass, CONFIG_VERSION, CONFIG_MINOR_VERSION + 3)
    assert entry.state is ConfigEntryState.LOADED


async def test_an_entry_from_a_newer_major_version_is_refused(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    entry = await _set_up(hass, CONFIG_VERSION + 1, 1)
    assert entry.state is ConfigEntryState.MIGRATION_ERROR


async def test_1_1_removes_the_volume_sensor(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """0.1.0 had a read-only volume sensor; the volume number replaces it."""
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: HOST}, version=1, minor_version=1
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    old = registry.async_get_or_create(
        "sensor", DOMAIN, "aa:bb:cc:dd:ee:ff_sensor_volume_db",
        config_entry=entry, suggested_object_id="avc_x3700h_volume",
    )
    kept = registry.async_get_or_create(
        "sensor", DOMAIN, "aa:bb:cc:dd:ee:ff_sensor_decoder",
        config_entry=entry, suggested_object_id="avc_x3700h_decoder",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (CONFIG_VERSION, CONFIG_MINOR_VERSION)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get(kept.entity_id) is not None


async def test_1_2_stores_the_trims_left_on_as_the_speakers(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Before 1.3 the trims of speakers the receiver lacked were disabled once."""
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        options={"channels_initialized": True},
        version=1,
        minor_version=2,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for code, disabled in (("FL", None), ("C", None), ("SBL", er.RegistryEntryDisabler.INTEGRATION)):
        registry.async_get_or_create(
            "number", DOMAIN, f"aa:bb:cc:dd:ee:ff_number_channel_trim_{code}",
            config_entry=entry, disabled_by=disabled,
        )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (1, CONFIG_MINOR_VERSION)
    assert entry.options["channels_configured"] == ["C", "FL"]


async def test_1_2_without_disabled_trims_stores_nothing(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Never applied (the speakers were never known): the first time stays the first."""
    entry = await _set_up(hass, 1, 2)
    assert (entry.version, entry.minor_version) == (1, CONFIG_MINOR_VERSION)
    assert "channels_configured" not in entry.options


async def test_1_3_moves_the_markers_into_their_store_and_keeps_them_as_options(
    hass: HomeAssistant, receiver_network: Any, hass_storage: dict[str, Any]
) -> None:
    """1.7: the integration's own markers leave the options; 1.6 still finds them there."""
    markers = {
        "channels_initialized": True,
        "channels_configured": ["C", "FL", "FR"],
        "entity_groups_enabled": ["aa:bb:cc:dd:ee:ff_number_bass"],
        "zone2_sources_asked": True,
    }
    entry = await _set_up(hass, 1, 3, sources={"zone2": []}, **markers)
    assert entry.state is ConfigEntryState.LOADED
    assert (entry.version, entry.minor_version) == (1, 4)
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.markers"]["data"]
    assert stored["entity_groups_enabled"] == ["aa:bb:cc:dd:ee:ff_number_bass"]
    assert stored["zone2_sources_asked"] is True
    assert stored["channels_initialized"] is True
    # Kept for going back to 1.6, untouched.
    assert {key: entry.options[key] for key in markers} == markers
    # Read from the store: "every input" for Zone 2 was chosen, it stays.
    assert entry.options["sources"]["zone2"] == []
