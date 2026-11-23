"""Tests for avr/transport/telnet.py: the command queue and its pacing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from custom_components.denonavr_plus.avr.const import (
    COMMAND_SPACING,
    POWER_ON_SETTLE,
    REPLY_TIMEOUT_MAX,
)
from custom_components.denonavr_plus.avr.transport.telnet import TelnetClient, is_query

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
                if self.close_after is not None and len(self.received) >= self.close_after:
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
