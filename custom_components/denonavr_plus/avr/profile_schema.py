"""The shape of protocol_profile.json, checked when it is loaded.

Every section, every key and every value type the profile may use is written
down here; anything else is an error. A misspelt key would otherwise read as
"this receiver has no such function" and pass every test that does not walk
that path. `doc` (a string or a list of strings) is allowed everywhere.

The variants ("older", "2013", "before_x", "2011", "marantz") are partial
overrides inside a control, the network audio grammar or an introspection
item; they are checked here too, and once more after they are applied
(profile.load_profile).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Spec:
    """A value's allowed shape."""

    def check(self, value: Any, path: str, problems: list[str]) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class Scalar(Spec):
    types: tuple[type, ...]
    name: str

    def check(self, value: Any, path: str, problems: list[str]) -> None:
        # bool is an int in Python; a number must not be a bool.
        if isinstance(value, bool) and bool not in self.types:
            problems.append(f"{path}: expected {self.name}, got bool")
        elif not isinstance(value, self.types):
            problems.append(f"{path}: expected {self.name}, got {type(value).__name__}")


STR = Scalar((str,), "a string")
INT = Scalar((int,), "an integer")
NUMBER = Scalar((int, float), "a number")
BOOL = Scalar((bool,), "true or false")


@dataclass(frozen=True)
class ListOf(Spec):
    item: Spec

    def check(self, value: Any, path: str, problems: list[str]) -> None:
        if not isinstance(value, list):
            problems.append(f"{path}: expected a list, got {type(value).__name__}")
            return
        for index, item in enumerate(value):
            self.item.check(item, f"{path}[{index}]", problems)


@dataclass(frozen=True)
class OneOf(Spec):
    options: tuple[Spec, ...]

    def check(self, value: Any, path: str, problems: list[str]) -> None:
        for option in self.options:
            found: list[str] = []
            option.check(value, path, found)
            if not found:
                return
        problems.append(f"{path}: none of the allowed shapes fits")


DOC = OneOf((STR, ListOf(STR)))
STRINGS = ListOf(STR)


@dataclass(frozen=True)
class MapOf(Spec):
    """A dict whose keys are data (codes, names), each value of one shape."""

    value: Spec

    def check(self, value: Any, path: str, problems: list[str]) -> None:
        if not isinstance(value, dict):
            problems.append(f"{path}: expected an object, got {type(value).__name__}")
            return
        for key, item in value.items():
            if key == "doc":
                DOC.check(item, f"{path}.doc", problems)
            else:
                self.value.check(item, f"{path}.{key}", problems)


@dataclass(frozen=True)
class Struct(Spec):
    """A dict with known keys; any other key is an error."""

    keys: dict[str, Spec]
    required: frozenset[str] = field(default_factory=frozenset)

    def check(self, value: Any, path: str, problems: list[str]) -> None:
        if not isinstance(value, dict):
            problems.append(f"{path}: expected an object, got {type(value).__name__}")
            return
        for key in sorted(self.required - value.keys()):
            problems.append(f"{path}: missing {key}")
        for key, item in value.items():
            if key == "doc":
                DOC.check(item, f"{path}.doc", problems)
            elif key in self.keys:
                self.keys[key].check(item, f"{path}.{key}", problems)
            else:
                problems.append(f"{path}: unknown key {key!r}")


#: The variants a control, the network audio grammar or an introspection item
#: may override (profile.profile_variant).
VARIANTS = ("older", "2013", "before_x", "2011", "marantz")

