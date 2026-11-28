"""Which entities there are: one plan, from the discovery, the state and the options.

Each platform has a `planned(coordinator, options)` function that builds its
entities without side effects; its `async_setup_entry` adds exactly those
(entity.add_planned keeps their unique ids). This module puts the platforms
together for the two other questions about the entities:

- After a start, once the receiver has answered everything, does it report
  other capabilities than the entities were built from? `changed()` builds
  the plan again and compares the unique ids (coordinator._async_after_start).
- Which registry entries are left from options that changed? An entity the
  options leave out (a hidden zone, a zone without activities any more) is
  removed instead of staying "no longer provided". Only those: an entity
  whose capability the receiver did not report at this start keeps its
  registry entry, with the user's name, room and settings
  (`async_remove_unplanned`).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity

from . import (
    activities,
    binary_sensor,
    button,
    choices,
    media_player,
    number,
    remote,
    select,
    sensor,
    switch,
    update,
)

if TYPE_CHECKING:
    from .coordinator import DenonAvrConfigEntry, DenonAvrCoordinator

PLANS: Mapping[str, Callable[[DenonAvrCoordinator, dict[str, Any]], list[Any]]] = {
    Platform.BINARY_SENSOR: binary_sensor.planned,
    Platform.BUTTON: button.planned,
    Platform.MEDIA_PLAYER: media_player.planned,
    Platform.NUMBER: number.planned,
    Platform.REMOTE: remote.planned,
    Platform.SELECT: select.planned,
    Platform.SENSOR: sensor.planned,
    Platform.SWITCH: switch.planned,
    Platform.UPDATE: update.planned,
}


def unique_ids(
    coordinator: DenonAvrCoordinator, options: dict[str, Any], platforms: list[str]
) -> dict[str, frozenset[str]]:
    """The unique ids the plan gives each platform."""

    return {
        platform: frozenset(
            entity.unique_id
            for entity in PLANS[platform](coordinator, options)
            if isinstance(entity, Entity) and entity.unique_id is not None
        )
        for platform in platforms
    }


def changed(coordinator: DenonAvrCoordinator, options: dict[str, Any]) -> list[str]:
    """The unique ids the plan adds or drops against what the platforms were set up with."""

    now = unique_ids(coordinator, options, list(coordinator.planned))
    return sorted(
        unique_id
        for platform, built in coordinator.planned.items()
        for unique_id in built ^ now[platform]
    )


def _every_option(coordinator: DenonAvrCoordinator, options: dict[str, Any]) -> dict[str, Any]:
    """The options with every zone shown and an activity on each zone.

    What the plan would hold if nothing were left out by the options; the
    difference to the real plan is what the options leave out.
    """

    zones = [zone.id for zone in coordinator.device.discovery.zones]
    every = {key: value for key, value in options.items() if key != choices.OPT_ZONES}
    every[activities.OPT_ACTIVITIES] = [
        {activities.KEY_NAME: f"plan {zone}", activities.KEY_ZONE: zone, activities.KEY_SOURCE: ""}
        for zone in zones
    ]
    return every


@callback
def async_remove_unplanned(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, coordinator: DenonAvrCoordinator
) -> None:
    """Remove the registry entries of entities the options leave out (E2: only those)."""

    options = dict(entry.options)
    platforms = list(coordinator.planned)
    planned = unique_ids(coordinator, options, platforms)
    possible = unique_ids(coordinator, _every_option(coordinator, options), platforms)
    left_out = {
        unique_id for platform in platforms for unique_id in possible[platform] - planned[platform]
    }
    if not left_out:
        return
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.unique_id in left_out:
            registry.async_remove(item.entity_id)
