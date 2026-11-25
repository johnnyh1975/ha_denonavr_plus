"""Tests for select.py."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
import json
from pathlib import Path

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr.profile import load_profile
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN
from custom_components.denonavr_plus.helpers import enum_options, option_key

from .common import entity_id, registry_entry
from .setup_api import FakeSetupApi, answering_receiver
from .test_init import HOST, FakeTelnet, loaded_entry, receiver_network  # noqa: F401

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
        "select",
        "select_option",
        {"entity_id": select, "option": option},
        blocking=True,
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
        "select",
        "select_option",
        {"entity_id": select, "option": "dark"},
        blocking=True,
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
                found[control["id"]] = [
                    option_key(v) for v in control["options"].values()
                ]
    return found


def test_every_fixed_option_has_a_name_in_every_language() -> None:
    """A key without a translation would show as the bare key."""
    package = Path(__file__).parent.parent / "custom_components" / "denonavr_plus"
    fixed = _fixed_options()
    assert len(fixed) > 25
    for path in [
        package / "strings.json",
        *sorted((package / "translations").glob("*.json")),
    ]:
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
    from homeassistant.const import STATE_UNKNOWN

    select = entity_id(hass, loaded_entry, "select_tuner_preset")
    await _push(hass, "PWON", "ZMON", "SICD")
    # Off the tuner it is unknown, not unavailable: it can still be chosen.
    assert hass.states.get(select).state == STATE_UNKNOWN
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


async def test_a_tuner_preset_switches_to_the_tuner_first(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    select = entity_id(hass, loaded_entry, "select_tuner_preset")
    await _push(hass, "PWON", "ZMON", "SICD")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    async def choose() -> None:
        await hass.services.async_call(
            "select", "select_option", {"entity_id": select, "option": "12"}, blocking=True
        )

    task = asyncio.ensure_future(choose())
    await asyncio.sleep(0.05)
    telnet.push("SITUNER")
    await task
    assert telnet.sent == ["SITUNER", "TPAN12"]


async def test_a_preset_from_standby_waits_for_the_zone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Nothing but ZMON until the zone reports on: a waking receiver drops commands."""
    from unittest.mock import patch

    select = entity_id(hass, loaded_entry, "select_tuner_preset")
    await _push(hass, "PWON", "ZMOFF", "SITUNER")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    async def choose() -> None:
        await hass.services.async_call(
            "select", "select_option", {"entity_id": select, "option": "12"}, blocking=True
        )

    with patch("custom_components.denonavr_plus.avr.device.TUNER_POWER_SETTLE", 0):
        task = asyncio.ensure_future(choose())
        await asyncio.sleep(0.3)
        assert telnet.sent == ["ZMON"]
        telnet.push("ZMON")
        await task
    assert telnet.sent == ["ZMON", "SITUNER", "TPAN12"]


# The speaker and /ajax selects, on a receiver that answers ----------------------


@pytest.fixture
async def setup_api(
    hass: HomeAssistant, receiver_network: Any
) -> AsyncIterator[FakeSetupApi]:
    api = FakeSetupApi()
    with answering_receiver(api):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        api.entry = entry  # type: ignore[attr-defined]
        yield api


async def _choose(
    hass: HomeAssistant, entry: MockConfigEntry, key: str, option: str
) -> list[str]:
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entity_id(hass, entry, key), "option": option},
        blocking=True,
    )
    return list(telnet.sent)


