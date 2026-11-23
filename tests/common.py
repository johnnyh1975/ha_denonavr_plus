"""Helpers shared by the platform tests."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


def registry_entry(
    hass: HomeAssistant, entry: MockConfigEntry, key: str
) -> er.RegistryEntry:
    """The registry entry whose unique id ends with the given key."""
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.unique_id.endswith(f"_{key}"):
            return item
    raise AssertionError(f"no entity for {key}")


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, key: str) -> str:
    return registry_entry(hass, entry, key).entity_id


async def async_enable(hass: HomeAssistant, entry: MockConfigEntry, *keys: str) -> None:
    """Enable entities that start disabled, then reload the entry for them."""
    registry = er.async_get(hass)
    for key in keys:
        registry.async_update_entity(entity_id(hass, entry, key), disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
