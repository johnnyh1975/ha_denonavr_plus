"""Tests for select.py."""

from __future__ import annotations

import asyncio

import pytest
import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.profile import load_profile
from custom_components.denonavr_plus.helpers import enum_options, option_key

from .common import entity_id, registry_entry
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401

__all__ = ["loaded_entry", "receiver_network"]

async def _push(hass: HomeAssistant, *lines: str) -> None:
    FakeTelnet.instances[-1].push(*lines)
    # Device updates are debounced by 0.1 s before they reach the coordinator.
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def test_setup_selects_start_disabled(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    for key in ("select_dynamic_volume", "select_dimmer", "select_sound_mode"):
        assert registry_entry(hass, loaded_entry, key).disabled_by is None, key
    for key in (
        "select_volume_limit",
        "select_lpf_for_lfe",
        "select_aspect_ratio",
        "select_eq_channel",
        "select_eq_speaker_selection",
    ):
        assert registry_entry(hass, loaded_entry, key).disabled_by is not None, key


async def test_choosing_an_option_sends_its_command(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    spec = load_profile().control("dynamic_volume")
    assert spec is not None
    select = entity_id(hass, loaded_entry, "select_dynamic_volume")
    state = hass.states.get(select)
    assert state is not None
    option = state.attributes["options"][-1]
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": option}, blocking=True
    )
    assert any(line.startswith(spec.prefix) for line in telnet.sent), telnet.sent


async def test_fixed_options_are_keys_shown_translated(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The state is a key; the name on screen comes from the translations."""
    select = entity_id(hass, loaded_entry, "select_dimmer")
    state = hass.states.get(select)
    assert state is not None
    assert state.attributes["options"] == ["bright", "dim", "dark", "off"]
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "dark"}, blocking=True
    )
    assert telnet.sent == ["DIM DAR"]
    await _push(hass, "DIM DAR")
    state = hass.states.get(select)
    assert state is not None and state.state == "dark"


def _fixed_options() -> dict[str, list[str]]:
    """Translation key -> option keys of every select with a fixed option set."""
    profile = load_profile()
    found = {
        spec.id: enum_options(spec)[0]
        for spec in profile.controls.values()
        if spec.kind == "enum"
    }
    found["speaker_size"] = [
        option_key(label) for label in profile.speakers["sizes"]["options"].values()
    ]
    found["subwoofer_count"] = [
        option_key(label)
        for label in profile.speakers.get(
            "subwoofer_count", {"NON": "None", "1SP": "1", "2SP": "2"}
        ).values()
    ]
    for section in ("video_web", "speakers_web"):
        for control in profile.grammar[section]["controls"]:
            if control.get("kind") == "select" and control.get("options"):
                found[control["id"]] = [option_key(v) for v in control["options"].values()]
    return found


def test_every_fixed_option_has_a_name_in_every_language() -> None:
    """A key without a translation would show as the bare key."""
    package = Path(__file__).parent.parent / "custom_components" / "denonavr_plus"
    fixed = _fixed_options()
    assert len(fixed) > 25
    for path in [package / "strings.json", *sorted((package / "translations").glob("*.json"))]:
        names = json.loads(path.read_text(encoding="utf-8"))["entity"]["select"]
        for key, options in fixed.items():
            state = names[key].get("state", {})
            missing = [option for option in options if option not in state]
            assert not missing, (path.name, key, missing)


@pytest.mark.assumed(
    "TPAN<nn> recalls a preset per the 2015 X-series document; an AVR-X2600H "
    "reports TPAN01, recalling through this select has not been tried"
)
async def test_the_tuner_preset_is_offered_while_the_tuner_plays(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from homeassistant.const import STATE_UNAVAILABLE

    select = entity_id(hass, loaded_entry, "select_tuner_preset")
    await _push(hass, "PWON", "ZMON", "SICD")
    assert hass.states.get(select).state == STATE_UNAVAILABLE
    await _push(hass, "SITUNER", "TPAN07")
    state = hass.states.get(select)
    assert state is not None and state.state == "07"
    assert len(state.attributes["options"]) == 56
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "12"}, blocking=True
    )
    assert telnet.sent == ["TPAN12"]
