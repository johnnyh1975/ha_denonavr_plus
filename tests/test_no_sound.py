"""Tests for no_sound.py: why a zone is silent, in plain words."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.const import DOMAIN
from custom_components.denonavr_plus.linked_players import OPT_LINKED_PLAYERS

from .common import entity_id
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def _push(hass: HomeAssistant, *lines: str) -> None:
    FakeTelnet.instances[-1].push(*lines)
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def _why(hass: HomeAssistant, entry: MockConfigEntry, *, fix: bool = False) -> list[dict[str, Any]]:
    response = await hass.services.async_call(
        DOMAIN,
        "why_no_sound",
        {"entity_id": entity_id(hass, entry, "media_player_main"), "fix": fix},
        blocking=True,
        return_response=True,
    )
    player = entity_id(hass, entry, "media_player_main")
    return response[player]["findings"]  # type: ignore[index,return-value]


async def test_a_silent_zone_is_explained_and_fixed_where_safe(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMOFF", "MUON", "MV15", "VSAUDIO TV", "PSFRONT SPB", "SIBD")
    found = await _why(hass, loaded_entry)
    assert [item["check"] for item in found] == [
        "zone_off",
        "muted",
        "volume_low",
        "hdmi_audio_tv",
        "speakers_b",
    ]
    assert found[0]["message"] == "The main zone is off."
    assert found[2]["message"] == "The volume is very low (-65 dB)."
    assert not any(item["fixed"] for item in found)

    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    found = await _why(hass, loaded_entry, fix=True)
    assert [item["check"] for item in found if item["fixed"]] == ["zone_off", "muted", "hdmi_audio_tv"]
    assert found[1]["message"] == "The main zone was muted; the sound is on again."
    assert {"ZMON", "MUOFF", "VSAUDIO AMP"} <= set(telnet.sent)
    # The volume and the speakers are the listener's call.
    assert not any(line.startswith(("MV", "PSFRONT")) for line in telnet.sent)


async def test_no_signal_and_the_linked_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "MUOFF", "MV50", "SIBD", "SSINFAISFSVNON")
    found = await _why(hass, loaded_entry)
    assert [item["check"] for item in found] == ["no_signal"]
    source = hass.states.get(entity_id(hass, loaded_entry, "media_player_main")).attributes["source"]  # type: ignore[union-attr]
    assert found[0]["message"] == f"The receiver gets no audio from {source}: is the device on and playing?"

    hass.states.async_set("media_player.shield", "paused", {"friendly_name": "Shield"})
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={**loaded_entry.options, OPT_LINKED_PLAYERS: {"BD": {"player": "media_player.shield"}}},
    )
    found = await _why(hass, loaded_entry)
    assert [item["check"] for item in found] == ["player_not_playing", "no_signal"]
    assert found[0]["message"] == f"Shield on {source} is not playing."

    hass.states.async_set("media_player.shield", "playing", {"friendly_name": "Shield"})
    found = await _why(hass, loaded_entry)
    assert [item["check"] for item in found] == ["no_signal_while_playing"]
    # With a signal there is nothing to find.
    await _push(hass, "SSINFAISFSV48K")
    assert [item["check"] for item in await _why(hass, loaded_entry)] == ["nothing_found"]


async def test_zone_2_following_the_main_zone_off_and_a_refused_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus.avr.models import ZoneDescriptor

    device = loaded_entry.runtime_data.device
    zone2 = next(zone for zone in device.discovery.zones if zone.id == "zone2")
    assert isinstance(zone2, ZoneDescriptor)
    await _push(hass, "PWON", "ZMOFF", "Z2ON", "Z2SOURCE", "Z2MUOFF", "Z250")
    player = entity_id(hass, loaded_entry, "media_player_zone2")
    response = await hass.services.async_call(
        DOMAIN, "why_no_sound", {"entity_id": player}, blocking=True, return_response=True
    )
    assert [item["check"] for item in response[player]["findings"]] == ["follows_main_off"]  # type: ignore[index]

    device._refused_sources["zone2"] = {"CD"}
    await _push(hass, "Z2CD")
    response = await hass.services.async_call(
        DOMAIN, "why_no_sound", {"entity_id": player}, blocking=True, return_response=True
    )
    assert [item["check"] for item in response[player]["findings"]] == ["refused_input"]  # type: ignore[index]


async def test_the_button_fixes_and_posts_what_it_found(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.components import persistent_notification

    await _push(hass, "PWON", "ZMON", "MUON", "MV50", "SINET")
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": entity_id(hass, loaded_entry, "button_why_no_sound_main")},
        blocking=True,
    )
    await hass.async_block_till_done()
    notifications = persistent_notification._async_get_or_create_notifications(hass)
    note = next(item for key, item in notifications.items() if "no_sound" in key)
    assert note["title"] == "Why no sound?"
    assert note["message"] == "- The main zone was muted; the sound is on again."
    assert "MUOFF" in FakeTelnet.instances[-1].sent


async def test_without_a_connection_it_says_so(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from unittest.mock import PropertyMock, patch

    from custom_components.denonavr_plus.avr import DenonAvrDevice

    with patch.object(DenonAvrDevice, "telnet_connected", new_callable=PropertyMock, return_value=False):
        found = await _why(hass, loaded_entry)
    assert [item["check"] for item in found] == ["not_connected"]


async def test_zone_2_on_the_main_zones_input_is_checked_on_that_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _push(hass, "PWON", "ZMON", "MUOFF", "MV50", "SIBD", "SSINFAISFSVNON")
    await _push(hass, "Z2ON", "Z2SOURCE", "Z2MUOFF", "Z250")
    player = entity_id(hass, loaded_entry, "media_player_zone2")
    response = await hass.services.async_call(
        DOMAIN, "why_no_sound", {"entity_id": player}, blocking=True, return_response=True
    )
    assert [item["check"] for item in response[player]["findings"]] == ["no_signal"]  # type: ignore[index]
