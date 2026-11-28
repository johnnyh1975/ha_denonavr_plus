"""Tests for switch.py."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.profile import load_profile
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .common import async_enable, entity_id, registry_entry
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

    await async_enable(hass, loaded_entry, "switch_music_main", "switch_music_zone2")
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

    await async_enable(hass, loaded_entry, "switch_music_main")
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
        # Grayed out on the receiver: shown with its value, not settable.
        arc_id = entity_id(hass, entry, "switch_arc")
        arc = hass.states.get(arc_id)
        assert arc is not None and arc.state == "off"
        assert arc.attributes["settable"] is False
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call("switch", "turn_on", {"entity_id": arc_id}, blocking=True)
        assert err.value.translation_key == "setting_not_taken_menu_grayed"

        for service in ("turn_off", "turn_on"):
            await hass.services.async_call(
                "switch", service, {"entity_id": hdmi_control}, blocking=True
            )

    assert api.writes == [
        ("video", 3, "<HDMIControl>2</HDMIControl>"),
        ("video", 3, "<HDMIControl>1</HDMIControl>"),
    ]


async def test_with_the_option_a_switch_in_standby_turns_the_receiver_on(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio
    from unittest.mock import patch

    hass.config_entries.async_update_entry(loaded_entry, options={"settings_turn_on": True})
    telnet = FakeTelnet.instances[-1]
    telnet.push("ZMOFF", "PSDYNEQ OFF")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    telnet.echo = True
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.POWER_ON_SETTLE", 0):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": entity_id(hass, loaded_entry, "switch_dynamic_eq")},
            blocking=True,
        )
    assert telnet.sent[:2] == ["ZMON", "PSDYNEQ ON"]


async def test_speech_clarity_puts_back_what_it_changed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio
    from unittest.mock import patch

    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "PSCLV 50", "PSDYNVOL OFF")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    switch = entity_id(hass, loaded_entry, "switch_speech_clarity")
    assert hass.states.get(switch).state == "off"
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        # The receiver takes both; on again sends nothing, the first values stay.
        telnet.push("PSCLV 53", "PSDYNVOL MED")
        await asyncio.sleep(0.15)
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert telnet.sent == ["PSCLV 53", "PSDYNVOL MED"]
    assert hass.states.get(switch).state == "on"

    # Removed and added again (a reload, a restart) it still knows what to put back.
    assert await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == "on"
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert telnet.sent == ["PSCLV 50", "PSDYNVOL OFF"]
    assert hass.states.get(switch).state == "off"


async def test_speech_clarity_refused_halfway_puts_back_what_did_change(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio
    from unittest.mock import patch

    import pytest
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.denonavr_plus.avr.errors import CommandNotSent

    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "PSCLV 50", "PSDYNVOL OFF")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    device = loaded_entry.runtime_data.device
    real = device.async_set_control

    async def _refuse_dynamic_volume(control_id: str, value: Any) -> None:
        if control_id == "dynamic_volume":
            raise CommandNotSent("PSDYNVOL MED")
        await real(control_id, value)

    switch = entity_id(hass, loaded_entry, "switch_speech_clarity")
    with (
        patch.object(device, "async_set_control", _refuse_dynamic_volume),
        patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    # The center was raised: on, so that off lowers it again.
    assert hass.states.get(switch).state == "on"
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert "PSCLV 50" in telnet.sent


async def test_speech_clarity_answers_turn_on_by_its_alias(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The user guide's recipe: an alias, then Home Assistant's own turn on."""
    from unittest.mock import patch

    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers import intent
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "intent", {})
    switch = entity_id(hass, loaded_entry, "switch_speech_clarity")
    er.async_get(hass).async_update_entity(switch, aliases={"speech clarity"})
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await intent.async_handle(
            hass,
            "test",
            intent.INTENT_TURN_ON,
            {"name": {"value": "Speech clarity"}},
            assistant="conversation",
        )
    assert hass.states.get(switch).state == "on"



async def test_speech_clarity_keeps_what_it_could_not_put_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio
    from unittest.mock import patch

    import pytest
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.denonavr_plus.avr.errors import CommandNotSent

    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "PSCLV 50", "PSDYNVOL OFF")
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()
    device = loaded_entry.runtime_data.device
    real = device.async_set_control
    switch = entity_id(hass, loaded_entry, "switch_speech_clarity")
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    telnet.push("PSCLV 53", "PSDYNVOL MED")
    await asyncio.sleep(0.15)

    async def _refuse_dynamic_volume(control_id: str, value: Any) -> None:
        if control_id == "dynamic_volume":
            raise CommandNotSent("PSDYNVOL OFF")
        await real(control_id, value)

    with (
        patch.object(device, "async_set_control", _refuse_dynamic_volume),
        patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    # The center is back, Dynamic Volume is not: still on, with that to put back.
    assert hass.states.get(switch).state == "on"
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    assert telnet.sent == ["PSDYNVOL OFF"]
    assert hass.states.get(switch).state == "off"


async def test_the_music_switch_starts_hidden_without_a_linked_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Meant as Music Assistant's power control; without a player it is clutter."""
    for key in ("switch_music_main", "switch_music_zone2"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None

