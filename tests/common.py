"""Helpers shared by the platform tests."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
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


async def options_step(hass: HomeAssistant, entry: Any, step: str) -> Any:
    """Open the options and pick a step from the menu."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert step in result["menu_options"], result["menu_options"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


def report_notification(hass: HomeAssistant, entry: Any) -> dict[str, Any] | None:
    """The notification pointing to the prefilled GitHub issue, if shown."""
    from homeassistant.components import persistent_notification

    from custom_components.denonavr_plus.const import DOMAIN

    notifications = persistent_notification._async_get_or_create_notifications(hass)
    return notifications.get(f"{DOMAIN}_capability_report_{entry.entry_id}")


async def set_up_and_settle(hass: HomeAssistant, **options: Any) -> MockConfigEntry:
    """Set up an entry and wait until its start is settled (the check after it is through).

    What the setup flow left to do (check, test) starts only then.
    """
    import asyncio
    from unittest.mock import patch

    from homeassistant.const import CONF_HOST

    from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.0.2.10"},
        options=options,
        minor_version=CONFIG_MINOR_VERSION,
    )
    entry.add_to_hass(hass)
    with patch("custom_components.denonavr_plus.avr.device.RESYNC_QUIET", 0.05):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        if hasattr(entry, "runtime_data"):
            async with asyncio.timeout(5):
                await entry.runtime_data.start_settled.wait()
    await hass.async_block_till_done()
    return entry
