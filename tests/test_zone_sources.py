"""Tests for zone_sources.py: Zone 2's inputs, asked once as a repair."""

from __future__ import annotations

import pytest
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import choices, zone_sources
from custom_components.denonavr_plus.avr.models import Discovery, SourceDescriptor
from custom_components.denonavr_plus.const import DOMAIN

from .common import entity_id
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


def _issue(hass: HomeAssistant, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, zone_sources.issue_id(entry.entry_id))


@pytest.mark.assumed(
    "the inputs with an HDMI connector as receivers of the X series are delivered "
    "(zone_sources.HDMI_INPUTS); the input assignment read from a receiver would settle it"
)
async def test_zone_2_inputs_are_asked_once_without_hdmi(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    assert _issue(hass, loaded_entry) is not None

    result = await manager.async_init(
        DOMAIN, data={"issue_id": zone_sources.issue_id(loaded_entry.entry_id)}
    )
    assert result["type"] is FlowResultType.FORM
    # The recorded receiver does not answer Z2HDA?: its HDMI inputs start deselected.
    default = next(iter(result["data_schema"].schema)).default()
    assert "AUX2" not in default and "GAME" not in default
    assert "CD" in default and "TUNER" in default

    result = await manager.async_configure(result["flow_id"], {"sources": ["CD", "TUNER"]})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options[choices.OPT_SOURCES]["zone2"] == ["CD", "TUNER"]
    assert loaded_entry.options[zone_sources.OPT_ZONE2_SOURCES_ASKED] is True
    assert _issue(hass, loaded_entry) is None
    zone2 = hass.states.get(entity_id(hass, loaded_entry, "media_player_zone2"))
    assert zone2.attributes["source_list"] == ["CD", "Tuner"]

    # Asked once: not again after a reload.
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, loaded_entry) is None


async def test_every_input_chosen_means_all(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    every = [s.code for s in loaded_entry.runtime_data.device.discovery.visible_sources()]
    result = await manager.async_init(
        DOMAIN, data={"issue_id": zone_sources.issue_id(loaded_entry.entry_id)}
    )
    await manager.async_configure(result["flow_id"], {"sources": every})
    await hass.async_block_till_done()
    assert loaded_entry.options[choices.OPT_SOURCES]["zone2"] == []


def test_a_zone_2_with_hdmi_starts_with_every_input() -> None:
    sources = [SourceDescriptor("AUX2", "Apple TV"), SourceDescriptor("CD", "CD")]
    discovery = Discovery(sources=sources)
    assert zone_sources.preselected(discovery) == ["CD"]
    discovery.answered = {zone_sources.HDMI_CONTROL}
    assert zone_sources.preselected(discovery) == ["AUX2", "CD"]


async def test_no_question_when_zone_2_is_hidden_or_its_inputs_are_chosen(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert _issue(hass, loaded_entry) is not None
    hass.config_entries.async_update_entry(
        loaded_entry, options={choices.OPT_SOURCES: {"zone2": ["CD"]}}
    )
    await hass.async_block_till_done()
    assert _issue(hass, loaded_entry) is None
    hass.config_entries.async_update_entry(loaded_entry, options={choices.OPT_ZONES: []})
    await hass.async_block_till_done()
    assert _issue(hass, loaded_entry) is None


async def test_the_repair_without_a_loaded_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    result = await manager.async_init(
        DOMAIN, data={"issue_id": zone_sources.issue_id(loaded_entry.entry_id)}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"


async def test_removing_the_entry_takes_the_question_away(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert _issue(hass, loaded_entry) is not None
    await hass.config_entries.async_remove(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, loaded_entry) is None
