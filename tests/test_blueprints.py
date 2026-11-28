"""The example blueprints and dashboard load as Home Assistant reads them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.automation.config import async_validate_config_item
from homeassistant.components.blueprint import models
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.core import HomeAssistant
from homeassistant.util import yaml

ROOT = Path(__file__).parent.parent
BLUEPRINTS = sorted((ROOT / "blueprints" / "automation" / "denonavr_plus").glob("*.yaml"))

#: Inputs to fill each blueprint with.
_INPUTS = {
    "tv_on.yaml": {
        "tv": "media_player.tv",
        "receiver": "media_player.avr_x2600h",
        "source": "TV AUDIO",
    },
    "night_mode.yaml": {
        "receiver": "media_player.avr_x2600h",
        "dynamic_volume": ["select.avr_x2600h_audio_dynamic_volume"],
        "dynamic_volume_night": "heavy",
        "dynamic_volume_day": "off",
        "speech_clarity": ["switch.avr_x2600h_speech_clarity"],
    },
}


def test_every_blueprint_has_inputs_to_test() -> None:
    assert {path.name for path in BLUEPRINTS} == set(_INPUTS)


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda path: path.name)
async def test_a_blueprint_makes_a_valid_automation(hass: HomeAssistant, path: Path) -> None:
    blueprint = models.Blueprint(
        yaml.load_yaml(str(path)), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    inputs = models.BlueprintInputs(
        blueprint,
        {"use_blueprint": {"path": path.name, "input": _INPUTS[path.name]}},
    )
    config = inputs.async_substitute()
    config["id"] = path.stem
    validated = await async_validate_config_item(hass, path.stem, config)
    assert validated is not None
    assert validated.validation_error is None, validated.validation_error


def test_the_example_dashboard_is_a_sections_view() -> None:
    view = yaml.load_yaml(str(ROOT / "examples" / "dashboard.yaml"))
    assert view["type"] == "sections"
    entities = [
        card["entity"]
        for section in view["sections"]
        for card in section["cards"]
        if "entity" in card
    ]
    assert entities and all(".avr_x2600h" in entity for entity in entities)


async def test_a_tv_that_drops_off_the_network_leaves_the_receiver_alone(
    hass: HomeAssistant,
) -> None:
    """off -> unavailable is no "TV on", unavailable -> off no "TV off"; off -> on is."""
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import async_mock_service

    path = ROOT / "blueprints" / "automation" / "denonavr_plus" / "tv_on.yaml"
    blueprint = models.Blueprint(
        yaml.load_yaml(str(path)), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    config = models.BlueprintInputs(
        blueprint, {"use_blueprint": {"path": path.name, "input": _INPUTS["tv_on.yaml"]}}
    ).async_substitute()
    config["id"] = "tv_on"
    select = async_mock_service(hass, "media_player", "select_source")
    turn_off = async_mock_service(hass, "media_player", "turn_off")
    hass.states.async_set("media_player.tv", "off")
    hass.states.async_set("media_player.avr_x2600h", "on", {"source": "TV AUDIO"})
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()

    for state in ("unavailable", "off"):
        hass.states.async_set("media_player.tv", state)
        await hass.async_block_till_done()
    assert select == [] and turn_off == []

    hass.states.async_set("media_player.tv", "on")
    await hass.async_block_till_done()
    assert len(select) == 1
    hass.states.async_set("media_player.tv", "off")
    await hass.async_block_till_done()
    assert len(turn_off) == 1


def test_the_example_dashboard_uses_entity_ids_a_new_setup_has() -> None:
    """Each entity of the example exists in a new setup (the recorded receiver)."""
    import json

    view = yaml.load_yaml(str(ROOT / "examples" / "dashboard.yaml"))
    snapshot = json.loads(
        (ROOT / "tests" / "fixtures" / "entity_ids" / "AVC-X3700H.json").read_text()
    )
    known = {item["entity_id"] for item in snapshot.values()}
    entities = {
        card["entity"].replace(".avr_x2600h", ".avc_x3700h")
        for section in view["sections"]
        for card in section["cards"]
        if "entity" in card
    }
    # Created only when the receiver reports quick selects; the recording does not.
    # Created only once the main zone has activities (test_select pins its id).
    model_dependent = {"select.avc_x3700h_quick_select", "select.avc_x3700h_activity"}
    assert entities - model_dependent <= known, entities - model_dependent - known


# The automation's next time trigger stays scheduled when the test ends.
@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_night_mode_switches_speech_clarity_on_at_night_and_off_by_day(
    hass: HomeAssistant, freezer: Any
) -> None:
    from datetime import timedelta

    from homeassistant.setup import async_setup_component
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import (
        async_fire_time_changed,
        async_mock_service,
    )

    path = ROOT / "blueprints" / "automation" / "denonavr_plus" / "night_mode.yaml"
    blueprint = models.Blueprint(
        yaml.load_yaml(str(path)), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    inputs = {
        "receiver": "media_player.avr_x2600h",
        "speech_clarity": ["switch.avr_x2600h_speech_clarity"],
        "start": "22:00:00",
        "end": "07:00:00",
    }
    config = models.BlueprintInputs(
        blueprint, {"use_blueprint": {"path": path.name, "input": inputs}}
    ).async_substitute()
    config["id"] = "night_mode"
    turn_on = async_mock_service(hass, "switch", "turn_on")
    turn_off = async_mock_service(hass, "switch", "turn_off")
    hass.states.async_set("media_player.avr_x2600h", "on", {"volume_level": 0.2})
    now = dt_util.now().replace(hour=21, minute=59, second=59, microsecond=0)
    freezer.move_to(now)
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()

    freezer.move_to(now + timedelta(seconds=1))
    async_fire_time_changed(hass, now + timedelta(seconds=1))
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in turn_on] == [["switch.avr_x2600h_speech_clarity"]]
    assert turn_off == []

    morning = now.replace(hour=7, minute=0, second=0) + timedelta(days=1)
    freezer.move_to(morning)
    async_fire_time_changed(hass, morning)
    await hass.async_block_till_done()
    assert [call.data["entity_id"] for call in turn_off] == [["switch.avr_x2600h_speech_clarity"]]
