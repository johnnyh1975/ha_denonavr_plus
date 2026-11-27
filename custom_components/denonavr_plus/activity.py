"""What a zone is playing, in one word: film, TV, music, radio, game.

For automations outside the receiver: dim the light for a film, keep the
heating up in the room that is in use, do not disturb during a game. Derived
from what is already known, in this order:

1. The zone is off, or an announcement is playing on it.
2. The player linked to the zone's input plays or pauses something whose
   media type says what it is (a film on the Apple TV, an episode, music).
3. The input itself: the tuner is radio, the network input and its services
   are music (a HEOS station radio), a game console's input is a game, the TV
   and cable inputs are TV, the disc players are film. These are the
   receiver's input codes, so a renamed input keeps its meaning.
4. Otherwise just on: an input the receiver has no idea about (Media Player,
   AUX) without a linked player that says more.
"""

from __future__ import annotations

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import State

OFF = "off"
ANNOUNCEMENT = "announcement"
MUSIC = "music"
RADIO = "radio"
FILM = "film"
TV = "tv"
GAME = "game"
ON = "on"

#: The states the sensor can have, in the order the options list shows them.
ACTIVITIES = [OFF, ANNOUNCEMENT, MUSIC, RADIO, FILM, TV, GAME, ON]

#: A linked player's media content type (Home Assistant's MediaType values and
#: what common integrations report) to what is playing.
_MEDIA_TYPES = {
    "music": MUSIC,
    "track": MUSIC,
    "album": MUSIC,
    "artist": MUSIC,
    "playlist": MUSIC,
    "podcast": MUSIC,
    "radio": RADIO,
    "movie": FILM,
    "video": FILM,
    "tvshow": TV,
    "episode": TV,
    "season": TV,
    "channel": TV,
    "game": GAME,
}

#: The receiver's input codes (SI) that say what they carry.
_INPUTS = {
    "TUNER": RADIO,
    "HDRADIO": RADIO,
    "DAB": RADIO,
    "IRADIO": RADIO,
    "SIRIUSXM": RADIO,
    "NET": MUSIC,
    "NETWORK": MUSIC,
    "FAVORITES": MUSIC,
    "SERVER": MUSIC,
    "USB/IPOD": MUSIC,
    "USB": MUSIC,
    "IPD": MUSIC,
    "SPOTIFY": MUSIC,
    "LASTFM": MUSIC,
    "PANDORA": MUSIC,
    "NAPSTER": MUSIC,
    "BT": MUSIC,
    "CD": MUSIC,
    "PHONO": MUSIC,
    "GAME": GAME,
    "GAME2": GAME,
    "TV": TV,
    "SAT/CBL": TV,
    "SAT": TV,
    "CBL": TV,
    "BD": FILM,
    "DVD": FILM,
}

#: The inputs that play from the network, where HEOS tells a station from a song.
_NETWORK = frozenset({"NET", "NETWORK", "FAVORITES", "SERVER", "USB/IPOD", "SPOTIFY", "PANDORA"})

#: Linked player states that say what the zone plays.
_ACTIVE = (MediaPlayerState.PLAYING, MediaPlayerState.BUFFERING, MediaPlayerState.PAUSED)


def zone_activity(
    *,
    power: bool | None,
    announcing: bool,
    source: str | None,
    linked: State | None,
    network_media_type: str | None,
) -> str | None:
    """What the zone plays; None while its power is not known yet.

    network_media_type is HEOS's (or the on-screen list's) 'song' or
    'station' for what the network input plays, when it plays.
    """

    if power is None:
        return None
    if not power:
        return OFF
    if announcing:
        return ANNOUNCEMENT
    if linked is not None and linked.state in _ACTIVE:
        kind = _MEDIA_TYPES.get(str(linked.attributes.get("media_content_type") or "").lower())
        if kind is not None:
            return kind
    if source is None:
        return ON
    code = source.upper()
    if code in _NETWORK and network_media_type == "station":
        return RADIO
    return _INPUTS.get(code) or ON
