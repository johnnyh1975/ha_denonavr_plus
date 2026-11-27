"""Tests for activity.py: what a zone plays, in one word."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import State

from custom_components.denonavr_plus.activity import ACTIVITIES, zone_activity


def _player(state: str, media_type: str | None) -> State:
    attributes: dict[str, Any] = {"media_content_type": media_type} if media_type else {}
    return State("media_player.apple_tv", state, attributes)


def _activity(**given: Any) -> str | None:
    values: dict[str, Any] = {
        "power": True,
        "announcing": False,
        "source": "MPLAY",
        "linked": None,
        "network_media_type": None,
    }
    values.update(given)
    return zone_activity(**values)


def test_off_and_unknown_power() -> None:
    assert _activity(power=None) is None
    assert _activity(power=False, announcing=True) == "off"


def test_an_announcement_wins_over_what_plays() -> None:
    assert _activity(announcing=True, linked=_player("playing", "movie")) == "announcement"


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [("movie", "film"), ("episode", "tv"), ("tvshow", "tv"), ("music", "music"), ("game", "game")],
)
def test_the_linked_player_says_what_plays(media_type: str, expected: str) -> None:
    assert _activity(source="GAME", linked=_player("playing", media_type)) == expected
    assert _activity(source="GAME", linked=_player("paused", media_type)) == expected


def test_an_idle_or_unclear_linked_player_leaves_it_to_the_input() -> None:
    assert _activity(source="BD", linked=_player("idle", "movie")) == "film"
    assert _activity(source="SAT/CBL", linked=_player("playing", "app")) == "tv"
    assert _activity(source="MPLAY", linked=_player("playing", None)) == "on"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("TUNER", "radio"),
        ("IRADIO", "radio"),
        ("NET", "music"),
        ("SERVER", "music"),
        ("CD", "music"),
        ("GAME", "game"),
        ("TV", "tv"),
        ("DVD", "film"),
        ("AUX1", "on"),
        (None, "on"),
    ],
)
def test_the_input_says_what_plays(source: str | None, expected: str) -> None:
    assert _activity(source=source) == expected


def test_a_heos_station_on_the_network_input_is_radio() -> None:
    assert _activity(source="NET", network_media_type="station") == "radio"
    assert _activity(source="NET", network_media_type="song") == "music"
    # A station on a game console's input is no radio.
    assert _activity(source="GAME", network_media_type="station") == "game"


def test_every_result_is_an_option() -> None:
    results = {
        _activity(power=False),
        _activity(announcing=True),
        _activity(source="NET"),
        _activity(source="TUNER"),
        _activity(source="DVD"),
        _activity(source="TV"),
        _activity(source="GAME"),
        _activity(source="AUX1"),
    }
    assert results == set(ACTIVITIES)
