"""Which sources, sound modes and zones Home Assistant shows, and their names.

The receiver reports its sources (with the names and hidden flags set on the
receiver) and its sound modes. The options narrow that down for Home
Assistant and rename entries there, without changing anything on the
receiver:

- sources per zone: which appear in the media player's list (absent or empty
  means every source the receiver shows);
- source names: a name per source code, used everywhere in Home Assistant;
- sound modes: which appear in the main zone's list, and their names;
- zones: which additional zones get entities (absent means all).

The current source or sound mode is always shown, also when it is not in the
selection, so the media player never shows nothing while something plays.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .avr.models import Discovery

OPT_SOURCES = "sources"
OPT_SOURCE_NAMES = "source_names"
OPT_SOUND_MODES = "sound_modes"
OPT_SOUND_MODE_NAMES = "sound_mode_names"
OPT_ZONES = "zones"
#: Quick selects and HEOS favorites follow the inputs in the main zone's
#: source menu (default on).
OPT_SOURCE_SHORTCUTS = "source_shortcuts"
#: Turning the main zone off puts the whole receiver in standby (PWSTANDBY),
#: so no other zone keeps playing.
OPT_MAIN_OFF_STANDBY = "main_off_standby"
#: A main-zone setting while the receiver is in standby turns the main zone
#: on first (default off): the receiver takes no setting in standby.
OPT_SETTINGS_TURN_ON = "settings_turn_on"

MAIN_ZONE = "main"


def _mapping(options: dict[str, Any], key: str) -> dict[str, str]:
    value = options.get(key)
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


# Zones -------------------------------------------------------------------


def zone_enabled(options: dict[str, Any], zone_id: str) -> bool:
    """The main zone always; another zone unless the options leave it out."""

    if zone_id == MAIN_ZONE:
        return True
    zones = options.get(OPT_ZONES)
    return zones is None or zone_id in zones


# Sources -----------------------------------------------------------------


def source_display(discovery: Discovery, options: dict[str, Any], code: str) -> str:
    """The name Home Assistant shows for a source code."""

    custom = _mapping(options, OPT_SOURCE_NAMES).get(code)
    return custom or discovery.source_name(code) or code


def source_codes(discovery: Discovery, options: dict[str, Any], zone_id: str) -> list[str]:
    """The source codes a zone's list offers, in the receiver's order."""

    visible = [source.code for source in discovery.visible_sources()]
    selection = options.get(OPT_SOURCES)
    chosen = selection.get(zone_id) if isinstance(selection, dict) else None
    if not chosen:
        return visible
    return [code for code in visible if code in chosen]


def source_list(discovery: Discovery, options: dict[str, Any], zone_id: str) -> list[str]:
    return [source_display(discovery, options, c) for c in source_codes(discovery, options, zone_id)]


def normalized(text: str) -> str:
    """A name compared without regard to case or repeated spaces."""

    return " ".join(text.split()).casefold()


def source_code_for(discovery: Discovery, options: dict[str, Any], display: str) -> str | None:
    """The source code behind a name: shown, the receiver's own, or the code.

    Case and spaces do not matter, so "apple tv" finds "Apple TV".
    """

    key = normalized(display)
    for source in discovery.sources:
        names = (source_display(discovery, options, source.code), source.name, source.code)
        if any(name and normalized(name) == key for name in names):
            return source.code
    return None


# Sound modes ---------------------------------------------------------------


#: Turns a receiver's sound mode name into the one shown (sound_modes.py).
Readable = Callable[[str], str]


def sound_mode_names(
    options: dict[str, Any], modes: list[str], readable: Readable | None = None
) -> dict[str, str]:
    """The name shown for each mode: renamed in the options, else readable.

    Two modes whose readable names would be the same keep the receiver's
    names, so every name in a list chooses one mode.
    """

    renamed = _mapping(options, OPT_SOUND_MODE_NAMES)
    shown = {mode: renamed.get(mode) or (readable(mode) if readable else mode) for mode in modes}
    counts: dict[str, int] = {}
    for name in shown.values():
        counts[name] = counts.get(name, 0) + 1
    return {
        mode: name if counts[name] == 1 or mode in renamed else mode
        for mode, name in shown.items()
    }


def shown_sound_modes(
    options: dict[str, Any], modes: list[str], current: str | None, readable: Readable | None = None
) -> dict[str, str]:
    """The modes the main zone's list offers, with their names; current one kept."""

    chosen = options.get(OPT_SOUND_MODES)
    if chosen:
        modes = [mode for mode in modes if mode in chosen]
    if current and current not in modes:
        modes = [*modes, current]
    return sound_mode_names(options, list(dict.fromkeys(modes)), readable)


def sound_mode_list(
    options: dict[str, Any], modes: list[str], current: str | None, readable: Readable | None = None
) -> list[str]:
    """The names of the sound modes the main zone's list offers."""

    return list(shown_sound_modes(options, modes, current, readable).values())


def sound_mode_match(
    options: dict[str, Any], value: str, known: list[str], readable: Readable | None = None
) -> str | None:
    """The sound mode a name stands for: renamed, readable or known, in any case."""

    key = normalized(value)
    for mode, name in _mapping(options, OPT_SOUND_MODE_NAMES).items():
        if normalized(name) == key:
            return mode
    found = next((mode for mode in known if normalized(mode) == key), None)
    if found is None and readable is not None:
        for mode, name in sound_mode_names(options, list(dict.fromkeys(known)), readable).items():
            if normalized(name) == key:
                return mode
    return found