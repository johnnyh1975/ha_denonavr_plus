"""Data update coordinator for the Denon AVR Plus integration.

The receiver pushes state changes over telnet, so this coordinator is push
driven: the device calls back whenever anything changes and the coordinator
forwards the new state to the entities. The periodic update is only a light HTTP
reconciliation poll that confirms the receiver is reachable and recovers state
if the telnet link is temporarily down.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .avr import AvrState, DenonAvrDevice
from .const import (
    CONF_PORT,
    DEFAULT_SETUP_REFRESH,
    DOMAIN,
    HTTP_FALLBACK_INTERVAL,
    OPT_HEOS,
    OPT_POLL_INTERVAL,
    OPT_SETUP_REFRESH,
    RECONCILE_INTERVAL,
    TELNET_OUTAGE_GRACE,
)

_LOGGER = logging.getLogger(__name__)

# The typed config entry carries the coordinator as its runtime data.
type DenonAvrConfigEntry = ConfigEntry[DenonAvrCoordinator]

def connection_options(entry: ConfigEntry) -> dict[str, Any]:
    """The connection options of an entry, with their defaults."""

    options = entry.options
    return {
        OPT_POLL_INTERVAL: int(
            options.get(OPT_POLL_INTERVAL, RECONCILE_INTERVAL.total_seconds())
        ),
        OPT_HEOS: bool(options.get(OPT_HEOS, True)),
        OPT_SETUP_REFRESH: int(options.get(OPT_SETUP_REFRESH, DEFAULT_SETUP_REFRESH)),
    }


_SNAPSHOT_VERSION = 1


def snapshot_store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, Any]]:
    """Where the last discovery of an entry is kept (see async_setup)."""

    return Store(hass, _SNAPSHOT_VERSION, f"{DOMAIN}.{entry_id}.discovery")


class DenonAvrCoordinator(DataUpdateCoordinator[AvrState]):
    """Coordinate updates from a single Denon AVR receiver."""

    def __init__(self, hass: HomeAssistant, entry: DenonAvrConfigEntry, host: str) -> None:
        self.connection = connection_options(entry)
        self._poll_interval = timedelta(seconds=self.connection[OPT_POLL_INTERVAL])
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {host}",
            update_interval=self._poll_interval,
            config_entry=entry,
        )
        session = async_get_clientsession(hass)
        self.device = DenonAvrDevice(
            session, host, entry.data.get(CONF_PORT), heos=self.connection[OPT_HEOS]
        )
        # When the setup settings were last read by the poll (see _async_update_data).
        self._setup_read_at: datetime | None = None
        self.device.register_update_callback(self._handle_device_update)
        # Registry id of the main receiver device, set once at setup so the
        # sub-devices can link to it via `via_device_id` (HA 2026.8+).
        self.main_device_id: str | None = None
        # The zones that got entities at setup (main always; see choices.py).
        self.shown_zones: frozenset[str] = frozenset()
        # Connection diagnosis: when the telnet link went down, and whether the
        # long outage has already been reported (log once, repair while it lasts).
        self._link_down_since: datetime | None = None
        self._outage_reported = False
        # Set when the receiver did not answer at setup and the media players
        # were built from the last discovery; the entry reloads once it answers.
        self.started_offline = False
        self._reload_scheduled = False
        self._snapshot = snapshot_store(hass, entry.entry_id)

    async def async_setup(self) -> None:
        """Discover the receiver and start the telnet transport.

        Raises ConnectionError when the receiver cannot be reached so the config
        entry setup is retried by Home Assistant.
        """

        try:
            await self.device.async_discover()
        except ConnectionError:
            # A receiver off without network standby does not answer. With the
            # last discovery the media players can still be set up (shown off,
            # see the turn-on trigger); everything else waits for a reload.
            snapshot = await self._snapshot.async_load()
            if not snapshot:
                raise
            self.device.restore_discovery(snapshot)
            self.started_offline = True
            _LOGGER.info(
                "%s does not answer; starting its media players from the last "
                "discovery until it does",
                self.device.discovery.device.model_name or "Denon AVR",
            )
            await self.device.async_start()
            return
        await self._snapshot.async_save(self.device.discovery_snapshot())
        await self.device.async_start()
        # Give the initial resync a moment so per channel entities can be built.
        await self.device.async_await_ready()

    async def async_shutdown(self) -> None:
        """Stop the telnet transport when the entry is unloaded."""

        await self.device.async_stop()
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        await super().async_shutdown()

    async def _async_update_data(self) -> AvrState:
        """Run the reconciliation poll and return the current state."""

        try:
            await self.device.async_poll()
            await self._maybe_read_setup()
        except Exception as err:  # noqa: BLE001 - surface as an update failure
            raise UpdateFailed(f"Error polling Denon AVR: {err}") from err
        self._check_link()
        return self.device.state

    async def _maybe_read_setup(self) -> None:
        """Read the setup settings every OPT_SETUP_REFRESH minutes (0: never).

        They are also read whenever the telnet link comes up and after each
        own write, so this only catches changes made on the receiver's menu.
        """

        minutes = self.connection[OPT_SETUP_REFRESH]
        if not minutes or not self.device.available:
            return
        now = dt_util.utcnow()
        if self._setup_read_at is None:
            # The link just came up and read them; count from here.
            self._setup_read_at = now
            return
        if now - self._setup_read_at >= timedelta(minutes=minutes):
            self._setup_read_at = now
            await self.device.async_refresh_setup()

    @callback
    def _handle_device_update(self) -> None:
        """Forward a pushed state change to the entities."""

        self._check_link()
        self.async_set_updated_data(self.device.state)

    @property
    def _issue_id(self) -> str:
        assert self.config_entry is not None
        return f"telnet_unavailable_{self.config_entry.entry_id}"

    @callback
    def _check_link(self) -> None:
        """Report a telnet link that stays down, and clear it once it is back.

        The receiver allows one telnet session. When it stays unreachable while
        the receiver still answers over HTTP, something holds or blocks the port
        (another integration, a firewall between networks): that is a repair.
        When nothing answers at all the receiver is most likely off without
        network standby, which can be intended, so that is only logged.
        """

        self._set_poll_interval()
        if self.started_offline and not self._reload_scheduled and (
            self.device.available or self.device.http_reachable
        ):
            # The receiver answers again: set it up in full.
            assert self.config_entry is not None
            self._reload_scheduled = True
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        model = self.device.discovery.device.model_name or "Denon AVR"
        if self.device.available:
            if self._outage_reported:
                _LOGGER.info("Telnet connection to %s is back", model)
            self._link_down_since = None
            self._outage_reported = False
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
            return
        now = dt_util.utcnow()
        if self._link_down_since is None:
            self._link_down_since = now
        if now - self._link_down_since < TELNET_OUTAGE_GRACE:
            return
        if not self._outage_reported:
            self._outage_reported = True
            if self.device.http_reachable:
                _LOGGER.warning(
                    "%s answers over HTTP but its telnet port has not accepted a "
                    "connection for %s minutes; another program may hold the only "
                    "telnet session, or port 23 is blocked between networks",
                    model,
                    int(TELNET_OUTAGE_GRACE.total_seconds() // 60),
                )
            else:
                _LOGGER.warning(
                    "%s has not been reachable for %s minutes; if it is in "
                    "standby, network control (network standby) is likely off",
                    model,
                    int(TELNET_OUTAGE_GRACE.total_seconds() // 60),
                )
        if self.device.http_reachable:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="telnet_unavailable",
                translation_placeholders={"model": model},
            )

    @callback
    def _set_poll_interval(self) -> None:
        """Poll more often while the media players depend on HTTP alone."""

        interval = (
            self._poll_interval if self.device.available else HTTP_FALLBACK_INTERVAL
        )
        if self.update_interval == interval:
            return
        self.update_interval = interval
        if interval == HTTP_FALLBACK_INTERVAL:
            # Read the HTTP status soon instead of at the old interval.
            self.hass.async_create_task(self.async_request_refresh())

    @property
    def available(self) -> bool:
        """Return whether the telnet link to the receiver is currently up."""

        return self.device.available
