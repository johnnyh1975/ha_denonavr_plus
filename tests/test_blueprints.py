"""The example blueprints and dashboard load as Home Assistant reads them."""

from __future__ import annotations

from pathlib import Path

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
