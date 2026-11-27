"""Announcements on a zone: play one over HEOS, then put the zone back.

One `Announcement` per zone goes through clear phases:

    idle → starting → playing → restoring → settling → idle

- **starting**: the zone's power, input, volume and mute are kept, the zone
  is switched on at the announcement volume and HEOS is asked to play.
- **playing**: in the background, waiting for HEOS to report the
  announcement playing and then stopping for good.
- **restoring**: the zone is put back as it was.
- **settling**: a moment longer, until the receiver has reported the input
  it went back to; until then input changes are no one's choice (linked
  players, the activity sensor).

A second announcement before the zone is back takes over: it keeps the first
snapshot, so the zone goes back to how it was before the first one. Switching
the zone off ends it without putting anything back. While an announcement is
not idle, the zone is in the coordinator's `announcing` set.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_call_later

from .avr.errors import CommandNotSent
from .const import DOMAIN

if TYPE_CHECKING:
    from .avr.device import DenonAvrDevice
    from .avr.models import ZoneSnapshot
    from .coordinator import DenonAvrCoordinator

_LOGGER = logging.getLogger(__name__)

#: How long until HEOS reports the announcement playing, how long a stop
#: must last to count as its end (HEOS stops briefly while it switches
#: streams, as Music Assistant's HEOS provider learnt), and the longest it may
#: play before the zone is put back.
ANNOUNCE_START_WAIT = 10.0
ANNOUNCE_POLL = 0.25
ANNOUNCE_STOP_GRACE = 3.0
ANNOUNCE_MAX_LENGTH = 300.0
#: How long after the restore an announcement still counts (the receiver
#: reports the input it went back to).
ANNOUNCE_SETTLE = 3.0


class Phase(StrEnum):
    """Where an announcement is."""

    IDLE = "idle"
    STARTING = "starting"
    PLAYING = "playing"
    RESTORING = "restoring"
    SETTLING = "settling"


class _Run:
    """One announcement; only the latest run may move the phases on."""


class Announcement:
    """The announcements of one zone."""

    def __init__(self, coordinator: DenonAvrCoordinator, zone_id: str) -> None:
        self._coordinator = coordinator
        self._zone_id = zone_id
        self.phase = Phase.IDLE
        self._run: _Run | None = None
        self._snapshot: ZoneSnapshot | None = None
        self._task: asyncio.Task[None] | None = None
        self._settle: CALLBACK_TYPE | None = None

    @property
    def _device(self) -> DenonAvrDevice:
        return self._coordinator.device

    def _heos_state(self) -> str | None:
        return self._coordinator.data.now_playing.state

    async def async_play(
        self,
        ensure_on: Callable[[], Awaitable[None]],
        volume: Callable[[], float | None],
        play: Callable[[], Awaitable[None]],
    ) -> None:
        """Start an announcement; the zone is put back in the background.

        `ensure_on` switches the zone on and waits for it, `volume` gives the
        announcement volume once it is on (None keeps it), `play` starts the
        stream. Whatever fails before the stream plays puts the zone back at
        once and is raised.
        """

        run = self._take_over()
        was_playing = self._heos_state() == "play"
        device = self._device
        try:
            await ensure_on()
            if (volume_raw := volume()) is not None:
                await device.async_set_volume_raw(self._zone_id, volume_raw)
            if self._coordinator.data.zone(self._zone_id).muted:
                # A muted zone would swallow it; the restore mutes it again.
                await device.async_set_mute(self._zone_id, False)
            await play()
        except BaseException:
            # It did not start: put the zone back now instead of after it.
            try:
                if self._snapshot is not None:
                    with suppress(CommandNotSent):
                        await device.async_restore(self._zone_id, self._snapshot)
            finally:
                self._restored(run)
            raise
        if run is not self._run:
            # A newer announcement started meanwhile: it puts the zone back.
            return
        self.phase = Phase.PLAYING
        self._task = self._coordinator.hass.async_create_background_task(
            self._async_restore_after(run, was_playing),
            f"{DOMAIN} restore after announcement",
        )

    @callback
    def cancel(self) -> None:
        """No announcement any more: the zone is switched off, or the entity goes.

        Switched off, nothing is put back afterwards (a restore would switch
        the zone on again).
        """

        self._run = None
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._cancel_settle()
        self._snapshot = None
        self._set_idle()

    def _take_over(self) -> _Run:
        """This announcement owns the zone from now on."""

        if self._task is not None and not self._task.done():
            # The earlier one's restore: this one puts the zone back instead.
            self._task.cancel()
        if self._snapshot is None:
            # Kept until settled: right after a restore the receiver has not
            # yet reported the input and volume put back.
            self._snapshot = self._device.zone_snapshot(self._zone_id)
        self._cancel_settle()
        run = self._run = _Run()
        self.phase = Phase.STARTING
        self._coordinator.announcing.add(self._zone_id)
        self._coordinator.async_update_listeners()
        return run

    async def _until(self, condition: Callable[[str | None], bool]) -> None:
        while not condition(self._heos_state()):
            await asyncio.sleep(ANNOUNCE_POLL)

    async def _async_restore_after(self, run: _Run, was_playing: bool) -> None:
        """Wait for the announcement to start and to end, then put the zone back.

        Never raises but when cancelled: a newer announcement then keeps the
        snapshot and puts the zone back after itself.
        """

        try:
            async with asyncio.timeout(ANNOUNCE_START_WAIT):
                if was_playing:
                    # A play before the restart stop is still the previous stream.
                    await self._until(lambda state: state != "play")
                await self._until(lambda state: state == "play")
        except TimeoutError:
            _LOGGER.debug("Announcement on %s: no clear start", self._zone_id)
        try:
            async with asyncio.timeout(ANNOUNCE_MAX_LENGTH):
                while self._heos_state() == "play":
                    await self._until(lambda state: state != "play")
                    try:
                        # A stop counts as the end only once it lasts.
                        async with asyncio.timeout(ANNOUNCE_STOP_GRACE):
                            await self._until(lambda state: state == "play")
                    except TimeoutError:
                        break
        except TimeoutError:
            _LOGGER.debug("Announcement on %s: no clear end, restoring", self._zone_id)
        snapshot = self._snapshot
        if snapshot is not None:
            self.phase = Phase.RESTORING
            try:
                await self._device.async_restore(self._zone_id, snapshot)
            except CommandNotSent as err:
                _LOGGER.debug("Restoring %s after an announcement failed: %s", self._zone_id, err)
        self._restored(run)

    @callback
    def _restored(self, run: _Run) -> None:
        """The zone is back; a moment later, once the receiver reported it, idle.

        Only for the latest announcement: one that a newer one took over
        leaves the zone to it.
        """

        if run is not self._run:
            return
        self._cancel_settle()
        self.phase = Phase.SETTLING

        @callback
        def settled(_now: Any) -> None:
            self._settle = None
            self._run = None
            self._snapshot = None
            self._set_idle()

        self._settle = async_call_later(self._coordinator.hass, ANNOUNCE_SETTLE, settled)

    def _cancel_settle(self) -> None:
        if self._settle is not None:
            self._settle()
            self._settle = None

    def _set_idle(self) -> None:
        self.phase = Phase.IDLE
        self._coordinator.announcing.discard(self._zone_id)
        self._coordinator.async_update_listeners()
