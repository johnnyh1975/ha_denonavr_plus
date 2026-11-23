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

from typing import Any

from .avr.models import Discovery

OPT_SOURCES = "sources"
OPT_SOURCE_NAMES = "source_names"
OPT_SOUND_MODES = "sound_modes"
OPT_SOUND_MODE_NAMES = "sound_mode_names"
OPT_ZONES = "zones"

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


def source_code_for(discovery: Discovery, options: dict[str, Any], display: str) -> str | None:
    """The source code behind a name Home Assistant showed."""

    for source in discovery.sources:
        if source_display(discovery, options, source.code) == display:
            return source.code
    return discovery.source_code(display)


# Sound modes ---------------------------------------------------------------


def sound_mode_display(options: dict[str, Any], mode: str) -> str:
    return _mapping(options, OPT_SOUND_MODE_NAMES).get(mode) or mode


def sound_mode_list(
    options: dict[str, Any], modes: list[str], current: str | None
) -> list[str]:
    """The sound modes the main zone's list offers, renamed, current one kept."""

    chosen = options.get(OPT_SOUND_MODES)
    if chosen:
        modes = [mode for mode in modes if mode in chosen]
    if current and current not in modes:
        modes = [*modes, current]
    return [sound_mode_display(options, mode) for mode in modes]


def sound_mode_for(options: dict[str, Any], display: str) -> str:
    """The receiver's sound mode behind a name Home Assistant showed."""

    for mode, name in _mapping(options, OPT_SOUND_MODE_NAMES).items():
        if name == display:
            return mode
    return display
