"""Tests for intent.py."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import activities
from custom_components.denonavr_plus.intent import (
    INTENT_SET_SOUND_MODE,
    INTENT_START_ACTIVITY,
    INTENT_WHY_NO_SOUND,
)

from .common import entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


@pytest.fixture
async def with_intents(hass: HomeAssistant, loaded_entry: MockConfigEntry) -> MockConfigEntry:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "intent", {})
    await hass.async_block_till_done()
    return loaded_entry


async def _push(hass: HomeAssistant, *lines: str) -> None:
    FakeTelnet.instances[-1].push(*lines)
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def _activities(hass: HomeAssistant, entry: MockConfigEntry, *items: dict[str, Any]) -> None:
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, activities.OPT_ACTIVITIES: list(items)}
    )
    await hass.async_block_till_done()


def _place(hass: HomeAssistant, player: str, area_id: str) -> None:
    """Put a zone's device in an area (its player has no name of its own)."""
    item = er.async_get(hass).async_get(player)
    assert item is not None and item.device_id is not None
    dr.async_get(hass).async_update_device(item.device_id, area_id=area_id)


async def _handle(hass: HomeAssistant, intent_type: str, **slots: Any) -> intent.IntentResponse:
    return await intent.async_handle(
        hass,
        "test",
        intent_type,
        {key: {"value": value} for key, value in slots.items()},
        assistant="conversation",
    )


async def test_an_activity_starts_by_voice(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _activities(hass, with_intents, {"name": "Film", "zone": "main", "source": "DVD"})
    await _push(hass, "PWON", "ZMON", "SICD")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    response = await _handle(hass, INTENT_START_ACTIVITY, activity="film")
    assert telnet.sent == ["SIDVD"]
    assert response.response_type is intent.IntentResponseType.ACTION_DONE
    assert response.speech["plain"]["speech"] == "Starting Film."
    assert response.success_results[0].id == entity_id(hass, with_intents, "media_player_main")


async def test_an_unknown_activity_is_named(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _activities(hass, with_intents, {"name": "Film", "zone": "main", "source": "DVD"})
    with pytest.raises(intent.IntentHandleError) as err:
        await _handle(hass, INTENT_START_ACTIVITY, activity="Disco")
    assert str(err.value) == "No zone has an activity called Disco. Activities: Film."


async def test_a_refused_input_is_said(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    from unittest.mock import patch

    await _activities(hass, with_intents, {"name": "Radio", "zone": "zone2", "source": "CD"})
    await _push(hass, "PWON", "Z2ON", "Z2DVD")
    with (
        patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05),
        pytest.raises(intent.IntentHandleError) as err,
    ):
        await _handle(hass, INTENT_START_ACTIVITY, activity="Radio")
    assert "did not switch this zone to CD" in str(err.value)


async def test_an_activity_of_two_zones_needs_the_zone_or_the_area(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _activities(
        hass,
        with_intents,
        {"name": "Music", "zone": "main", "source": "CD"},
        {"name": "Music", "zone": "zone2", "source": "CD"},
    )
    await _push(hass, "PWON", "ZMON", "SIDVD", "Z2ON", "Z2DVD")
    with pytest.raises(intent.IntentHandleError) as err:
        await _handle(hass, INTENT_START_ACTIVITY, activity="Music")
    assert "several zones" in str(err.value)

    zone2 = entity_id(hass, with_intents, "media_player_zone2")
    telnet = FakeTelnet.instances[-1]
    telnet.echo = True
    telnet.sent.clear()
    await _handle(hass, INTENT_START_ACTIVITY, activity="Music", name=hass.states.get(zone2).name)
    assert telnet.sent == ["Z2CD"]

    # From a satellite in the kitchen, where Zone 2 plays.
    kitchen = ar.async_get(hass).async_create("Kitchen")
    _place(hass, zone2, kitchen.id)
    telnet.sent.clear()
    await _handle(hass, INTENT_START_ACTIVITY, activity="Music", preferred_area_id=kitchen.id)
    assert telnet.sent == ["Z2CD"]


async def test_why_no_sound_answers_for_the_main_zone(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "SICD", "MUON")
    response = await _handle(hass, INTENT_WHY_NO_SOUND)
    assert response.response_type is intent.IntentResponseType.QUERY_ANSWER
    assert "muted" in response.speech["plain"]["speech"]
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await _handle(hass, INTENT_WHY_NO_SOUND, fix=True)
    assert "MUOFF" in telnet.sent


async def test_why_no_sound_for_a_player_of_another_integration_says_so(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.kitchen_radio", "on", {"friendly_name": "Kitchen radio"})
    with pytest.raises(intent.IntentHandleError) as err:
        await _handle(hass, INTENT_WHY_NO_SOUND, name="Kitchen radio")
    assert str(err.value) == "No zone of a Denon or Marantz receiver is meant."


async def test_why_no_sound_of_two_zones_in_an_area_asks_which(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    living = ar.async_get(hass).async_create("Living room")
    for key in ("media_player_main", "media_player_zone2"):
        _place(hass, entity_id(hass, with_intents, key), living.id)
    with pytest.raises(intent.IntentHandleError) as err:
        await _handle(hass, INTENT_WHY_NO_SOUND, area="Living room")
    assert str(err.value).startswith("Which zone?")


async def test_the_sound_mode_is_set_by_its_shown_name(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _push(
        hass,
        "PWON",
        "ZMON",
        "SIDVD",
        "OPSMLALL MOV071Dolby Surround",
        "OPSMLALL MUS011Stereo",
        "OPSMLALL END",
    )
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    # Zone 2 has no sound modes: the main zone is meant without asking.
    response = await _handle(hass, INTENT_SET_SOUND_MODE, sound_mode="stereo")
    assert "MSSTEREO" in telnet.sent
    # Said with the name the list shows.
    assert response.speech["plain"]["speech"] == "Sound mode Stereo."
    assert response.success_results[0].id == entity_id(hass, with_intents, "media_player_main")


async def test_an_unknown_sound_mode_is_answered_with_the_list(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "OPSMLALL MUS011Stereo", "OPSMLALL END")
    with pytest.raises(intent.IntentHandleError) as err:
        await _handle(hass, INTENT_SET_SOUND_MODE, sound_mode="Disco")
    assert "Disco" in str(err.value) and "Stereo" in str(err.value)



async def test_naming_zone_2_sets_the_sound_mode_of_its_receiver(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "OPSMLALL MUS011Stereo", "OPSMLALL END")
    zone2 = entity_id(hass, with_intents, "media_player_zone2")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    response = await _handle(
        hass, INTENT_SET_SOUND_MODE, sound_mode="Stereo", name=hass.states.get(zone2).name
    )
    assert "MSSTEREO" in telnet.sent
    assert response.success_results[0].id == entity_id(hass, with_intents, "media_player_main")


async def test_the_answer_comes_in_the_language_of_the_request(
    hass: HomeAssistant, with_intents: MockConfigEntry
) -> None:
    await _activities(hass, with_intents, {"name": "Film", "zone": "main", "source": "DVD"})
    with pytest.raises(intent.IntentHandleError) as err:
        await intent.async_handle(
            hass,
            "test",
            INTENT_START_ACTIVITY,
            {"activity": {"value": "Disco"}},
            assistant="conversation",
            language="de",
        )
    assert str(err.value) == "Keine Zone hat eine Aktivität Disco. Aktivitäten: Film."
