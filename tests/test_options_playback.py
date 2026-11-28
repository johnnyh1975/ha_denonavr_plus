"""Tests for options_playback.py: linked players and how playback is shown."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry



from .common import options_step

HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X3600H"

pytestmark = pytest.mark.usefixtures("flow_without_setup")


async def _link_step(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Configure → Playback → Link a player to an input."""
    result = await options_step(hass, entry, "playback")
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["playback_link", "playback_display"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "playback_link"}
    )


async def _input(hass: HomeAssistant, entry: MockConfigEntry, code: str) -> Any:
    """The linked player form of one input."""
    result = await _link_step(hass, entry)
    return await hass.config_entries.options.async_configure(result["flow_id"], {"input": code})


async def test_a_player_is_linked_to_an_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.apple_tv", "idle", {"friendly_name": "Apple TV"})
    result = await _link_step(hass, loaded_entry)
    assert result["step_id"] == "playback_link"
    inputs = next(field for field in result["data_schema"].schema if str(field) == "input")
    offered = [
        option["value"] for option in result["data_schema"].schema[inputs].config["options"]
    ]
    # Not the tuner; the network input for Music Assistant's player.
    assert "DVD" in offered and "NETWORK" in offered and "TUNER" not in offered
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"input": "DVD"})
    assert result["step_id"] == "linked_input"
    # The receiver's own players are not offered.
    player = next(field for field in result["data_schema"].schema if str(field) == "player")
    own = entity_id_of_main(hass, loaded_entry)
    assert own in result["data_schema"].schema[player].config["exclude_entities"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"player": "media_player.apple_tv", "follow": True, "full_volume": False, "power": True},
    )
    # Saved at once: closing a dialog loses nothing done before it.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["linked_players"] == {
        "DVD": {"player": "media_player.apple_tv", "follow": True, "full_volume": False, "power": True},
    }
    result = await _link_step(hass, loaded_entry)
    inputs = next(field for field in result["data_schema"].schema if str(field) == "input")
    labels = {
        option["value"]: option["label"]
        for option in result["data_schema"].schema[inputs].config["options"]
    }
    assert labels["DVD"].endswith("→ Apple TV")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"input": "CD"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"player": "media_player.streamer", "follow": False, "full_volume": True, "power": False},
    )
    assert loaded_entry.options["linked_players"] == {
        "DVD": {"player": "media_player.apple_tv", "follow": True, "full_volume": False, "power": True},
        "CD": {"player": "media_player.streamer", "follow": False, "full_volume": True, "power": False},
    }

    # Clearing the player unlinks the input; its switches have no meaning then.
    result = await _input(hass, loaded_entry, "DVD")
    player = next(field for field in result["data_schema"].schema if str(field) == "player")
    assert player.description == {"suggested_value": "media_player.apple_tv"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"follow": True, "full_volume": False}
    )
    assert loaded_entry.options["linked_players"] == {
        "CD": {"player": "media_player.streamer", "follow": False, "full_volume": True, "power": False}
    }


async def test_how_playback_is_shown_is_its_own_step(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await options_step(hass, loaded_entry, "playback")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "playback_display"}
    )
    fields = {str(field): field for field in result["data_schema"].schema}
    # Pausing the linked player on turn off is on unless turned off.
    assert fields["pause_on_off"].default() is True
    assert fields["signal_state"].default() is False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal_state": True, "pause_on_off": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["signal_state"] is True
    assert loaded_entry.options["pause_on_off"] is False


async def test_a_player_of_the_receiver_cannot_be_linked(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _input(hass, loaded_entry, "DVD")
    with pytest.raises(InvalidData):
        # The selector already refuses it.
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"player": entity_id_of_main(hass, loaded_entry), "follow": False, "full_volume": False},
        )


