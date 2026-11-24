"""The Denon AVR Plus integration.

Sets up a single receiver from a config entry: it discovers the device over
HTTP, starts the telnet push transport, and forwards the platforms. The
coordinator (stored as the entry runtime data) owns the device connection.
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.typing import ConfigType

from . import choices, command_test, entity_groups
from .avr.profile import load_profile
from .const import CONF_HOST, CONF_PORT, DOMAIN, PLATFORMS
from .coordinator import (
    DenonAvrConfigEntry,
    DenonAvrCoordinator,
    connection_options,
    snapshot_store,
)
from .capabilities import OPT_CHECK_PENDING, CapabilityResults, async_run_pending
from .command_test import OPT_TEST_PENDING
from .entity import ZONE_DEVICES
from .migrations import async_migrate_entry as async_migrate_entry  # Home Assistant looks it up here
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

# One time marker (stored in the entry options) so the channel default disabling
# runs only once and never overrides the user's later manual choices.
_CHANNELS_INITIALIZED = "channels_initialized"
# Unique id fragment identifying per channel entities and how to read the code.
_CHANNEL_MARKER = "_channel_trim_"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions, once, so they exist even before an entry loads."""

    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Set up Denon AVR from a config entry."""

    host = entry.data[CONF_HOST]
    # Warm the protocol profile cache off the event loop so constructing the
    # device (which reads the bundled profile) does no blocking I/O in the loop.
    await hass.async_add_executor_job(load_profile)
    coordinator = DenonAvrCoordinator(hass, entry, host)

    # async_setup starts the telnet transport; if anything after that fails we
    # must stop it, otherwise the reconnecting task would leak (unload is not
    # called for a failed setup and runtime_data is never assigned).
    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except ConnectionError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_reach",
            translation_placeholders={"host": host, "error": str(err)},
        ) from err
    except Exception:
        await coordinator.async_shutdown()
        raise

    entry.runtime_data = coordinator
    # Entries created before the port was stored learn it at their first setup,
    # so later setups never have to guess it again.
    port = coordinator.device.http_port
    if port is not None and entry.data.get(CONF_PORT) != port:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_PORT: port})
    # Register the main receiver device up front so the logical sub-devices
    # (Audio/Video/Speakers/…) can link to it by id via `via_device_id`; the old
    # `via_device` identifier tuple is deprecated (removed in HA 2027.8.0).
    _register_main_device(hass, entry, coordinator)
    coordinator.shown_zones = _shown_zones(entry, coordinator)
    _remove_hidden_zones(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, _platforms(coordinator))
    _apply_channel_defaults(hass, entry, coordinator)
    if not coordinator.started_offline:
        entity_groups.async_apply(
            hass, entry, coordinator.device.discovery.configured_channels
        )
    _sub_devices_follow_room(hass, entry, coordinator)
    coordinator.capabilities.async_update_issues(coordinator.device.discovery.device.model_name)
    pending = entry.options.get(OPT_CHECK_PENDING) or entry.options.get(OPT_TEST_PENDING)
    if pending and not coordinator.started_offline:
        entry.async_create_background_task(
            hass, _async_run_pending(hass, entry), "denonavr_plus capability check"
        )
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_run_pending(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """What was chosen when the receiver was added: the check, then the command test."""

    test = bool(entry.options.get(OPT_TEST_PENDING))
    if entry.options.get(OPT_CHECK_PENDING):
        # With the test after it, the pointer to GitHub comes once, after both.
        await async_run_pending(hass, entry, report=not test)
    if test and not entry.options.get(OPT_CHECK_PENDING):
        await command_test.async_run_pending(hass, entry)


def _platforms(coordinator: DenonAvrCoordinator) -> list[Platform]:
    """All platforms, or only the media players when started without the receiver.

    The other entities need what only the receiver can tell; they keep their
    registry entries and come back with the reload once it answers.
    """

    return [Platform.MEDIA_PLAYER] if coordinator.started_offline else PLATFORMS


async def async_remove_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """Remove the kept discovery, capability check and command test with the entry."""

    await snapshot_store(hass, entry.entry_id).async_remove()
    results = CapabilityResults(hass, entry.entry_id)
    await results.async_remove()
    results.async_delete_issues()
    await command_test.CommandTestResults(hass, entry.entry_id, results.lock).async_remove()


def _shown_zones(entry: DenonAvrConfigEntry, coordinator: DenonAvrCoordinator) -> frozenset[str]:
    options = dict(entry.options)
    return frozenset(
        zone.id
        for zone in coordinator.device.discovery.zones
        if choices.zone_enabled(options, zone.id)
    )


def _remove_hidden_zones(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, coordinator: DenonAvrCoordinator
) -> None:
    """Remove the entities of zones the options leave out.

    Otherwise they would stay in the registry as "no longer provided".
    """

    hidden = {
        zone.id for zone in coordinator.device.discovery.zones
    } - coordinator.shown_zones
    if not hidden:
        return
    registry = er.async_get(hass)
    suffixes = tuple(
        f"_{kind}_{zone}"
        for zone in hidden
        for kind in ("media_player", "number_volume", "sensor_source")
    )
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.unique_id.endswith(suffixes):
            registry.async_remove(item.entity_id)
    # And the zone's own device, which has nothing left on it.
    devices = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(devices, entry.entry_id):
        if any(
            domain == DOMAIN and value.endswith(tuple(f"_{zone}" for zone in hidden))
            for domain, value in device.identifiers
        ):
            devices.async_remove_device(device.id)


async def _async_entry_updated(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> None:
    """Options changed: entity groups, and a reload for other zones or connection.

    The entities read the source, sound mode and volume options when they
    need them, so only a change of zones (entities created or removed) or of
    the connection (poll, HEOS, setup reads) needs a reload.
    """

    coordinator = entry.runtime_data
    if not coordinator.started_offline:
        entity_groups.async_apply(
            hass, entry, coordinator.device.discovery.configured_channels
        )
    if (
        _shown_zones(entry, coordinator) != coordinator.shown_zones
        or connection_options(entry) != coordinator.connection
    ):
        hass.config_entries.async_schedule_reload(entry.entry_id)
        return
    coordinator.async_update_listeners()


def _register_main_device(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    coordinator: DenonAvrCoordinator,
) -> None:
    """Create/refresh the main receiver device and record its registry id."""

    device = coordinator.device.discovery.device
    connections = set()
    if device.mac_address:
        connections.add((CONNECTION_NETWORK_MAC, device.mac_address))
    entry_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        connections=connections,
        identifiers={(DOMAIN, coordinator.device.identifier)},
        manufacturer=device.manufacturer or "Denon",
        model=device.model_name,
        name=device.model_name or "Denon AVR",
        sw_version=device.firmware_version,
        # hardware_type is the protocol family (avr-x-2016), not the hardware;
        # it is in the diagnostics. None clears what earlier versions stored.
        hw_version=None,
        serial_number=device.serial_number,
        configuration_url=coordinator.device.setup_url,
    )
    coordinator.main_device_id = entry_device.id


def _sub_devices_follow_room(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    coordinator: DenonAvrCoordinator,
) -> None:
    """Keep Audio, Video, Speakers and the other sub-devices in the receiver's room.

    A sub-device without a room, or in the room the receiver just left, moves
    with the receiver. One the user put in another room stays there.
    Otherwise their entities would be missing from the room's area. The zone
    devices are left alone: zone 2 plays in a room of its own.
    """

    registry = dr.async_get(hass)
    main_id = coordinator.main_device_id
    if main_id is None:
        return

    @callback
    def follow(previous_area: str | None) -> None:
        main = registry.async_get(main_id)
        if main is None:
            return
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if (
                device.via_device_id == main_id
                and not _is_zone_device(device)
                and device.area_id in (None, previous_area)
                and device.area_id != main.area_id
            ):
                registry.async_update_device(device.id, area_id=main.area_id)

    @callback
    def main_updated(event: Event[dr.EventDeviceRegistryUpdatedData]) -> None:
        data = event.data
        if data["action"] != "update" or data["device_id"] != main_id:
            return
        if "area_id" in data["changes"]:
            follow(data["changes"]["area_id"])

    follow(None)
    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, main_updated))


def _is_zone_device(device: dr.DeviceEntry) -> bool:
    """Whether a sub-device is the device of zone 2 or zone 3."""

    return any(
        domain == DOMAIN and value.endswith(tuple(f"_{zone}" for zone in ZONE_DEVICES))
        for domain, value in device.identifiers
    )


def _apply_channel_defaults(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    coordinator: DenonAvrCoordinator,
) -> None:
    """Disable per channel entities for speakers the receiver has not configured.

    Home Assistant keeps entity registry entries across a remove/re-add (keyed by
    unique id), so `entity_registry_enabled_default` cannot retroactively disable
    already registered channels. This runs once (guarded by a stored flag) and
    explicitly disables the non configured channels, while never touching a
    channel again afterwards so the user's manual enable/disable choices stick.
    """

    if entry.options.get(_CHANNELS_INITIALIZED):
        return
    configured = coordinator.device.discovery.configured_channels
    if not configured:
        # Speaker configuration unknown; do not disable anything (safe fallback).
        return

    registry = er.async_get(hass)
    for regentry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if _CHANNEL_MARKER not in regentry.unique_id:
            continue
        code = regentry.unique_id.split(_CHANNEL_MARKER)[-1]
        if code not in configured and regentry.disabled_by is None:
            registry.async_update_entity(
                regentry.entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )

    hass.config_entries.async_update_entry(
        entry, options={**entry.options, _CHANNELS_INITIALIZED: True}
    )


async def async_unload_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Unload a config entry and stop the telnet transport."""

    unloaded = await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry.runtime_data)
    )
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
