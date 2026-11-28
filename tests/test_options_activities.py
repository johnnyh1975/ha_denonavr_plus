"""Tests for options_activities.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import config_flow


from .common import entity_id, options_step

HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X3600H"

pytestmark = pytest.mark.usefixtures("flow_without_setup")


def _field(result: Any, name: str) -> Any:
    return next(field for field in result["data_schema"].schema if str(field) == name)


async def _configure(hass: HomeAssistant, result: Any, data: dict[str, Any]) -> Any:
    return await hass.config_entries.options.async_configure(result["flow_id"], data)


async def test_activities_are_added_changed_and_deleted(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await options_step(hass, loaded_entry, "activities")
    # None yet: straight to a new one, name and zone first.
    assert result["step_id"] == "activity"
    zones = result["data_schema"].schema[_field(result, "zone")].config["options"]
    assert zones == ["main", "zone2"]
    assert "delete" not in {str(field) for field in result["data_schema"].schema}
    result = await _configure(hass, result, {"name": "  Film ", "zone": "main"})
    assert result["step_id"] == "activity_details"
    assert result["description_placeholders"] == {"activity": "Film"}
    sources = [
        option["value"]
        for option in result["data_schema"].schema[_field(result, "source")].config["options"]
    ]
    assert "DVD" in sources
    # The recorded receiver has the HDMI output select and the audio delay.
    hdmi = result["data_schema"].schema[_field(result, "hdmi_output")].config["options"]
    assert [option["value"] for option in hdmi] == ["AUTO", "1", "2"]
    delay = result["data_schema"].schema[_field(result, "audio_delay")].config
    assert (delay["min"], delay["max"]) == (0.0, 500.0)
    # Anything that can be switched on, not our players.
    wake = result["data_schema"].schema[_field(result, "wake_entity")].config
    assert wake["domain"] == ["media_player", "remote", "switch"]
    assert entity_id(hass, loaded_entry, "media_player_main") in wake["exclude_entities"]
    result = await _configure(
        hass,
        result,
        {
            "source": "DVD",
            "volume_db": -30.0,
            "audio_delay": 40.0,
            "clarity": "on",
            "wake_player": False,
            "wake_entity": "media_player.tv",
        },
    )
    # Saved at once: closing the dialog later loses nothing.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["activities"] == [
        {
            "name": "Film",
            "zone": "main",
            "source": "DVD",
            "volume_db": -30.0,
            "clarity": "on",
            "audio_delay": 40,
            "wake_entity": "media_player.tv",
        }
    ]

    # With one: new, or change one.
    result = await options_step(hass, loaded_entry, "activities")
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["activity_new", "activity_change"]
    assert result["description_placeholders"] == {"count": "1"}
    result = await _configure(hass, result, {"next_step_id": "activity_new"})
    result = await _configure(hass, result, {"name": "film", "zone": "zone2"})
    assert result["errors"] == {"base": "duplicate_name"}
    result = await _configure(hass, result, {"name": "   ", "zone": "zone2"})
    assert result["errors"] == {"base": "activity_incomplete"}
    result = await _configure(hass, result, {"name": "Music", "zone": "zone2"})
    # Zone 2 is asked only what it takes.
    assert {str(field) for field in result["data_schema"].schema} == {
        "source", "volume_db", "wake_player", "wake_entity"
    }
    result = await _configure(hass, result, {"source": "CD", "wake_player": False})
    assert loaded_entry.options["activities"][1] == {"name": "Music", "zone": "zone2", "source": "CD"}

    # Changing one keeps its values; delete removes it.
    result = await options_step(hass, loaded_entry, "activities")
    result = await _configure(hass, result, {"next_step_id": "activity_change"})
    assert result["data_schema"].schema[_field(result, "activity")].config["options"] == [
        "Film",
        "Music",
    ]
    result = await _configure(hass, result, {"activity": "Film"})
    assert _field(result, "zone").default() == "main"
    assert _field(result, "delete").default() is False
    result = await _configure(hass, result, {"name": "Film", "zone": "main", "delete": False})
    assert _field(result, "source").default() == "DVD"
    result = await _configure(
        hass, result, {"source": "DVD", "volume_db": -20.0, "clarity": "keep", "wake_player": False}
    )
    assert loaded_entry.options["activities"][0]["volume_db"] == -20.0
    result = await options_step(hass, loaded_entry, "activities")
    result = await _configure(hass, result, {"next_step_id": "activity_change"})
    result = await _configure(hass, result, {"activity": "Music"})
    result = await _configure(hass, result, {"name": "Music", "zone": "zone2", "delete": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [item["name"] for item in loaded_entry.options["activities"]] == ["Film"]


async def test_the_activity_steps_need_the_receiver(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    with patch.object(config_flow.DenonAvrOptionsFlow, "_discovery", return_value=None):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "activities"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connected"


async def test_an_activity_of_a_hidden_zone_can_be_deleted(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone 2 hidden since: its activity keeps its zone in the form."""
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "activities": [
                {"name": "Film", "zone": "main", "source": "DVD"},
                {"name": "Music", "zone": "zone2", "source": "CD"},
            ],
            "zones": [],
        },
    )
    await hass.async_block_till_done()
    result = await options_step(hass, loaded_entry, "activities")
    result = await _configure(hass, result, {"next_step_id": "activity_change"})
    result = await _configure(hass, result, {"activity": "Music"})
    assert _field(result, "zone").default() == "zone2"
    result = await _configure(hass, result, {"name": "Music", "zone": "zone2", "delete": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [a["name"] for a in loaded_entry.options["activities"]] == ["Film"]


async def test_an_activity_whose_input_left_the_list_can_be_changed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone 2's list without the HDMI inputs: its activity on BD still saves."""
    assert "BD" not in loaded_entry.options["sources"]["zone2"]
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={**loaded_entry.options, "activities": [{"name": "TV", "zone": "zone2", "source": "BD"}]},
    )
    await hass.async_block_till_done()
    result = await options_step(hass, loaded_entry, "activities")
    result = await _configure(hass, result, {"next_step_id": "activity_change"})
    result = await _configure(hass, result, {"activity": "TV"})
    result = await _configure(hass, result, {"name": "TV", "zone": "zone2", "delete": False})
    assert _field(result, "source").default() == "BD"
    result = await _configure(hass, result, {"source": "BD", "volume_db": -30.0, "wake_player": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["activities"][0]["source"] == "BD"
