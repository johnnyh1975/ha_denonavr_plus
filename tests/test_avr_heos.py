"""Tests for avr/transport/heos.py: the receiver's player and the heartbeat."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.denonavr_plus.avr.transport import heos
from custom_components.denonavr_plus.avr.transport.heos import HeosClient

HOST = "192.0.2.10"
SPEAKER = {"name": "Kitchen", "pid": 101, "model": "HEOS 1", "ip": "192.0.2.20"}
RECEIVER = {"name": "Living room", "pid": 202, "model": "Denon AVR-X2600H", "ip": HOST}


def _client() -> tuple[HeosClient, list[dict[str, Any]]]:
    updates: list[dict[str, Any]] = []
    return HeosClient(HOST, on_update=updates.append), updates


def _feed(client: HeosClient, command: str, message: str = "", payload: Any = None) -> None:
    body: dict[str, Any] = {"heos": {"command": command, "result": "success", "message": message}}
    if payload is not None:
        body["payload"] = payload
    client._handle_message(json.dumps(body))


def _sent(client: HeosClient) -> list[str]:
    sent = []
    while not client._queue.empty():
        sent.append(client._queue.get_nowait())
    return sent


def test_picks_the_player_with_the_receivers_address() -> None:
    client, _ = _client()
    _feed(client, "player/get_players", payload=[SPEAKER, RECEIVER])
    assert all("pid=202" in command for command in _sent(client))


def test_single_player_without_address_is_taken() -> None:
    client, _ = _client()
    player = {key: value for key, value in RECEIVER.items() if key != "ip"}
    _feed(client, "player/get_players", payload=[player])
    assert _sent(client)
    assert client._pid == 202


def test_events_of_other_players_are_ignored() -> None:
    client, updates = _client()
    _feed(client, "player/get_players", payload=[SPEAKER, RECEIVER])
    _sent(client)

    _feed(client, "event/player_state_changed", "pid=101&state=play")
    _feed(client, "event/player_now_playing_changed", "pid=101")
    assert updates == []
    assert _sent(client) == []

    _feed(client, "event/player_state_changed", "pid=202&state=play")
    assert updates[-1]["state"] == "play"


def test_stop_clears_the_track() -> None:
    client, updates = _client()
    _feed(client, "player/get_players", payload=[RECEIVER])
    _feed(
        client,
        "player/get_now_playing_media",
        "pid=202",
        {"type": "song", "song": "Track", "artist": "Artist", "album": "Album"},
    )
    _feed(client, "event/player_state_changed", "pid=202&state=play")
    assert updates[-1]["title"] == "Track"

    _feed(client, "event/player_state_changed", "pid=202&state=stop")
    assert updates[-1]["state"] == "stop"
    assert updates[-1]["title"] is None
    assert updates[-1]["artist"] is None

    # A late now-playing answer must not bring the stopped track back.
    _feed(client, "player/get_now_playing_media", "pid=202", {"song": "Track"})
    assert updates[-1]["title"] is None


def test_playing_again_after_stop_reads_the_track_again() -> None:
    client, _ = _client()
    _feed(client, "player/get_players", payload=[RECEIVER])
    _feed(client, "event/player_state_changed", "pid=202&state=stop")
    _sent(client)

    _feed(client, "event/player_state_changed", "pid=202&state=play")
    assert _sent(client) == ["heos://player/get_now_playing_media?pid=202"]


class _HeosServer:
    """A HEOS stand-in on localhost that records commands and connections."""

    def __init__(self, *, answer_heartbeat: bool) -> None:
        self.answer_heartbeat = answer_heartbeat
        self.connections = 0
        self.received: list[str] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return int(self.server.sockets[0].getsockname()[1])

    async def stop(self) -> None:
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while line := await reader.readline():
                command = line.decode().strip()
                self.received.append(command)
                if command == "heos://system/heart_beat" and not self.answer_heartbeat:
                    continue
                name = command.removeprefix("heos://").split("?")[0]
                reply = {"heos": {"command": name, "result": "success", "message": ""}}
                writer.write(json.dumps(reply).encode() + b"\r\n")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()


async def _run_client(server: _HeosServer, seconds: float) -> None:
    port = await server.start()
    client = HeosClient("127.0.0.1", on_update=lambda _: None, port=port)
    with (
        patch.object(heos, "HEARTBEAT_INTERVAL", 0.05),
        patch.object(heos, "HEARTBEAT_TIMEOUT", 0.05),
        patch.object(heos, "RECONNECT_BACKOFF_MIN", 0.01),
    ):
        await client.async_start()
        await asyncio.sleep(seconds)
        await client.async_stop()
    await server.stop()


@pytest.mark.usefixtures("socket_enabled")
async def test_an_idle_link_sends_a_heartbeat_and_stays() -> None:
    server = _HeosServer(answer_heartbeat=True)
    await _run_client(server, 0.4)
    assert "heos://system/heart_beat" in server.received
    assert server.connections == 1


@pytest.mark.usefixtures("socket_enabled")
async def test_an_unanswered_heartbeat_opens_the_link_again() -> None:
    server = _HeosServer(answer_heartbeat=False)
    await _run_client(server, 0.4)
    assert "heos://system/heart_beat" in server.received
    assert server.connections >= 2
