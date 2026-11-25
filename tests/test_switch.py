"""Tests for switch.py."""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.profile import load_profile
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .common import entity_id, registry_entry
from .setup_api import FakeSetupApi, answering_receiver
from .test_init import HOST, FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_switches_that_repeat_the_media_player_start_disabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for key in ("switch_dynamic_eq", "switch_cinema_eq", "switch_all_zone_stereo"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is None, key
    # Power and mute are on the media player; the rest is set once.
    for key in ("switch_main_power", "switch_main_mute", "switch_auto_lip_sync"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key


async def test_turning_a_switch_on_and_off_sends_its_command(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    spec = load_profile().control("dynamic_eq")
    assert spec is not None
    telnet = FakeTelnet.instances[-1]
    switch = entity_id(hass, loaded_entry, "switch_dynamic_eq")
    for service, value in (("turn_on", spec.get("on")), ("turn_off", spec.get("off"))):
        telnet.sent.clear()
        await hass.services.async_call(
            "switch", service, {"entity_id": switch}, blocking=True
        )
        assert any(line == f"{spec.prefix}{value}" for line in telnet.sent), telnet.sent


async def test_a_switch_the_receiver_keeps_says_why(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio
    from unittest.mock import patch

    import pytest
    from homeassistant.exceptions import HomeAssistantError

    telnet = FakeTelnet.instances[-1]
    telnet.push("ZMOFF", "PSDYNEQ OFF")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    switch = entity_id(hass, loaded_entry, "switch_dynamic_eq")
    name = hass.states.get(switch).name
    with (
        patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert err.value.translation_key == "setting_not_taken_standby"
    assert err.value.translation_placeholders["setting"] == name


async def test_a_switch_the_receiver_has_not_reported_shows_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    switch = entity_id(hass, loaded_entry, "switch_dynamic_eq")
    loaded_entry.runtime_data.data.values.pop("dynamic_eq", None)
    loaded_entry.runtime_data.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "off"


async def test_the_music_switch_never_ends_a_film(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """On: zone on and on the network. Off: only while it plays the network."""
    import asyncio

    telnet = FakeTelnet.instances[-1]
    music = entity_id(hass, loaded_entry, "switch_music_main")
    zone2_music = entity_id(hass, loaded_entry, "switch_music_zone2")
    assert zone2_music != music

    telnet.push("PWON", "ZMOFF")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert hass.states.get(music).state == "off"
    telnet.sent.clear()
    from unittest.mock import patch

    # A zone that does not report on in time still gets the network input.
    with patch("custom_components.denonavr_plus.switch.TURN_ON_WAIT", 0.05):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": music}, blocking=True
        )
    assert telnet.sent == ["ZMON", "SINET"]

    telnet.push("ZMON", "SINET")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert hass.states.get(music).state == "on"
    telnet.sent.clear()
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": music}, blocking=True
    )
    assert telnet.sent == []

    # Someone watches TV: off leaves the zone alone.
    telnet.push("SITV")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    assert hass.states.get(music).state == "off"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": music}, blocking=True
    )
    assert telnet.sent == []
    telnet.push("SINET")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": music}, blocking=True
    )
    assert telnet.sent == ["ZMOFF"]


async def test_the_music_switch_turns_on_at_the_turn_on_volume(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio
    from unittest.mock import patch

    from custom_components.denonavr_plus import volume

    hass.config_entries.async_update_entry(
        loaded_entry, options={"main": {volume.OPT_TURN_ON_VOLUME: -30.0}}
    )
    await hass.async_block_till_done()
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMOFF")
    await asyncio.sleep(0.15)
    music = entity_id(hass, loaded_entry, "switch_music_main")
    telnet.sent.clear()

    async def answer() -> None:
        while "ZMON" not in telnet.sent:
            await asyncio.sleep(0.01)
        telnet.push("ZMON")

    task = asyncio.create_task(answer())
    with patch("custom_components.denonavr_plus.switch.TURN_ON_SETTLE", 0):
        await asyncio.wait_for(
            hass.services.async_call(
                "switch", "turn_on", {"entity_id": music}, blocking=True
            ),
            5,
        )
    await task
    assert telnet.sent == ["ZMON", "MV50", "SINET"]


async def test_setup_api_switches_follow_the_receiver(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    api = FakeSetupApi()
    with answering_receiver(api):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hdmi_control = entity_id(hass, entry, "switch_hdmi_control")
        state = hass.states.get(hdmi_control)
        assert state is not None and state.state == "on"
        # Grayed out on the receiver: shown, but not settable.
        arc = hass.states.get(entity_id(hass, entry, "switch_arc"))
        assert arc is not None and arc.state == "unavailable"

        for service in ("turn_off", "turn_on"):
            await hass.services.async_call(
                "switch", service, {"entity_id": hdmi_control}, blocking=True
            )

    assert api.writes == [
        ("video", 3, "<HDMIControl>2</HDMIControl>"),
        ("video", 3, "<HDMIControl>1</HDMIControl>"),
    ]
