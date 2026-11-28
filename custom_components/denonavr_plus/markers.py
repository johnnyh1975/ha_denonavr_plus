"""What the integration keeps for itself about an entry, apart from the options (172).

The options hold what the user chose. Markers are the integration's own
bookkeeping: which speakers the channel defaults were applied for, which
entities a group turned on, whether Zone 2's inputs were chosen, what the
setup flow left to do after the first start. Kept in the options they
would set off the options listener (entity groups, reload checks) on every
write, and a write between reading and saving the options could get lost.

Here they are in a store of their own per entry. Writing one sets nothing
off; the store is saved shortly after, at unload and when Home Assistant
stops.

Before 1.7 the markers were options. The migration to entry version 1.4
copies them here and leaves them in the options for one release, so going
back to 1.6 finds them. The two that are set once and never change back
(`MIRRORED`: the channel defaults were applied, Zone 2's inputs were chosen)
are also written to the options when 1.7 sets them first: 1.6 would apply
the channel defaults or the Zone 2 preselection again without them. They
are never read from there. The hand-over from the setup flow (`PENDING`)
still goes through the options (the flow has no entry id to store under
yet): every setup takes those into the store and removes them from the
options.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

#: The speakers the channel defaults were last applied for (__init__.py).
CHANNELS_APPLIED = "channels_configured"
#: The channel defaults were applied once.
CHANNELS_INITIALIZED = "channels_initialized"
#: The unique ids an entity group turned on (entity_groups.py).
GROUP_ENABLED = "entity_groups_enabled"
#: Zone 2's inputs were chosen, by the user or the preselection (zone_sources.py).
ZONE2_SOURCES_ASKED = "zone2_sources_asked"
#: Markers that were options before 1.7; the migration copies them.
PERSISTENT = (CHANNELS_APPLIED, CHANNELS_INITIALIZED, GROUP_ENABLED, ZONE2_SOURCES_ASKED)
#: Set once, never back: also written to the options, for going back to 1.6.
MIRRORED = (CHANNELS_INITIALIZED, ZONE2_SOURCES_ASKED)

#: Left by the setup flow for the first start: the capability check, the
#: command test, taking another integration's entry over.
CHECK_PENDING = "capability_check_pending"
TEST_PENDING = "command_test_pending"
MOVE_PENDING = "move_pending"
PENDING = (CHECK_PENDING, TEST_PENDING, MOVE_PENDING)

STORE_VERSION = 1
#: Seconds after a change before the store is written.
SAVE_DELAY = 1.0


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.markers")


class Markers:
    """The markers of one entry, loaded at setup (coordinator.async_setup)."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = _store(hass, entry_id)
        self._data: dict[str, Any] = {}
        self._closed = False

    async def async_load(self) -> None:
        self._data = dict(await self._store.async_load() or {})

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    @callback
    def set(self, key: str, value: Any) -> None:
        if self._closed or (key in self._data and self._data[key] == value):
            return
        self._data[key] = value
        self._store.async_delay_save(lambda: dict(self._data), SAVE_DELAY)

    @callback
    def remove(self, key: str) -> None:
        if not self._closed and key in self._data:
            del self._data[key]
            self._store.async_delay_save(lambda: dict(self._data), SAVE_DELAY)

    async def async_save(self) -> None:
        """Write now (at unload: the next setup reads the store)."""

        await self._store.async_save(dict(self._data))

    async def async_close(self) -> None:
        """Write now and take no more changes: after an unload the next setup
        has its own markers, and a late write of this one would overwrite them."""

        await self.async_save()
        self._closed = True

    @callback
    def async_take_pending(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Take what the setup flow left in the options; they keep only choices."""

        found = {key: entry.options[key] for key in PENDING if key in entry.options}
        if not found:
            return
        for key, value in found.items():
            self.set(key, value)
        update_options(hass, entry, remove=found)


async def async_copy_from_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Migration: the markers that were options, into the store (they stay as options too)."""

    store = _store(hass, entry.entry_id)
    data = dict(await store.async_load() or {})
    for key in PERSISTENT:
        if key in entry.options and key not in data:
            data[key] = entry.options[key]
    await store.async_save(data)


async def async_remove(hass: HomeAssistant, entry_id: str) -> None:
    """With the entry."""

    await _store(hass, entry_id).async_remove()


@callback
def update_options(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    changes: dict[str, Any] | None = None,
    remove: Any = (),
) -> None:
    """Change some options, read and written in one step (no await between).

    Every option write goes through here: the options are read at the moment
    of writing, so a change made meanwhile elsewhere is not lost.
    """

    options = {key: value for key, value in entry.options.items() if key not in remove}
    options.update(changes or {})
    if options != dict(entry.options):
        hass.config_entries.async_update_entry(entry, options=options)
