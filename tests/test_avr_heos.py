"""Tests for avr/transport/heos.py: the receiver's player and the heartbeat."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
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


def _playing(client: HeosClient) -> None:
    _feed(client, "player/get_players", payload=[RECEIVER])
    _feed(client, "event/player_state_changed", "pid=202&state=play")
    _sent(client)


def test_the_play_mode_is_read_and_followed() -> None:
    client, updates = _client()
    _feed(client, "player/get_players", payload=[RECEIVER])
    assert "heos://player/get_play_mode?pid=202" in _sent(client)
    _feed(client, "player/get_play_mode", "pid=202&repeat=on_all&shuffle=off")
    assert (updates[-1]["repeat"], updates[-1]["shuffle"]) == ("on_all", False)
    _feed(client, "event/shuffle_mode_changed", "pid=202&shuffle=on")
    assert updates[-1]["shuffle"] is True
    # Another player's change is not this receiver's.
    _feed(client, "event/repeat_mode_changed", "pid=101&repeat=on_one")
    assert updates[-1]["repeat"] == "on_all"


async def test_setting_one_play_mode_keeps_the_other() -> None:
    client, _ = _client()
    _feed(client, "player/get_players", payload=[RECEIVER])
    _feed(client, "player/get_play_mode", "pid=202&repeat=on_one&shuffle=on")
    _sent(client)
    await client.async_set_shuffle(False)
    await client.async_set_repeat("off")
    assert _sent(client) == [
        "heos://player/set_play_mode?pid=202&repeat=on_one&shuffle=off",
        "heos://player/set_play_mode?pid=202&repeat=off&shuffle=on",
    ]


def test_progress_counts_only_when_the_position_jumps() -> None:
    """HEOS reports every second; Home Assistant extrapolates in between."""
    client, updates = _client()
    now = [1000.0]
    client._clock = lambda: now[0]
    _playing(client)
    _feed(client, "event/player_now_playing_progress", "pid=202&cur_pos=30000&duration=240000")
    assert (updates[-1]["position"], updates[-1]["duration"]) == (30.0, 240.0)
    count = len(updates)
    now[0] += 1
    _feed(client, "event/player_now_playing_progress", "pid=202&cur_pos=31000&duration=240000")
    assert len(updates) == count
    # A seek.
    now[0] += 1
    _feed(client, "event/player_now_playing_progress", "pid=202&cur_pos=120000&duration=240000")
    assert (updates[-1]["position"], updates[-1]["position_at"]) == (120.0, 1002.0)


def test_pause_freezes_the_position_and_stop_clears_it() -> None:
    client, updates = _client()
    now = [1000.0]
    client._clock = lambda: now[0]
    _playing(client)
    _feed(client, "event/player_now_playing_progress", "pid=202&cur_pos=10000&duration=200000")
    now[0] += 5
    _feed(client, "event/player_state_changed", "pid=202&state=pause")
    assert (updates[-1]["position"], updates[-1]["position_at"]) == (15.0, 1005.0)
    _feed(client, "event/player_state_changed", "pid=202&state=stop")
    assert updates[-1]["position"] is None and updates[-1]["duration"] is None


@pytest.mark.assumed(
    "browse/play_stream is in the HEOS CLI specification 1.14; whether an "
    "AVR-X2600H plays any URL with it has not been tried"
)
async def test_a_url_is_played_with_its_reserved_characters_encoded() -> None:
    client, _ = _client()
    assert not await client.async_play_url("http://example.com/a.mp3")
    _feed(client, "player/get_players", payload=[RECEIVER])
    _sent(client)
    assert await client.async_play_url("http://example.com/tts?a=1&b=50%")
    assert _sent(client) == [
        "heos://browse/play_stream?pid=202&url=http://example.com/tts?a%3D1%26b%3D50%25"
    ]


def test_the_outcome_of_a_command_is_kept_without_the_url() -> None:
    """For the diagnostics: whether the receiver took play_stream, not what was played."""
    client, _ = _client()
    assert not client.connected
    _feed(client, "player/get_players", payload=[RECEIVER])
    client._handle_message(
        json.dumps(
            {
                "heos": {
                    "command": "browse/play_stream",
                    "result": "fail",
                    "message": "eid=9&text=Unknown error&pid=202&url=http://192.0.2.5/tts.mp3",
                }
            }
        )
    )
    _feed(client, "player/set_play_mode", "pid=202&repeat=on_all&shuffle=off")
    _feed(client, "player/get_play_state", "pid=202&state=play")
    assert client.command_results == {
        "browse/play_stream": {"result": "fail", "eid": "9", "text": "Unknown error"},
        "player/set_play_mode": {"result": "success"},
    }


class _HeosServer:
    """A HEOS stand-in on localhost that records commands and connections."""

    def __init__(
        self,
        *,
        answer_heartbeat: bool,
        replies: Callable[[str, str], list[dict]] | None = None,
    ) -> None:
        self.answer_heartbeat = answer_heartbeat
        # Scripted answers by command name and full command; None: a plain success.
        self.replies = replies
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
                scripted = self.replies(name, command) if self.replies else None
                if scripted is None:
                    scripted = [{"heos": {"command": name, "result": "success", "message": ""}}]
                for reply in scripted:
                    writer.write(json.dumps(reply).encode() + b"\r\n")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()


async def _run_client(server: _HeosServer, seconds: float, timeout: float) -> None:
    port = await server.start()
    client = HeosClient("127.0.0.1", on_update=lambda _: None, port=port)
    with (
        patch.object(heos, "HEARTBEAT_INTERVAL", 0.05),
        patch.object(heos, "HEARTBEAT_TIMEOUT", timeout),
        patch.object(heos, "RECONNECT_BACKOFF_MIN", 0.01),
    ):
        await client.async_start()
        await asyncio.sleep(seconds)
        await client.async_stop()
    await server.stop()


@pytest.mark.usefixtures("socket_enabled")
async def test_an_idle_link_sends_a_heartbeat_and_stays() -> None:
    server = _HeosServer(answer_heartbeat=True)
    # A generous timeout: a busy test runner must not turn a reply into a miss.
    await _run_client(server, 0.4, timeout=1.0)
    assert "heos://system/heart_beat" in server.received
    assert server.connections == 1


@pytest.mark.usefixtures("socket_enabled")
async def test_an_unanswered_heartbeat_opens_the_link_again() -> None:
    server = _HeosServer(answer_heartbeat=False)
    await _run_client(server, 0.6, timeout=0.05)
    assert "heos://system/heart_beat" in server.received
    assert server.connections >= 2


async def test_no_heartbeat_while_messages_arrive() -> None:
    client, _ = _client()
    loop = asyncio.get_running_loop()
    with patch.object(heos, "HEARTBEAT_INTERVAL", 0.2):
        task = asyncio.ensure_future(client._heartbeat_loop())
        for _ in range(25):
            client._last_received = loop.time()
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert _sent(client) == []


class _ClosingWriter:
    """A writer whose close waiter is shared, as asyncio's is."""

    def __init__(self) -> None:
        self.closer: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        await self.closer


