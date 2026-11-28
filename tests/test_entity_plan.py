"""Tests for entity_plan.py: one plan for what is set up, compared and removed."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import PLATFORMS, choices, entity_plan
from custom_components.denonavr_plus.const import DOMAIN

PACKAGE = Path(__file__).parents[1] / "custom_components" / "denonavr_plus"


def _registered(hass: HomeAssistant, entry: MockConfigEntry) -> set[str]:
    return {
        item.unique_id for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }


def _check_plan(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    coordinator = entry.runtime_data
    options = dict(entry.options)
    unique_ids = set().union(*entity_plan.unique_ids(coordinator, options, list(PLATFORMS)).values())
    # Every platform was set up from its plan, and nothing else was added.
    assert set(coordinator.planned) == set(PLATFORMS)
    assert unique_ids == _registered(hass, entry)
    # Built again from the same receiver, the plan is the same.
    assert entity_plan.plan_keys(coordinator, options, list(PLATFORMS)) == coordinator.planned
    assert entity_plan.changed(coordinator, dict(entry.options)) == []


async def test_the_plan_is_what_the_recorded_receiver_is_set_up_with(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The AVC-X3700H of the entity snapshot."""
    _check_plan(hass, loaded_entry)


async def test_the_plan_is_what_a_web_control_receiver_is_set_up_with(
    hass: HomeAssistant, web_control: Any
) -> None:
    """The setup interface of the recorded AVC-X3900H: inputs, speaker layout."""
    _check_plan(hass, web_control.entry)
    assert any("_select_speaker_layout" in key for key in web_control.entry.runtime_data.planned["select"])


@pytest.mark.parametrize("module", sorted(str(platform) for platform in PLATFORMS))
def test_a_platform_adds_only_its_plan(module: str) -> None:
    """A platform that built entities outside its plan would not be compared after a start."""
    tree = ast.parse((PACKAGE / f"{module}.py").read_text(encoding="utf-8"))
    setup = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_setup_entry"
    )
    calls = [
        node for node in ast.walk(setup) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    added = [call for call in calls if call.func.id == "async_add_entities"]  # type: ignore[attr-defined]
    assert len(added) == 1
    inner = added[0].args[0]
    assert isinstance(inner, ast.Call) and getattr(inner.func, "id", None) == "add_planned"
    assert getattr(inner.args[2].func, "id", None) == "planned"  # type: ignore[attr-defined]
    assert module in entity_plan.PLANS


async def test_entities_a_hidden_zone_leaves_are_removed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Also those on the receiver's own device, which the zone's device did not take along."""
    assert any(key.endswith("_zone2") for key in _registered(hass, loaded_entry))
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, choices.OPT_ZONES: []}
    )
    await hass.async_block_till_done()
    assert not [key for key in _registered(hass, loaded_entry) if key.endswith("_zone2")]


async def test_an_entity_the_receiver_did_not_report_this_time_keeps_its_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Decision E2: only what the options leave out goes; names and rooms stay.

    A quick select from an earlier start, which the receiver did not name this
    time (still starting, say), is left as it is.
    """
    from custom_components.denonavr_plus.entity import unique_id

    coordinator = loaded_entry.runtime_data
    assert 7 not in coordinator.device.discovery.quick_select_names
    registry = er.async_get(hass)
    earlier = registry.async_get_or_create(
        "button",
        DOMAIN,
        unique_id(coordinator.device, "button_quick_select_save_7"),
        config_entry=loaded_entry,
    )
    registry.async_update_entity(earlier.entity_id, name="Movie night")
    entity_plan.async_remove_unplanned(hass, loaded_entry, coordinator)
    kept = registry.async_get(earlier.entity_id)
    assert kept is not None and kept.name == "Movie night"


async def test_the_last_activity_of_a_zone_takes_its_select_along(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus import activities

    film = {activities.KEY_NAME: "Film", activities.KEY_ZONE: "main", activities.KEY_SOURCE: "DVD"}
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, activities.OPT_ACTIVITIES: [film]}
    )
    await hass.async_block_till_done()
    assert any(key.endswith("_select_activity_main") for key in _registered(hass, loaded_entry))
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, activities.OPT_ACTIVITIES: []}
    )
    await hass.async_block_till_done()
    assert not any(key.endswith("_select_activity_main") for key in _registered(hass, loaded_entry))
