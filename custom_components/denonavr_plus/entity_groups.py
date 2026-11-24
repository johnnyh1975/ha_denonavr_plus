"""Entity groups: show the entities that start hidden, a group at a time.

Most entities start disabled (see helpers.control_enabled_by_default): set-once
setup values, speaker setup, video, the graphic EQ, diagnostics. The options
turn them on by group instead of one by one on the device pages.

The groups follow the devices the entities are on (Audio, Speakers, Video and
Picture, Graphic EQ, the zones, the receiver itself) and, across them, the
diagnostic entities.

What the user did by hand stays: a group turns on only entities the
integration disabled, and turning a group off disables only those the group
turned on (their unique ids are kept in the options). An entity the user
disabled or enabled is never touched. Channel entities of speakers the
receiver does not have stay off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN

if TYPE_CHECKING:
    from .coordinator import DenonAvrConfigEntry

#: Entry option: the groups shown.
OPT_ENTITY_GROUPS = "entity_groups"
#: Entry option: the unique ids the groups turned on (and only these go off again).
OPT_GROUP_ENABLED = "entity_groups_enabled"

GROUPS = ("audio", "speakers", "video", "graphic_eq", "zones", "receiver", "diagnostic")

#: Sub-device (the suffix of its identifier) -> group.
_SUB_DEVICE_GROUP = {
    "audio": "audio",
    "speakers": "speakers",
    "video": "video",
    "picture": "video",
    "eq": "graphic_eq",
    "zone2": "zones",
    "zone3": "zones",
}
_CHANNEL_MARKERS = ("_channel_trim_", "_channel_distance_")


def _device_groups(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    """Device id -> the group of the entities on it."""

    groups: dict[str, str] = {}
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry_id):
        group = "receiver"
        for domain, value in device.identifiers:
            if domain != DOMAIN:
                continue
            for sub, sub_group in _SUB_DEVICE_GROUP.items():
                if value.endswith(f"_{sub}"):
                    group = sub_group
        groups[device.id] = group
    return groups


def group_of(item: er.RegistryEntry, devices: dict[str, str]) -> str:
    if item.entity_category is EntityCategory.DIAGNOSTIC:
        return "diagnostic"
    return devices.get(item.device_id or "", "receiver")


def _missing_channel(item: er.RegistryEntry, configured: set[str] | frozenset[str]) -> bool:
    """A channel entity of a speaker the receiver does not have."""

    for marker in _CHANNEL_MARKERS:
        if marker in item.unique_id:
            return bool(configured) and item.unique_id.split(marker)[-1] not in configured
    return False


def hidden_counts(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, configured: set[str] | frozenset[str]
) -> dict[str, int]:
    """Per group, how many entities it can show: hidden by the integration or by it."""

    devices = _device_groups(hass, entry.entry_id)
    ours = set(entry.options.get(OPT_GROUP_ENABLED, []))
    counts: dict[str, int] = {}
    for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id):
        hidden = item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
        if (hidden and not _missing_channel(item, configured)) or item.unique_id in ours:
            group = group_of(item, devices)
            counts[group] = counts.get(group, 0) + 1
    return counts


@callback
def async_apply(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, configured: set[str] | frozenset[str]
) -> bool:
    """Turn the chosen groups on and the others off; True when one was turned on.

    Entities turned off go at once. Those turned on appear when Home Assistant
    reloads the entry, which it does by itself half a minute after the last
    entity was enabled; a reload of our own would only come on top of it.
    """

    wanted = set(entry.options.get(OPT_ENTITY_GROUPS, []))
    ours = set(entry.options.get(OPT_GROUP_ENABLED, []))
    registry = er.async_get(hass)
    devices = _device_groups(hass, entry.entry_id)
    enabled = False
    kept: set[str] = set()
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        group = group_of(item, devices)
        if item.unique_id in ours:
            if item.disabled_by is not None:
                continue  # disabled since: by the user, or gone with its zone
            if group in wanted:
                kept.add(item.unique_id)
            else:
                registry.async_update_entity(
                    item.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
                )
            continue
        if (
            group in wanted
            and item.disabled_by is er.RegistryEntryDisabler.INTEGRATION
            and not _missing_channel(item, configured)
        ):
            registry.async_update_entity(item.entity_id, disabled_by=None)
            kept.add(item.unique_id)
            enabled = True
    if kept != ours:
        options: dict[str, Any] = {**entry.options, OPT_GROUP_ENABLED: sorted(kept)}
        if not kept:
            options.pop(OPT_GROUP_ENABLED)
        hass.config_entries.async_update_entry(entry, options=options)
    return enabled