_CONTROL_KEYS: dict[str, Spec] = {
    "kind": STR,
    "scope": STR,
    "group": STR,
    "zone": STR,
    "feature": STR,
    "subdevice": STR,
    "prefix": STR,
    "query": STR,
    "set_prefix": STR,
    "read_query": STR,
    "list_query": STR,
    "values": STRINGS,
    "labels": STRINGS,
    "on": STR,
    "off": STR,
    "up": STR,
    "down": STR,
    "min": NUMBER,
    "max": NUMBER,
    "step": NUMBER,
    "center": INT,
    "width": INT,
    "unit": STR,
    "count_from": STR,
    "exact_onoff": BOOL,
    "exact_enum": BOOL,
    "range_from": OneOf((STR, STRINGS)),
    "listed_only": STRINGS,
    "listed_as": STRINGS,
    "published_from": STRINGS,
    "variants": ListOf(Struct({"prefix": STR, "query": STR}, frozenset({"prefix", "query"}))),
}
CONTROL = Struct(
    {**_CONTROL_KEYS, **{variant: Struct(_CONTROL_KEYS) for variant in VARIANTS}},
    frozenset({"kind", "scope", "group"}),
)

_TUNER_PRESETS = Struct(
    {"count": INT, "format": STR, "prefix": STR, "up": STR, "down": STR},
)
_NETAUDIO_KEYS: dict[str, Spec] = {
    "sources": STRINGS,
    "query": STR,
    "now_playing": STR,
    "lines": MapOf(STR),
    "position_line": STR,
    "position_doc": STR,
    "transport": MapOf(STR),
    "art_path": STR,
    "tuner_presets": _TUNER_PRESETS,
    "tuner_queries": STRINGS,
    "tuner_sources": STRINGS,
}
NETAUDIO = Struct(
    {**_NETAUDIO_KEYS, **{variant: Struct(_NETAUDIO_KEYS) for variant in VARIANTS}}
)

_INTROSPECTION_KEYS: dict[str, Spec] = {
    "query": STR,
    "queries": STRINGS,
    "prefix": STR,
    "set_prefix": STR,
    "all_group": STR,
    "hidden_token": STR,
    "visible_token": STR,
}
INTROSPECTION_ITEM = Struct(
    {**_INTROSPECTION_KEYS, **{variant: Struct(_INTROSPECTION_KEYS) for variant in VARIANTS}}
)

_SETUP_COLLECTION = Struct(
    {
        "id": STR,
        "type": INT,
        "root": STR,
        "item": STR,
        "key_attr": STR,
        "value_field": STR,
        "write_field": STR,
        "name_field": STR,
        "entities": BOOL,
        "availability_from": STR,
        "options_from": STR,
        "options": STRINGS,
        "option_keys": MapOf(STR),
        "conditional_options": MapOf(OneOf((STR, STRINGS, MapOf(STR)))),
        "position_keys": MapOf(STR),
        "value_keys": MapOf(STR),
    },
    frozenset({"id", "type", "root", "item"}),
)
_SETUP_CONTROL = Struct(
    {
        "id": STR,
        "kind": STR,
        "type": INT,
        "tag": STR,
        "field": STR,
        "list": STR,
        "dynamic": BOOL,
        "on": STR,
        "off": STR,
        "options": MapOf(STR),
        "value_list": BOOL,
        "amptype_tag": STR,
        "amptype_options": MapOf(ListOf(STRINGS)),
    },
    frozenset({"id", "kind"}),
)
_SETUP_PAGES: dict[str, Spec] = {
    "config_section": STR,
    "availability_type": INT,
    "write_settle_seconds": NUMBER,
    "wrap_root": BOOL,
    "collections": ListOf(_SETUP_COLLECTION),
    "controls": ListOf(_SETUP_CONTROL),
    "menus": MapOf(STR),
}

