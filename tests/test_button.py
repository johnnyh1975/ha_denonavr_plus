"""Tests for button.py."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import layout_repair
from custom_components.denonavr_plus.avr.speaker_layout import SpeakerLayout
from custom_components.denonavr_plus.avr.transport import WebConfigClient
from custom_components.denonavr_plus.button import DenonAvrQuickSelectRecall, DenonAvrQuickSelectSave
from custom_components.denonavr_plus.const import DOMAIN

from .common import async_enable, entity_id
from .fakes import FakeTelnet


async def test_rejected_eq_write_is_reported(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await async_enable(hass, loaded_entry, "button_eq_curve_copy")
    button = entity_id(hass, loaded_entry, "button_eq_curve_copy")
    with (
        patch.object(WebConfigClient, "async_set", AsyncMock(return_value=False)),
        pytest.raises(HomeAssistantError) as err,
    ):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "setup_write_failed"
    assert err.value.translation_placeholders == {"model": "AVC-X3700H"}


async def test_accepted_eq_write_passes(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await async_enable(hass, loaded_entry, "button_eq_curve_copy")
    button = entity_id(hass, loaded_entry, "button_eq_curve_copy")
    write = AsyncMock(return_value=True)
    with patch.object(WebConfigClient, "async_set", write):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    write.assert_awaited_once()


@pytest.mark.assumed(
    "CVZRL is documented only in the 2015 X-series document and NSRBT only in the "
    "denonavr library; pressing both on a receiver would settle them"
)
async def test_command_buttons_send_their_commands(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await async_enable(
        hass, loaded_entry, "button_channel_level_reset", "button_network_restart"
    )
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    for key in ("button_channel_level_reset", "button_network_restart"):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": entity_id(hass, loaded_entry, key)},
            blocking=True,
        )
    assert telnet.sent == ["CVZRL", "NSRBT"]


@pytest.mark.assumed(
    "MSQUICK<n> MEMORY comes from the denonavr library; saving a preset on a "
    "receiver would settle it"
)
async def test_a_quick_select_preset_can_be_saved(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    coordinator = loaded_entry.runtime_data
    coordinator.device.discovery.quick_select_names[2] = "Movie night"
    button = DenonAvrQuickSelectSave(coordinator, 2)
    assert button.translation_placeholders == {"name": "Movie night"}
    assert button.entity_registry_enabled_default is False
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await button.async_press()
    assert telnet.sent == ["MSQUICK2 MEMORY"]


@pytest.mark.assumed(
    "the soft power cycle follows TheFab21/ha-denon-marantz-avr; whether it brings "
    "back a lost sound would only show on a receiver that lost it"
)
async def test_recover_audio_cycles_standby_and_restores_the_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    await async_enable(hass, loaded_entry, "button_recover_audio")
    button = entity_id(hass, loaded_entry, "button_recover_audio")
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "SITV")
    await asyncio.sleep(0.15)
    telnet.sent.clear()

    async def answer() -> None:
        for _ in range(300):
            if "PWON" in telnet.sent:
                telnet.push("PWON", "ZMON")
                return
            await asyncio.sleep(0.01)

    task = asyncio.create_task(answer())
    with patch("custom_components.denonavr_plus.avr.device.RECOVER_STANDBY_TIME", 0):
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    await task
    assert telnet.sent == ["PWSTANDBY", "PWON", "SITV"]


async def test_a_speaker_layout_the_receiver_does_not_take_is_an_error(
    hass: HomeAssistant, web_control: Any
) -> None:
    """The recorded page does not change after the write: the layout was not taken."""
    entry = web_control.entry
    layout = entry.runtime_data.device.speaker_layout
    layout._settle = 0
    layout.stage("17")
    button = entity_id(hass, entry, "button_speaker_layout_apply")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", "press", {"entity_id": button}, blocking=True
        )
    assert err.value.translation_key == "setup_write_failed"
    assert web_control.writes == [("speakers", 15, '<Config index="1">17</Config>')]
    # Maybe taken all the same, later: the way back is kept as a repair.
    point = layout_repair.open_point(hass, entry.entry_id)
    assert point is not None and point["unsettled"] and point["before"]["mode"] == "6"


LAYOUT_POINT = {
    "before": {
        "mode": "6",
        "positions": {"3": "4"},
        "front_speaker": "A",
        "speaker_preset": None,
    },
    "after": {
        "mode": "17",
        "positions": {"3": "5"},
        "front_speaker": "A",
        "speaker_preset": None,
    },
    "unsettled": False,
}


async def test_a_second_layout_change_goes_back_to_the_state_before_the_first(
    hass: HomeAssistant, web_control: Any
) -> None:
    entry = web_control.entry
    button = entity_id(hass, entry, "button_speaker_layout_apply")
    second = {**LAYOUT_POINT, "after": {**LAYOUT_POINT["after"], "mode": "1"}}
    apply = AsyncMock(side_effect=[LAYOUT_POINT, second, None])
    with patch.object(SpeakerLayout, "async_apply", apply):
        for _ in range(3):
            await hass.services.async_call(
                "button", "press", {"entity_id": button}, blocking=True
            )
    assert apply.await_args_list[0].args == (None,)
    assert apply.await_args_list[1].args == (LAYOUT_POINT["before"],)
    # Nothing prepared the third time: the open repair stays as it was.
    assert layout_repair.open_point(hass, entry.entry_id) == second


async def test_each_quick_select_has_a_button_per_zone(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """The select shows nothing once a preset is changed; a button is plain to use."""
    from unittest.mock import patch

    from homeassistant.const import CONF_HOST

    from custom_components.denonavr_plus.avr.models import Discovery
    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION

    from .setup_api import FakeSetupApi, answering_receiver

    answered = Discovery.has_control

    def zone2_answers(self: Discovery, scope: Any, feature: Any, control_id: str) -> bool:
        # As a receiver that answers Z2QUICK ? (an AVR-X2000 and an AVR-X2600H do).
        return control_id == "zone2_quick_select" or answered(self, scope, feature, control_id)

    with (
        patch.object(Discovery, "has_control", zone2_answers),
        answering_receiver(FakeSetupApi()),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        button = entity_id(hass, entry, "button_quick_select_main_2")
        assert hass.states.get(button).attributes["friendly_name"].endswith("Quick Select Music")
        telnet = FakeTelnet.instances[-1]
        telnet.sent.clear()
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
        assert telnet.sent == ["MSQUICK2"]
        telnet.sent.clear()
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": entity_id(hass, entry, "button_quick_select_zone2_1")},
            blocking=True,
        )
        assert telnet.sent == ["Z2QUICK1"]
        coordinator = entry.runtime_data
    # The main zone's start shown, Zone 2's with the zone's other settings hidden.
    zones = {zone.id: zone for zone in coordinator.device.discovery.zones}
    assert DenonAvrQuickSelectRecall(coordinator, zones["main"], 1).entity_registry_enabled_default
    assert not DenonAvrQuickSelectRecall(coordinator, zones["zone2"], 1).entity_registry_enabled_default

