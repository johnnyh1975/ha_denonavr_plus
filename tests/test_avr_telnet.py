"""Tests for avr/transport/telnet.py: the command queue and its pacing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from unittest.mock import patch

import pytest

from custom_components.denonavr_plus.avr.const import (
    COMMAND_SPACING,
    POWER_ON_SETTLE,
    REPLY_TIMEOUT_MAX,
)
from custom_components.denonavr_plus.avr.transport import telnet
from custom_components.denonavr_plus.avr.transport.telnet import (
    TelnetClient,
    async_probe,
    is_query,
)

LOOPBACK = "127.0.0.1"

# These tests talk to a real telnet server on the loopback interface.
pytestmark = pytest.mark.usefixtures("socket_enabled")


def _answer_everything(line: str) -> str | None:
    # Replies and events have the form of the command: the same two letters.
    return f"{line[:2]}X"


class FakeReceiver:
    """A telnet server that records what arrives and answers on request."""

    def __init__(
        self,
        reply: Callable[[str], str | None] = _answer_everything,
        delay: float = 0.0,
        close_after: int | None = None,
    ) -> None:
        self.reply = reply
        self.delay = delay
        self.close_after = close_after
        self.received: list[tuple[float, str]] = []
        self.port = 0
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, LOOPBACK, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def lines(self) -> list[str]:
        return [line for _, line in self.received]

    async def wait_for(self, count: int, timeout: float = 5.0) -> None:
        async with asyncio.timeout(timeout):
            while len(self.received) < count:
                await asyncio.sleep(0.01)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        loop = asyncio.get_running_loop()
        try:
            while True:
                raw = await reader.readuntil(b"\r")
                line = raw.decode().strip("\r")
                self.received.append((loop.time(), line))
                if (
                    self.close_after is not None
                    and len(self.received) >= self.close_after
                ):
                    break
                answer = self.reply(line)
                if answer is not None:
                    await asyncio.sleep(self.delay)
                    writer.write(answer.encode() + b"\r")
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def receiver() -> AsyncIterator[FakeReceiver]:
    server = FakeReceiver()
    yield server
    await server.stop()


async def _connected_client(
    server: FakeReceiver,
    on_connected: Callable[[TelnetClient], list[str]],
    on_availability: Callable[[bool], None] | None = None,
) -> TelnetClient:
    """Start a client against the server; on connect it queues the given lines."""
    await server.start()
    client: TelnetClient

    async def _queue() -> None:
        for command in on_connected(client):
            await client.async_send(command)

    client = TelnetClient(
        LOOPBACK,
        on_line=lambda line: None,
        on_connected=_queue,
        on_availability=on_availability,
        port=server.port,
    )
    await client.async_start()
    return client


def test_queries_and_commands_are_told_apart() -> None:
    assert is_query("PW?")
    assert is_query("PSBAS ?")
    assert not is_query("MVUP")
    assert not is_query("PSBAS 52")


async def test_a_command_goes_before_waiting_queries(receiver: FakeReceiver) -> None:
    # The resync queues its queries first; a user's command still goes first.
    client = await _connected_client(receiver, lambda _: ["PW?", "MV?", "SI?", "MVUP"])
    try:
        await receiver.wait_for(4)
    finally:
        await client.async_stop()
    assert receiver.lines == ["MVUP", "PW?", "MV?", "SI?"]


async def test_the_next_command_waits_for_the_reply() -> None:
    server = FakeReceiver(delay=0.3)
    client = await _connected_client(server, lambda _: ["PW?", "MV?"])
    try:
        await server.wait_for(2)
    finally:
        await client.async_stop()
        await server.stop()
    (first, _), (second, _) = server.received
    assert second - first >= 0.28
    assert client.response_time is not None
    assert client.response_time >= 0.28


async def test_a_command_without_reply_costs_only_the_timeout() -> None:
    # Setting the current value is answered with nothing.
    server = FakeReceiver(reply=lambda line: None)
    client = await _connected_client(server, lambda _: ["PSBAS 50", "PSTRE 50"])
    try:
        await server.wait_for(2)
    finally:
        await client.async_stop()
        await server.stop()
    (first, _), (second, _) = server.received
    assert COMMAND_SPACING <= second - first <= REPLY_TIMEOUT_MAX + 0.3


async def test_a_quick_reply_keeps_the_minimum_gap(receiver: FakeReceiver) -> None:
    client = await _connected_client(receiver, lambda _: ["PW?", "MV?"])
    try:
        await receiver.wait_for(2)
    finally:
        await client.async_stop()
    (first, _), (second, _) = receiver.received
    assert second - first >= COMMAND_SPACING - 0.01


async def test_power_on_is_given_time_to_settle(receiver: FakeReceiver) -> None:
    client = await _connected_client(receiver, lambda _: ["PWON", "MV?"])
    try:
        await receiver.wait_for(2)
    finally:
        await client.async_stop()
    (first, _), (second, _) = receiver.received
    assert second - first >= POWER_ON_SETTLE - 0.05


async def test_the_queue_is_dropped_with_the_link() -> None:
    server = FakeReceiver(close_after=1)
    dropped = asyncio.Event()

    def _availability(up: bool) -> None:
        if not up:
            dropped.set()

    client = await _connected_client(
        server, lambda _: ["PW?", "MV?", "SI?", "MS?"], _availability
    )
    try:
        async with asyncio.timeout(5):
            await dropped.wait()
        # Nothing from the old session waits to be sent after a reconnect.
        assert client.queued == 0
    finally:
        await client.async_stop()
        await server.stop()


async def test_nothing_is_queued_while_the_link_is_down() -> None:
    client = TelnetClient(LOOPBACK, on_line=lambda line: None)
    await client.async_send("MVUP")
    assert client.queued == 0


async def _sessions(
    server: FakeReceiver, commands: list[str], seconds: float, silence: float = 1.0
) -> int:
    """How many sessions the client opens while it sends `commands`."""
    opened = 0

    async def _connected() -> None:
        nonlocal opened
        opened += 1
        if opened == 1:
            for command in commands:
                await client.async_send(command)

    await server.start()
    client = TelnetClient(
        LOOPBACK, on_line=lambda line: None, on_connected=_connected, port=server.port
    )
    with patch.object(telnet, "SILENCE_TIMEOUT", silence):
        await client.async_start()
        try:
            await asyncio.sleep(seconds)
        finally:
            await client.async_stop()
            await server.stop()
    return opened


async def test_a_receiver_that_stops_answering_gets_a_new_session() -> None:
    """An AVR-X2600H kept the socket open and answered nothing more."""
    answered = 0

    def reply(line: str) -> str | None:
        nonlocal answered
        answered += 1
        return f"{line[:2]}X" if answered <= 1 else None

    assert (
        await _sessions(FakeReceiver(reply), ["MSALL ZONE STEREO", "MNZST OFF"], 3.0)
        >= 2
    )


async def test_queries_without_answer_are_not_a_silent_receiver() -> None:
    """Many models leave some queries unanswered; the power query still is."""

    def reply(line: str) -> str | None:
        return "PWON" if line == "PW?" else None

    unknown = [f"PSX{number} ?" for number in range(5)]
    # The probe waits behind the query in flight (up to REPLY_TIMEOUT_MAX of
    # 1 s); half the silence timeout must leave room for that and its answer.
    assert await _sessions(FakeReceiver(reply), unknown, 6.0, silence=4.0) == 1


class BusyReceiver(FakeReceiver):
    """Drops a query that arrives while it still answers the previous one.

    Seen on an AVR-X2000: of about 70 setup queries sent 80 ms apart it
    answered the first ~20.
    """

    busy_for = 0.15

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        loop = asyncio.get_running_loop()
        busy_until = 0.0
        try:
            while True:
                raw = await reader.readuntil(b"\r")
                now = loop.time()
                line = raw.decode().strip("\r")
                self.received.append((now, line))
                if now < busy_until:
                    continue
                busy_until = now + self.busy_for
                answer = f"{line.rstrip('?').strip()}OK\r".encode()
                loop.call_later(self.busy_for, writer.write, answer)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            await asyncio.sleep(self.busy_for)
            writer.close()


@pytest.mark.parametrize(("reply_wait", "answered"), [(0.0, False), (0.25, True)])
async def test_a_paced_probe_gets_every_answer_of_a_busy_receiver(
    reply_wait: float, answered: bool
) -> None:
    server = BusyReceiver()
    await server.start()
    queries = [f"Q{index:02d} ?" for index in range(8)]
    lines: list[str] = []
    try:
        await async_probe(
            LOOPBACK,
            queries,
            lines.append,
            lambda: True,
            port=server.port,
            deadline=1.0,
            reply_wait=reply_wait,
        )
    finally:
        await server.stop()
    assert (len(lines) == len(queries)) is answered


# The probe and the link's edges ----------------------------------------------


async def _closed_port() -> int:
    """A loopback port nothing listens on."""
    server = await asyncio.start_server(lambda r, w: None, LOOPBACK, 0)
    port = int(server.sockets[0].getsockname()[1])
    server.close()
    await server.wait_closed()
    return port


async def test_a_probe_of_an_unreachable_receiver_gives_nothing() -> None:
    lines: list[str] = []
    await async_probe(
        LOOPBACK, ["PW?"], lines.append, lambda: True, port=await _closed_port()
    )
    assert lines == []


async def test_a_probe_waits_out_silence_until_the_deadline() -> None:
    server = FakeReceiver(reply=lambda line: None)
    await server.start()
    lines: list[str] = []
    try:
        await async_probe(
            LOOPBACK,
            ["PW?"],
            lines.append,
            lambda: False,
            port=server.port,
            deadline=0.5,
            reply_wait=0.1,
        )
    finally:
        await server.stop()
    assert lines == []
    assert server.lines == ["PW?"]


async def test_a_probe_ends_when_the_receiver_hangs_up() -> None:
    server = FakeReceiver(close_after=1)
    await server.start()
    lines: list[str] = []
    try:
        async with asyncio.timeout(2.0):
            await async_probe(
                LOOPBACK,
                ["PW?"],
                lines.append,
                lambda: False,
                port=server.port,
                deadline=5.0,
            )
    finally:
        await server.stop()
    assert lines == []


async def test_an_unreachable_receiver_is_retried_while_down() -> None:
    states: list[bool] = []
    client = TelnetClient(
        LOOPBACK,
        on_line=lambda line: None,
        on_availability=states.append,
        port=await _closed_port(),
    )
    with patch.object(telnet, "RECONNECT_BACKOFF_MIN", 0.05):
        await client.async_start()
        await asyncio.sleep(0.2)
        assert not client.connected
        await client.async_stop()
    assert states == []


async def _raw_receiver(data: bytes) -> tuple[asyncio.Server, int]:
    """A receiver that sends ``data`` once and hangs up."""

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(data)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(serve, LOOPBACK, 0)
    return server, int(server.sockets[0].getsockname()[1])


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # A last line without its terminator still counts; empty lines do not.
        (b"\rPWON", ["PWON"]),
        # A line longer than the read buffer breaks the session, nothing is fed.
        (b"X" * 70_000, []),
    ],
)
async def test_what_arrives_before_the_receiver_hangs_up(
    data: bytes, expected: list[str]
) -> None:
    server, port = await _raw_receiver(data)
    lines: list[str] = []
    client = TelnetClient(LOOPBACK, on_line=lines.append, port=port)
    try:
        await client.async_start()
        await asyncio.sleep(0.3)
    finally:
        await client.async_stop()
        server.close()
        await server.wait_closed()
    assert lines[: len(expected)] == expected
    assert all(line.startswith("PW") for line in lines)


async def test_an_idle_link_is_kept_alive(receiver: FakeReceiver) -> None:
    with patch.object(telnet, "KEEPALIVE_INTERVAL", 0.2):
        client = await _connected_client(receiver, lambda client: [])
        try:
            await receiver.wait_for(1, timeout=3.0)
        finally:
            await client.async_stop()
    assert receiver.lines[0] == "PW?"


async def test_the_log_keeps_what_went_out_and_came_back(receiver: FakeReceiver) -> None:
    """For the diagnostics: the lines with their direction, oldest first."""
    client = await _connected_client(receiver, lambda _: ["PW?"])
    try:
        await receiver.wait_for(1)
        async with asyncio.timeout(5):
            while len(client.log) < 2:
                await asyncio.sleep(0.01)
    finally:
        await client.async_stop()
    assert [entry.split(" ", 1)[1] for entry in client.log] == ["> PW?", "< PWX"]


async def test_a_receiver_that_answers_its_control_connection() -> None:
    from custom_components.denonavr_plus.avr.transport import telnet

    async def answering(host: str, queries: list[str], on_line, is_complete, **kwargs) -> None:  # type: ignore[no-untyped-def]
        assert queries == ["PW?"]
        on_line("PWON")

    async def silent(host: str, queries: list[str], on_line, is_complete, **kwargs) -> None:  # type: ignore[no-untyped-def]
        return None

    with patch.object(telnet, "async_probe", answering):
        assert await telnet.async_answers("192.0.2.10")
    with patch.object(telnet, "async_probe", silent):
        assert not await telnet.async_answers("192.0.2.10")
