"""HEOS music in the media browser: browse pages and playing from them."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.components.media_player import BrowseError, MediaPlayerEnqueue
from homeassistant.exceptions import HomeAssistantError

from custom_components.denonavr_plus import heos_media
from custom_components.denonavr_plus.avr.transport.heos import (
    ADD_TO_END,
    REPLACE_AND_PLAY,
    HeosCommandError,
    HeosItem,
)


class _Heos:
    """Answers like HeosClient's browse methods, and records what is played."""

    def __init__(self, pages: dict[tuple[int, str | None, int], tuple[list[HeosItem], int]]) -> None:
        self.pages = pages
        self.played: list[tuple[str, Any]] = []
        self.fail: Exception | None = None

    async def async_music_sources(self) -> list[HeosItem]:
        if self.fail:
            raise self.fail
        return [
            HeosItem("Favorites", 1028, container=True),
            HeosItem("Local Music", 1024, container=True, image_url="http://example.com/l.png"),
            HeosItem("Pandora", 1, container=True, available=False),
            HeosItem("TuneIn", 3, container=True, available=False),
        ]

    async def async_browse(
        self, sid: int, cid: str | None = None, start: int = 0
    ) -> tuple[list[HeosItem], int]:
        if self.fail:
            raise self.fail
        return self.pages[(sid, cid, start)]

    async def async_play_preset(self, number: int) -> None:
        if self.fail:
            raise self.fail
        self.played.append(("preset", number))

    async def async_play_station(self, sid: int, cid: str | None, mid: str, name: str) -> None:
        self.played.append(("station", (sid, cid, mid, name)))

    async def async_add_to_queue(
        self, sid: int, cid: str, criteria: int, mid: str | None = None
    ) -> None:
        self.played.append(("queue", (sid, cid, criteria, mid)))


def _heos() -> _Heos:
    return _Heos(
        {
            (1028, None, 0): ([HeosItem("Bayern 3", 1028, mid="s1", type="station", playable=True)], 1),
            (1024, None, 0): (
                [
                    HeosItem("Server", 1024, cid="srv/1", container=True, type="container"),
                    HeosItem("Album / Live", 1024, cid="a&1", container=True, playable=True, type="album"),
                    HeosItem("Radio", 1024, mid="r1", type="station", playable=True),
                ],
                120,
            ),
            (1024, "a&1", 0): (
                [HeosItem("Song", 1024, mid="m/1", type="song", playable=True)],
                1,
            ),
        }
    )


async def test_the_music_sources_and_their_pages() -> None:
    heos = _heos()
    root = await heos_media.async_browse(heos, "heos", "HEOS", "More")  # type: ignore[arg-type]
    titles = [child.title for child in root.children or []]
    # A source that is not available stays out; TuneIn plays even so. The queue follows.
    assert titles == ["Favorites", "Local Music", "TuneIn", "Queue"]
    favorites_id, local_id, _tunein, queue_id = (
        child.media_content_id for child in root.children or []
    )
    assert queue_id == "heos_queue"

    favorites = await heos_media.async_browse(heos, favorites_id, "HEOS", "More")  # type: ignore[arg-type]
    assert favorites.title == "Favorites"
    assert [(c.media_content_id, c.can_play) for c in favorites.children or []] == [
        ("heos_favorite/1", True)
    ]

    local = await heos_media.async_browse(heos, local_id, "HEOS", "More")  # type: ignore[arg-type]
    server, album, radio, more = local.children or []
    assert (server.can_expand, server.can_play) == (True, False)
    assert server.media_content_id.startswith("heos/1024/srv%2F1/0/")
    assert (album.can_expand, album.can_play) == (True, True)
    assert album.media_content_id == "heos_container/1024/a%261/Album%20%2F%20Live"
    assert radio.media_content_id == "heos_station/1024//r1/Radio"
    assert (more.title, more.media_content_id) == ("More", "heos/1024//50/Local%20Music")

    # A playable container opens as its first page, a track inside it plays alone.
    page = await heos_media.async_browse(heos, album.media_content_id, "HEOS", "More")  # type: ignore[arg-type]
    assert page.title == "Album / Live"
    (song,) = page.children or []
    assert song.media_content_id == "heos_track/1024/a%261/m%2F1"


async def test_playing_follows_the_kind_of_item() -> None:
    heos = _heos()
    await heos_media.async_play(heos, "heos_favorite/2", None)  # type: ignore[arg-type]
    await heos_media.async_play(heos, "heos_station/1024//r1/Radio", None)  # type: ignore[arg-type]
    await heos_media.async_play(heos, "heos_container/1024/a%261/Album", None)  # type: ignore[arg-type]
    await heos_media.async_play(
        heos, "heos_track/1024/a%261/m%2F1", MediaPlayerEnqueue.ADD  # type: ignore[arg-type]
    )
    assert heos.played == [
        ("preset", 2),
        ("station", (1024, None, "r1", "Radio")),
        ("queue", (1024, "a&1", REPLACE_AND_PLAY, None)),
        ("queue", (1024, "a&1", ADD_TO_END, "m/1")),
    ]


async def test_failures_are_reported() -> None:
    heos = _heos()
    with pytest.raises(HomeAssistantError):
        await heos_media.async_play(heos, "heos_track/1024/only-two", None)  # type: ignore[arg-type]
    heos.fail = HeosCommandError("browse/play_preset", "Invalid preset")
    with pytest.raises(HomeAssistantError) as err:
        await heos_media.async_play(heos, "heos_favorite/9", None)  # type: ignore[arg-type]
    assert err.value.translation_placeholders == {"error": "browse/play_preset: Invalid preset"}
    heos.fail = ConnectionError("gone")
    with pytest.raises(BrowseError):
        await heos_media.async_browse(heos, "heos", "HEOS", "More")  # type: ignore[arg-type]
    with pytest.raises(BrowseError):
        await heos_media.async_browse(heos, "heos/x", "HEOS", "More")  # type: ignore[arg-type]
    assert heos_media.is_heos_id("heos") and not heos_media.is_heos_id(None)
    assert not heos_media.is_heos_id("source/CD")
