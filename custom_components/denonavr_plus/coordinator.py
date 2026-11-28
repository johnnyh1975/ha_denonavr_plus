"""Data update coordinator for the Denon AVR Plus integration.

The receiver pushes state changes over telnet, so this coordinator is push
driven: the device calls back whenever anything changes and the coordinator
forwards the new state to the entities. The periodic update is only a light HTTP
reconciliation poll that confirms the receiver is reachable and recovers state
if the telnet link is temporarily down.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .avr import AvrState, DenonAvrDevice
from .avr.models import KnownAnswers
from .capabilities import CapabilityResults
from .command_test import CommandTestResults
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

#: Entries set up again after a start in this run because the receiver then
#: reported other capabilities (at most once each).
_REBUILT_AFTER_FAST_START: set[str] = set()
#: How long the check after the start waits for the platforms to be set up.
PLATFORMS_READY_WAIT = 60.0


class StartPath(StrEnum):
    """How the entry started (coordinator.async_setup), shown in the diagnostics."""

    #: The receiver did not answer: media players from the last discovery.
    OFFLINE = "offline"
    #: Everything the entities are built from came from the last start.
    FAST = "fast"
    #: Speakers, quick selects and inputs from the last start (another
    #: firmware or snapshot layout); waits for the receiver's first answer.
    PRELOADED = "preloaded"
    #: No usable snapshot: asks and waits for the receiver's answers.
    FULL = "full"

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

#: Seconds without a change before the snapshot is saved while running:
#: longer than the speakers take to settle (SPEAKERS_SETTLE), so a speaker
#: change or a resync in progress is not what is kept.
SNAPSHOT_SAVE_DELAY = 10.0


class ModelNotNamedYet(ConnectionError):
    """The receiver answered, still without the model it named before."""


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
        # The first poll follows the discovery, which has just read the signal.
        self._polled = False
        self.device.register_update_callback(self._handle_device_update)
        # Registry id of the main receiver device, set once at setup so the
        # sub-devices can link to it via `via_device_id` (HA 2026.8+).
        self.main_device_id: str | None = None
        # The zones that got entities at setup (main always; see choices.py).
        self.shown_zones: frozenset[str] = frozenset()
        # Zones playing an announcement: their input changes are no one's
        # choice (linked_players.py leaves the players alone then).
        self.announcing: set[str] = set()
        # The zones that got an activity select at setup (activities.py).
        self.activity_zones: frozenset[str] = frozenset()
        # The activity last started on each zone, however it was started.
        self.started_activities: dict[str, str] = {}
        # Connection diagnosis: when the telnet link went down, and whether the
        # long outage has already been reported (log once, repair while it lasts).
        self._link_down_since: datetime | None = None
        self._outage_reported = False
        self._playback_error: str | None = None
        self._app_control_off = False
        # How the entry started; with OFFLINE the entry reloads once the
        # receiver answers.
        self.start_path: StartPath | None = None
        # Platform -> the unique ids it was set up with (entity.add_planned),
        # and whether all platforms are (set by the setup in __init__).
        self.planned: dict[str, frozenset[str]] = {}
        self.platforms_ready = asyncio.Event()
        self._reload_scheduled = False
        self._identify_after: datetime | None = None
        self._snapshot = snapshot_store(hass, entry.entry_id)
        # The snapshot as last saved; set once a start has saved it, from then
        # on every change is saved too (see _keep_snapshot).
        self._saved_snapshot: dict[str, Any] | None = None
        self._pending_snapshot: dict[str, Any] | None = None
        # The firmware the device card shows (see _follow_firmware).
        self._firmware_shown: tuple[str | None, str | None] | None = None
        # The last capability check of this receiver (see capabilities.py).
        self.capabilities = CapabilityResults(hass, entry.entry_id)
        # The last command test (see command_test.py); one at a time with the check.
        self.command_test = CommandTestResults(hass, entry.entry_id, self.capabilities.lock)

    async def async_setup(self) -> None:
        """Discover the receiver and start the telnet transport.

        Raises ConnectionError when the receiver cannot be reached so the config
        entry setup is retried by Home Assistant.
        """

        await self.capabilities.async_load()
        await self.command_test.async_load()
        snapshot = await self._snapshot.async_load()
        # What the receiver answered before: the capability check asks one
        # query at a time, so it comes first; otherwise the last probe.
        known = [
            item
            for item in (
                KnownAnswers.from_check(self.capabilities.result),
                KnownAnswers.from_dict((snapshot or {}).get("known_answers")),
            )
            if item is not None
        ]
        try:
            await self.device.async_discover(known, snapshot)
        except ConnectionError:
            # A receiver off without network standby does not answer. With the
            # last discovery the media players can still be set up (shown off,
            # see the turn-on trigger); everything else waits for a reload.
            if not snapshot:
                raise
            self.device.restore_discovery(snapshot)
            self.start_path = StartPath.OFFLINE
            _LOGGER.info(
                "%s does not answer; starting its media players from the last "
                "discovery until it does",
                self.device.discovery.device.model_name or "Denon AVR",
            )
            await self.device.async_start()
            return
        if not self.device.discovery.device.model_name and (
            (snapshot or {}).get("device") or {}
        ).get("model_name"):
            # A receiver still starting answers without its model (as the
            # official integration found, core #193612). Entities and devices
            # set up now would be named "Denon AVR"; try again a little later.
            # Only for a receiver that named it before: one that never does
            # (no ModelName, or a UPnP description without it) is set up as
            # "Denon AVR", as the config flow named it.
            raise ModelNotNamedYet("the receiver did not name its model yet")
        # A model-dependent control answered once stays: one late reply at a
        # start (or a receiver in standby) must not drop its entity.
        self.device.discovery.answered |= set((snapshot or {}).get("answered", []))
        if self.device.fast_start:
            # Everything the entities are built from came from the last start:
            # no wait at all. What it took from the snapshot is read again in
            # the background.
            self.start_path = StartPath.FAST
            await self.device.async_start()
        elif self.device.preload_discovery(snapshot):
            # What the entities are built from is known from the last start:
            # wait for the receiver's first answer only; the resync goes on.
            self.start_path = StartPath.PRELOADED
            await self.device.async_start()
            await self.device.async_await_ready(preloaded=True)
        else:
            self.start_path = StartPath.FULL
            await self.device.async_start()
            # Give the initial resync a moment so per channel entities can be built.
            await self.device.async_await_ready()
            # Saved after the wait, which fills the sources from Deviceinfo
            # when the telnet introspection brought none; saved again once the
            # resync is through (_async_after_start).
            await self._async_save_snapshot()
        # Whatever the path, once the receiver has answered everything the
        # snapshot is saved and the entities are checked against it: if it
        # reports other capabilities than they were built from, the entry is
        # set up once more.
        assert self.config_entry is not None
        self.config_entry.async_create_background_task(
            self.hass, self._async_after_start(), "denonavr_plus after start"
        )

    @property
    def started_offline(self) -> bool:
        """Whether the receiver did not answer at setup (StartPath.OFFLINE)."""

        return self.start_path is StartPath.OFFLINE

    async def _async_save_snapshot(self) -> None:
        """Save the snapshot after a start; from then on changes are kept too."""

        snapshot = self.device.discovery_snapshot()
        await self._snapshot.async_save(snapshot)
        self._saved_snapshot = snapshot

    @callback
    def _keep_snapshot(self) -> None:
        """Save what changed after the start, so the next one starts from it.

        Only while telnet is connected; saved once nothing has changed for
        SNAPSHOT_SAVE_DELAY, and at the latest when Home Assistant stops.
        """

        if self._saved_snapshot is None or not self.device.telnet_connected:
            return
        snapshot = self.device.discovery_snapshot()
        if snapshot == (self._pending_snapshot or self._saved_snapshot):
            return
        self._pending_snapshot = snapshot
        self._snapshot.async_delay_save(self._take_pending_snapshot, SNAPSHOT_SAVE_DELAY)

    @callback
    def _take_pending_snapshot(self) -> dict[str, Any]:
        """The snapshot the delayed save writes, now kept as saved."""

        snapshot = self._pending_snapshot or self._saved_snapshot or {}
        self._saved_snapshot, self._pending_snapshot = snapshot, None
        return snapshot

    async def _async_after_start(self) -> None:
        """Once the resync is through: save the snapshot, rebuild if the layout changed.

        After a fast start this first reads what it took from the snapshot.
        """

        assert self.config_entry is not None
        entry_id = self.config_entry.entry_id
        try:
            if self.start_path is StartPath.FAST:
                await self.device.async_complete_fast_start()
            else:
                await self.device.async_await_ready()
                await self.device.async_await_resync()
            await self._async_save_snapshot()
        except Exception as err:  # noqa: BLE001 - a background task must not raise
            _LOGGER.debug("Discovery after the start not completed: %s", err)
            return
        # What the platforms were built from is compared once they are all set up.
        try:
            async with asyncio.timeout(PLATFORMS_READY_WAIT):
                await self.platforms_ready.wait()
        except TimeoutError:
            return
        # The plan holds the platforms, which import this module.
        from .entity_plan import changed  # noqa: PLC0415

        difference = changed(self, dict(self.config_entry.options))
        if not difference:
            return
        if entry_id in _REBUILT_AFTER_FAST_START:
            # Once per run: a receiver whose answers vary must not reload in turn.
            _LOGGER.debug("Entities changed again after a rebuild; keeping them")
            return
        _REBUILT_AFTER_FAST_START.add(entry_id)
        _LOGGER.info(
            "%s reports other capabilities than its entities were built from "
            "(%d entities differ); setting them up again",
            self.device.discovery.device.model_name or "Denon AVR",
            len(difference),
        )
        self.hass.config_entries.async_schedule_reload(entry_id)

    async def async_shutdown(self) -> None:
        """Stop the telnet transport when the entry is unloaded."""

        await self.device.async_stop()
        if self._pending_snapshot is not None:
            # A reload starts from it; the delayed save would come too late.
            await self._snapshot.async_save(self._take_pending_snapshot())
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        # A playback error is about the session that ends here.
        ir.async_delete_issue(self.hass, DOMAIN, self._playback_issue_id)
        # Asked again at the next start; the new coordinator starts from "on".
        assert self.config_entry is not None
        ir.async_delete_issue(self.hass, DOMAIN, f"app_control_off_{self.config_entry.entry_id}")
        self.capabilities.async_delete_check_issue()
        await super().async_shutdown()

    async def _async_update_data(self) -> AvrState:
        """Run the reconciliation poll and return the current state."""

        try:
            await self.device.async_poll()
            await self._maybe_read_setup()
            if self.device.available:
                if self._polled:
                    await self.device.async_refresh_signal()
                await self.device.async_check_firmware()
            self._polled = True
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
        self._check_playback_error()
        self._check_app_control()
        self._follow_firmware()
        self._keep_snapshot()
        # Not async_set_updated_data: that also moves the next poll a full
        # interval on, so a receiver that reports more often than the poll
        # interval would never be polled (firmware check, setup refresh,
        # the HTTP fallback while telnet is down).
        self.data = self.device.state
        self.last_update_success = True
        self.async_update_listeners()

    @callback
    def _follow_firmware(self) -> None:
        """Show a new firmware on the device card after the receiver updated itself."""

        info = self.device.discovery.device
        firmware = info.firmware_text
        shown = (firmware, info.network_module_version)
        if firmware is None or shown == self._firmware_shown or self.main_device_id is None:
            return
        registry = dr.async_get(self.hass)
        if (entry := registry.async_get(self.main_device_id)) and (
            entry.sw_version,
            entry.hw_version,
        ) != shown:
            registry.async_update_device(
                entry.id, sw_version=firmware, hw_version=info.network_module_version
            )
        self._firmware_shown = shown

    @callback
    def _check_app_control(self) -> None:
        """A repair while the receiver reports control by the remote app switched off.

        Denon's app asks NSARA ? and treats OFF as off; what else the
        setting blocks is not known. Not yet seen on a receiver.
        """

        assert self.config_entry is not None
        off = self.device.state.values.get("remote_app_control") is False
        if off == self._app_control_off:
            return
        self._app_control_off = off
        issue_id = f"app_control_off_{self.config_entry.entry_id}"
        if off:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="app_control_off",
                translation_placeholders={
                    "model": self.device.discovery.device.model_name or "Denon AVR"
                },
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    @callback
    def _check_playback_error(self) -> None:
        """Show HEOS's last playback error as a repair, until something plays again."""

        assert self.config_entry is not None
        error = self.device.state.now_playing.error
        if error == self._playback_error:
            return
        self._playback_error = error
        issue_id = self._playback_issue_id
        if error is None:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="heos_playback_error",
            translation_placeholders={
                "model": self.device.discovery.device.model_name or "Denon AVR",
                "error": error,
            },
        )

    @property
    def _playback_issue_id(self) -> str:
        assert self.config_entry is not None
        return f"heos_playback_error_{self.config_entry.entry_id}"

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
        if (
            self.started_offline
            and not self._reload_scheduled
            and (self.device.available or self.device.http_reachable)
            and (self._identify_after is None or dt_util.utcnow() >= self._identify_after)
        ):
            # The receiver answers again: set it up in full, but only once its
            # device description answers too; telnet alone (a receiver still
            # booting, HTTP blocked) would fail the setup and start it over.
            assert self.config_entry is not None
            self._reload_scheduled = True
            self.config_entry.async_create_background_task(
                self.hass, self._async_reload_when_described(), "denonavr_plus full setup"
            )
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

    async def _async_reload_when_described(self) -> None:
        """Reload once Deviceinfo answers; otherwise try again a minute later."""

        assert self.config_entry is not None
        try:
            await self.device.async_fetch_identity()
        except Exception as err:  # noqa: BLE001 - a background task must not raise
            _LOGGER.debug("Receiver answers, but not its device description yet: %s", err)
            self._identify_after = dt_util.utcnow() + RECONCILE_INTERVAL
            self._reload_scheduled = False
            return
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

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
