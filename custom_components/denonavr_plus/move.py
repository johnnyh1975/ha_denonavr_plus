"""Moving a receiver over from another Denon integration, and back.

From the official Denon AVR Network Receivers integration (`denonavr`) or
from Denon AVR (`denon_avr`, LaserGuruGuy), whose entry for the same
receiver is found by its address, MAC or serial number:

1. The old entry is disabled (the receiver takes one telnet session).
2. Each old entity with a counterpart here is handed over with
   `async_update_entity_platform`: it keeps its entity id, name, area and
   history, and is from then on this integration's entity. The counterpart
   that this integration had created is removed first.
   - `denon_avr` used the same unique ids, so the counterparts are found by
     them; `denonavr` has only media players (`<id>-Main`, `-Zone2`,
     `-Zone3`).
3. From `denonavr`, the volume scale option of the official integration is
   switched on, so `volume_set` levels sound the same.
4. What automations and scripts use that is not here any more is listed in
   a repair (move_check.py).

Everything handed over is kept in .storage, so the move can be undone from
the options: this entry is disabled, the entities go back, the old entry is
enabled again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryDisabler
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import UNDEFINED, UndefinedType

from .const import DOMAIN
from .move_check import Finding, Replacements, findings
from .volume import OPT_OFFICIAL_SCALE

if TYPE_CHECKING:
    from .coordinator import DenonAvrConfigEntry

_LOGGER = logging.getLogger(__name__)

OFFICIAL = "denonavr"
LASERGURUGUY = "denon_avr"
OLD_DOMAINS = (OFFICIAL, LASERGURUGUY)
#: How the old integrations are called (product names, the same in every language).
NAMES = {OFFICIAL: "Denon AVR Network Receivers", LASERGURUGUY: "Denon AVR"}

#: Entry option: the old entries to take over at the next setup (chosen when
#: the receiver was added; the old entries are disabled already).
OPT_MOVE_PENDING = "move_pending"
ISSUE_AVAILABLE = "move_available"
ISSUE_CHECK = "move_check"

#: The official integration's zones and the keys of the media players here.
_OFFICIAL_ZONES = {"Main": "media_player_main", "Zone2": "media_player_zone2", "Zone3": "media_player_zone3"}
#: Keys of the entities that replace attributes and sound modes of `denonavr`.
_SOUND_MODE_SENSOR = "sensor_sound_mode"
_DYNAMIC_EQ_SWITCH = "switch_dynamic_eq"
_SOUND_MODE_GROUP = "select_sound_mode_group"
_STORE_VERSION = 1


def store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    return Store(hass, _STORE_VERSION, f"{DOMAIN}.{entry_id}.move")


@dataclass
class Moved:
    """What a move handed over, enough to hand it back."""

    domain: str
    entry_id: str
    title: str
    entities: list[dict[str, Any]] = field(default_factory=list)
    official_scale: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "entry_id": self.entry_id,
            "title": self.title,
            "entities": self.entities,
            "official_scale": self.official_scale,
        }


# Finding the old entries -----------------------------------------------------


def old_entries(
    hass: HomeAssistant, host: str | None, mac: str | None, serial: str | None
) -> list[ConfigEntry]:
    """The other integrations' entries for this receiver."""

    mac = format_mac(mac) if mac else None
    found = []
    for domain in OLD_DOMAINS:
        for entry in hass.config_entries.async_entries(domain):
            unique_id = (entry.unique_id or "").lower()
            if (
                (host and entry.data.get(CONF_HOST) == host)
                or (mac and unique_id == mac)
                or (serial and unique_id.endswith(f"-{serial.lower()}"))
            ):
                found.append(entry)
    return found


