"""The Denon AVR Plus integration.

Sets up a single receiver from a config entry: it discovers the device over
HTTP, starts the telnet push transport, and forwards the platforms. The
coordinator (stored as the entry runtime data) owns the device connection.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.typing import ConfigType

from . import choices, command_test, entity_groups, linked_players, move, zone_sources
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
# The speakers' channels the defaults were last applied for, so a speaker set
# up or removed on the receiver later is followed.
_CHANNELS_APPLIED = "channels_configured"
#: Seconds the reported speakers stay the same before the channels follow.
SPEAKERS_SETTLE = 3.0
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
    # Renamed and removed linked players, following one, keeping it at full volume.
    entry.async_on_unload(linked_players.LinkedPlayers(hass, entry).async_start())
    _apply_channel_defaults(hass, entry, coordinator)
    entry.async_on_unload(_follow_speakers(hass, entry, coordinator))
    if not coordinator.started_offline:
        entity_groups.async_apply(
            hass, entry, coordinator.device.discovery.configured_channels
        )
    _sub_devices_follow_room(hass, entry, coordinator)
    if not coordinator.started_offline:
        # Started from the stored data, the entry reloads once the receiver
        # answers; its generation is read then.
        coordinator.capabilities.async_update_issues(coordinator.device.discovery.device)
        zone_sources.async_update_issue(hass, entry)
    if entry.options.get(move.OPT_MOVE_PENDING):
        # Taking another integration's entry over, chosen when the receiver
        # was added; the reload after it runs what else is pending. It starts
        # once this setup has finished, which the reload would otherwise cut.
        started = False

        @callback
        def _start_move() -> None:
            nonlocal started
            if entry.state is ConfigEntryState.LOADED and not started:
                started = True
                # Not at once: the setup that is ending would see the reload.
                hass.async_create_background_task(
                    move.async_run_pending(hass, entry), "denonavr_plus move", eager_start=False
                )

        entry.async_on_unload(entry.async_on_state_change(_start_move))
        entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
        return True
    move.async_update_issue(hass, entry)
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
    await move.store(hass, entry.entry_id).async_remove()
    for issue in (move.ISSUE_AVAILABLE, move.ISSUE_CHECK):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")
    ir.async_delete_issue(hass, DOMAIN, zone_sources.issue_id(entry.entry_id))
    ir.async_delete_issue(hass, DOMAIN, f"app_control_off_{entry.entry_id}")
    ir.async_delete_issue(hass, DOMAIN, linked_players.issue_id(entry.entry_id))


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
        # Zone 2's inputs chosen in the options, or Zone 2 hidden: no question.
        zone_sources.async_update_issue(hass, entry)
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


def _follow_speakers(
    hass: HomeAssistant, entry: DenonAvrConfigEntry, coordinator: DenonAvrCoordinator
) -> CALLBACK_TYPE:
    """Apply the channel defaults again when the receiver reports other speakers.

    The speakers are asked again on a sound mode or input change, and the first
    start may have come from the stored data. An answer comes line by line, so
    the defaults follow only once the speakers stayed the same for
    SPEAKERS_SETTLE seconds, not on half an answer.
    """

    seen = set(coordinator.device.discovery.configured_channels)
    pending: CALLBACK_TYPE | None = None

    @callback
    def settled(_now: Any) -> None:
        nonlocal pending
        pending = None
        _apply_channel_defaults(hass, entry, coordinator)

    @callback
    def updated() -> None:
        nonlocal seen, pending
        configured = coordinator.device.discovery.configured_channels
        if configured == seen:
            return
        seen = set(configured)
        if pending is not None:
            pending()
        pending = async_call_later(hass, SPEAKERS_SETTLE, settled)

    remove = coordinator.async_add_listener(updated)

    @callback
    def stop() -> None:
        remove()
        if pending is not None:
            pending()

    return stop


def _apply_channel_defaults(
    hass: HomeAssistant,
    entry: DenonAvrConfigEntry,
    coordinator: DenonAvrCoordinator,
) -> None:
    """Keep the per channel entities in step with the speakers the receiver has.

    Home Assistant keeps entity registry entries across a remove/re-add (keyed by
    unique id), so `entity_registry_enabled_default` cannot retroactively disable
    already registered channels. The first time the speakers are known, the
    channels of speakers the receiver has not set up are disabled. Later, when
    the receiver reports other speakers (one set up or removed on the
    receiver), only the channels whose speaker changed follow: a removed one is
    disabled, one set up again is enabled if this integration disabled it. The
    speakers last applied are stored in the options; channels whose speaker
    did not change are never touched, so the user's own choices stick.
    """

    configured = coordinator.device.discovery.configured_channels
    if not configured:
        # Speaker configuration unknown; do not disable anything (safe fallback).
        return
    applied = entry.options.get(_CHANNELS_APPLIED)
    if applied is not None and set(applied) == configured:
        return

    registry = er.async_get(hass)
    channels = [
        (regentry, regentry.unique_id.split(_CHANNEL_MARKER)[-1])
        for regentry in er.async_entries_for_config_entry(registry, entry.entry_id)
        if _CHANNEL_MARKER in regentry.unique_id
    ]
    previous: set[str] | None
    if applied is not None:
        previous = set(applied)
    elif entry.options.get(_CHANNELS_INITIALIZED):
        # Set up before the speakers were stored: the channels left on stand for
        # the speakers the receiver had then.
        previous = {code for regentry, code in channels if regentry.disabled_by is None}
    else:
        previous = None
    for regentry, code in channels:
        removed = code not in configured and (previous is None or code in previous)
        added = previous is not None and code in configured and code not in previous
        if removed and regentry.disabled_by is None:
            registry.async_update_entity(
                regentry.entity_id,
                disabled_by=er.RegistryEntryDisabler.INTEGRATION,
            )
        elif added and regentry.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
            registry.async_update_entity(regentry.entity_id, disabled_by=None)

    hass.config_entries.async_update_entry(
        entry,
        options={
            **entry.options,
            _CHANNELS_INITIALIZED: True,
            _CHANNELS_APPLIED: sorted(configured),
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: DenonAvrConfigEntry) -> bool:
    """Unload a config entry and stop the telnet transport."""

    unloaded = await hass.config_entries.async_unload_platforms(
        entry, _platforms(entry.runtime_data)
    )
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
