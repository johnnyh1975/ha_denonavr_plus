"""Loader and typed accessor for the external protocol profile.

The profile (protocol_profile.json) is the single place that holds the fixed
Denon telnet wire grammar. This module loads it once and exposes convenient
lookups. It contains engine logic only, never device configuration.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

# The profile file lives next to this module inside the avr package.
_PROFILE_PATH = Path(__file__).parent / "protocol_profile.json"


class ControlSpec:
    """Typed view over a single control entry from the profile."""

    def __init__(self, control_id: str, data: dict[str, Any]) -> None:
        self.id = control_id
        self._data = data

    @property
    def kind(self) -> str:
        """The value kind that drives encoding/decoding (power, onoff, ...)."""

        return str(self._data["kind"])

    @property
    def scope(self) -> str:
        """One of 'core', 'zone2' or 'feature'."""

        return str(self._data.get("scope", "feature"))

    @property
    def group(self) -> str | None:
        """The command family this control belongs to.

        One of the profile's command groups (power, volume, source, surround,
        audyssey, tone, audio, speaker, video, system). Purely a classification
        for organising the growing control set; it does not affect the wire
        protocol.
        """

        return self._data.get("group")

    @property
    def zone(self) -> str | None:
        """The zone this control belongs to, if any."""

        return self._data.get("zone")

    @property
    def feature(self) -> str | None:
        """The receiver FuncName that gates a feature control, if any."""

        return self._data.get("feature")

    @property
    def prefix(self) -> str:
        """The telnet token that prefixes both queries and set commands."""

        return str(self._data.get("prefix", ""))

    @property
    def query(self) -> str | None:
        """The full query string that asks the receiver for the current value."""

        return self._data.get("query")

    @property
    def values(self) -> list[str]:
        """The ordered wire values for an enum control (empty otherwise)."""

        return list(self._data.get("values", []))

    def get(self, key: str, default: Any = None) -> Any:
        """Return a raw profile attribute (for kind specific extras)."""

        return self._data.get(key, default)


class ProtocolProfile:
    """The parsed protocol profile with grammar, controls and introspection."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self.variant: str | None = None
        # Every variant applied, in order (e.g. ("older", "marantz")).
        self.variants: tuple[str, ...] = ()
        self.grammar: dict[str, Any] = raw.get("grammar", {})
        self.zones: dict[str, Any] = raw.get("zones", {})
        self.speakers: dict[str, Any] = raw.get("speakers", {})
        # Command buttons: name -> telnet command ("doc" left out).
        self.button_commands: dict[str, str] = {
            name: command for name, command in raw.get("buttons", {}).items() if name != "doc"
        }
        # Remote entity: command name -> telnet command ("doc" left out).
        self.remote_commands: dict[str, str] = dict(raw.get("remote", {}).get("commands", {}))
        self.sound_mode_genre_commands: dict[str, str] = {
            code: value
            for code, value in raw.get("sound_mode_genre_commands", {}).items()
            if code != "doc"
        }
        self.sound_mode_wire_overrides: dict[str, str] = {
            name: value
            for name, value in raw.get("sound_mode_wire_overrides", {}).items()
            if name != "doc"
        }
        # Function name in Deviceinfo -> MS wire token, for receivers that do
        # not list their sound modes over telnet.
        self.listed_sound_modes: dict[str, str] = {
            name: value
            for name, value in raw.get("listed_sound_modes", {}).items()
            if name != "doc"
        }
        self.introspection: dict[str, dict[str, Any]] = raw.get("introspection", {})
        # Now playing and transport over telnet for receivers without HEOS.
        self.netaudio: dict[str, Any] = raw.get("netaudio", {})
        self.readonly: dict[str, dict[str, Any]] = raw.get("readonly", {})
        self.refresh: dict[str, Any] = raw.get("refresh", {})
        # Receiver-type classification (device hw_version), by CommApiVers /
        # ModelName patterns, matching the official denonavr library. Not a Gen
        # code map; see the section's own doc.
        self.receiver_type: dict[str, str] = raw.get("receiver_type", {})
        self.controls: dict[str, ControlSpec] = {
            control_id: ControlSpec(control_id, data)
            for control_id, data in raw.get("controls", {}).items()
        }

    # Grammar helpers. These read the numeric protocol facts from the profile so
    # that even the reference points are data driven.
    @property
    def line_terminator(self) -> str:
        return str(self.grammar.get("line_terminator", "\r"))

    @property
    def list_terminator(self) -> str:
        return str(self.grammar.get("list_terminator", "END"))

    @property
    def no_signal_token(self) -> str:
        return str(self.grammar.get("no_signal_token", "NON"))

    @property
    def volume_reference(self) -> float:
        return float(self.grammar.get("volume_reference", 80.0))

    @property
    def level_reference(self) -> float:
        return float(self.grammar.get("level_reference", 50.0))

    @property
    def volume_max_fallback(self) -> float:
        return float(self.grammar.get("volume_max_fallback", 98.0))

    @property
    def distance(self) -> dict[str, Any]:
        """Speaker distance grammar (divisor, min, max, step, unit)."""

        return cast(dict[str, Any], self.grammar.get("distance", {}))

    @property
    def crossover(self) -> dict[str, Any]:
        """Speaker crossover grammar (width, unit, discrete Hz value set)."""

        return cast(dict[str, Any], self.grammar.get("crossover", {}))

    def group_channels(self, group: str) -> list[str]:
        """Return the channel codes that make up a speaker group.

        Groups map to a fixed pair/single of channels (protocol grammar). The
        count groups (subwoofer, surround back) can be single or double; both
        possible members are returned so the group can be named/matched whether
        the receiver configured it as one speaker or two.
        """

        groups = self.speakers.get("groups", {})
        if group in groups:
            return list(groups[group])
        count = self.speakers.get("count_groups", {})
        if group in count:
            members = list(count[group].get("single", []))
            for code in count[group].get("double", []):
                if code not in members:
                    members.append(code)
            return members
        return []

    @property
    def refresh_groups(self) -> dict[str, dict[str, Any]]:
        """Coalesced re-query groups keyed by id.

        Each group has 'queries' (commands to re-send), 'on' (line prefixes
        whose arrival schedules the re-query) and 'debounce' (seconds). This
        keeps derived state the receiver does not push on every relevant change
        (audio format, channel/speaker layout, signal-dependent mode lists) in
        sync, fully data driven from the profile's refresh section.
        """

        return cast(dict[str, dict[str, Any]], self.refresh.get("groups", {}))

    def introspection_query(self, key: str) -> str | None:
        """Return the query string for an introspection item, or None."""

        return self.introspection.get(key, {}).get("query")

    def control(self, control_id: str) -> ControlSpec | None:
        """Return a control spec by id, or None when unknown."""

        return self.controls.get(control_id)

    def controls_by_scope(self, scope: str) -> list[ControlSpec]:
        """Return all control specs with the given scope."""

        return [spec for spec in self.controls.values() if spec.scope == scope]

    def controls_by_group(self, group: str) -> list[ControlSpec]:
        """Return all control specs classified under the given command group."""

        return [spec for spec in self.controls.values() if spec.group == group]

    def feature_controls(self) -> list[ControlSpec]:
        """Return all control specs that are gated by a receiver feature."""

        return [spec for spec in self.controls.values() if spec.feature]


