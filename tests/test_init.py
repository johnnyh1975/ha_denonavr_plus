"""Tests for __init__.py: setting up and unloading a receiver.

The receiver's network is replaced at its edges only: the Deviceinfo and
status documents are served from recordings, and the telnet and HEOS
transports are fakes built through the same constructor signature as the
real ones (AGENTS.md, rule 3). Everything between -- discovery, the parser,
the coordinator and the entities -- is the real code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.transport import (
    GoformClient,
    UpnpClient,
    WebConfigClient,
)
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .fakes import HOST, FakeTelnet

def _main_player(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain == "media_player" and item.unique_id.endswith("_media_player_main"):
            return item.entity_id
    raise AssertionError("no main zone media player")


async def _settle(hass: HomeAssistant) -> None:
    # Device updates are debounced by 0.1 s before they reach the coordinator.
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def test_setup_creates_receiver_and_zones(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    players = [
        e for e in er.async_entries_for_config_entry(registry, loaded_entry.entry_id)
        if e.domain == "media_player"
    ]
    # The recording reports two zones.
    assert len(players) == 2


async def test_setup_resynchronises_on_connect(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    assert telnet.host == HOST
    # The full state is queried after connecting, power and volume among it.
    assert "PW?" in telnet.sent
    assert "MV?" in telnet.sent


async def test_pushed_state_reaches_the_media_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    entity_id = _main_player(hass, loaded_entry)
    FakeTelnet.instances[-1].push("PWON", "ZMON", "MVMAX 98", "MV49", "MUOFF", "SITV")
    await _settle(hass)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["volume_level"] == pytest.approx(49 / 98)
    assert state.attributes["is_volume_muted"] is False


async def test_media_player_commands_reach_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    entity_id = _main_player(hass, loaded_entry)
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "MVMAX 98", "MV40")
    await _settle(hass)
    telnet.sent.clear()

    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.5}, blocking=True
    )
    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert telnet.sent == ["MV49", "ZMOFF"]


async def test_lost_link_makes_entities_unavailable(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    entity_id = _main_player(hass, loaded_entry)
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON")
    await _settle(hass)
    assert hass.states.get(entity_id).state == STATE_ON

    telnet.set_available(False)
    await _settle(hass)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_unload_stops_the_link(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED
    assert telnet.connected is False


async def test_unreachable_receiver_retries_setup(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    with patch.object(GoformClient, "async_get_device_info", AsyncMock(return_value=None)):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key == "cannot_reach"


async def test_setup_remembers_the_goform_port(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An entry created without a port learns it at its first setup."""
    assert loaded_entry.data[CONF_PORT] == 8080


async def test_a_zone_switched_off_loses_its_entities(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)

    def zone2_entities() -> list[str]:
        return [
            item.unique_id
            for item in er.async_entries_for_config_entry(registry, loaded_entry.entry_id)
            if item.unique_id.endswith("_zone2")
        ]

    # media player, volume, source and activity sensors, music, why no sound
    assert len(zone2_entities()) == 6
    # Switching zone 2 off reloads the entry and removes its entities.
    hass.config_entries.async_update_entry(loaded_entry, options={"zones": []})
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert zone2_entities() == []
    # Switching it on again brings them back.
    hass.config_entries.async_update_entry(loaded_entry, options={"zones": ["zone2"]})
    await hass.async_block_till_done()
    assert len(zone2_entities()) == 6


