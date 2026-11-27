"""Speech clarity: dialogue easier to follow with one switch.

On, it raises what the receiver offers for speech and remembers what it
changed: the Dialogue Enhancer at medium where the receiver has one, else the
center level by a few dB; and Dynamic Volume at medium where it was off, so
quiet speech and loud effects come closer. Off puts back what it changed and
nothing else. Like the night modes of soundbars (Sonos Speech Enhancement),
made from the receiver's own settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .avr.device import DenonAvrDevice

DIALOG_ENHANCER = "dialog_enhancer"
CENTER_LEVEL = "dialog_level"
DYNAMIC_VOLUME = "dynamic_volume"
#: What speech clarity sets.
ENHANCER_ON = "MED"
DYNAMIC_VOLUME_ON = "MED"
#: How much the center level is raised where there is no Dialogue Enhancer.
CENTER_RAISE_DB = 3.0
CENTER_MAX_DB = 12.0


def controls(device: DenonAvrDevice) -> list[str]:
    """The settings speech clarity can use on this receiver."""

    discovery = device.discovery
    found = []
    for control_id in (DIALOG_ENHANCER, CENTER_LEVEL, DYNAMIC_VOLUME):
        spec = device.profile.control(control_id)
        if spec is not None and discovery.has_control(spec.scope, spec.feature, spec.id):
            found.append(control_id)
    return found


def targets(device: DenonAvrDevice, before: dict[str, Any] | None = None) -> dict[str, Any]:
    """What to set for speech clarity, from the current values.

    The center is raised from its value before speech clarity, where that is
    known, so switching it on again does not raise it twice.
    """

    values = device.state.values
    available = controls(device)
    wanted: dict[str, Any] = {}
    if DIALOG_ENHANCER in available:
        wanted[DIALOG_ENHANCER] = ENHANCER_ON
    elif CENTER_LEVEL in available and isinstance(
        center := (before or {}).get(CENTER_LEVEL, values.get(CENTER_LEVEL)), (int, float)
    ):
        wanted[CENTER_LEVEL] = min(float(center) + CENTER_RAISE_DB, CENTER_MAX_DB)
    if DYNAMIC_VOLUME in available and values.get(DYNAMIC_VOLUME) == "OFF":
        wanted[DYNAMIC_VOLUME] = DYNAMIC_VOLUME_ON
    return {key: value for key, value in wanted.items() if values.get(key) != value}


async def async_turn_on(device: DenonAvrDevice, before: dict[str, Any]) -> None:
    """Raise speech; notes in before what it changes, to put back.

    Noted before each change, so what was changed before a refusal is put
    back too.
    """

    for control_id, value in targets(device, before).items():
        # The first value seen stays: again after a refusal keeps it.
        before.setdefault(control_id, device.state.values.get(control_id))
        await device.async_set_control(control_id, value)


async def async_turn_off(device: DenonAvrDevice, before: dict[str, Any]) -> None:
    """Put back what speech clarity changed; takes out of before what is back.

    What could not be put back (a refusal) stays in before, for another try.
    """

    for control_id, value in list(before.items()):
        if value is not None:
            await device.async_set_control(control_id, value)
        del before[control_id]
