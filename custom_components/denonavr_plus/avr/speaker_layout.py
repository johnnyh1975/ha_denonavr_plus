"""Speaker layout of the Web Control generation: choose, apply, put back.

On the AVC-X3800H/X3900H the speaker layout (the successor of Amp Assign)
is item 1 of the setup interface's SpeakerLayout page (speakers, type 15).
Changing it makes the receiver rebuild its speaker setup: it resets the
choices of the speaker positions and front speaker A/B. So a new layout is
only staged by its select and written by a button; what the receiver changed
besides is returned, so a repair can put it back.

Sources: LaserGuruGuy's recordings of an AVC-X3900H (the page) and his
integration, which verified the write (<Config index="1">mode</Config>) and
the restore of positions on that receiver. The receiver rebuilds the layout
for about 2 seconds after a write; its own page reads again after that.

No Home Assistant here; the restore point is plain data, kept by the caller
(in the repair), so it survives a restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .errors import SetupWriteError
from .video_config import VideoConfigController

LAYOUT = "speaker_layout"
POSITIONS = "speaker_layout_config"
MODE_SLOT = "1"

#: Why a restore is not done: the speaker preset is another one now, or the
#: layout was changed again since.
PRESET_CHANGED = "preset_changed"
LAYOUT_CHANGED = "layout_changed"


class LayoutNotSeen(SetupWriteError):
    """The receiver did not show the new layout in time; it may have taken it."""

    def __init__(self, point: dict[str, Any]) -> None:
        super().__init__(LAYOUT)
        self.point = point


class RestoreRefused(Exception):
    """A restore point that does not fit what the receiver has now."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SpeakerLayout:
    """The staged speaker layout of one receiver."""

    def __init__(
        self,
        speakers: VideoConfigController,
        telnet_value: Callable[[str], Any],
        set_telnet: Callable[[str, Any], Awaitable[None]],
        grammar: dict[str, Any],
    ) -> None:
        self._speakers = speakers
        self._telnet_value = telnet_value
        self._set_telnet = set_telnet
        self._settle = float(grammar.get("layout_settle_seconds", 2.0))
        self._attempts = int(grammar.get("layout_attempts", 4))
        self._restore_timeout = float(grammar.get("restore_timeout", 60))
        self._modes: dict[str, str] = dict(grammar.get("amp_modes", {}))
        self.pending: str | None = None
        self._lock = asyncio.Lock()

    @property
    def supported(self) -> bool:
        return MODE_SLOT in self._speakers.collection(LAYOUT)

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def current(self) -> str | None:
        item = self._speakers.collection(LAYOUT).get(MODE_SLOT)
        return item["value"] if item else None

    @property
    def options(self) -> list[str]:
        item = self._speakers.collection(LAYOUT).get(MODE_SLOT)
        return list(item["options"]) if item else []

    def key(self, mode: str) -> str:
        """The stable name of a layout id (mode_<id> when unknown)."""

        return self._modes.get(mode, f"mode_{mode}")

    def mode_of(self, key: str) -> str | None:
        return next((mode for mode in self.options if self.key(mode) == key), None)

    def stage(self, mode: str) -> bool:
        """Prepare a layout; choosing the current one drops what was prepared."""

        if self.busy or mode not in self.options:
            return False
        self.pending = None if mode == self.current else mode
        return True

    # Reading -------------------------------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        """The layout, the positions one can set now, front speaker A/B and the preset."""

        positions = {
            slot: item["value"]
            for slot, item in self._speakers.collection(POSITIONS).items()
            if item["display"] >= 3
        }
        return {
            "mode": self.current,
            "positions": positions,
            "front_speaker": self._telnet_value("front_speaker"),
            "speaker_preset": self._telnet_value("speaker_preset"),
        }

    async def _read(self) -> bool:
        return await self._speakers.async_read_collection(LAYOUT)

    async def _settled(self, shown: Callable[[], bool]) -> bool:
        """Until the receiver shows it and two reads in a row agree."""

        last: dict[str, Any] | None = None
        for _ in range(self._attempts + 2):
            await asyncio.sleep(self._settle)
            if not await self._read():
                continue
            now = self._snapshot()
            if shown() and now == last:
                return True
            last = now
        return False

    # Apply ---------------------------------------------------------------

    async def async_apply(self, before: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Write the prepared layout; the state before and after, None when nothing was written.

        ``before`` is the state before an earlier apply whose repair is still
        open: it stays the one to go back to. Raises LayoutNotSeen, with the
        restore point, when the receiver did not show the new layout in time:
        it may have taken it all the same.
        """

        mode = self.pending
        if mode is None or self.busy:
            return None
        async with self._lock:
            if not await self._read():
                # What it has now is not known: no write without a way back.
                raise SetupWriteError(LAYOUT)
            before = before or self._snapshot()
            await self._speakers.async_set_item(LAYOUT, MODE_SLOT, mode, read=False)
            self.pending = None
            seen = await self._settled(lambda: self.current == mode)
            point = {"before": before, "after": self._snapshot(), "unsettled": not seen}
        if not seen:
            raise LayoutNotSeen(point)
        return point

    # Restore -------------------------------------------------------------

    async def async_restore(self, point: dict[str, Any]) -> list[str]:
        """Put back the layout and what its change reset; returns what is still different.

        What the user changed since the change is left alone; it is judged
        before anything is written, as the receiver rebuilds the positions
        again when the old layout comes back. Refused when the speaker preset
        is another one now, or the layout another than before or after.
        """

        async with self._lock:
            async with asyncio.timeout(self._restore_timeout):
                return await self._restore(point)

    async def _restore(self, point: dict[str, Any]) -> list[str]:
        before = point["before"]
        if not await self._read():
            raise SetupWriteError(LAYOUT)
        preset = before.get("speaker_preset")
        if preset is not None and self._telnet_value("speaker_preset") != preset:
            raise RestoreRefused(PRESET_CHANGED)
        # After a change that was not seen in time, what is there now is its result.
        after = self._snapshot() if point.get("unsettled") else point["after"]
        if self.current not in (after.get("mode"), before.get("mode")):
            raise RestoreRefused(LAYOUT_CHANGED)
        now = self._snapshot()
        newer = {
            slot for slot, value in now["positions"].items()
            if slot in after["positions"] and value != after["positions"][slot]
        }
        front_newer = now["front_speaker"] != after.get("front_speaker")
        # 1. The layout; no position is written into the other one.
        if before.get("mode") not in (None, self.current):
            await self._speakers.async_set_item(LAYOUT, MODE_SLOT, before["mode"], read=False)
            if not await self._settled(lambda: self.current == before["mode"]):
                raise SetupWriteError(LAYOUT)
        # 2. The positions; one can bring another back, so each is read again.
        for slot, value in before["positions"].items():
            item = self._speakers.collection(POSITIONS).get(slot)
            if (
                slot in newer
                or item is None
                or item["value"] == value
                or item["display"] < 3
                or value not in item["options"]
            ):
                continue
            await self._speakers.async_set_item(POSITIONS, slot, value, read=False)
            await self._settled(self._shows(slot, value))
        # 3. Front speaker A/B, unless changed by hand since.
        front = before.get("front_speaker")
        if front is not None and not front_newer and self._telnet_value("front_speaker") != front:
            await self._set_telnet("front_speaker", front)
        await self._read()
        left = changes({"before": before, "after": self._snapshot()})
        return [slot for slot in left if slot not in newer and not (slot == "front_speaker" and front_newer)]

    def _shows(self, slot: str, value: str) -> Callable[[], bool]:
        return lambda: self._position(slot) == value

    def _position(self, slot: str) -> str | None:
        item = self._speakers.collection(POSITIONS).get(slot)
        return item["value"] if item else None


def changes(point: dict[str, Any]) -> list[str]:
    """What differs besides the layout: position indexes (settable ones) and front_speaker."""

    before, after = point["before"], point["after"]
    differ = [
        slot
        for slot in sorted(set(before["positions"]) | set(after["positions"]), key=lambda s: (len(s), s))
        if before["positions"].get(slot) != after["positions"].get(slot)
    ]
    if before.get("front_speaker") is not None and before["front_speaker"] != after.get("front_speaker"):
        differ.append("front_speaker")
    return differ
