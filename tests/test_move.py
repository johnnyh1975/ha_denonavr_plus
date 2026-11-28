"""Tests for move.py: taking a receiver over from another Denon integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryDisabler
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import move
from custom_components.denonavr_plus.const import DOMAIN
from custom_components.denonavr_plus.volume import OPT_OFFICIAL_SCALE

from .fakes import HOST

SERIAL = "0000000000001"


def _ours(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, er.RegistryEntry]:
    """This integration's entities by key (the unique id without the identifier)."""
    registry = er.async_get(hass)
    return {
        item.unique_id.split("_", 1)[1]: item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


def _official(hass: HomeAssistant, **kwargs: Any) -> MockConfigEntry:
    """An entry of the official integration with its main zone media player."""
    old = MockConfigEntry(
        domain=move.OFFICIAL,
        unique_id=f"AVC-X3700H-{SERIAL}",
        data={CONF_HOST: HOST},
        title="Wohnzimmer",
        **kwargs,
    )
    old.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "media_player",
        move.OFFICIAL,
        f"{old.unique_id}-Main",
        config_entry=old,
        suggested_object_id="wohnzimmer",
    )
    return old


async def _move(hass: HomeAssistant, entry: MockConfigEntry, old: MockConfigEntry) -> move.Moved:
    moved = await move.async_move(hass, entry, old)
    await hass.async_block_till_done()
    return moved


async def test_an_official_entry_is_found_by_address_mac_or_serial(
    hass: HomeAssistant,
) -> None:
    by_address = MockConfigEntry(domain=move.OFFICIAL, data={CONF_HOST: HOST})
    by_serial = MockConfigEntry(
        domain=move.OFFICIAL, unique_id=f"AVR-X2600H-{SERIAL}", data={CONF_HOST: "192.0.2.99"}
    )
    by_mac = MockConfigEntry(
        domain=move.LASERGURUGUY, unique_id="aa:bb:cc:dd:ee:ff", data={CONF_HOST: "192.0.2.98"}
    )
    other = MockConfigEntry(domain=move.OFFICIAL, data={CONF_HOST: "192.0.2.97"})
    for item in (by_address, by_serial, by_mac, other):
        item.add_to_hass(hass)
    found = move.old_entries(hass, HOST, "AA:BB:CC:DD:EE:FF", SERIAL)
    assert {item.entry_id for item in found} == {
        by_address.entry_id,
        by_serial.entry_id,
        by_mac.entry_id,
    }
    assert move.old_entries(hass, None, None, None) == []


