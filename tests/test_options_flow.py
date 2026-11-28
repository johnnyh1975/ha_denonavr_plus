"""Tests for options_flow.py: the menu and the steps for volume, zones,
connection, entities, sources and sound modes."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import move
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN


from custom_components.denonavr_plus.options_flow import DenonAvrOptionsFlow

from .common import options_step

HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X3600H"

pytestmark = pytest.mark.usefixtures("flow_without_setup")


async def test_options_keep_the_markers_they_do_not_show(hass: HomeAssistant) -> None:
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION,
        options={"channels_initialized": True},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for zone in ("main", "zone2"):
        registry.async_get_or_create(
            "media_player", DOMAIN, f"aa:bb:cc:dd:ee:ff_media_player_{zone}", config_entry=entry
        )

    result = await options_step(hass, entry, "volume")
    assert result["type"] is FlowResultType.FORM
    # One section per zone of the receiver, read from the registry.
    fields = [str(key) for key in result["data_schema"].schema]
    assert fields == ["official_volume_scale", "main", "zone2"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"official_volume_scale": True, "main": {"max_volume": -10.0}, "zone2": {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "channels_initialized": True,
        "official_volume_scale": True,
        "main": {"max_volume": -10.0},
        "zone2": {},
    }


async def test_options_refuse_a_turn_on_volume_above_the_maximum(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.10"})
    entry.add_to_hass(hass)
    result = await options_step(hass, entry, "volume")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"official_volume_scale": False, "main": {"max_volume": -30.0, "turn_on_volume": -20.0}},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "turn_on_above_max"}


async def test_steps_that_need_the_receiver_are_offered_only_with_it(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A menu entry that only ends in "not connected" is not offered."""
    from .fakes import FakeTelnet

    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["menu_options"] == [
        "volume", "zones", "sources", "playback", "activities", "connection", "entities", "tests_start"
    ]
    hass.config_entries.options.async_abort(result["flow_id"])
    FakeTelnet.instances[-1].set_available(False)
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.11"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["volume", "zones", "connection"]
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert "tests_start" not in result["menu_options"]


async def test_a_step_opened_before_the_receiver_went_away_says_so(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for step in ("sources", "playback"):
        result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
        with patch.object(DenonAvrOptionsFlow, "_discovery", return_value=None):
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"next_step_id": step}
            )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "not_connected"


async def test_sources_are_chosen_per_zone_and_renamed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await options_step(hass, loaded_entry, "sources")
    assert result["step_id"] == "sources"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"main": {"sources": ["DVD", "TUNER"]}, "zone2": {"sources": ["TUNER"]}},
    )
    assert result["step_id"] == "source_names"
    # Only the chosen sources are offered for renaming.
    assert [str(key) for key in result["data_schema"].schema] == ["DVD", "TUNER"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"DVD": "Player", "TUNER": "Tuner"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["sources"] == {"main": ["DVD", "TUNER"], "zone2": ["TUNER"]}
    # An unchanged name is not stored.
    assert loaded_entry.options["source_names"] == {"DVD": "Player"}


async def test_choosing_every_source_means_all(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    every = [s.code for s in loaded_entry.runtime_data.device.discovery.visible_sources()]
    result = await options_step(hass, loaded_entry, "sources")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {"sources": every}, "zone2": {"sources": every}}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert loaded_entry.options["sources"] == {"main": [], "zone2": []}


async def test_every_input_chosen_for_zone_2_stays_chosen(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Chosen here while Zone 2 was never preselected (as from the snapshot).

    "Every input" is stored as an empty list; the preselection at the next
    start must not take it for "never chosen".
    """
    from custom_components.denonavr_plus import zone_sources

    loaded_entry.runtime_data.markers.remove(zone_sources.OPT_ZONE2_SOURCES_ASKED)
    every = [s.code for s in loaded_entry.runtime_data.device.discovery.visible_sources()]
    result = await options_step(hass, loaded_entry, "sources")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {"sources": every}, "zone2": {"sources": every}}
    )
    await hass.config_entries.options.async_configure(result["flow_id"], {})
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.options["sources"]["zone2"] == []


async def test_two_sources_cannot_share_a_name(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await options_step(hass, loaded_entry, "sources")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {"sources": ["DVD", "CD"]}, "zone2": {"sources": []}}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"DVD": "Disc", "CD": "Disc"}
    )
    assert result["errors"] == {"base": "duplicate_name"}


async def test_two_inputs_the_receiver_names_alike_do_not_block_saving(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An input named "Tuner" on the receiver, next to the tuner's readable name."""
    discovery = loaded_entry.runtime_data.device.discovery
    for source in discovery.sources:
        if source.code == "CD":
            source.name = discovery.source_name("TUNER") or "Tuner"
    result = await options_step(hass, loaded_entry, "sources")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {"sources": ["CD", "TUNER"]}, "zone2": {"sources": []}}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # A name given here still may not clash.
    result = await options_step(hass, loaded_entry, "sources")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {"sources": ["CD", "DVD"]}, "zone2": {"sources": []}}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"DVD": discovery.source_name("CD")}
    )
    assert result["errors"] == {"base": "duplicate_name"}


