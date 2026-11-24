"""HEOS music in the media browser: sources, favorites, servers, services.

The receiver's HEOS player browses its music sources (HEOS favorites, local
media servers, TuneIn, playlists, history, signed-in services) and plays from
them, as the HEOS integration and TheFab21's Denon/Marantz integration do.
This module turns HEOS browse results into Home Assistant's BrowseMedia and
plays a chosen item. Containers come in pages of 50 with a "more" entry.

Media content ids, each part percent-encoded:

- ``heos`` the music sources; ``heos/<sid>/<cid>/<start>/<title>`` a page of
  a source (empty cid) or a container
- ``heos_favorite/<n>`` HEOS favorite n (from 1): browse/play_preset
- ``heos_station/<sid>/<cid>/<mid>/<name>`` a station: browse/play_stream
- ``heos_container/<sid>/<cid>/<title>`` a playable container:
  browse/add_to_queue, and its first page when browsed
- ``heos_track/<sid>/<cid>/<mid>`` a track of a container: add_to_queue with mid
- ``heos_queue`` the queue; ``heos_queue/<qid>`` an item of it: player/play_queue
"""

from __future__ import annotations

from urllib.parse import quote, unquote

from homeassistant.components.media_player import (
    BrowseError,
    BrowseMedia,
    MediaClass,
    MediaPlayerEnqueue,
    MediaType,
)
from homeassistant.exceptions import HomeAssistantError

from .avr.transport.heos import (
    ADD_TO_END,
    BROWSE_PAGE,
    FAVORITES_SID,
    PLAY_NEXT,
    TUNEIN_SID,
    PLAY_NOW,
    REPLACE_AND_PLAY,
    HeosClient,
    HeosCommandError,
    HeosItem,
)
from .const import DOMAIN

ROOT = "heos"
QUEUE = "heos_queue"
_PAGE = "heos/"
_QUEUE_ITEM = "heos_queue/"
_FAVORITE = "heos_favorite/"
_STATION = "heos_station/"
_CONTAINER = "heos_container/"
_TRACK = "heos_track/"
PREFIXES = (_FAVORITE, _STATION, _CONTAINER, _TRACK, _QUEUE_ITEM)

#: Home Assistant's enqueue choices -> HEOS add criteria (as the HEOS
#: integration maps them: no choice replaces the queue and plays).
_CRITERIA = {
    None: REPLACE_AND_PLAY,
    MediaPlayerEnqueue.ADD: ADD_TO_END,
    MediaPlayerEnqueue.NEXT: PLAY_NEXT,
    MediaPlayerEnqueue.PLAY: PLAY_NOW,
    MediaPlayerEnqueue.REPLACE: REPLACE_AND_PLAY,
}

_CLASSES = {
    "artist": MediaClass.ARTIST,
    "album": MediaClass.ALBUM,
    "song": MediaClass.TRACK,
    "station": MediaClass.CHANNEL,
    "genre": MediaClass.GENRE,
    "playlist": MediaClass.PLAYLIST,
}


def _part(value: str | None) -> str:
    return quote(value or "", safe="")


def _id(prefix: str, *parts: object) -> str:
    return prefix + "/".join(_part(str(part)) for part in parts)


def _parts(media_id: str, prefix: str, count: int) -> list[str]:
    parts = [unquote(part) for part in media_id[len(prefix):].split("/")]
    if len(parts) != count:
        raise ValueError(media_id)
    return parts


def is_heos_id(media_id: str | None) -> bool:
    if not media_id:
        return False
    return media_id in (ROOT, QUEUE) or media_id.startswith((_PAGE, *PREFIXES))


async def async_browse(
    heos: HeosClient, media_id: str, title: str, more: str, queue: str = "Queue"
) -> BrowseMedia:
    """The music sources (``heos``), the queue, or one page of a source or container.

    `title` names the music sources and `queue` the queue; a page carries its
    own name. `more` names the entry of the next page.
    """

    try:
        if media_id == ROOT:
            sources = await heos.async_music_sources()
            children = [
                _source(item) for item in sources if item.available or item.sid == TUNEIN_SID
            ]
            children.append(_directory(QUEUE, queue, [], thumbnail=None, listed=False))
            return _directory(ROOT, title, children, thumbnail=None)
        if media_id == QUEUE:
            queued = await heos.async_get_queue()
            return _directory(QUEUE, queue, [_queue_item(item) for item in queued], thumbnail=None)
        if media_id.startswith(_CONTAINER):
            media_id = page_of(media_id)
        sid_text, cid, start_text, title = _parts(media_id, _PAGE, 4)
        sid, start = int(sid_text), int(start_text)
        items, total = await heos.async_browse(sid, cid or None, start)
    except (HeosCommandError, ConnectionError, TimeoutError, ValueError) as err:
        raise BrowseError(
            translation_domain=DOMAIN,
            translation_key="heos_browse_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    children = [_child(item, sid, cid or None, start + index) for index, item in enumerate(items)]
    if items and (total == 0 or start + len(items) < total):
        # The next page; a total of 0 means the receiver does not know it.
        children.append(
            BrowseMedia(
                media_class=MediaClass.DIRECTORY,
                media_content_id=_id(_PAGE, sid, cid or "", start + BROWSE_PAGE, title),
                media_content_type=MediaType.MUSIC,
                title=more,
                can_play=False,
                can_expand=True,
            )
        )
    return _directory(media_id, title, children, thumbnail=None)


def _directory(
    media_id: str,
    title: str,
    children: list[BrowseMedia],
    thumbnail: str | None,
    listed: bool = True,
) -> BrowseMedia:
    """A folder; `listed` False for an entry in a list (its children load on opening)."""

    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=media_id,
        media_content_type=MediaType.MUSIC,
        title=title,
        can_play=False,
        can_expand=True,
        children=children if listed else None,
        children_media_class=MediaClass.DIRECTORY,
        thumbnail=thumbnail,
    )


