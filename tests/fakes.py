"""Fakes for the receiver's network edges, shared by the tests.

The receiver's network is replaced at its edges only: the Deviceinfo and
status documents are served from recordings, and the telnet and HEOS
transports are fakes with the same constructor and public methods as the
real ones (AGENTS.md, rule 3; tests/test_fakes.py keeps them in step).
Everything between -- discovery, the parser, the coordinator and the
entities -- is the real code. The fixtures using them are in conftest.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.transport import (
    GoformClient,
    UpnpClient,
    WebConfigClient,
)

HOST = "192.0.2.10"

RECORDING = "AVC-X3700H-Deviceinfo-8080.xml"


def deviceinfo(name: str) -> str:
    """The text of a recorded Deviceinfo document, e.g. 'AVC-X3700H-Deviceinfo-8080.xml'."""
    return (Path(__file__).parent / "fixtures" / "denonavr" / name).read_text(encoding="utf-8")


class FakeTelnet:
    """Stands in for TelnetClient: records commands, lets a test push lines."""

    instances: list[FakeTelnet] = []

    def __init__(
        self,
        host: str,
        on_line: Callable[[str], None],
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_availability: Callable[[bool], None] | None = None,
        port: int = 23,
    ) -> None:
        self.host = host
        self._on_line = on_line
        self._on_connected = on_connected
        self._on_availability = on_availability
        self.connected = False
        self.sent: list[str] = []
        self.response_time: float | None = 0.042
        self.queued = 0
        self.log: list[str] = []
        # Answer each command with itself, as a receiver that takes it does.
        self.echo = False
        FakeTelnet.instances.append(self)

    async def async_start(self) -> None:
        self.connected = True
        if self._on_availability is not None:
            self.set_available(True)
        if self._on_connected is not None:
            await self._on_connected()

    async def async_stop(self) -> None:
        self.connected = False

    async def async_send(self, command: str) -> None:
        self.sent.append(command)
        if self.echo and not command.endswith("?"):
            self.push(command)

    def push(self, *lines: str) -> None:
        for line in lines:
            self._on_line(line)

    def set_available(self, up: bool) -> None:
        """The link goes down or comes back, as the real client reports it."""
        self.connected = up
        if self._on_availability is not None:
            self._on_availability(up)


class FakeHeos:
    """Stands in for HeosClient: records play mode commands, lets a test push now playing."""

    instances: list[FakeHeos] = []

    def __init__(self, host: str, on_update: Callable[[dict[str, Any]], None], port: int = 1255) -> None:
        self.host = host
        self.on_update = on_update
        self.calls: list[tuple[str, Any]] = []
        # A HEOS receiver whose HEOS link is up; tests of a missing link set False.
        self.connected = True
        self.command_results: dict[str, dict[str, str]] = {}
        self.favorites: list[str] = []
        self.update: bool | None = None
        self.log: list[str] = []
        FakeHeos.instances.append(self)

    async def async_set_repeat(self, repeat: str) -> None:
        self.calls.append(("repeat", repeat))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        self.calls.append(("shuffle", shuffle))

    async def async_play(self) -> None:
        self.calls.append(("play", None))

    async def async_check_update(self) -> bool | None:
        return self.update

    async def async_play_preset(self, number: int) -> None:
        self.calls.append(("play_preset", number))

    async def async_get_queue(self) -> list[dict[str, Any]]:
        return [
            {"queue_id": 1, "title": "Song", "artist": "Band", "album": None, "image_url": None},
            {"queue_id": 2, "title": "Other", "artist": None, "album": None, "image_url": None},
        ]

    async def async_play_queue_item(self, queue_id: int) -> None:
        self.calls.append(("play_queue", queue_id))

    async def async_clear_queue(self) -> None:
        self.calls.append(("clear_queue", None))

    async def async_remove_from_queue(self, queue_ids: list[int]) -> None:
        self.calls.append(("remove_from_queue", queue_ids))

    async def async_move_queue_item(self, queue_ids: list[int], destination: int) -> None:
        self.calls.append(("move_queue_item", (queue_ids, destination)))

    async def async_add_to_queue(
        self, sid: int, cid: str, criteria: int, mid: str | None = None
    ) -> None:
        self.calls.append(("add_to_queue", (sid, cid, criteria, mid)))

    async def async_music_sources(self) -> list[Any]:
        from custom_components.denonavr_plus.avr.transport.heos import HeosItem

        return [HeosItem("Favorites", 1028, container=True)]

    async def async_pause(self) -> None:
        self.calls.append(("pause", None))

    async def async_stop_playback(self) -> None:
        self.calls.append(("stop", None))

    async def async_next(self) -> None:
        self.calls.append(("next", None))

    async def async_previous(self) -> None:
        self.calls.append(("previous", None))

    async def async_play_url(self, url: str) -> bool:
        self.calls.append(("url", url))
        return True

    async def async_browse(self, sid: int, cid: str | None = None, start: int = 0) -> Any:
        self.calls.append(("browse", (sid, cid)))
        return [], 0

    async def async_play_station(self, sid: int, cid: str | None, mid: str, name: str) -> None:
        self.calls.append(("play_station", (sid, cid, mid, name)))

    async def async_refresh_favorites(self) -> None:
        self.calls.append(("refresh_favorites", None))

    async def async_start(self) -> None:
        return None

    async def async_stop(self) -> None:
        return None


_ORIGINAL_AWAIT_READY = DenonAvrDevice.async_await_ready


async def _await_ready_without_waiting(
    self: DenonAvrDevice, timeout: float = 8.0, *, preloaded: bool = False
) -> None:
    # The real method waits up to 8 s for the first resync; the fakes answer
    # nothing, so skip the wait but keep the fallback logic it runs.
    await _ORIGINAL_AWAIT_READY(self, timeout=0, preloaded=preloaded)


@contextmanager
def receiver_network_patches() -> Iterator[None]:
    """Patch the receiver's network edges with the fakes and recordings."""
    FakeTelnet.instances.clear()
    FakeHeos.instances.clear()
    with (
        patch("custom_components.denonavr_plus.avr.device.TelnetClient", FakeTelnet),
        patch("custom_components.denonavr_plus.avr.device.HeosClient", FakeHeos),
        patch("custom_components.denonavr_plus.avr.device.async_probe", AsyncMock()),
        patch.object(
            GoformClient, "async_get_device_info", AsyncMock(return_value=deviceinfo(RECORDING))
        ),
        patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)),
        patch.object(GoformClient, "async_post", AsyncMock(return_value=None)),
        patch.object(UpnpClient, "async_get_description", AsyncMock(return_value=None)),
        patch.object(WebConfigClient, "async_get", AsyncMock(return_value=None)),
        patch.object(DenonAvrDevice, "async_await_ready", _await_ready_without_waiting),
    ):
        yield