async def test_sound_modes_are_chosen_and_renamed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from .fakes import FakeTelnet

    FakeTelnet.instances[-1].push(
        "OPSMLALL MOV071Dolby Surround", "OPSMLALL MUS011Stereo", "OPSMLALL END"
    )
    await asyncio.sleep(0.15)
    result = await options_step(hass, loaded_entry, "sound_modes")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sound_modes": ["Stereo"]}
    )
    assert result["step_id"] == "sound_mode_names"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"Stereo": "Zwei Kanäle"}
    )
    assert loaded_entry.options["sound_modes"] == ["Stereo"]
    assert loaded_entry.options["sound_mode_names"] == {"Stereo": "Zwei Kanäle"}
    # Readable names are on unless switched off.
    assert loaded_entry.options["readable_sound_modes"] is True


async def test_a_stored_source_no_longer_offered_does_not_block_the_step(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The form's defaults were the stored list, and submitting them failed."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "sources": {"main": ["DVD", "GONE"]}}
    )
    await hass.async_block_till_done()
    result = await options_step(hass, loaded_entry, "sources")
    # The form's own defaults, as a user who changes nothing sends them.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {}, "zone2": {}}
    )
    assert result["step_id"] == "source_names"
    assert [str(key) for key in result["data_schema"].schema][0] == "DVD"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Kept for when the receiver offers it again.
    assert loaded_entry.options["sources"]["main"] == ["DVD", "GONE"]


async def test_a_stored_sound_mode_no_longer_reported_does_not_block_the_step(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from .fakes import FakeTelnet

    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "sound_modes": ["Stereo", "Gone"]}
    )
    await hass.async_block_till_done()
    FakeTelnet.instances[-1].push(
        "OPSMLALL MOV071Dolby Surround", "OPSMLALL MUS011Stereo", "OPSMLALL END"
    )
    await asyncio.sleep(0.15)
    result = await options_step(hass, loaded_entry, "sound_modes")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "sound_mode_names"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert loaded_entry.options["sound_modes"] == ["Stereo", "Gone"]


async def test_zones_can_be_switched_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await options_step(hass, loaded_entry, "zones")
    assert result["step_id"] == "zones"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"zones": [], "main_off_standby": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["zones"] == []
    assert loaded_entry.options["main_off_standby"] is True
    assert loaded_entry.options["settings_turn_on"] is False
    # A change of zones reloads the entry; let it finish inside the test, or
    # it runs during teardown without the fake receiver.
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert loaded_entry.runtime_data.shown_zones == frozenset({"main"})


async def test_connection_options_open_without_the_receiver(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.10"})
    entry.add_to_hass(hass)
    result = await options_step(hass, entry, "connection")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "connection"
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema
    }
    assert defaults == {"poll_interval": 60, "heos": True, "setup_refresh": 15}


async def test_connection_options_reload_the_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await options_step(hass, loaded_entry, "connection")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_interval": 120.0, "heos": False, "setup_refresh": 0.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["poll_interval"] == 120
    assert loaded_entry.options["setup_refresh"] == 0
    # A change of the connection reloads the entry; let it finish here.
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    coordinator = loaded_entry.runtime_data
    assert coordinator.connection == {"poll_interval": 120, "heos": False, "setup_refresh": 0}
    assert coordinator.update_interval == timedelta(seconds=120)


async def test_the_move_is_undone_from_the_options(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    old = MockConfigEntry(domain=move.OFFICIAL, data={CONF_HOST: HOST}, title="Wohnzimmer")
    old.add_to_hass(hass)
    await move.async_move(hass, loaded_entry, old)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["menu_options"][-1] == "move_back"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "move_back"}
    )
    assert result["step_id"] == "move_back"
    assert result["description_placeholders"] == {"titles": "Wohnzimmer", "count": "0"}
    with patch.object(move, "async_move_back", AsyncMock()) as back:
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "moved_back")
    back.assert_awaited_once_with(hass, loaded_entry)


async def test_a_receiver_with_one_zone_gets_the_power_settings(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus.avr.models import ZoneDescriptor

    discovery = loaded_entry.runtime_data.device.discovery
    with patch.object(discovery, "zones", [ZoneDescriptor(id="main", index=1)]):
        result = await options_step(hass, loaded_entry, "zones")
        assert [str(key) for key in result["data_schema"].schema] == [
            "main_off_standby",
            "settings_turn_on",
        ]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"main_off_standby": False, "settings_turn_on": True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["settings_turn_on"] is True
    assert "zones" not in loaded_entry.options