def running_old_entries(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> list[ConfigEntry]:
    """Old entries for this entry's receiver that are not disabled."""

    device = entry.runtime_data.device.discovery.device
    return [
        old
        for old in old_entries(
            hass, entry.data.get(CONF_HOST), device.mac_address, device.serial_number
        )
        if old.disabled_by is None
    ]


async def async_disable(hass: HomeAssistant, old: ConfigEntry) -> None:
    """Disable an old entry (it unloads): the receiver has one telnet session."""

    if old.disabled_by is None:
        await hass.config_entries.async_set_disabled_by(old.entry_id, ConfigEntryDisabler.USER)


# Handing over ----------------------------------------------------------------


def pairs(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, old: ConfigEntry
) -> list[tuple[er.RegistryEntry, er.RegistryEntry]]:
    """(old entity, its counterpart here) for each old entity that has one."""

    registry = er.async_get(hass)
    ours = {
        (item.domain, _key(item.unique_id)): item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    found = []
    for item in er.async_entries_for_config_entry(registry, old.entry_id):
        if old.domain == LASERGURUGUY:
            key = _key(item.unique_id)
        else:
            zone = item.unique_id.rsplit("-", 1)[-1]
            key = _OFFICIAL_ZONES.get(zone, "") if item.domain == "media_player" else ""
        counterpart = ours.get((item.domain, key))
        if counterpart is not None:
            found.append((item, counterpart))
    return found


def _changed(now: str, new: str) -> str | UndefinedType:
    """The new unique id, or UNDEFINED when it stays the same.

    Home Assistant checks a new unique id against the entity's current
    platform, where an unchanged one is found as in use by the entity itself.
    """

    return UNDEFINED if new == now else new


def _key(unique_id: str) -> str:
    """The entity's key: the unique id without the receiver's identifier."""

    return unique_id.split("_", 1)[1] if "_" in unique_id else unique_id


async def async_move(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, old: ConfigEntry, *, reload: bool = True
) -> Moved:
    """Hand the old entry's entities over to this entry; reload it afterwards.

    The handed-over entities load with the reload (`reload=False`: the
    caller reloads).
    """

    await async_disable(hass, old)
    registry = er.async_get(hass)
    moved = Moved(old.domain, old.entry_id, old.title)
    for item, counterpart in pairs(hass, entry, old):
        moved.entities.append(
            {
                "entity_id": item.entity_id,
                "platform": item.platform,
                "unique_id": item.unique_id,
                "device_id": item.device_id,
                "our_unique_id": counterpart.unique_id,
            }
        )
        registry.async_remove(counterpart.entity_id)
        registry.async_update_entity_platform(
            item.entity_id,
            DOMAIN,
            new_config_entry_id=entry.entry_id,
            new_unique_id=_changed(item.unique_id, counterpart.unique_id),
            new_device_id=None,
        )
    options = {k: v for k, v in entry.options.items() if k != OPT_MOVE_PENDING}
    pending = [item for item in entry.options.get(OPT_MOVE_PENDING, []) if item != old.entry_id]
    if pending:
        options[OPT_MOVE_PENDING] = pending
    if old.domain == OFFICIAL and not options.get(OPT_OFFICIAL_SCALE):
        options[OPT_OFFICIAL_SCALE] = True
        moved.official_scale = True
    kept = await store(hass, entry.entry_id).async_load() or {}
    moves = [item for item in kept.get("moves", []) if item["entry_id"] != old.entry_id]
    await store(hass, entry.entry_id).async_save({"moves": [*moves, moved.as_dict()]})
    hass.config_entries.async_update_entry(entry, options=options)
    _LOGGER.info(
        "Took over %s entities from %s (%s)", len(moved.entities), old.title, old.domain
    )
    if old.domain == OFFICIAL:
        async_check(hass, entry, moved)
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_AVAILABLE}_{entry.entry_id}")
    if reload:
        hass.config_entries.async_schedule_reload(entry.entry_id)
    return moved


async def async_run_pending(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """The move chosen when the receiver was added (the entities exist now).

    Not a task of the entry: the reload at the end would cancel it.
    """

    for entry_id in list(entry.options.get(OPT_MOVE_PENDING, [])):
        old = hass.config_entries.async_get_entry(entry_id)
        if old is not None:
            await async_move(hass, entry, old, reload=False)
    if entry.options.get(OPT_MOVE_PENDING):
        options = {k: v for k, v in entry.options.items() if k != OPT_MOVE_PENDING}
        hass.config_entries.async_update_entry(entry, options=options)
    hass.config_entries.async_schedule_reload(entry.entry_id)


async def async_moved(hass: HomeAssistant, entry_id: str) -> list[dict[str, Any]]:
    """The moves that can be undone."""

    kept = await store(hass, entry_id).async_load() or {}
    return list(kept.get("moves", []))


async def async_move_back(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """Undo every move: this entry off, the entities back, the old entries on."""

    moves = await async_moved(hass, entry.entry_id)
    await hass.config_entries.async_set_disabled_by(entry.entry_id, ConfigEntryDisabler.USER)
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    for move in moves:
        old = hass.config_entries.async_get_entry(move["entry_id"])
        if old is None:
            continue
        for item in move["entities"]:
            current = registry.async_get(item["entity_id"])
            if current is None or current.platform != DOMAIN:
                continue
            device_id = item["device_id"] if devices.async_get(item["device_id"] or "") else None
            registry.async_update_entity_platform(
                item["entity_id"],
                item["platform"],
                new_config_entry_id=old.entry_id,
                new_unique_id=_changed(current.unique_id, item["unique_id"]),
                new_device_id=device_id,
            )
        await hass.config_entries.async_set_disabled_by(old.entry_id, None)
    if any(move.get("official_scale") for move in moves):
        options = {k: v for k, v in entry.options.items() if k != OPT_OFFICIAL_SCALE}
        hass.config_entries.async_update_entry(entry, options=options)
    await store(hass, entry.entry_id).async_remove()
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_CHECK}_{entry.entry_id}")


# Offering it, and what to check afterwards ---------------------------------------


def async_update_issue(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """Offer the move while another integration runs this receiver too."""

    issue_id = f"{ISSUE_AVAILABLE}_{entry.entry_id}"
    running = running_old_entries(hass, entry)
    if not running:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_AVAILABLE,
        translation_placeholders={
            "title": running[0].title,
            "integration": NAMES[running[0].domain],
        },
        data={"entry_id": entry.entry_id, "old_entry_id": running[0].entry_id},
    )


def async_check(hass: HomeAssistant, entry: DenonAvrConfigEntry, moved: Moved) -> list[Finding]:
    """List what automations and scripts use that is not here; a repair if any."""

    found = findings(_configurations(hass), _replacements(hass, entry, moved))
    issue_id = f"{ISSUE_CHECK}_{entry.entry_id}"
    if not found:
        ir.async_delete_issue(hass, DOMAIN, issue_id)
        return found
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CHECK,
        translation_placeholders={
            "title": moved.title,
            "findings": "\n".join(f"- {finding.line()}" for finding in found),
        },
        data={"entry_id": entry.entry_id},
    )
    return found


def _configurations(hass: HomeAssistant) -> list[tuple[str, Any]]:
    """The automations and scripts as Home Assistant loaded them."""

    configs: list[tuple[str, Any]] = []
    for domain in ("automation", "script"):
        component = hass.data.get(domain)
        for entity in getattr(component, "entities", ()):
            config = getattr(entity, "raw_config", None)
            if config is not None:
                configs.append((entity.entity_id, config))
    return configs


def _replacements(hass: HomeAssistant, entry: DenonAvrConfigEntry, moved: Moved) -> Replacements:
    registry = er.async_get(hass)
    ours = {
        _key(item.unique_id): item.entity_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    main = entry.runtime_data.device.discovery
    return Replacements(
        moved=frozenset(item["entity_id"] for item in moved.entities),
        sound_mode_sensor=ours.get(_SOUND_MODE_SENSOR),
        dynamic_eq_switch=ours.get(_DYNAMIC_EQ_SWITCH),
        sound_mode_group_select=ours.get(_SOUND_MODE_GROUP),
        sound_modes=frozenset(main.all_sound_modes),
    )