GRAMMAR = Struct(
    {
        "line_terminator": STR,
        "list_terminator": STR,
        "no_signal_token": STR,
        "volume_reference": NUMBER,
        "level_reference": NUMBER,
        "volume_max_fallback": NUMBER,
        "information": Struct({"section": STR, "type": INT, "dts_path": STR}),
        "hdmi_setup": Struct({"query": STR, "prefix": STR, "items": MapOf(STR)}),
        "audio_menu_status": Struct(
            {"query": STR, "prefix": STR, "items": STRINGS, "controls": MapOf(STRINGS)}
        ),
        "distance": Struct(
            {"divisor": INT, "min": NUMBER, "max": NUMBER, "step": NUMBER, "unit": STR}
        ),
        "crossover": Struct(
            {"speaker_selection": MapOf(STR), "width": INT, "unit": STR, "values": ListOf(INT)}
        ),
        "graphic_eq": Struct(
            {
                "config_section": STR,
                "config_type": INT,
                "root_tag": STR,
                "band_tag_prefix": STR,
                "db_divisor": INT,
                "bands": STRINGS,
                "min_db": NUMBER,
                "max_db": NUMBER,
                "step_db": NUMBER,
                "speaker_selection": MapOf(STR),
                "channel_read_param": STR,
                "channel_enable": STR,
                "channels_lr": STRINGS,
                "channels_each": STRINGS,
            }
        ),
        "video_web": Struct(
            {
                **_SETUP_PAGES,
                "features": MapOf(STR),
                "pass_through_values": MapOf(STR),
            }
        ),
        "speakers_web": Struct(
            {
                **_SETUP_PAGES,
                "layout_settle_seconds": NUMBER,
                "restore_timeout": NUMBER,
                "layout_attempts": INT,
                "amp_modes": MapOf(STR),
            }
        ),
    }
)

_COUNT_GROUP = Struct({"single": STRINGS, "double": STRINGS, "count_tokens": STRINGS})
SPEAKERS = Struct(
    {
        "absent_token": STR,
        "absent_tokens": STRINGS,
        "double_token": STR,
        "subwoofer_count": MapOf(STR),
        "subwoofer_presence": MapOf(STR),
        "sizes": Struct({"options": MapOf(STR)}),
        "groups": MapOf(STRINGS),
        "count_groups": MapOf(_COUNT_GROUP),
    }
)

ZONES = Struct(
    {
        "main_zone_id": STR,
        "additional_prefix": STR,
        "power_on": STR,
        "power_off": STR,
        "mute_token": STR,
        "mute_on": STR,
        "mute_off": STR,
        "status_lite_main": STR,
        "status_lite_additional": STR,
    }
)

REFRESH_GROUP = Struct(
    {
        "queries": STRINGS,
        "on": STRINGS,
        "on_change": STR,
        "debounce": NUMBER,
        "setup_interface": BOOL,
    },
    frozenset({"queries"}),
)

READONLY_ITEM = Struct(
    {"kind": STR, "prefix": STR, "query": STR},
    frozenset({"kind", "prefix", "query"}),
)

PROFILE = Struct(
    {
        "profile_version": INT,
        "sound_mode_wire_overrides": MapOf(STR),
        "sound_mode_genre_commands": MapOf(STR),
        "listed_sound_modes": MapOf(STR),
        "source_names": MapOf(STR),
        "netaudio": NETAUDIO,
        "grammar": GRAMMAR,
        "speakers": SPEAKERS,
        "zones": ZONES,
        "introspection": MapOf(INTROSPECTION_ITEM),
        "receiver_type": Struct(
            {
                "commapi_pattern": STR,
                "avr_x_pattern": STR,
                "avr_x_type": STR,
                "avr_x_port80_type": STR,
                "default_type": STR,
            }
        ),
        "readonly": MapOf(READONLY_ITEM),
        "refresh": Struct({"groups": MapOf(REFRESH_GROUP)}),
        "buttons": MapOf(STR),
        "remote": Struct({"commands": MapOf(STR)}),
        "controls": MapOf(CONTROL),
    },
    frozenset({"profile_version", "grammar", "controls"}),
)


def problems(raw: Any) -> list[str]:
    """Everything in a profile that does not fit its shape (empty: it fits)."""

    found: list[str] = []
    PROFILE.check(raw, "profile", found)
    return found