async def test_stopping_while_a_dropped_link_closes() -> None:
    """Unloading during a reconnect: the close was cancelled once already."""
    client, _ = _client()
    client._writer = _ClosingWriter()  # type: ignore[assignment]
    closing = asyncio.ensure_future(client._close_socket())
    await asyncio.sleep(0)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    # The second close must not wait on the cancelled waiter again.
    await client.async_stop()


def _answer(name: str, message: str = "", payload: Any = None, result: str = "success") -> dict:
    reply: dict[str, Any] = {"heos": {"command": name, "result": result, "message": message}}
    if payload is not None:
        reply["payload"] = payload
    return reply


_SOURCES = [
    {"name": "Favorites", "sid": 1028, "type": "heos_service", "available": "true", "image_url": ""},
    {"name": "TuneIn", "sid": 3, "type": "music_service", "available": "true"},
    {"name": "Pandora", "sid": 1, "type": "music_service", "available": "false"},
]


def _browse_replies(name: str, command: str) -> list[dict] | None:
    """A receiver with two favorites, TuneIn and a 120-item local container."""
    if name == "player/get_players":
        return [_answer(name, payload=[{"pid": 202, "name": "AVR", "ip": "127.0.0.1"}])]
    if name == "system/check_account":
        return [_answer(name, "signed_in&un=user@example.com")]
    if name == "browse/get_music_sources":
        return [_answer(name, payload=_SOURCES)]
    if name == "browse/browse" and "sid=1028" in command:
        favorites = [
            {"container": "no", "playable": "yes", "type": "station", "name": "Bayern 3", "mid": "s1"},
            {"container": "no", "playable": "yes", "type": "station", "name": "Klassik & Jazz", "mid": "s2"},
        ]
        return [_answer(name, "sid=1028&returned=2&count=2", favorites)]
    if name == "browse/browse" and "cid=Albums%26More" in command:
        # Interim answer first, as the CLI sends for slow sources.
        page = [{"container": "no", "playable": "yes", "type": "song", "name": "Track", "mid": "m1"}]
        return [
            _answer(name, "command under process&sid=4"),
            _answer(name, "sid=4&cid=Albums%26More&returned=1&count=120", page),
        ]
    if name == "browse/play_preset" and "preset=9" in command:
        return [_answer(name, "eid=2&text=Invalid preset", result="fail")]
    return None