def _queue_item(item: dict[str, object]) -> BrowseMedia:
    artist = item.get("artist")
    title = str(item.get("title") or "")
    return BrowseMedia(
        media_class=MediaClass.TRACK,
        media_content_id=_id(_QUEUE_ITEM, item["queue_id"]),
        media_content_type=MediaType.MUSIC,
        title=f"{title} – {artist}" if artist else title,
        can_play=True,
        can_expand=False,
        thumbnail=item.get("image_url"),  # type: ignore[arg-type]
    )


def _source(item: HeosItem) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=_id(_PAGE, item.sid, "", 0, item.name),
        media_content_type=MediaType.MUSIC,
        title=item.name,
        can_play=False,
        can_expand=True,
        thumbnail=item.image_url,
    )


def _child(item: HeosItem, sid: int, parent: str | None, index: int) -> BrowseMedia:
    """A browse entry: a container to open (and play, if playable) or an item."""

    media_class = _CLASSES.get(item.type or "", MediaClass.MUSIC)
    if sid == FAVORITES_SID:
        # Favorites play by their position (play_preset), counted from 1.
        content_id = _id(_FAVORITE, index + 1)
        return _item(content_id, item, MediaClass.CHANNEL, can_play=True, can_expand=False)
    if not item.cid and not item.mid and item.sid != sid:
        # A source within a source: a media server or USB stick under Local
        # Music, an input device under AUX inputs. HEOS lists it with its own
        # sid and no cid (CLI spec 4.4.3); it opens by that sid.
        return _item(_id(_PAGE, item.sid, "", 0, item.name), item, MediaClass.DIRECTORY, False, True)
    if item.container:
        if item.playable:
            return _expandable_playable(item, sid, media_class)
        return _item(_id(_PAGE, sid, item.cid, 0, item.name), item, media_class, False, True)
    if item.type == "station" and item.mid:
        content_id = _id(_STATION, sid, parent or "", item.mid, item.name)
    elif item.mid and parent:
        content_id = _id(_TRACK, sid, parent, item.mid)
    else:
        return _item(_id(_PAGE, sid, parent or "", 0, item.name), item, media_class, False, False)
    return _item(content_id, item, media_class, can_play=item.playable, can_expand=False)


def _expandable_playable(item: HeosItem, sid: int, media_class: MediaClass) -> BrowseMedia:
    """A playable container (an album, a playlist): opens as a page, plays as a whole.

    Home Assistant browses with the same id it plays, so the container id
    serves both: browsing a ``heos_container`` id shows its first page.
    """

    return _item(
        _id(_CONTAINER, sid, item.cid, item.name), item, media_class, can_play=True, can_expand=True
    )


def _item(
    content_id: str, item: HeosItem, media_class: MediaClass, can_play: bool, can_expand: bool
) -> BrowseMedia:
    return BrowseMedia(
        media_class=media_class,
        media_content_id=content_id,
        media_content_type=MediaType.MUSIC,
        title=item.name,
        can_play=can_play,
        can_expand=can_expand,
        thumbnail=item.image_url,
    )


def page_of(media_id: str) -> str:
    """The page id a browse of a playable container id opens."""

    sid, cid, title = _parts(media_id, _CONTAINER, 3)
    return _id(_PAGE, sid, cid, 0, title)


async def async_play(
    heos: HeosClient, media_id: str, enqueue: MediaPlayerEnqueue | None
) -> None:
    """Play a favorite, a station, a container or a track."""

    criteria = _CRITERIA.get(enqueue, REPLACE_AND_PLAY)
    try:
        if media_id.startswith(_QUEUE_ITEM):
            (queue_id,) = _parts(media_id, _QUEUE_ITEM, 1)
            await heos.async_play_queue_item(int(queue_id))
        elif media_id.startswith(_FAVORITE):
            (number,) = _parts(media_id, _FAVORITE, 1)
            await heos.async_play_preset(int(number))
        elif media_id.startswith(_STATION):
            sid, cid, mid, name = _parts(media_id, _STATION, 4)
            await heos.async_play_station(int(sid), cid or None, mid, name)
        elif media_id.startswith(_CONTAINER):
            sid, cid, _title = _parts(media_id, _CONTAINER, 3)
            await heos.async_add_to_queue(int(sid), cid, criteria)
        else:
            sid, cid, mid = _parts(media_id, _TRACK, 3)
            await heos.async_add_to_queue(int(sid), cid, criteria, mid)
    except ValueError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="heos_failed",
            translation_placeholders={"error": f"unknown item {media_id}"},
        ) from err
    except (HeosCommandError, ConnectionError, TimeoutError) as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="heos_failed",
            translation_placeholders={"error": str(err)},
        ) from err
