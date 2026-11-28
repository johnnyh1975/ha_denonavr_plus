"""Tests for zone_sources.py: Zone 2's inputs, chosen once after setup."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import choices, zone_sources
from custom_components.denonavr_plus.avr.models import Discovery, SourceDescriptor
from custom_components.denonavr_plus.const import DOMAIN

from .common import entity_id


@pytest.mark.assumed(
    "the inputs with an HDMI connector as receivers of the X series are delivered "
    "(zone_sources.HDMI_INPUTS); the input assignment read from a receiver would settle it"
)
async def test_zone_2_starts_without_the_hdmi_inputs_and_nothing_is_asked(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Before 1.6 a warning repair asked; it is a choice, not a problem."""
    assert not ir.async_get(hass).issues
    chosen = loaded_entry.options[choices.OPT_SOURCES]["zone2"]
    # The recorded receiver does not answer Z2HDA?: its HDMI inputs are left out.
    assert "AUX2" not in chosen and "GAME" not in chosen
    # Named as in the Deviceinfo source list the recording's inputs come from.
    assert not {"CBLSAT", "Blu-ray", "Media Player"} & set(chosen)
    assert "CD" in chosen and "TUNER" in chosen
    assert loaded_entry.runtime_data.markers.get(zone_sources.OPT_ZONE2_SOURCES_ASKED) is True
    zone2 = hass.states.get(entity_id(hass, loaded_entry, "media_player_zone2"))
    assert "CD" in zone2.attributes["source_list"]
    assert "AUX2" not in zone2.attributes["source_list"]
    # Chosen once: a list changed later in the options stays.
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={**loaded_entry.options, choices.OPT_SOURCES: {"zone2": ["CD", "TUNER"]}},
    )
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.options[choices.OPT_SOURCES]["zone2"] == ["CD", "TUNER"]


def test_a_zone_2_with_hdmi_starts_with_every_input() -> None:
    sources = [SourceDescriptor("AUX2", "Apple TV"), SourceDescriptor("CD", "CD")]
    discovery = Discovery(sources=sources)
    assert zone_sources.preselected(discovery) == ["CD"]
    discovery.answered = {zone_sources.HDMI_CONTROL}
    assert zone_sources.preselected(discovery) == ["AUX2", "CD"]


async def test_nothing_is_chosen_for_a_hidden_zone_2_or_one_already_chosen(
    hass: HomeAssistant, receiver_network: None
) -> None:
    from homeassistant.const import CONF_HOST

    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION

    for options in (
        {choices.OPT_SOURCES: {"zone2": ["CD"]}},
        {choices.OPT_ZONES: ["main"]},
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.10"},
            minor_version=CONFIG_MINOR_VERSION,
            options=options,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.runtime_data.markers.get(zone_sources.OPT_ZONE2_SOURCES_ASKED) is None
        assert entry.options.get(choices.OPT_SOURCES, {}).get("zone2") in (None, ["CD"])
        await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


async def test_an_open_question_from_before_goes(
    hass: HomeAssistant, receiver_network: None
) -> None:
    from homeassistant.const import CONF_HOST

    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION
    )
    entry.add_to_hass(hass)
    ir.async_create_issue(
        hass,
        DOMAIN,
        zone_sources.issue_id(entry.entry_id),
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="zone2_sources",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, zone_sources.issue_id(entry.entry_id)) is None