@pytest.mark.usefixtures("socket_enabled")
async def test_browse_asks_and_waits_for_the_answer() -> None:
    """Sources, a page past an interim answer, favorites once signed in, a failure."""
    server = _HeosServer(answer_heartbeat=True, replies=_browse_replies)
    port = await server.start()
    updates: list[dict] = []
    client = HeosClient("127.0.0.1", on_update=updates.append, port=port)
    await client.async_start()
    try:
        for _ in range(100):
            if client.connected and client.favorites:
                break
            await asyncio.sleep(0.01)
        assert client.favorites == ["Bayern 3", "Klassik & Jazz"]

        sources = await client.async_music_sources()
        assert [(item.sid, item.available) for item in sources] == [
            (1028, True), (3, True), (1, False)
        ]
        items, total = await client.async_browse(4, "Albums&More", 50)
        assert (items[0].name, items[0].mid, total) == ("Track", "m1", 120)
        assert "heos://browse/browse?sid=4&cid=Albums%26More&range=50,99" in server.received

        await client.async_play_preset(2)
        await client.async_play_station(3, "CID-1", "s=1", "Q & A")
        await client.async_add_to_queue(4, "Albums&More", heos.PLAY_NEXT, mid="m1")
        assert server.received[-3:] == [
            "heos://browse/play_preset?pid=202&preset=2",
            "heos://browse/play_stream?pid=202&sid=3&cid=CID-1&mid=s%3D1&name=Q %26 A",
            "heos://browse/add_to_queue?pid=202&sid=4&cid=Albums%26More&mid=m1&aid=2",
        ]
        with pytest.raises(heos.HeosCommandError, match="Invalid preset"):
            await client.async_play_preset(9)
    finally:
        await client.async_stop()
        await server.stop()


async def test_a_request_without_a_link_or_an_answer_fails() -> None:
    client, _ = _client()
    with pytest.raises(ConnectionError):
        await client.async_music_sources()
    with pytest.raises(ConnectionError):
        await client.async_play_preset(1)


@pytest.mark.usefixtures("socket_enabled")
async def test_requests_fail_when_the_link_drops_and_favorites_clear_when_signed_out() -> None:
    def replies(name: str, command: str) -> list[dict] | None:
        if name == "player/get_players":
            return [_answer(name, payload=[{"pid": 202, "name": "AVR", "ip": "127.0.0.1"}])]
        if name == "system/check_account":
            return [_answer(name, "signed_out")]
        if name == "browse/get_music_sources":
            return []  # never answered
        return None

    server = _HeosServer(answer_heartbeat=True, replies=replies)
    port = await server.start()
    client = HeosClient("127.0.0.1", on_update=lambda _: None, port=port)
    client.favorites = ["old"]
    await client.async_start()
    try:
        for _ in range(100):
            if client.connected and not client.favorites:
                break
            await asyncio.sleep(0.01)
        assert client.favorites == []
        with patch.object(heos, "REQUEST_TIMEOUT", 0.05), pytest.raises(TimeoutError):
            await client.async_music_sources()
        pending = asyncio.ensure_future(client.async_music_sources())
        await asyncio.sleep(0.05)
        await client.async_stop()
        with pytest.raises(ConnectionError):
            await pending
    finally:
        await client.async_stop()
        await server.stop()


def test_a_sources_change_reads_the_favorites_again() -> None:
    client, _ = _client()
    with patch.object(client, "_schedule_favorites") as schedule:
        _feed(client, "event/sources_changed")
        _feed(client, "event/user_changed")
    assert schedule.call_count == 2


def test_a_playback_error_is_kept_for_the_diagnostics() -> None:
    client, _ = _client()
    _feed(client, "player/get_players", payload=[RECEIVER])
    _feed(client, "event/player_playback_error", "pid=202&error=Could not download")
    _feed(client, "event/player_playback_error", "pid=999&error=Someone else")
    assert client.command_results["playback_error"] == {
        "result": "fail",
        "text": "Could not download",
    }

