"""Volume options and scales, shared by the media players and the dB numbers.

The receiver counts volume on a raw scale (0 to about 98, 0.5 steps) with
0 dB at the reference (80). Home Assistant's media player wants 0 to 1.
Two mappings are offered:

- by default 0..1 spans the reachable range, from minimum to the receiver's
  maximum (MVMAX), so the slider uses its whole length;
- with the option "volume scale of the official integration", 1 is +20 dB
  and 0 is −80 dB, as the official Denon AVR integration computes it
  ((dB + 80) / 100). `volume_set` values in automations then sound the same
  after moving over.

Per zone the options can set a maximum (Home Assistant never sets the volume
above it) and a turn-on volume (applied when Home Assistant switches the zone
on).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .avr import DenonAvrDevice

OPT_OFFICIAL_SCALE = "official_volume_scale"
OPT_MAX_VOLUME = "max_volume"
OPT_TURN_ON_VOLUME = "turn_on_volume"

#: The official integration's mapping: level = (dB + 80) / 100.
_OFFICIAL_OFFSET_DB = 80.0
_OFFICIAL_SPAN_DB = 100.0


@dataclass(frozen=True)
class ZoneVolumeOptions:
    """The volume options of one zone, in dB relative to the reference."""

    max_db: float | None = None
    turn_on_db: float | None = None


def zone_options(options: dict[str, Any], zone_id: str) -> ZoneVolumeOptions:
    section = options.get(zone_id)
    if not isinstance(section, dict):
        return ZoneVolumeOptions()
    max_db = section.get(OPT_MAX_VOLUME)
    turn_on_db = section.get(OPT_TURN_ON_VOLUME)
    return ZoneVolumeOptions(
        max_db=float(max_db) if max_db is not None else None,
        turn_on_db=float(turn_on_db) if turn_on_db is not None else None,
    )


def uses_official_scale(options: dict[str, Any]) -> bool:
    return bool(options.get(OPT_OFFICIAL_SCALE, False))


def ceiling_raw(device: DenonAvrDevice, zone_id: str, options: dict[str, Any]) -> float:
    """The highest raw volume Home Assistant may set for a zone."""

    ceiling = device.volume_effective_max(zone_id)
    max_db = zone_options(options, zone_id).max_db
    if max_db is not None:
        ceiling = min(ceiling, device.volume_reference + max_db)
    return max(0.0, ceiling)


def level_from_raw(
    device: DenonAvrDevice, zone_id: str, raw: float, options: dict[str, Any]
) -> float | None:
    """The 0..1 media player level for a raw volume."""

    if uses_official_scale(options):
        level = (raw - device.volume_reference + _OFFICIAL_OFFSET_DB) / _OFFICIAL_SPAN_DB
    else:
        top = device.volume_effective_max(zone_id)
        if not top:
            return None
        level = raw / top
    return max(0.0, min(1.0, level))


def raw_from_level(
    device: DenonAvrDevice, zone_id: str, level: float, options: dict[str, Any]
) -> float:
    """The raw volume for a 0..1 media player level, within the zone's limit."""

    if uses_official_scale(options):
        raw = level * _OFFICIAL_SPAN_DB - _OFFICIAL_OFFSET_DB + device.volume_reference
    else:
        raw = level * device.volume_effective_max(zone_id)
    return clamp_raw(device, zone_id, raw, options)


def clamp_raw(
    device: DenonAvrDevice, zone_id: str, raw: float, options: dict[str, Any]
) -> float:
    """A raw volume kept between the minimum and the zone's limit, on 0.5 steps."""

    step = device.volume_step or 0.5
    raw = round(raw / step) * step
    return max(0.0, min(raw, ceiling_raw(device, zone_id, options)))