async def test_the_device_shows_no_protocol_family_as_hardware(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """hardware_type is the protocol family (avr-x-2016), not the hardware."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), loaded_entry.entry_id)
    assert devices
    assert all(device.hw_version is None for device in devices)


async def test_the_device_is_registered_with_its_dts_decoder_from_the_start(
    hass: HomeAssistant, web_control: Any
) -> None:
    """The recorded AVC-X3900H names DTS 4.30.10.15 on its information page."""
    from custom_components.denonavr_plus import _register_main_device

    entry = web_control.entry
    coordinator = entry.runtime_data
    info = coordinator.device.discovery.device
    assert info.dts_version == "4.30.10.15"
    info.firmware_version, info.network_module_version = "3.139.174", "Aios 6.5V"
    _register_main_device(hass, entry, coordinator)
    main = dr.async_get(hass).async_get(coordinator.main_device_id)
    assert main is not None
    assert main.sw_version == "3.139.174 (DTS 4.30.10.15)"
    assert main.hw_version == "Aios 6.5V"
    # The interface level of the recording, for support without diagnostics.
    assert main.model_id == "CommApiVers 0301"


async def test_sub_devices_follow_the_receivers_room(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Seen on an AVR-X2600H: only the receiver was in its room, not Audio, Video..."""
    areas = ar.async_get(hass)
    living, kitchen, cellar = (areas.async_create(name).id for name in ("Living", "Kitchen", "Cellar"))
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, loaded_entry.entry_id)
    main = next(device for device in devices if device.via_device_id is None)
    subs = [
        device
        for device in devices
        if device.via_device_id == main.id and not device.name.endswith("Zone 2")
    ]
    zone2 = next(device for device in devices if device.name.endswith("Zone 2"))
    assert len(subs) >= 2

    registry.async_update_device(main.id, area_id=living)
    await hass.async_block_till_done()
    assert {registry.async_get(sub.id).area_id for sub in subs} == {living}
    # Zone 2 plays in a room of its own: it does not follow.
    assert registry.async_get(zone2.id).area_id is None

    # One the user puts elsewhere stays there when the receiver moves.
    registry.async_update_device(subs[0].id, area_id=cellar)
    registry.async_update_device(main.id, area_id=kitchen)
    await hass.async_block_till_done()
    assert registry.async_get(subs[0].id).area_id == cellar
    assert {registry.async_get(sub.id).area_id for sub in subs[1:]} == {kitchen}

    # A sub-device without a room takes the receiver's at the next start.
    registry.async_update_device(subs[1].id, area_id=None)
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(subs[1].id).area_id == kitchen


async def test_zone_2_has_a_device_of_its_own(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone 2 usually plays in another room, so it gets a device with its own room."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    main = next(
        device
        for device in dr.async_entries_for_config_entry(devices, loaded_entry.entry_id)
        if device.via_device_id is None
    )
    on_zone2 = {
        entity_id: entities.async_get(entity_id).device_id
        for entity_id in (
            "media_player.avc_x3700h_zone_2",
            "number.avc_x3700h_zone_2_volume",
            "sensor.avc_x3700h_zone_2_source",
        )
    }
    (device_id,) = set(on_zone2.values())
    zone2 = devices.async_get(device_id)
    assert zone2.name == "AVC-X3700H Zone 2"
    assert zone2.via_device_id == main.id
    assert entities.async_get("media_player.avc_x3700h").device_id == main.id

    # Left out in the options, the zone loses its device with its entities.
    hass.config_entries.async_update_entry(loaded_entry, options={"zones": []})
    await hass.async_block_till_done()
    assert devices.async_get(device_id) is None


