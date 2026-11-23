"""Data update coordinator for the Denon AVR Plus integration.

The receiver pushes state changes over telnet, so this coordinator is push
driven: the device calls back whenever anything changes and the coordinator
forwards the new state to the entities. The periodic update is only a light HTTP
reconciliation poll that confirms the receiver is reachable and recovers state
if the telnet link is temporarily down.
"""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .avr import AvrState, DenonAvrDevice
from .const import (
    CONF_PORT,
    DOMAIN,
    HTTP_FALLBACK_INTERVAL,
    RECONCILE_INTERVAL,
    TELNET_OUTAGE_GRACE,
)

_LOGGER = logging.getLogger(__name__)

# The typed config entry carries the coordinator as its runtime data.
type DenonAvrConfigEntry = ConfigEntry[DenonAvrCoordinator]


class DenonAvrCoordinator(DataUpdateCoordinator[AvrState]):
    """Coordinate updates from a single Denon AVR receiver."""

    def __init__(self, hass: HomeAssistant, entry: DenonAvrConfigEntry, host: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} {host}",
            update_interval=RECONCILE_INTERVAL,
            config_entry=entry,
        )
        session = async_get_clientsession(hass)
        self.device = DenonAvrDevice(session, host, entry.data.get(CONF_PORT))
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

    async def async_setup(self) -> None:
        """Discover the receiver and start the telnet transport.

        Raises ConnectionError when the receiver cannot be reached so the config
        entry setup is retried by Home Assistant.
        """

        await self.device.async_discover()
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
        except Exception as err:  # noqa: BLE001 - surface as an update failure
            raise UpdateFailed(f"Error polling Denon AVR: {err}") from err
        self._check_link()
        return self.device.state

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
            RECONCILE_INTERVAL if self.device.available else HTTP_FALLBACK_INTERVAL
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
