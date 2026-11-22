"""Tests for avr/transport/heos.py: which HEOS player is the receiver."""

from __future__ import annotations

import json
from typing import Any

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
