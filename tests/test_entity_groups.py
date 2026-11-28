"""Tests for entity_groups.py: hidden entities shown and hidden by group."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import entity_groups
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN



INTEGRATION = er.RegistryEntryDisabler.INTEGRATION
USER = er.RegistryEntryDisabler.USER


def _entry(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> er.RegistryEntry:
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.unique_id.endswith(f"_{key}"):
            return item
    raise AssertionError(f"no entity {key}")


def _disabled(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> Any:
    return _entry(hass, entry, key).disabled_by


async def _choose(hass: HomeAssistant, entry: MockConfigEntry, groups: list[str]) -> Any:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "entities"}
    )
    assert result["step_id"] == "entities"
    # A toggle per group offered; the ones not named are off.
    fields = [str(key) for key in result["data_schema"].schema]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {field: field in groups for field in fields}
    )
    await hass.async_block_till_done()
    return result


async def test_the_groups_of_the_recording(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    counts = entity_groups.hidden_counts(hass, loaded_entry, set())
    # Channel trims, the remote and the network music switches start hidden too.
    assert counts == {
        "audio": 12,
        "speakers": 54,
        "video": 6,
        "graphic_eq": 15,
        "zones": 2,
        "receiver": 13,
        "diagnostic": 7,
    }


async def test_a_group_is_shown_and_hidden_again(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)
    # Hidden by the user: the group leaves it hidden.
    registry.async_update_entity(_entry(hass, loaded_entry, "number_lfe").entity_id, disabled_by=USER)
    # Shown by the user: the group never hides it.
    registry.async_update_entity(
        _entry(hass, loaded_entry, "switch_center_spread").entity_id, disabled_by=None
    )

    result = await _choose(hass, loaded_entry, ["audio"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["entity_groups"] == ["audio"]
    assert _disabled(hass, loaded_entry, "switch_loudness_management") is None
    assert _disabled(hass, loaded_entry, "number_lfe") is USER
    # Other groups stay as they were.
    assert _disabled(hass, loaded_entry, "select_aspect_ratio") is INTEGRATION
    ours = loaded_entry.runtime_data.markers.get("entity_groups_enabled")
    assert len(ours) == 10
    assert not any(uid.endswith(("number_lfe", "switch_center_spread")) for uid in ours)

    # Hidden by hand since: forgotten, not shown again.
    registry.async_update_entity(
        _entry(hass, loaded_entry, "number_effect_level").entity_id, disabled_by=USER
    )
    await _choose(hass, loaded_entry, [])
    assert _disabled(hass, loaded_entry, "switch_loudness_management") is INTEGRATION
    assert _disabled(hass, loaded_entry, "switch_center_spread") is None
    assert _disabled(hass, loaded_entry, "number_effect_level") is USER
    assert loaded_entry.runtime_data.markers.get("entity_groups_enabled") == []


async def test_shown_entities_come_with_the_reload(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _choose(hass, loaded_entry, ["video"])
    item = _entry(hass, loaded_entry, "select_aspect_ratio")
    assert item.disabled_by is None
    assert hass.states.get(item.entity_id) is None
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(item.entity_id) is not None
    # Applied again at the setup of the reload: nothing changes.
    assert loaded_entry.options["entity_groups"] == ["video"]
    assert len(loaded_entry.runtime_data.markers.get("entity_groups_enabled")) == 6


async def test_speakers_the_receiver_does_not_have_stay_hidden(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    # The recording does not report its speakers; as a receiver that does.
    loaded_entry.runtime_data.device.discovery.configured_channels = {"FL", "FR", "C", "SW"}
    await _choose(hass, loaded_entry, ["speakers"])
    assert _disabled(hass, loaded_entry, "number_channel_distance_FL") is None
    assert _disabled(hass, loaded_entry, "number_channel_distance_TFL") is INTEGRATION
    assert _disabled(hass, loaded_entry, "select_front_speaker") is None
    counts = entity_groups.hidden_counts(hass, loaded_entry, {"FL", "FR", "C", "SW"})
    # Four trims, four distances, the three settings the group showed, and the
    # level reset.
    assert counts["speakers"] == 12


async def test_the_groups_apply_at_setup(hass: HomeAssistant, receiver_network: Any) -> None:
    """Also when chosen while the integration was not loaded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10"},
        minor_version=CONFIG_MINOR_VERSION,
        options={"entity_groups": ["diagnostic"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _disabled(hass, entry, "sensor_sample_rate") is None
    assert len(entry.runtime_data.markers.get("entity_groups_enabled")) == 7


async def test_only_groups_with_hidden_entities_are_offered(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    with patch.object(entity_groups, "hidden_counts", return_value={"video": 2}):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "entities"}
        )
    assert [str(key) for key in result["data_schema"].schema] == ["video"]
    assert result["description_placeholders"]["n_video"] == "2"
    assert result["description_placeholders"]["n_audio"] == "0"

    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    with patch.object(entity_groups, "hidden_counts", return_value={}):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "entities"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_hidden_entities"
