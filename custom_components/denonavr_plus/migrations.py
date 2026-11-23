"""Config entry migrations, one step per schema version.

Every change to what a config entry stores (its data, its options) or to
what the entity and device registries key on (unique ids, device
identifiers) raises the entry version and adds a step here, so an existing
installation is brought along at its next start instead of breaking. Steps
stay forever: somebody may update from any released version.

Versioning follows Home Assistant:

- a **minor** version for a change an older release can still read (a new
  option with a default, a cleaned-up value). Home Assistant does not stop a
  downgrade across minor versions.
- a **major** version for a change an older release cannot read (a renamed
  key, new unique ids). Home Assistant refuses to load an entry whose major
  version is newer than the installed release.

Version history (the entry version, not the release version):

  1.1 (0.1.0): host, and the goform port once detected; options hold the
               one-time marker for disabling unconfigured channels.
  1.2:         the read-only volume sensor (unique id ..._sensor_volume_db)
               is replaced by a settable volume number per zone; its
               registry entry is removed. Options may hold volume settings
               per zone, absent means none.

To add a step: raise CONFIG_VERSION or CONFIG_MINOR_VERSION in const.py,
register a function under the version it starts from in STEPS, and add a
test in tests/test_migrations.py that builds an entry of the old version and
checks the result.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import CONFIG_MINOR_VERSION, CONFIG_VERSION
from .coordinator import DenonAvrConfigEntry

_LOGGER = logging.getLogger(__name__)

#: A step brings an entry from the version it is registered under to the
#: next one and returns that version as (major, minor). It updates the entry
#: itself, through hass.config_entries.async_update_entry or the registries.
Step = Callable[[HomeAssistant, DenonAvrConfigEntry], Awaitable[tuple[int, int]]]

async def _remove_volume_sensor(
    hass: HomeAssistant, entry: DenonAvrConfigEntry
) -> tuple[int, int]:
    """1.1 -> 1.2: the volume number replaces the read-only volume sensor."""

    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain == "sensor" and item.unique_id.endswith("_sensor_volume_db"):
            registry.async_remove(item.entity_id)
    return (1, 2)


#: The steps, by the version they start from.
STEPS: dict[tuple[int, int], Step] = {
    (1, 1): _remove_volume_sensor,
}


async def async_migrate_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Bring a config entry to the current version, step by step."""

    current = (CONFIG_VERSION, CONFIG_MINOR_VERSION)
    version = (entry.version, entry.minor_version)
    if version[0] == current[0] and version[1] > current[1]:
        # Written by a newer release of the same major version (a downgrade):
        # minor changes stay readable, so load it as it is.
        return True
    while version < current:
        step = STEPS.get(version)
        if step is None:
            _LOGGER.error(
                "Cannot migrate the Denon AVR Plus entry %s from version %s.%s; "
                "remove it and add the receiver again",
                entry.title,
                *version,
            )
            return False
        new_version = await step(hass, entry)
        hass.config_entries.async_update_entry(
            entry, version=new_version[0], minor_version=new_version[1]
        )
        _LOGGER.info(
            "Migrated the Denon AVR Plus entry %s from version %s.%s to %s.%s",
            entry.title,
            *version,
            *new_version,
        )
        version = new_version
    return True