async def test_the_move_chosen_at_setup_runs_once_the_entities_exist(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Chosen when the receiver was added; the old entry is already off."""
    old = MockConfigEntry(
        domain="denonavr", unique_id="AVC-X3700H-1", data={CONF_HOST: HOST}, title="Wohnzimmer"
    )
    old.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "media_player", "denonavr", "AVC-X3700H-1-Main", config_entry=old,
        suggested_object_id="wohnzimmer",
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        minor_version=CONFIG_MINOR_VERSION,
        options={"move_pending": [old.entry_id]},
    )
    entry.add_to_hass(hass)
    # The move reloads the entry as soon as it is set up: wait for that.
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    item = er.async_get(hass).async_get("media_player.wohnzimmer")
    assert item is not None and item.config_entry_id == entry.entry_id
    assert "move_pending" not in entry.options
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("media_player.wohnzimmer") is not None


async def test_a_pending_move_whose_old_entry_is_gone(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        minor_version=CONFIG_MINOR_VERSION,
        options={"move_pending": ["gone"]},
    )
    entry.add_to_hass(hass)
    # The move reloads the entry as soon as it is set up: wait for that.
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert "move_pending" not in entry.options
    assert entry.state is ConfigEntryState.LOADED


def _show_trims(hass: HomeAssistant, entry: MockConfigEntry, codes: tuple[str, ...]) -> None:
    """Show channel trims as a user does (they start hidden)."""
    from .common import registry_entry

    registry = er.async_get(hass)
    for code in codes:
        item = registry_entry(hass, entry, f"number_channel_trim_{code}")
        registry.async_update_entity(item.entity_id, disabled_by=None)


async def test_a_speaker_set_up_later_keeps_its_trim_hidden_while_none_is_shown(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Trims start hidden; one speaker added must not show its trim alone."""
    from .common import registry_entry

    telnet = FakeTelnet.instances[-1]
    with patch("custom_components.denonavr_plus.SPEAKERS_SETTLE", 0):
        telnet.push("SSSPCFRO SMA", "SSSPCCEN NON", "SSSPCSUA NON", "SSSPCSWF NO")
        await _settle(hass)
        assert registry_entry(hass, loaded_entry, "number_channel_trim_FL").disabled_by is not None
        telnet.push("SSSPCFRO SMA", "SSSPCCEN SMA", "SSSPCSUA NON", "SSSPCSWF NO")
        await _settle(hass)
    assert registry_entry(hass, loaded_entry, "number_channel_trim_C").disabled_by is not None


async def test_channels_follow_a_speaker_set_up_or_removed_on_the_receiver(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The AVR-X2000 whose owner set center, surround and subwoofer to None."""

    from .common import registry_entry

    registry = er.async_get(hass)
    disabled = er.RegistryEntryDisabler.INTEGRATION
    telnet = FakeTelnet.instances[-1]

    def state(code: str) -> er.RegistryEntryDisabler | None:
        return registry_entry(hass, loaded_entry, f"number_channel_trim_{code}").disabled_by

    with patch("custom_components.denonavr_plus.SPEAKERS_SETTLE", 0):
        telnet.push("SSSPCFRO SMA", "SSSPCCEN SMA", "SSSPCSUA SMA", "SSSPCSWF YES")
        await _settle(hass)
        # Trims start hidden; this owner shows those of the speakers there.
        _show_trims(hass, loaded_entry, ("FL", "FR", "C", "SL", "SR", "SW"))
        assert [state(c) for c in ("C", "SL", "SW")] == [None, None, None]
        assert state("SBL") is disabled
        # Turned on by the user although the receiver has no such speaker.
        registry.async_update_entity(
            registry_entry(hass, loaded_entry, "number_channel_trim_SBL").entity_id,
            disabled_by=None,
        )

        telnet.push("SSSPCFRO LAR", "SSSPCCEN NON", "SSSPCSUA NON", "SSSPCSWF NO")
        await _settle(hass)
        assert [state(c) for c in ("C", "SL", "SR", "SW")] == [disabled] * 4
        assert state("FL") is None
        assert state("SBL") is None

        telnet.push("SSSPCFRO SMA", "SSSPCCEN SMA", "SSSPCSUA NON", "SSSPCSWF NO")
        await _settle(hass)
        assert state("C") is None
        assert state("SL") is disabled


async def test_distances_follow_a_speaker_removed_on_the_receiver(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Like the levels: a distance of a speaker removed is hidden."""
    from .common import registry_entry

    registry = er.async_get(hass)
    telnet = FakeTelnet.instances[-1]

    def distance(code: str) -> er.RegistryEntryDisabler | None:
        return registry_entry(hass, loaded_entry, f"number_channel_distance_{code}").disabled_by

    with patch("custom_components.denonavr_plus.SPEAKERS_SETTLE", 0):
        telnet.push("SSSPCFRO SMA", "SSSPCCEN SMA", "SSSPCSUA NON", "SSSPCSWF NO")
        await _settle(hass)
        for code in ("FL", "C"):
            registry.async_update_entity(
                registry_entry(hass, loaded_entry, f"number_channel_distance_{code}").entity_id,
                disabled_by=None,
            )
        telnet.push("SSSPCFRO SMA", "SSSPCCEN NON", "SSSPCSUA NON", "SSSPCSWF NO")
        await _settle(hass)
        assert distance("C") is er.RegistryEntryDisabler.INTEGRATION
        assert distance("FL") is None
        telnet.push("SSSPCFRO SMA", "SSSPCCEN SMA", "SSSPCSUA NON", "SSSPCSWF NO")
        await _settle(hass)
        assert distance("C") is None


async def test_channels_wait_until_the_speaker_answer_is_complete(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An answer comes line by line; half of it must not switch channels off."""

    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from .common import registry_entry

    telnet = FakeTelnet.instances[-1]
    telnet.push("SSSPCFRO SMA")
    await _settle(hass)
    _show_trims(hass, loaded_entry, ("C",))
    trim = registry_entry(hass, loaded_entry, "number_channel_trim_C")
    assert trim.disabled_by is None
    telnet.push("SSSPCCEN SMA", "SSSPCSUA NON")
    await _settle(hass)
    assert registry_entry(hass, loaded_entry, "number_channel_trim_C").disabled_by is None

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=4))
    await hass.async_block_till_done()
    assert registry_entry(hass, loaded_entry, "number_channel_trim_C").disabled_by is None
    disabled = er.RegistryEntryDisabler.INTEGRATION
    assert registry_entry(hass, loaded_entry, "number_channel_trim_SL").disabled_by is disabled


async def test_activities_reload_only_when_a_zone_gains_or_loses_its_select(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus import activities

    from .common import entity_id

    def _set(*names: str) -> None:
        hass.config_entries.async_update_entry(
            loaded_entry,
            options={
                activities.OPT_ACTIVITIES: [
                    {"name": name, "zone": "main", "source": "CD"} for name in names
                ]
            },
        )

    _set("Film")
    await hass.async_block_till_done()
    coordinator = loaded_entry.runtime_data
    assert coordinator.activity_zones == frozenset({"main"})
    # Another name in the same zone: the select shows it without a reload.
    _set("Film", "Music")
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data is coordinator
    select = entity_id(hass, loaded_entry, "select_activity_main")
    assert hass.states.get(select).attributes["options"] == ["Film", "Music"]
    _set()
    await hass.async_block_till_done()
    assert loaded_entry.runtime_data is not coordinator
    assert loaded_entry.runtime_data.activity_zones == frozenset()
    # Its select goes with the last activity instead of staying "no longer provided".
    assert hass.states.get(select) is None
    from homeassistant.helpers import entity_registry as er

    assert er.async_get(hass).async_get(select) is None


async def test_linking_a_player_to_the_network_input_shows_the_music_switches(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from .common import registry_entry

    registry = er.async_get(hass)
    # One the user switched off stays off.
    registry.async_update_entity(
        registry_entry(hass, loaded_entry, "switch_music_zone2").entity_id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "linked_players": {"NETWORK": {"player": "media_player.music_assistant"}},
        },
    )
    await hass.async_block_till_done()
    assert registry_entry(hass, loaded_entry, "switch_music_main").disabled_by is None
    assert (
        registry_entry(hass, loaded_entry, "switch_music_zone2").disabled_by
        is er.RegistryEntryDisabler.USER
    )


async def test_a_receiver_without_its_model_yet_is_tried_again(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Still starting, it answers without the model it named before: tried again.

    No entities named "Denon AVR", and the reason says it was reached.
    """
    import re

    from custom_components.denonavr_plus.avr.transport import GoformClient
    from custom_components.denonavr_plus.coordinator import snapshot_store

    from .fakes import RECORDING, deviceinfo

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION)
    entry.add_to_hass(hass)
    await snapshot_store(hass, entry.entry_id).async_save({"device": {"model_name": "AVC-X3700H"}})
    text = re.sub(r"<ModelName>.*?</ModelName>", "", deviceinfo(RECORDING), flags=re.S)
    with patch.object(GoformClient, "async_get_device_info", AsyncMock(return_value=text)):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.reason is not None and "not named its model" in entry.reason
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_a_receiver_that_never_names_its_model_is_set_up(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Without a model it never named (no ModelName): set up, not retried forever."""
    import re

    from custom_components.denonavr_plus.avr.transport import GoformClient

    from .fakes import RECORDING, deviceinfo

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION)
    entry.add_to_hass(hass)
    text = re.sub(r"<ModelName>.*?</ModelName>", "", deviceinfo(RECORDING), flags=re.S)
    with patch.object(GoformClient, "async_get_device_info", AsyncMock(return_value=text)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_showing_zone_2_later_reloads_once(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Zone 2 shown in the options: one reload, which also preselects its inputs."""
    from custom_components.denonavr_plus import choices, zone_sources

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: HOST},
        minor_version=CONFIG_MINOR_VERSION,
        options={choices.OPT_ZONES: ["main"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    reload = hass.config_entries.async_reload
    reloads: list[str] = []

    async def counted(entry_id: str) -> bool:
        reloads.append(entry_id)
        return await reload(entry_id)

    with patch.object(hass.config_entries, "async_reload", counted):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, choices.OPT_ZONES: ["main", "zone2"]}
        )
        await hass.async_block_till_done()
    assert len(reloads) == 1
    assert entry.runtime_data.markers.get(zone_sources.OPT_ZONE2_SOURCES_ASKED) is True
    assert await hass.config_entries.async_unload(entry.entry_id)
