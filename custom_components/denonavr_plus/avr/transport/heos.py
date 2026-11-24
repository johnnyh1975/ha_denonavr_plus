"""HEOS CLI transport (port 1255) for now-playing media and album art.

The goform now-playing endpoints are disabled on HEOS-era firmware (they answer
403), so the receiver's network now-playing metadata - track/artist/album, the
transport state and a real album-art image URL - is read from the HEOS command
line interface instead. This transport owns that TCP socket only; it parses the
newline-delimited JSON messages and hands a plain snapshot dict to a callback.

It is strictly auxiliary: any failure here leaves core telnet control untouched,
so every error is swallowed and simply results in no now-playing data. The
connection registers for HEOS change events and re-queries on the ones that
matter (now-playing changed, transport state changed). The once-a-second
progress events only count when the position jumps (a seek, a new track), so
Home Assistant extrapolates in between as it does for other players. Only the receiver's own player counts: the HEOS
system lists every HEOS device in the home, so the player is matched on the
receiver's address and events of other players are ignored.

Browsing (music sources, HEOS favorites, local servers, TuneIn, playlists)
asks and waits for the answer: a request is matched to the next reply with
the same command, past the interim "command under process" replies. HEOS
favorites need the HEOS system signed in to an account, which the HEOS app
normally has done; this client does not sign in.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs

from ..const import (
    CONNECT_TIMEOUT,
    RECONNECT_BACKOFF_MAX,
    RECONNECT_BACKOFF_MIN,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 1255
_TERMINATOR = b"\r\n"
# HEOS tolerates back-to-back commands, but a tiny gap keeps ordering sane.
_COMMAND_SPACING = 0.05
# A HEOS socket can die without a FIN (receiver in deep standby, a NAT or
# Wi-Fi timeout), and with no events nothing would notice. After this long
# without any message, ask with heart_beat; no message within the timeout
# means the link is dead and it is opened again. pyheos also beats after 10 s.
HEARTBEAT_INTERVAL = 10.0
HEARTBEAT_TIMEOUT = 5.0
_HEARTBEAT = "heos://system/heart_beat"
# A progress report this far from where the position should be (seconds) is
# a jump worth reporting: a seek, a new track, a stall.
_POSITION_JUMP = 2.0
REPEAT_MODES = ("off", "on_all", "on_one")
#: How long a browse request waits for its answer.
REQUEST_TIMEOUT = 10.0
#: Items per browse page (the CLI returns at most 50 or 100).
BROWSE_PAGE = 50
#: The title HEOS gives a stream it cannot read the metadata of.
_URL_STREAM = "url stream"
#: The HEOS favorites source.
FAVORITES_SID = 1028
#: TuneIn, which can report itself unavailable and still play (as the HEOS
#: integration of Home Assistant lists it regardless).
TUNEIN_SID = 3
#: Add criteria of browse/add_to_queue.
PLAY_NOW, PLAY_NEXT, ADD_TO_END, REPLACE_AND_PLAY = 1, 2, 3, 4


class HeosCommandError(Exception):
    """The receiver answered a HEOS request with a failure."""

    def __init__(self, command: str, text: str) -> None:
        super().__init__(f"{command}: {text}")
        self.text = text


@dataclass(frozen=True)
class HeosItem:
    """A music source, a container or a playable item from HEOS browse."""

    name: str
    sid: int
    cid: str | None = None
    mid: str | None = None
    image_url: str | None = None
    #: artist, album, song, station, container, playlist, ... or the source
    #: type (music_service, heos_server, heos_service, dlna_server).
    type: str | None = None
    container: bool = False
    playable: bool = False
    available: bool = True


class HeosClient:
    """Read now-playing media from the receiver's HEOS CLI, with events."""

    def __init__(
        self,
        host: str,
        on_update: Callable[[dict], None],
        port: int = DEFAULT_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._on_update = on_update

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._manager_task: asyncio.Task | None = None
        self._stopped = False
        self._pid: int | None = None
        # Loop time of the last message received, for the heartbeat.
        self._last_received = 0.0
        # Current snapshot; only re-broadcast when something actually changes.
        self._snapshot: dict = {
            "state": None,
            "media_type": None,
            "title": None,
            "artist": None,
            "album": None,
            "image_url": None,
            "position": None,
            "duration": None,
            "position_at": None,
            "repeat": None,
            "shuffle": None,
            "service": None,
            "station": None,
            "queue_id": None,
            "error": None,
        }
        # Wall clock, replaceable in tests.
        self._clock: Callable[[], float] = time.time
        # The receiver's last answer to each command this client sent (result,
        # and eid and text on a failure), for the diagnostics.
        self.command_results: dict[str, dict[str, str]] = {}
        # Requests waiting for their answer, per command, oldest first, and by
        # their SEQUENCE number (the CLI repeats it in the answer).
        self._pending: dict[str, deque[asyncio.Future[dict[str, Any]]]] = {}
        self._by_sequence: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._sequence = 0
        # Names of the HEOS favorites (play_preset numbers from 1), empty while
        # the HEOS system is not signed in.
        self.favorites: list[str] = []
        # Music source names by source id, for the now-playing service.
        self._source_names: dict[int, str] = {}
        self._favorites_task: asyncio.Task[None] | None = None

    @property
    def connected(self) -> bool:
        """Connected, and the receiver's own player is known."""

        return self._writer is not None and self._pid is not None

    async def async_start(self) -> None:
        """Start the background connection manager."""

        self._stopped = False
        if self._manager_task is None or self._manager_task.done():
            self._manager_task = asyncio.ensure_future(self._manage())

    async def async_stop(self) -> None:
        """Stop the connection manager and close the socket."""

        self._stopped = True
        if self._favorites_task is not None:
            self._favorites_task.cancel()
        if self._manager_task is not None:
            self._manager_task.cancel()
            try:
                await self._manager_task
            except asyncio.CancelledError:
                pass
            self._manager_task = None
        await self._close_socket()

    # Transport controls (no-ops until a player id is known) ---------------

    async def async_play(self) -> None:
        await self._send_player("set_play_state", "&state=play")

    async def async_pause(self) -> None:
        await self._send_player("set_play_state", "&state=pause")

    async def async_stop_playback(self) -> None:
        await self._send_player("set_play_state", "&state=stop")

    async def async_next(self) -> None:
        await self._send_player("play_next", "")

    async def async_previous(self) -> None:
        await self._send_player("play_previous", "")

    async def async_play_url(self, url: str) -> bool:
        """Play a stream URL on the receiver (HEOS browse/play_stream).

        False while the receiver's player is not known yet.
        """

        if self._pid is None:
            return False
        await self._queue.put(
            f"heos://browse/play_stream?pid={self._pid}&url={_quote(url)}"
        )
        return True

    # Browsing and playing from HEOS ----------------------------------------

    async def async_music_sources(self) -> list[HeosItem]:
        """The HEOS music sources (favorites, local servers, TuneIn, ...)."""

        message = await self._request("browse/get_music_sources")
        return [
            HeosItem(
                name=str(item.get("name", "")),
                sid=int(item["sid"]),
                image_url=item.get("image_url") or None,
                type=item.get("type"),
                container=True,
                available=str(item.get("available", "true")).lower() == "true",
            )
            for item in message.get("payload") or []
            if "sid" in item
        ]

    async def async_browse(
        self, sid: int, cid: str | None = None, start: int = 0
    ) -> tuple[list[HeosItem], int]:
        """One page of a source or a container, and the total (0: unknown)."""

        query = f"sid={sid}"
        if cid is not None:
            query += f"&cid={_quote(cid)}"
        query += f"&range={start},{start + BROWSE_PAGE - 1}"
        message = await self._request("browse/browse", query)
        attrs = _parse_message(message.get("heos", {}).get("message", ""))
        items = [
            HeosItem(
                name=str(item.get("name", "")),
                sid=int(item.get("sid", sid)),
                cid=item.get("cid"),
                mid=item.get("mid"),
                image_url=item.get("image_url") or None,
                type=item.get("type"),
                container=item.get("container") == "yes",
                playable=item.get("playable") == "yes",
            )
            for item in message.get("payload") or []
        ]
        count = attrs.get("count", "0")
        return items, int(count) if count.isdigit() else 0

    async def async_play_preset(self, number: int) -> None:
        """Play HEOS favorite `number` (1 and above)."""

        await self._request("browse/play_preset", f"pid={self._own_pid()}&preset={number}")

    async def async_play_station(self, sid: int, cid: str | None, mid: str, name: str) -> None:
        query = f"pid={self._own_pid()}&sid={sid}"
        if cid:
            query += f"&cid={_quote(cid)}"
        query += f"&mid={_quote(mid)}&name={_quote(name)}"
        await self._request("browse/play_stream", query)

    async def async_add_to_queue(
        self, sid: int, cid: str, criteria: int, mid: str | None = None
    ) -> None:
        """Queue a playable container, or one track of it (mid)."""

        query = f"pid={self._own_pid()}&sid={sid}&cid={_quote(cid)}"
        if mid:
            query += f"&mid={_quote(mid)}"
        await self._request("browse/add_to_queue", f"{query}&aid={criteria}")

    # The HEOS queue ------------------------------------------------------

    async def async_get_queue(self) -> list[dict[str, Any]]:
        """The queue: qid, song, artist, album, image_url (the first 100)."""

        message = await self._request("player/get_queue", f"pid={self._own_pid()}&range=0,99")
        return [
            {
                "queue_id": int(item["qid"]),
                "title": item.get("song"),
                "artist": item.get("artist"),
                "album": item.get("album"),
                "image_url": item.get("image_url") or None,
            }
            for item in message.get("payload") or []
            if str(item.get("qid", "")).isdigit()
        ]

    async def async_play_queue_item(self, queue_id: int) -> None:
        await self._request("player/play_queue", f"pid={self._own_pid()}&qid={queue_id}")

    async def async_clear_queue(self) -> None:
        await self._request("player/clear_queue", f"pid={self._own_pid()}")

    async def async_remove_from_queue(self, queue_ids: list[int]) -> None:
        ids = ",".join(str(qid) for qid in queue_ids)
        await self._request("player/remove_from_queue", f"pid={self._own_pid()}&qid={ids}")

    async def async_move_queue_item(self, queue_ids: list[int], destination: int) -> None:
        ids = ",".join(str(qid) for qid in queue_ids)
        await self._request(
            "player/move_queue_item", f"pid={self._own_pid()}&sqid={ids}&dqid={destination}"
        )

    async def async_refresh_favorites(self) -> None:
        """Read the favorites' names if the HEOS system is signed in. Never raises."""

        try:
            names = {item.sid: item.name for item in await self.async_music_sources()}
        except Exception as err:  # noqa: BLE001 - a background task; an odd answer too
            _LOGGER.debug("HEOS music sources of %s not read: %s", self._host, err)
        else:
            if names != self._source_names:
                self._source_names = names
                # Name the service of what plays now.
                self._request_now_playing()
        try:
            account = await self._request("system/check_account")
            if "signed_in" not in account.get("heos", {}).get("message", "").split("&")[0]:
                favorites: list[str] = []
            else:
                items, _ = await self.async_browse(FAVORITES_SID)
                favorites = [item.name for item in items]
        except Exception as err:  # noqa: BLE001 - a background task; an odd answer too
            _LOGGER.debug("HEOS favorites of %s not read: %s", self._host, err)
            return
        if favorites != self.favorites:
            self.favorites = favorites
            self._on_update(dict(self._snapshot))

    def _schedule_favorites(self) -> None:
        if self._favorites_task is None or self._favorites_task.done():
            self._favorites_task = asyncio.ensure_future(self.async_refresh_favorites())

    def _own_pid(self) -> int:
        if self._pid is None:
            raise ConnectionError("HEOS player not known yet")
        return self._pid

    async def _request(self, command: str, query: str = "") -> dict[str, Any]:
        """Send a command and wait for its answer; raise on a failure."""

        if self._writer is None:
            raise ConnectionError("HEOS not connected")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        waiting = self._pending.setdefault(command, deque())
        waiting.append(future)
        self._sequence += 1
        sequence = str(self._sequence)
        self._by_sequence[sequence] = future
        # The SEQUENCE comes back in the answer, so a late answer to a request
        # that timed out never reaches the next one (as pyheos matches them).
        query = f"SEQUENCE={sequence}" + (f"&{query}" if query else "")
        await self._queue.put(f"heos://{command}?{query}")
        try:
            message = await asyncio.wait_for(future, REQUEST_TIMEOUT)
        finally:
            if future in waiting:
                waiting.remove(future)
            self._by_sequence.pop(sequence, None)
        heos = message.get("heos", {})
        if heos.get("result") == "fail":
            attrs = _parse_message(heos.get("message", ""))
            raise HeosCommandError(command, attrs.get("text", "failed"))
        return message

    def _answer(self, command: str, heos: dict[str, Any], message: dict[str, Any]) -> None:
        """Hand an answer to the oldest request waiting for it."""

        text = str(heos.get("message", ""))
        if "command under process" in text:
            return
        sequence = _parse_message(text).get("SEQUENCE")
        if sequence is not None:
            # An answer that names its request goes to that one only, or to
            # none when it has given up waiting.
            future = self._by_sequence.pop(sequence, None)
            if future is None:
                return
            waiting = self._pending.get(command)
            if waiting and future in waiting:
                waiting.remove(future)
        else:
            waiting = self._pending.get(command)
            if not waiting:
                return
            future = waiting.popleft()
        if not future.done():
            future.set_result(message)

    def _fail_pending(self) -> None:
        self._by_sequence.clear()
        for waiting in self._pending.values():
            while waiting:
                future = waiting.popleft()
                if not future.done():
                    future.set_exception(ConnectionError("HEOS connection closed"))

    async def async_set_repeat(self, repeat: str) -> None:
        """Repeat off, all or one (off, on_all, on_one)."""

        await self._set_play_mode(repeat=repeat)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._set_play_mode(shuffle=shuffle)

    async def _set_play_mode(self, repeat: str | None = None, shuffle: bool | None = None) -> None:
        # The command takes both; the one not changed keeps its current value.
        repeat = repeat or self._snapshot.get("repeat") or "off"
        if shuffle is None:
            shuffle = bool(self._snapshot.get("shuffle"))
        await self._send_player(
            "set_play_mode", f"&repeat={repeat}&shuffle={'on' if shuffle else 'off'}"
        )

    async def _send_player(self, command: str, extra: str) -> None:
        if self._pid is None:
            return
        await self._queue.put(f"heos://player/{command}?pid={self._pid}{extra}")

    # Connection lifecycle -------------------------------------------------

    async def _manage(self) -> None:
        """Keep the HEOS connection up, reconnecting with backoff. Never raises."""

        backoff = RECONNECT_BACKOFF_MIN
        while not self._stopped:
            try:
                await self._connect()
                backoff = RECONNECT_BACKOFF_MIN
                await self._run_session()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - auxiliary, must not propagate
                _LOGGER.debug("HEOS connection to %s failed: %s", self._host, err)
            finally:
                await self._close_socket()
            if self._stopped:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, RECONNECT_BACKOFF_MAX)

    async def _connect(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self._host, self._port), timeout=CONNECT_TIMEOUT
        )
        _LOGGER.debug("HEOS connected to %s:%s", self._host, self._port)
        # Drain any stale queued commands from a previous session, then bootstrap.
        while not self._queue.empty():
            self._queue.get_nowait()
        await self._queue.put("heos://system/register_for_change_events?enable=on")
        await self._queue.put("heos://player/get_players")

    async def _run_session(self) -> None:
        self._last_received = asyncio.get_running_loop().time()
        tasks = [
            asyncio.ensure_future(self._read_loop()),
            asyncio.ensure_future(self._write_loop()),
            asyncio.ensure_future(self._heartbeat_loop()),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _close_socket(self) -> None:
        """Close the writer and drop the streams.

        The streams are dropped before waiting, as in the telnet client: if
        the wait is cancelled (the integration unloads while a dropped link
        is being closed), async_stop must not wait on the same cancelled
        close again.
        """

        writer = self._writer
        self._reader = None
        self._writer = None
        self._fail_pending()
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    # I/O loops ------------------------------------------------------------

    async def _read_loop(self) -> None:
        assert self._reader is not None
        while True:
            try:
                raw = await self._reader.readuntil(_TERMINATOR)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as err:
                raise ConnectionError("HEOS stream closed") from err
            self._last_received = asyncio.get_running_loop().time()
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                self._handle_message(line)

    async def _write_loop(self) -> None:
        assert self._writer is not None
        while True:
            command = await self._queue.get()
            self._writer.write(command.encode("utf-8") + _TERMINATOR)
            await self._writer.drain()
            await asyncio.sleep(_COMMAND_SPACING)

    async def _heartbeat_loop(self) -> None:
        """End the session when an idle link no longer answers a heart_beat."""

        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            if loop.time() - self._last_received < HEARTBEAT_INTERVAL:
                continue
            asked = loop.time()
            await self._queue.put(_HEARTBEAT)
            await asyncio.sleep(HEARTBEAT_TIMEOUT)
            if self._last_received < asked:
                raise ConnectionError("HEOS heart_beat unanswered")

    # Message handling -----------------------------------------------------

    def _handle_message(self, line: str) -> None:
        """Parse one HEOS JSON message and react to it. Never raises."""

        try:
            message = json.loads(line)
        except ValueError:
            return
        heos = message.get("heos", {})
        command = str(heos.get("command", "")).strip()
        attrs = _parse_message(heos.get("message", ""))
        if not command.startswith("event/"):
            self._answer(command, heos, message)
        if command in _SENT_COMMANDS and heos.get("result"):
            # Only the outcome: the message also repeats the URL that was played.
            self.command_results[command] = {"result": heos["result"]} | {
                key: attrs[key] for key in ("eid", "text") if key in attrs
            }

        if command == "player/get_players":
            player = self._own_player(message.get("payload") or [])
            if player is not None:
                self._pid = player.get("pid")
                # Bootstrap the current media and transport state.
                self._request_now_playing()
                self._request_play_state()
                self._request_play_mode()
                self._schedule_favorites()
            return

        if command in ("event/sources_changed", "event/user_changed"):
            # Favorites added or removed, or the HEOS account changed.
            self._schedule_favorites()
            return

        # Events name the player they are about; the HEOS system holds every
        # HEOS device in the home, so ignore events for the other players.
        if command.startswith(("event/player_", "event/repeat_", "event/shuffle_")) and (
            not self._is_own(attrs)
        ):
            return

        if command == "event/player_playback_error":
            # Kept for the diagnostics, as Music Assistant logs it.
            self.command_results["playback_error"] = {
                "result": "fail",
                "text": attrs.get("error", ""),
            }
            self._update(error=attrs.get("error") or "unknown")
            return

        if command in ("player/get_play_mode", "player/set_play_mode"):
            self._apply_play_mode(attrs)
            return

        if command == "event/repeat_mode_changed":
            self._apply_play_mode(attrs)
            return

        if command == "event/shuffle_mode_changed":
            self._apply_play_mode(attrs)
            return

        if command == "event/player_now_playing_progress":
            self._apply_progress(attrs)
            return

        if command == "player/get_now_playing_media":
            self._apply_now_playing(message.get("payload") or {})
            return

        if command == "player/get_play_state":
            self._apply_state(attrs.get("state"))
            return

        if command == "event/player_state_changed":
            self._apply_state(attrs.get("state"))
            return

        if command == "event/player_now_playing_changed":
            self._request_now_playing()
            return

        # Everything else: ignore.

    def _own_player(self, players: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Pick the receiver's own player from the HEOS system's player list.

        The list holds every HEOS device in the home, so the first entry can be
        a speaker. Match on the address the receiver was set up with; the
        players carry it in an ``ip`` field. Without a match, a lone player is
        the receiver; with several and no match the first is kept (as before),
        which the debug log records.
        """

        if not players:
            return None
        for player in players:
            if player.get("ip") == self._host:
                return player
        if len(players) > 1:
            _LOGGER.debug(
                "No HEOS player has the address %s; using %s",
                self._host,
                players[0].get("name"),
            )
        return players[0]

    def _is_own(self, attrs: dict[str, str]) -> bool:
        return self._pid is not None and attrs.get("pid") == str(self._pid)

    def _request_now_playing(self) -> None:
        if self._pid is not None:
            self._queue.put_nowait(
                f"heos://player/get_now_playing_media?pid={self._pid}"
            )

    def _request_play_mode(self) -> None:
        if self._pid is not None:
            self._queue.put_nowait(f"heos://player/get_play_mode?pid={self._pid}")

    def _apply_play_mode(self, attrs: dict[str, str]) -> None:
        fields: dict[str, Any] = {}
        if attrs.get("repeat") in REPEAT_MODES:
            fields["repeat"] = attrs["repeat"]
        if attrs.get("shuffle") in ("on", "off"):
            fields["shuffle"] = attrs["shuffle"] == "on"
        if fields:
            self._update(**fields)

    def _expected_position(self) -> float | None:
        """Where the track should be now, from the last report."""

        position = self._snapshot.get("position")
        if position is None:
            return None
        if self._snapshot.get("state") != "play":
            return float(position)
        return float(position) + self._clock() - float(self._snapshot.get("position_at") or 0)

    def _apply_progress(self, attrs: dict[str, str]) -> None:
        try:
            position = int(attrs["cur_pos"]) / 1000
            duration = int(attrs["duration"]) / 1000
        except (KeyError, ValueError):
            return
        expected = self._expected_position()
        if (
            expected is None
            or abs(position - expected) > _POSITION_JUMP
            or duration != self._snapshot.get("duration")
        ):
            self._update(position=position, duration=duration or None, position_at=self._clock())

    def _request_play_state(self) -> None:
        if self._pid is not None:
            self._queue.put_nowait(f"heos://player/get_play_state?pid={self._pid}")

    def _apply_now_playing(self, payload: dict) -> None:
        # HEOS keeps answering with the last track after a stop; showing it
        # would leave a stale title in Home Assistant.
        if self._snapshot.get("state") == "stop":
            return
        image = (payload.get("image_url") or "").strip() or None
        title = payload.get("song") or payload.get("station")
        if str(title or "").strip().casefold() == _URL_STREAM:
            # HEOS's name for a stream it cannot read (an announcement, a URL),
            # as Music Assistant's HEOS provider notes: no title is better.
            title = None
        sid, qid = str(payload.get("sid", "")), str(payload.get("qid", ""))
        self._update(
            media_type=payload.get("type"),
            title=title,
            artist=payload.get("artist"),
            album=payload.get("album"),
            image_url=image,
            service=self._source_names.get(int(sid)) if sid.isdigit() else None,
            station=(payload.get("station") or None) if payload.get("type") == "station" else None,
            queue_id=int(qid) if qid.isdigit() else None,
        )

    def _apply_state(self, state: str | None) -> None:
        if state == "stop":
            self._update(
                state=state,
                media_type=None,
                title=None,
                artist=None,
                album=None,
                image_url=None,
                position=None,
                duration=None,
                position_at=None,
                service=None,
                station=None,
                queue_id=None,
            )
        elif state:
            # Freeze or restart the position where it stands now.
            expected = self._expected_position()
            if expected is not None and state != self._snapshot.get("state"):
                self._update(state=state, position=expected, position_at=self._clock())
            else:
                self._update(state=state)
            if state == "play" and self._snapshot.get("error"):
                # Something plays again: the last error is over.
                self._update(error=None)
            # Playback resumed after a stop: the track was cleared, read it again.
            if state == "play" and self._snapshot.get("title") is None:
                self._request_now_playing()

    def _update(self, **fields) -> None:
        """Merge fields into the snapshot and broadcast if anything changed."""

        changed = False
        for key, value in fields.items():
            if self._snapshot.get(key) != value:
                self._snapshot[key] = value
                changed = True
        if changed:
            self._on_update(dict(self._snapshot))


#: The commands this client sends on the user's behalf.
_SENT_COMMANDS = frozenset(
    {
        "browse/play_stream",
        "player/set_play_state",
        "player/set_play_mode",
        "player/play_next",
        "player/play_previous",
    }
)


def _quote(value: str) -> str:
    """Encode the three characters HEOS reserves in attribute values (CLI spec 1.14)."""

    return value.replace("%", "%25").replace("&", "%26").replace("=", "%3D")


def _parse_message(message: str) -> dict[str, str]:
    """Parse a HEOS 'message' field ('pid=1&state=play') into a flat dict."""

    if not message:
        return {}
    return {key: values[0] for key, values in parse_qs(message).items() if values}
