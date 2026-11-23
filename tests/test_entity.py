"""Tests for entity.py: entity ids stay English in every language."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.const import DOMAIN

from .test_init import HOST, receiver_network  # noqa: F401 -- fixture

__all__ = ["receiver_network"]

LANGUAGES = ["de", "en", "es", "fr", "it", "nl", "pl", "pt"]


async def _entity_ids(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Set up the receiver in a language; unique id -> entity id."""
    hass.config.language = language
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    return {
        item.unique_id: item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


# Recorded with the English setup of the AVC-X3700H recording.
ENGLISH_IDS = {
    "number_volume_main": "number.avc_x3700h_volume",
    "number_volume_zone2": "number.avc_x3700h_zone_2_volume",
    "sensor_sample_rate": "sensor.avc_x3700h_sample_rate",
    "switch_main_power": "switch.avc_x3700h_main_power",
    "number_treble": "number.avc_x3700h_audio_treble",
    "number_channel_trim_C": "number.avc_x3700h_speakers_center_trim",
    "number_channel_distance_FL": "number.avc_x3700h_speakers_front_l_distance",
    "select_eq_channel": "select.avc_x3700h_graphic_eq_channel",
    "button_eq_apply": "button.avc_x3700h_graphic_eq_apply",
    "media_player_main": "media_player.avc_x3700h",
    "media_player_zone2": "media_player.avc_x3700h_zone_2",
}


@pytest.mark.parametrize("language", LANGUAGES)
async def test_entity_ids_are_english_in_every_language(
    hass: HomeAssistant, receiver_network: Any, language: str
) -> None:
    ids = await _entity_ids(hass, language)
    by_key = {uid.split("_", 1)[1]: entity_id for uid, entity_id in ids.items()}
    for key, expected in ENGLISH_IDS.items():
        assert by_key.get(key) == expected, (language, key)
    # Nothing outside ASCII slugs, whatever the translation.
    assert all(entity_id.isascii() for entity_id in ids.values())


async def test_names_are_translated_but_ids_are_not(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    ids = await _entity_ids(hass, "de")
    volume = next(eid for uid, eid in ids.items() if uid.endswith("_number_volume_main"))
    trim = next(eid for uid, eid in ids.items() if uid.endswith("_number_channel_trim_C"))
    assert volume == "number.avc_x3700h_volume"
    assert trim == "number.avc_x3700h_speakers_center_trim"
    volume_state = hass.states.get(volume)
    trim_state = hass.states.get(trim)
    assert volume_state is not None and "Lautstärke" in volume_state.attributes["friendly_name"]
    assert trim_state is not None and trim_state.attributes["friendly_name"].endswith("Center Pegel")


SNAPSHOT = Path(__file__).parent / "fixtures" / "entity_ids" / "AVC-X3700H.json"


async def test_entities_match_the_snapshot(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Every entity of the recorded AVC-X3700H: id, start state, category.

    A change here changes users' installations. If it is intended, update the
    snapshot (UPDATE_ENTITY_SNAPSHOT=1 pytest tests/test_entity.py) and, for a
    released change of unique ids or entity ids, add a step in migrations.py.
    """
    await _entity_ids(hass, "en")
    registry = er.async_get(hass)
    current = {
        item.unique_id.split("_", 1)[1]: {
            "entity_id": item.entity_id,
            "enabled": item.disabled_by is None,
            "category": item.entity_category.value if item.entity_category else None,
        }
        for item in registry.entities.values()
        if item.platform == DOMAIN
    }
    if os.environ.get("UPDATE_ENTITY_SNAPSHOT"):
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(dict(sorted(current.items())), indent=2) + "\n")
    expected = json.loads(SNAPSHOT.read_text())
    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    changed = sorted(k for k in set(current) & set(expected) if current[k] != expected[k])
    assert not (added or removed or changed), (
        f"added: {added}\nremoved: {removed}\n"
        f"changed: {[(k, expected[k], current[k]) for k in changed]}"
    )