def _state(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    state = hass.states.get(entity_id(hass, entry, key))
    assert state is not None
    return state.state


async def test_speaker_selects_show_and_set_the_receivers_values(
    hass: HomeAssistant, setup_api: FakeSetupApi
) -> None:
    entry = setup_api.entry  # type: ignore[attr-defined]
    assert _state(hass, entry, "select_crossover_FRO") == "80 Hz"
    assert _state(hass, entry, "select_speaker_size_FRO") == "small"
    assert _state(hass, entry, "select_speaker_size_CEN") == "large"
    assert _state(hass, entry, "select_subwoofer_count") == "1"

    assert await _choose(hass, entry, "select_crossover_FRO", "100 Hz") == [
        "SSCFRFRO 100"
    ]
    assert await _choose(hass, entry, "select_speaker_size_FRO", "large") == [
        "SSSPCFRO LAR"
    ]
    assert await _choose(hass, entry, "select_subwoofer_count", "2") == ["SSSPCSWF 2SP"]


async def test_the_crossover_mode_decides_which_crossover_applies(
    hass: HomeAssistant, setup_api: FakeSetupApi
) -> None:
    entry = setup_api.entry  # type: ignore[attr-defined]
    assert _state(hass, entry, "select_crossover_speaker_selection") == "Individual"
    assert _state(hass, entry, "select_crossover_all") == "unavailable"

    assert await _choose(hass, entry, "select_crossover_speaker_selection", "All") == [
        "SSCFR ALL"
    ]
    await _push(hass, "SSCFR ALL")

    assert _state(hass, entry, "select_crossover_FRO") == "unavailable"
    assert _state(hass, entry, "select_crossover_all") == "80 Hz"
    assert await _choose(hass, entry, "select_crossover_all", "120 Hz") == [
        "SSCFRALL 120"
    ]


async def test_quick_select_recalls_a_preset_by_name(
    hass: HomeAssistant, setup_api: FakeSetupApi
) -> None:
    entry = setup_api.entry  # type: ignore[attr-defined]
    assert _state(hass, entry, "select_quick_select") == "TV"

    assert await _choose(hass, entry, "select_quick_select", "Music") == ["MSQUICK2"]
    await _push(hass, "MSQUICK0")

    assert _state(hass, entry, "select_quick_select") == "unknown"


async def test_graphic_eq_selects_switch_mode_and_channel(
    hass: HomeAssistant, setup_api: FakeSetupApi
) -> None:
    entry = setup_api.entry  # type: ignore[attr-defined]
    # The curve is read by the poll after setup; read it now.
    await entry.runtime_data.device.graphic_eq.refresh()
    await _push(hass)
    state = hass.states.get(entity_id(hass, entry, "select_eq_channel"))
    assert state is not None
    # Flags 3303: channels 0, 1 and 3 are adjustable.
    assert state.attributes["options"] == ["Front L", "Front R", "Surround L"]
    assert _state(hass, entry, "select_eq_speaker_selection") == "Each"

    await _choose(hass, entry, "select_eq_channel", "Surround L")
    assert setup_api.reads[-1] == ("audio", 10, {"opt1": "3", "opt2": "0"})

    await _choose(hass, entry, "select_eq_speaker_selection", "All")
    assert setup_api.writes[-1] == (
        "audio",
        10,
        "<GraphicEQ><SpeakerSelection>1</SpeakerSelection></GraphicEQ>",
    )


async def test_setup_api_selects_write_the_chosen_value(
    hass: HomeAssistant, setup_api: FakeSetupApi
) -> None:
    entry = setup_api.entry  # type: ignore[attr-defined]
    # Fixed options are keys; receiver-supplied ones keep the receiver's names.
    assert _state(hass, entry, "select_hdmi_power_off_control") == "video"
    assert _state(hass, entry, "select_pass_through_source") == "Apple TV"
    assert _state(hass, entry, "select_amp_assign") == "7.1ch + ZONE2"
    assert _state(hass, entry, "select_height_speaker") == "unknown"

    await _choose(hass, entry, "select_hdmi_power_off_control", "all")
    await _choose(hass, entry, "select_pass_through_source", "Blu-ray")
    await _choose(hass, entry, "select_amp_assign", "9.1ch")

    assert setup_api.writes == [
        ("video", 3, "<PowerOffControl>1</PowerOffControl>"),
        ("video", 3, "<PassThroughSource><Source>2</Source></PassThroughSource>"),
        ("speakers", 2, "<AmpAssign><AssignMode>1</AssignMode></AmpAssign>"),
    ]


@pytest.mark.assumed(
    "VSSCH8K comes from Denon's 2020 command list; no receiver has taken it yet"
)
async def test_the_resolution_follows_the_receivers_list(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """An AVC-X3700H lists its HDMI resolutions as Resolution, with 8K and
    without 1080p:24Hz and 4K (60/50); it answers VSSCH ? as an AVR-X2600H does."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.const import CONF_HOST
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.denonavr_plus.avr.transport import GoformClient
    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

    from .conftest import deviceinfo

    async def answering(
        host: str, queries: list[str], on_line: Any, is_complete: Any, **kwargs: Any
    ) -> None:
        if "VSSCH ?" in queries:
            on_line("VSSCHAUTO")

    with (
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("AVC-X3700H-Deviceinfo-8080.xml")),
        ),
        patch("custom_components.denonavr_plus.avr.device.async_probe", answering),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        select = entity_id(hass, entry, "select_resolution_hdmi")
        er.async_get(hass).async_update_entity(select, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(select)
    assert state is not None
    assert state.attributes["options"] == ["auto", "480p_576p", "1080i", "720p", "1080p", "4k", "8k"]


async def test_the_sound_mode_select_shows_readable_names(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    select = entity_id(hass, loaded_entry, "select_sound_mode")
    await _push(hass, "OPSML 010Stereo", "OPSML 021Dolby Audio - DD + DSur", "OPSML END")
    state = hass.states.get(select)
    assert state.attributes["options"] == ["Stereo", "Dolby Surround"]
    assert state.state == "Dolby Surround"
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await hass.services.async_call(
        "select", "select_option", {"entity_id": select, "option": "Dolby Surround"}, blocking=True
    )
    assert telnet.sent and telnet.sent[0].startswith("MS")


async def test_a_mode_has_the_same_name_in_the_select_and_the_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Two modes the receiver listed would both read Dolby Surround: both keep their names."""
    select = entity_id(hass, loaded_entry, "select_sound_mode")
    main = entity_id(hass, loaded_entry, "media_player_main")
    await _push(
        hass,
        "PWON",
        "ZMON",
        "OPSMLALL MOV021Dolby Audio - DD + DSur",
        "OPSMLALL MOV030Multi In + DSur",
        "OPSMLALL END",
        "OPSML 021Dolby Audio - DD + DSur",
        "OPSML END",
    )
    assert hass.states.get(select).attributes["options"] == ["Dolby Audio - DD + DSur"]
    assert hass.states.get(main).attributes["sound_mode"] == "Dolby Audio - DD + DSur"
