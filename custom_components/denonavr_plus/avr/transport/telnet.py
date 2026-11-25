"""Asynchronous telnet transport (port 23) for the Denon AVR client library.

This module owns the raw telnet socket only. It knows nothing about the protocol
grammar; it just delivers received lines to a callback and serialises outgoing
commands. Robustness features:

* A single background manager task keeps the connection up and reconnects with
  exponential backoff after any failure.
* Outgoing commands go through a queue. Commands that change something go
  before waiting queries, so a user's action is not stuck behind the full
  resync. After each command the client waits for the receiver's reply (or a
  short timeout that follows the measured response time), with a minimum gap,
  because the receiver drops commands that arrive too quickly.
* Whatever is still queued when the link drops is discarded, and nothing is
  queued while it is down: stale commands are never sent after a reconnect.
* A keepalive probe is sent when the link is idle so a silently dropped socket
  is detected quickly.
* A receiver that stops answering while the socket stays open is noticed:
  when nothing came back for SILENCE_TIMEOUT after a command, the session is
  closed and opened again (which resynchronises the state).
* On every (re)connection a callback is invoked so the owner can resynchronise
  the full state.

A one-shot `async_probe` helper is also provided for deterministic discovery: it
opens a short-lived connection, sends queries, and feeds the replies to a
callback, entirely separate from the persistent connection above.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable

from ..const import (
    COMMAND_SPACING,
    CONNECT_TIMEOUT,
    KEEPALIVE_INTERVAL,
    POWER_ON_SETTLE,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_MIN,
    REPLY_TIMEOUT_MAX,
    REPLY_TIMEOUT_MIN,
    SILENCE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

# The primary control channel's TCP port.
DEFAULT_PORT = 23
# The receiver terminates every line with a carriage return.
_TERMINATOR = b"\r"
# A harmless query used to keep the link alive and to detect a dead socket.
_KEEPALIVE_COMMAND = "PW?"
# Every message starts with a two-letter command, and replies and events have
# the same form as the command (docs/protocol/02, "Framing").
_FAMILY_LENGTH = 2
# The power-on command after which the receiver needs time (docs/protocol/02).
_POWER_ON_COMMAND = "PWON"
# Reply wait = this many times the average response time, within the bounds.
_REPLY_TIMEOUT_FACTOR = 4.0
# Weight of a new sample in the running average of the response time.
_RESPONSE_SMOOTHING = 0.2

#: How many sent and received lines the communication log keeps.
LOG_LINES = 300


def is_query(command: str) -> bool:
    """Return True for a query ('PW?', 'PSBAS ?'), False for a command."""

    return command.rstrip().endswith("?")


async def async_answers(host: str, *, port: int = DEFAULT_PORT, wait: float = 3.0) -> bool:
    """Whether the receiver accepts a control connection and answers PW? on it.

    Asked when a receiver is added: it answers over HTTP, but another
    program may hold its one telnet connection, or port 23 may be blocked.
    """

    answered = False

    def on_line(_line: str) -> None:
        nonlocal answered
        answered = True

    await async_probe(host, ["PW?"], on_line, lambda: answered, port=port, deadline=wait)
    return answered


async def async_probe(
    host: str,
    queries: list[str],
    on_line: Callable[[str], None],
    is_complete: Callable[[], bool],
    *,
    port: int = DEFAULT_PORT,
    deadline: float = 3.0,
    reply_wait: float = 0.0,
) -> None:
    """Open a short-lived telnet connection to run discovery queries.

    Sends every query, then feeds each received line to `on_line` until
    `is_complete()` returns True or `deadline` seconds pass, then closes.
    With `reply_wait`, each query waits for a line, at most that long, before
    the next goes out: older receivers (AVR-X2000) drop queries that arrive
    while they are still answering an earlier one. This is
    separate from the persistent connection so discovery is deterministic at
    setup time. It never raises; on any failure it simply returns and the
    persistent resync fills the model slightly later.
    """

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Discovery probe could not connect to %s: %s", host, err)
        return

    loop = asyncio.get_running_loop()
    try:
        for query in queries:
            writer.write(query.encode("utf-8") + _TERMINATOR)
            await writer.drain()
            await asyncio.sleep(COMMAND_SPACING)
            if reply_wait:
                await _read_one(reader, on_line, reply_wait)
        # Read responses until the key discovery data is present or the deadline.
        end = loop.time() + deadline
        while loop.time() < end:
            try:
                raw = await asyncio.wait_for(reader.readuntil(_TERMINATOR), timeout=0.4)
            except asyncio.TimeoutError:
                if is_complete():
                    break
                continue
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                break
            line = raw.decode("utf-8", errors="replace").strip("\r\n")
            if line:
                on_line(line)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def _read_one(
    reader: asyncio.StreamReader, on_line: Callable[[str], None], timeout: float
) -> None:
    """Feed the next line to `on_line`, if one arrives within `timeout`."""

    try:
        raw = await asyncio.wait_for(reader.readuntil(_TERMINATOR), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return
    line = raw.decode("utf-8", errors="replace").strip("\r\n")
    if line:
        on_line(line)


class TelnetClient:
    """Manage a resilient telnet connection to the receiver."""

    def __init__(
        self,
        host: str,
        on_line: Callable[[str], None],
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_availability: Callable[[bool], None] | None = None,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._on_line = on_line
        self._on_connected = on_connected
        self._on_availability = on_availability

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        # Commands that change something are sent before waiting queries.
        self._commands: deque[str] = deque()
        self._queries: deque[str] = deque()
        self._queued = asyncio.Event()
        # The command family whose reply the writer is waiting for.
        self._awaiting: str | None = None
        self._replied = asyncio.Event()
        self._response_time: float | None = None
        self._manager_task: asyncio.Task[None] | None = None
        self._connected = False
        self._stopped = False
        self._last_activity = 0.0
        # Since when commands went out with nothing coming back, if they did.
        self._silent_since: float | None = None
        self._silence_probed = False
        # The last lines sent (">") and received ("<"), for the diagnostics.
        self._log: deque[str] = deque(maxlen=LOG_LINES)

    @property
    def log(self) -> list[str]:
        """The last lines sent and received, oldest first, with UTC times."""

        return list(self._log)

    def _record(self, direction: str, line: str) -> None:
        stamp = datetime.now(UTC).strftime("%H:%M:%S.%f")[:-3]
        self._log.append(f"{stamp} {direction} {line}")

    @property
    def connected(self) -> bool:
        """Return True when the telnet link is currently up."""

        return self._connected

    @property
    def queued(self) -> int:
        """Return the number of commands and queries waiting to be sent."""

        return len(self._commands) + len(self._queries)

    @property
    def response_time(self) -> float | None:
        """Return the average time the receiver takes to reply, in seconds."""

        return self._response_time

    async def async_start(self) -> None:
        """Start the background connection manager."""

        self._stopped = False
        if self._manager_task is None or self._manager_task.done():
            self._manager_task = asyncio.ensure_future(self._manage())

    async def async_stop(self) -> None:
        """Stop the connection manager and close the socket."""

        self._stopped = True
        if self._manager_task is not None:
            self._manager_task.cancel()
            try:
                await self._manager_task
            except asyncio.CancelledError:
                pass
            self._manager_task = None
        await self._close_socket()

    async def async_send(self, command: str) -> None:
        """Queue a command for delivery to the receiver.

        While the link is down the command is dropped: it would be stale by the
        time the link is back, and the resync after reconnecting reads the
        state fresh anyway.
        """

        if not self._connected:
            _LOGGER.debug("Telnet to %s is down, not sending %s", self._host, command)
            return
        if is_query(command):
            self._queries.append(command)
        else:
            self._commands.append(command)
        self._queued.set()

    def _clear_queue(self) -> None:
        """Drop everything still queued, for example after the link dropped."""

        if self.queued:
            _LOGGER.debug(
                "Dropping %s queued telnet commands for %s", self.queued, self._host
            )
        self._commands.clear()
        self._queries.clear()
        self._queued.clear()
        self._awaiting = None

    # Connection lifecycle -------------------------------------------------

    async def _manage(self) -> None:
        """Keep the connection up, reconnecting with exponential backoff."""

        backoff = RECONNECT_BACKOFF_MIN
        while not self._stopped:
            try:
                await self._connect()
                backoff = RECONNECT_BACKOFF_MIN
                await self._run_session()
            except asyncio.CancelledError:
                raise
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug("Telnet connection to %s failed: %s", self._host, err)
            finally:
                await self._set_connected(False)
                self._clear_queue()
                await self._close_socket()

            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _connect(self) -> None:
        """Open the telnet socket."""

        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=CONNECT_TIMEOUT
        )
        self._last_activity = asyncio.get_running_loop().time()
        self._silent_since = None
        self._silence_probed = False
        await self._set_connected(True)
        _LOGGER.debug("Telnet connected to %s:%s", self._host, self._port)
        # Let the owner resynchronise the full state on (re)connect.
        if self._on_connected is not None:
            await self._on_connected()

    async def _run_session(self) -> None:
        """Run the reader, writer and keepalive concurrently until one ends."""

        reader_task = asyncio.ensure_future(self._read_loop())
        writer_task = asyncio.ensure_future(self._write_loop())
        keepalive_task = asyncio.ensure_future(self._keepalive_loop())
        tasks = [reader_task, writer_task, keepalive_task]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # Surface an exception (for example a dropped socket) if one occurred.
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_socket(self) -> None:
        """Close the writer and drop the streams.

        The streams are dropped before waiting: if the wait is cancelled (the
        integration unloads while a dropped link is being closed), a second
        call must not wait on the same cancelled close again.
        """

        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _set_connected(self, connected: bool) -> None:
        """Update and broadcast the connection state."""

        if connected == self._connected:
            return
        self._connected = connected
        if self._on_availability is not None:
            self._on_availability(connected)

    # I/O loops ------------------------------------------------------------

    async def _read_loop(self) -> None:
        """Read carriage return terminated lines and hand them to the callback."""

        assert self._reader is not None
        while True:
            try:
                raw = await self._reader.readuntil(_TERMINATOR)
            except asyncio.IncompleteReadError as err:
                if err.partial:
                    self._deliver(err.partial)
                raise ConnectionError("Telnet stream closed")
            except asyncio.LimitOverrunError as err:
                # The oversized data stays in the buffer, so retrying would spin.
                # Treat it as a broken stream and let the manager reconnect.
                raise ConnectionError("Telnet line exceeded read buffer") from err
            self._last_activity = asyncio.get_running_loop().time()
            self._deliver(raw)

    def _deliver(self, raw: bytes) -> None:
        """Decode and forward a received line."""

        line = raw.decode("utf-8", errors="replace").strip("\r\n")
        if not line:
            return
        self._silent_since = None
        self._silence_probed = False
        if self._awaiting is not None and line.startswith(self._awaiting):
            self._replied.set()
        self._record("<", line)
        self._on_line(line)

    async def _write_loop(self) -> None:
        """Write queued commands, each after the reply to the one before."""

        assert self._writer is not None
        loop = asyncio.get_running_loop()
        while True:
            command = await self._next_command()
            self._awaiting = command[:_FAMILY_LENGTH]
            self._replied.clear()
            self._writer.write(command.encode("utf-8") + _TERMINATOR)
            self._record(">", command)
            await self._writer.drain()
            sent = loop.time()
            self._last_activity = sent
            if self._silent_since is None:
                self._silent_since = sent
            await self._await_reply(sent)
            self._awaiting = None
            gap = POWER_ON_SETTLE if command == _POWER_ON_COMMAND else COMMAND_SPACING
            remaining = sent + gap - loop.time()
            if remaining > 0:
                await asyncio.sleep(remaining)

    async def _next_command(self) -> str:
        """Return the next command to send: changes first, then queries."""

        while not self.queued:
            self._queued.clear()
            await self._queued.wait()
        if self._commands:
            return self._commands.popleft()
        return self._queries.popleft()

    def _reply_timeout(self) -> float:
        """How long to wait for a reply, from the measured response time."""

        if self._response_time is None:
            return REPLY_TIMEOUT_MAX
        timeout = self._response_time * _REPLY_TIMEOUT_FACTOR
        return min(REPLY_TIMEOUT_MAX, max(REPLY_TIMEOUT_MIN, timeout))

    async def _await_reply(self, sent: float) -> None:
        """Wait for the reply to the command just sent, or for the timeout.

        A command that sets the current value gets no reply, and neither does a
        query the model does not know; both just cost the timeout.
        """

        try:
            await asyncio.wait_for(self._replied.wait(), self._reply_timeout())
        except TimeoutError:
            return
        sample = asyncio.get_running_loop().time() - sent
        if self._response_time is None:
            self._response_time = sample
        else:
            self._response_time += _RESPONSE_SMOOTHING * (sample - self._response_time)

    async def _keepalive_loop(self) -> None:
        """Probe an idle link; end the session when the receiver stays silent."""

        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(min(1.0, KEEPALIVE_INTERVAL / 2, SILENCE_TIMEOUT / 4))
            now = loop.time()
            if self._silent_since is not None:
                silent = now - self._silent_since
                if silent >= SILENCE_TIMEOUT:
                    _LOGGER.warning(
                        "%s answered nothing for %.0f s; reconnecting telnet", self._host, silent
                    )
                    raise ConnectionError("Receiver stopped answering")
                if silent >= SILENCE_TIMEOUT / 2 and not self._silence_probed:
                    # Ahead of everything queued: a run of queries this model
                    # does not answer must not look like a silent receiver.
                    self._silence_probed = True
                    self._commands.appendleft(_KEEPALIVE_COMMAND)
                    self._queued.set()
            elif now - self._last_activity >= KEEPALIVE_INTERVAL:
                await self.async_send(_KEEPALIVE_COMMAND)