async def test_the_official_media_player_keeps_its_entity_id_name_and_area(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    old = _official(hass)
    registry = er.async_get(hass)
    room = ar.async_get(hass).async_create("Wohnzimmer")
    registry.async_update_entity("media_player.wohnzimmer", name="Receiver", area_id=room.id)
    ours = _ours(hass, loaded_entry)["media_player_main"]

    moved = await _move(hass, loaded_entry, old)

    item = registry.async_get("media_player.wohnzimmer")
    assert item is not None
    assert (item.platform, item.config_entry_id, item.unique_id) == (
        DOMAIN,
        loaded_entry.entry_id,
        ours.unique_id,
    )
    assert (item.name, item.area_id) == ("Receiver", room.id)
    # The entity this integration had made for it is gone; the moved one runs.
    assert registry.async_get(ours.entity_id) is None
    assert hass.states.get("media_player.wohnzimmer") is not None
    # The old entry is off: the receiver takes one telnet session.
    assert old.disabled_by is ConfigEntryDisabler.USER
    # volume_set levels of automations sound as with the official integration.
    assert loaded_entry.options[OPT_OFFICIAL_SCALE] is True
    assert moved.official_scale is True
    assert [item["entity_id"] for item in moved.entities] == ["media_player.wohnzimmer"]
    assert await move.async_moved(hass, loaded_entry.entry_id) == [moved.as_dict()]


async def test_denon_avr_entities_are_matched_by_their_unique_ids(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Denon AVR Plus kept the unique ids of LaserGuruGuy's integration."""
    registry = er.async_get(hass)
    ours = _ours(hass, loaded_entry)
    old = MockConfigEntry(domain=move.LASERGURUGUY, data={CONF_HOST: HOST}, title="Denon")
    old.add_to_hass(hass)
    switch = ours["switch_dynamic_eq"]
    registry.async_get_or_create(
        "switch",
        move.LASERGURUGUY,
        switch.unique_id,
        config_entry=old,
        suggested_object_id="denon_dynamic_eq",
    )
    # An entity this integration no longer has stays with the old entry.
    gone = registry.async_get_or_create(
        "sensor",
        move.LASERGURUGUY,
        f"{switch.unique_id.split('_', 1)[0]}_sensor_volume_db",
        config_entry=old,
    )

    moved = await _move(hass, loaded_entry, old)

    assert [item["entity_id"] for item in moved.entities] == ["switch.denon_dynamic_eq"]
    taken = registry.async_get("switch.denon_dynamic_eq")
    assert taken is not None and taken.platform == DOMAIN
    left = registry.async_get(gone.entity_id)
    assert left is not None and left.config_entry_id == old.entry_id
    # Not from the official integration: the volume scale stays as it was.
    assert OPT_OFFICIAL_SCALE not in loaded_entry.options


async def test_the_move_is_undone(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    old = _official(hass)
    await _move(hass, loaded_entry, old)
    # The official integration is not installed here: enabling its entry only
    # fails to set it up.
    await move.async_move_back(hass, loaded_entry)
    await hass.async_block_till_done()

    item = er.async_get(hass).async_get("media_player.wohnzimmer")
    assert item is not None
    assert (item.platform, item.config_entry_id, item.unique_id) == (
        move.OFFICIAL,
        old.entry_id,
        f"{old.unique_id}-Main",
    )
    assert old.disabled_by is None
    assert loaded_entry.disabled_by is ConfigEntryDisabler.USER
    assert OPT_OFFICIAL_SCALE not in loaded_entry.options
    assert await move.async_moved(hass, loaded_entry.entry_id) == []


async def test_an_entity_changed_since_is_left_where_it_is(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Removed, or given to another integration since: nothing to hand back."""
    old = _official(hass)
    await _move(hass, loaded_entry, old)
    er.async_get(hass).async_remove("media_player.wohnzimmer")
    gone = MockConfigEntry(domain=move.OFFICIAL)
    with patch.object(
        move,
        "async_moved",
        AsyncMock(return_value=[{"entry_id": gone.entry_id, "entities": [], "title": "x"}]),
    ):
        await move.async_move_back(hass, loaded_entry)
        await hass.async_block_till_done()
    assert er.async_get(hass).async_get("media_player.wohnzimmer") is None


async def test_automations_using_the_official_integration_are_listed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    old = _official(hass)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "1",
                    "alias": "Raw command",
                    "triggers": [{"trigger": "event", "event_type": "x"}],
                    "actions": [
                        {
                            "action": "denonavr.get_command",
                            "target": {"entity_id": "media_player.wohnzimmer"},
                            "data": {"command": "/goform/formiPhoneAppDirect.xml?MVUP"},
                        }
                    ],
                }
            ]
        },
    )
    await _move(hass, loaded_entry, old)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"move_check_{loaded_entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders == {
        "title": "Wohnzimmer",
        "findings": "- automation.raw_command: denonavr.get_command → denonavr_plus.send_command",
    }


async def test_nothing_to_check_leaves_no_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    await _move(hass, loaded_entry, _official(hass))
    issue_id = f"move_check_{loaded_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_another_integration_running_the_receiver_is_a_repair(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    issue_id = f"move_available_{loaded_entry.entry_id}"
    old = _official(hass)
    move.async_update_issue(hass, loaded_entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {
        "title": "Wohnzimmer",
        "integration": "Denon AVR Network Receivers",
    }
    assert issue.data == {"entry_id": loaded_entry.entry_id, "old_entry_id": old.entry_id}
    # Disabled, it no longer disturbs: the repair goes.
    await move.async_disable(hass, old)
    move.async_update_issue(hass, loaded_entry)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