def profile_variant(profile: ProtocolProfile, variant: str) -> ProtocolProfile:
    """The profile with its `variant` entries (e.g. "older") applied.

    Those of each control and of the network audio grammar. A copy: the cached profile stays as it is for other receivers.
    """

    raw = copy.deepcopy(profile._raw)  # noqa: SLF001 - the profile's own data
    for control in raw.get("controls", {}).values():
        override = control.pop(variant, None)
        if isinstance(override, dict):
            override.pop("doc", None)
            control.update(override)
    netaudio = raw.get("netaudio", {})
    override = netaudio.pop(variant, None)
    if isinstance(override, dict):
        netaudio.update(override)
    for entry in raw.get("introspection", {}).values():
        override = entry.pop(variant, None) if isinstance(entry, dict) else None
        if isinstance(override, dict):
            override.pop("doc", None)
            entry.update(override)
    result = ProtocolProfile(raw)
    # Applied on top of another variant, the profile keeps naming the first.
    result.variant = profile.variant or variant
    result.variants = (*profile.variants, variant)
    return result


#: Variants by receiver generation. "older": the web interface on port 80
#: (receivers until 2015). "2013" on top of it: receivers before the 2014
#: encodings (picture levels in two digits, tuner presets A1-G8), which
#: report a CommApiVers below 0250 or none at all.
OLDER = "older"
BEFORE_2014 = "2013"
COMM_API_2014: int = 250
#: Marantz names some commands differently (Smart Select: MSSMART, Z2SMART),
#: from interface level 0300 on: those receivers name the presets Smart
#: Select, Marantz's 2019 document gives MSSMART, and the manufacturer's apps
#: use HTTP for them. To Marantz receivers below 0300 (NR1604, SR5008) the
#: apps send MSQUICK<n>. The brand is BrandCode 1 in Deviceinfo.xml.
MARANTZ = "marantz"
BRAND_MARANTZ: int = 1
COMM_API_SMART_SELECT: int = 300
#: Before 2014, receivers outside the AVR-X and AVR-E series follow the
#: documents of 2011 to 2013 (AVR-1912/2112CI, AVR-1713, AVR-2113CI): the
#: restorer as MODE1-MODE3. The AVR-X2000/E400 document and an AVR-X2000
#: (PSRSTR LOW) use LOW, MED, HI.
BEFORE_X = "before_x"
_X_SERIES = re.compile(r"^AVR-[XE]\d")
#: Receivers without Deviceinfo.xml (2010-2012) on top: the 2011 encodings
#: of the AVR-1912/2112CI document (Dynamic Volume NGT, EVE, DAY).
NO_DEVICEINFO = "2011"


def profile_for_receiver(
    profile: ProtocolProfile,
    http_port: int | None,
    comm_api_version: int | None,
    brand_code: int | None = None,
    model_name: str | None = None,
) -> ProtocolProfile:
    """The profile with the variants that fit the receiver's generation and brand."""

    result = profile
    if http_port == 80:
        result = profile_variant(result, OLDER)
        if comm_api_version is None or comm_api_version < COMM_API_2014:
            result = profile_variant(result, BEFORE_2014)
            if not _X_SERIES.match(model_name or ""):
                result = profile_variant(result, BEFORE_X)
            if comm_api_version is None:
                result = profile_variant(result, NO_DEVICEINFO)
    if (
        brand_code == BRAND_MARANTZ
        and comm_api_version is not None
        and comm_api_version >= COMM_API_SMART_SELECT
    ):
        result = profile_variant(result, MARANTZ)
    return result


@lru_cache(maxsize=1)
def load_profile() -> ProtocolProfile:
    """Load and cache the protocol profile bundled with the package."""

    with _PROFILE_PATH.open(encoding="utf-8") as handle:
        raw = json.load(handle)
    return ProtocolProfile(raw)


def range_paths(spec: ControlSpec) -> list[str]:
    """Where in Deviceinfo.xml a control's plain value range is published, in order."""

    paths = spec.get("range_from")
    if not paths:
        return []
    return [paths] if isinstance(paths, str) else [str(path) for path in paths]
