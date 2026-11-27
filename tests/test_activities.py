"""Tests for activities.py."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import activities
from custom_components.denonavr_plus.choices import OPT_ZONES
from custom_components.denonavr_plus.linked_players import OPT_LINKED_PLAYERS

from .common import entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]

FILM = {"name": "Film", "zone": "main", "source": "DVD"}


async def _push(hass: HomeAssistant, *lines: str) -> None:
    FakeTelnet.instances[-1].push(*lines)
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def _with_activities(
    hass: HomeAssistant, entry: MockConfigEntry, *items: dict[str, Any], **options: Any
) -> None:
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, **options, activities.OPT_ACTIVITIES: list(items)}
    )
    await hass.async_block_till_done()


def test_incomplete_activities_are_left_out() -> None:
    options = {
        activities.OPT_ACTIVITIES: [
            FILM,
            {"name": "", "zone": "main", "source": "CD"},
            {"name": "No input", "zone": "main"},
            "not a dict",
            {**FILM, "name": "Odd", "clarity": "loud", "volume_db": "loud"},
        ]
    }
    found = activities.activities(options)
    assert [item.name for item in found] == ["Film", "Odd"]
    # Values that are not one of the choices fall back to leaving things as they are.
    assert found[1].clarity == activities.CLARITY_KEEP and found[1].volume_db is None


def test_an_activity_is_stored_without_its_unset_parts() -> None:
    full = {
        **FILM,
        "sound_mode": "Dolby Surround",
        "volume_db": -30.0,
        "hdmi_output": "1",
        "clarity": "on",
        "wake_player": True,
        "audio_delay": 40,
        "wake_entity": "media_player.tv",
    }
    assert activities.parse(full).as_dict() == full
    # Something that cannot be switched on is not kept.
    assert activities.parse({**FILM, "wake_entity": "light.lamp"}).wake_entity is None
    assert activities.parse({**FILM, "audio_delay": 40.0}).audio_delay == 40
    assert activities.parse({**FILM, "clarity": "keep", "wake_player": False}).as_dict() == FILM


def test_activities_are_found_by_name_and_zone() -> None:
    options = {activities.OPT_ACTIVITIES: [FILM, {"name": "Music", "zone": "zone2", "source": "NET"}]}
    assert activities.find(options, "  film ") == activities.parse(FILM)
    assert activities.find(options, "Music", "main") is None
    assert activities.zones_with_activities(options) == frozenset({"main", "zone2"})
    assert activities.zones_with_activities({}) == frozenset()


async def test_starting_an_activity_sets_the_zone_up_in_order(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("media_player.blu_ray", "off")
    hass.states.async_set("media_player.tv", "off")
    woken: list[ServiceCall] = []

    async def _turn_on(call: ServiceCall) -> None:
        woken.append(call)

    await _with_activities(
        hass,
        loaded_entry,
        {
            **FILM,
            "volume_db": -30.0,
            "hdmi_output": "1",
            "audio_delay": 40,
            "wake_player": True,
            "wake_entity": "media_player.tv",
        },
        **{OPT_LINKED_PLAYERS: {"DVD": {"player": "media_player.blu_ray"}}},
    )
    await _push(hass, "PWON", "ZMON", "SICD", "MV40")
    hass.services.async_register("media_player", "turn_on", _turn_on)
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    activity = activities.find(dict(loaded_entry.options), "Film")
    assert activity is not None
    from homeassistant.core import Context

    context = Context()
    await activities.async_start(hass, loaded_entry.runtime_data, activity, context)
    await hass.async_block_till_done()
    # Who started it is kept: the logbook shows the woken devices as caused by it.
    assert all(call.context.id == context.id for call in woken)
    # Input, HDMI output, audio delay, then volume; -30 dB is 50 on the receiver's scale.
    assert telnet.sent == ["SIDVD", "VSMONI1", "PSDELAY 040", "MV50"]
    # The linked player and the TV.
    assert {entity for call in woken for entity in call.data["entity_id"]} == {
        "media_player.blu_ray",
        "media_player.tv",
    }


async def test_an_activity_keeps_below_the_zone_limit(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _with_activities(
        hass, loaded_entry, {**FILM, "volume_db": 10.0}, main={"max_volume": -20.0}
    )
    await _push(hass, "PWON", "ZMON", "SIDVD", "MV40")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    activity = activities.find(dict(loaded_entry.options), "Film")
    assert activity is not None
    await activities.async_start(hass, loaded_entry.runtime_data, activity)
    # The input is sent again (the media player does so); -20 dB is 60.
    assert telnet.sent == ["SIDVD", "MV60"]


async def test_sound_mode_hdmi_and_clarity_are_for_the_main_zone_only(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    item = {
        "name": "Kitchen",
        "zone": "zone2",
        "source": "CD",
        "sound_mode": "Stereo",
        "hdmi_output": "1",
        "audio_delay": 40,
        "clarity": "on",
    }
    await _with_activities(hass, loaded_entry, item)
    await _push(hass, "PWON", "Z2ON", "Z2CD")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    activity = activities.find(dict(loaded_entry.options), "Kitchen")
    assert activity is not None
    await activities.async_start(hass, loaded_entry.runtime_data, activity)
    assert telnet.sent == ["Z2CD"]


async def test_an_activity_of_a_hidden_zone_says_so(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _with_activities(
        hass,
        loaded_entry,
        {"name": "Kitchen", "zone": "zone2", "source": "CD"},
        **{OPT_ZONES: ["main"]},
    )
    activity = activities.find(dict(loaded_entry.options), "Kitchen")
    assert activity is not None
    with pytest.raises(ServiceValidationError) as err:
        await activities.async_start(hass, loaded_entry.runtime_data, activity)
    assert err.value.translation_key == "activity_zone_missing"


async def test_an_activity_switches_speech_clarity(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _with_activities(hass, loaded_entry, {**FILM, "clarity": "on"})
    await _push(hass, "PWON", "ZMON", "SIDVD", "PSCLV 50", "PSDYNVOL OFF")
    clarity = entity_id(hass, loaded_entry, "switch_speech_clarity")
    activity = activities.find(dict(loaded_entry.options), "Film")
    assert activity is not None
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await activities.async_start(hass, loaded_entry.runtime_data, activity)
    assert hass.states.get(clarity).state == "on"
