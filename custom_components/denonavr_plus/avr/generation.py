"""Receiver generations: what the receiver says about itself in Deviceinfo.xml.

A receiver reports its interface level (CommApiVers) and its kind of device
(ProductCategory). The generations of Denon and Marantz AV receivers from
2013 on are described (docs/COMPATIBILITY.md, docs/protocol/01-overview.md):
their encodings are in the protocol profile (avr/profile.py), so a model of
these generations needs no test before it is used. Anything else, a system
or an interface level not seen yet, is not described.

Receivers from 2010 to 2012 have no Deviceinfo.xml at all; they never get
here (errors.UnsupportedReceiver).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import DeviceInfo

#: ProductCategory of an AV receiver; 2 is a system (M-CR510, DRA-N4).
AV_RECEIVER = 1


@dataclass(frozen=True)
class Generation:
    """One generation: its interface level, model years and goform port."""

    key: str
    level: int
    years: str
    port: int


GENERATIONS: tuple[Generation, ...] = (
    # AVR-X2000 (tested), AVR-X4000, AVR-1713, AVR-3313, NR1604, SR5008.
    Generation("2013", 210, "2013", 80),
    # AVR-X1100W.
    Generation("2014", 250, "2014", 80),
    # AVR-X2100W, AVR-X4100W, SR6011: receivers without HEOS.
    Generation("2014_2016", 300, "2014-2016", 80),
    # AVR-X2600H and AVR-X3600H (tested), AVC-X3700H, NR1609, SR6012 …
    Generation("2016", 301, "2016 on", 8080),
)


def generation_of(device: DeviceInfo) -> Generation | None:
    """The described generation of an AV receiver; None for anything else."""

    if device.product_category != AV_RECEIVER:
        return None
    for generation in GENERATIONS:
        if generation.level == device.comm_api_version:
            return generation
    return None
