"""Naming and option helpers shared by the entity platforms.

These functions turn the profile control specs and the device published metadata
into the display names and option lists the entities need. Names always prefer
what the receiver published; a humanised id is only a last resort.
"""

from __future__ import annotations

import re
from typing import Any

from . import choices
from .avr.models import Discovery
from .avr.profile import ControlSpec


def humanize(text: str) -> str:
    """Turn an id such as 'dynamic_eq' into a readable 'Dynamic Eq'."""

    return text.replace("_", " ").replace(":", "").strip().title()


# Protocol command group -> HA sub-device card. Presentation only: this mirrors
# the receiver's own setup menus (Audio / Video / Speakers) so a large control
# set is split across cohesive device pages instead of crowding one device.
# Groups absent here (power, volume, source, system) keep their entities on the
# main receiver device with the daily-use playback controls.
_GROUP_SUB_DEVICE: dict[str, str] = {
    "audio": "audio",
    "audyssey": "audio",
    "tone": "audio",
    "surround": "audio",
    "video": "video",
    "speaker": "speakers",
}

# Controls whose card differs from their command group's default. All Zone Stereo
# is a whole-house playback toggle (its state is derived from the sound mode), so
# it stays with the playback controls on the main device rather than the Audio
# settings card.
_MAIN_DEVICE_CONTROLS = {"all_zone_stereo"}

# Profile controls that start enabled: what one changes while listening (tone,
# dialogue, loudness, sleep timer, display). Every other control is a set-once
# setup value or duplicates the media player (power, mute, speaker and video
# setup, volume limits, sound mode parameters) and starts disabled, ready to be
# enabled on the device page. Registered entities keep the user's choice.
_EVERYDAY_CONTROLS = frozenset(
    {
        "all_zone_stereo",
        "bass",
        "cinema_eq",
        "dialog_control",
        "dialog_level",
        "dimmer",
        "dynamic_compression",
        "dynamic_eq",
        "dynamic_volume",
        "multeq",
        "sleep_timer",
        "subwoofer_level",
        "subwoofer_switch",
        "tone_control",
        "treble",
    }
)


def control_enabled_by_default(spec: ControlSpec) -> bool:
    """Return whether an entity for this profile control starts enabled."""

    return spec.id in _EVERYDAY_CONTROLS


def control_zone_shown(spec: ControlSpec, options: dict[str, Any]) -> bool:
    """Whether a control's zone is shown: zone 2 settings go with zone 2."""

    sub_device = spec.get("subdevice")
    if sub_device in ("zone2", "zone3"):
        return choices.zone_enabled(options, str(sub_device))
    return True


def control_sub_device(spec: ControlSpec) -> str | None:
    """Return the sub-device card an entity for this control belongs on.

    None means the main receiver device. Resolution order: an explicit profile
    ``subdevice`` wins (e.g. the graphic EQ on/off lives with the EQ bands), then
    a per-control exception, then the control's command group.
    """

    explicit = spec.get("subdevice")
    if explicit is not None:
        return str(explicit)
    if spec.id in _MAIN_DEVICE_CONTROLS:
        return None
    return _GROUP_SUB_DEVICE.get(spec.group or "")


def group_name(discovery: Discovery, channels: list[str]) -> str:
    """Return a display name for a speaker group from its channel names.

    A group's crossover applies to a speaker pair (or single). The receiver
    names the individual channels ('Front L', 'Front R'), so derive the group
    label from those: for a pair, drop the trailing side token to get the shared
    base ('Front'); for a single channel, use its name. Prefer the channels the
    receiver actually configured (they carry real names), falling back to the
    protocol channel codes only when none are configured.
    """

    configured = {ch.code for ch in discovery.channels}
    codes = [c for c in channels if c in configured] or channels
    names = [discovery.channel_name(c) for c in codes]
    if not names:
        return channels[0] if channels else ""
    if len(names) == 1:
        return names[0]
    bases = [n.rsplit(" ", 1)[0] for n in names if " " in n]
    if bases and len(set(bases)) == 1:
        return bases[0]
    return names[0]


def option_key(label: str) -> str:
    """The state key of a select option: its English label as a slug.

    Select states are these keys; their names on screen come from the
    translations (entity.select.<translation key>.state.<key>). Home Assistant
    accepts [a-z0-9-_] and no "_" or "-" at either end; a leading minus
    becomes "minus_" so "-20 dB" and "20 dB" stay apart.
    """

    text = label.strip().lower()
    prefix = "minus_" if text.startswith("-") else ""
    slug = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return prefix + slug


def _comparable(label: str) -> str:
    """A label reduced to lower case letters and digits ("+5dB" -> "5db")."""

    return "".join(char for char in label.lower() if char.isalnum())


def enum_options(
    spec: ControlSpec, published: list[str] | None = None
) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """The option keys of an enum control and the maps between key and wire value.

    The wire values and their English labels come from the profile, in the
    receiver's order; the keys are the labels as slugs, translated on screen.
    Without labels for every value the wire tokens stand in.

    `published` are the option names the receiver lists for the control in
    Deviceinfo.xml (`listed_as` gives the receiver's names where they differ
    from the labels). When each of them is one of the profile's, only those
    are offered: an AVR-X2600H has no Manual for MultEq, an AVR-X4300H no
    Bypass for Video Mode. Otherwise the profile's options stay, except the
    `listed_only` values, which only a receiver that lists them gets.
    """

    values = list(spec.values)
    labels = spec.get("labels") or []
    if len(labels) != len(values):
        labels = [humanize(value) for value in values]
    names = [_comparable(name) for name in (spec.get("listed_as") or labels)]
    offered = {_comparable(name) for name in published or ()}
    if offered and len(names) == len(values) and offered <= set(names):
        kept = [i for i, name in enumerate(names) if name in offered]
    else:
        only_listed = set(spec.get("listed_only") or ())
        kept = [i for i, value in enumerate(values) if value not in only_listed]
    values = [values[i] for i in kept]
    labels = [labels[i] for i in kept]
    keys = [option_key(label) for label in labels]
    return keys, dict(zip(keys, values)), dict(zip(values, keys))