def entity_id_of_main(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    from homeassistant.helpers import entity_registry as er

    return next(
        item.entity_id
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.unique_id.endswith("_media_player_main")
    )


async def test_a_rename_during_the_playback_dialog_is_kept(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Saving writes the input edited in the dialog, not an old copy of the rest."""
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "linked_players": {"CD": {"player": "media_player.streamer"}},
        },
    )
    result = await _input(hass, loaded_entry, "DVD")
    # The streamer is renamed meanwhile (linked_players.py rewrites the options).
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "linked_players": {
                "CD": {"player": "media_player.kitchen", "follow": False, "full_volume": False, "power": False}
            },
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"player": "media_player.apple_tv", "follow": False, "full_volume": False, "power": False}
    )
    assert loaded_entry.options["linked_players"] == {
        "CD": {"player": "media_player.kitchen", "follow": False, "full_volume": False, "power": False},
        "DVD": {"player": "media_player.apple_tv", "follow": False, "full_volume": False, "power": False},
    }


async def test_the_network_input_has_no_full_volume(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Music Assistant's player may have the receiver itself as its volume."""
    result = await _input(hass, loaded_entry, "NETWORK")
    assert result["step_id"] == "linked_input"
    assert [str(field) for field in result["data_schema"].schema] == ["player", "follow", "power"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"player": "media_player.music_assistant", "follow": True}
    )
    assert loaded_entry.options["linked_players"] == {
        "NETWORK": {"player": "media_player.music_assistant", "follow": True, "full_volume": False, "power": False}
    }


@pytest.mark.assumed(
    "Music Assistant's Home Assistant MediaPlayers provider names its player by "
    "the entity id it wraps, which its Home Assistant entity takes as unique id "
    "(the integration says 'unique_id is the MA player_id'); a Music Assistant "
    "with that provider would settle it"
)
async def test_music_assistants_player_made_of_this_receiver_is_not_offered(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Linked to the network input it would send play back and forth."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    own = entity_id_of_main(hass, loaded_entry)
    wrapped = registry.async_get_or_create("media_player", "music_assistant", own)
    other = registry.async_get_or_create("media_player", "music_assistant", "a1b2c3d4e5f6")
    result = await _input(hass, loaded_entry, "NETWORK")
    player = next(field for field in result["data_schema"].schema if str(field) == "player")
    excluded = result["data_schema"].schema[player].config["exclude_entities"]
    assert own in excluded and wrapped.entity_id in excluded
    assert other.entity_id not in excluded


async def test_a_player_named_like_the_input_is_suggested(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An input renamed after its device; Music Assistant's player of the receiver."""
    from homeassistant.helpers import entity_registry as er

    hass.states.async_set("media_player.living_room_apple_tv", "idle", {"friendly_name": "Living Room Apple TV"})
    hass.states.async_set("media_player.kitchen", "idle", {"friendly_name": "Kitchen"})
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "source_names": {"DVD": "Apple TV"}}
    )
    await hass.async_block_till_done()

    async def suggested(code: str) -> Any:
        result = await _input(hass, loaded_entry, code)
        player = next(field for field in result["data_schema"].schema if str(field) == "player")
        return player.description["suggested_value"]

    assert await suggested("DVD") == "media_player.living_room_apple_tv"
    # A default name says nothing about the device.
    assert await suggested("CD") is None

    registry = er.async_get(hass)
    model = loaded_entry.runtime_data.device.discovery.device.model_name
    ma = registry.async_get_or_create("media_player", "music_assistant", "ma_receiver", suggested_object_id="ma_receiver")
    hass.states.async_set(ma.entity_id, "idle", {"friendly_name": f"Denon {model}"})
    assert await suggested("NETWORK") == ma.entity_id
    # Two that fit: no suggestion.
    other = registry.async_get_or_create("media_player", "music_assistant", "ma_other", suggested_object_id="ma_other")
    hass.states.async_set(other.entity_id, "idle", {"friendly_name": f"{model} (AirPlay)"})
    assert await suggested("NETWORK") is None
