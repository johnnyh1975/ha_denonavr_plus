"""Parsers for the Denon AVR client library.

Two responsibilities live here:

* parse_device_info: turn the HTTP Deviceinfo XML into a Discovery object with
  the device identity, the advertised features, the channel list (codes plus
  display names), the option labels for enum controls and the numeric metadata
  for level controls. All of this is device published.

* TelnetParser: turn incoming telnet lines into updates of the Discovery (for
  the SS introspection responses) and the volatile AvrState. Decoding of values
  is driven by the protocol profile so the wire grammar stays in one place.
"""

from __future__ import annotations

from dataclasses import replace

import logging
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from typing import Any

from .codec import decode_centered, decode_half_step, decode_volume
from .models import (
    AvrState,
    ChannelDescriptor,
    Discovery,
    NowPlaying,
    SourceDescriptor,
    ZoneDescriptor,
)
from .profile import ControlSpec, ProtocolProfile

_LOGGER = logging.getLogger(__name__)


def _format_mac(raw: str | None) -> str | None:
    """Format a bare MAC such as 'AABBCCDDEEFF' as 'aa:bb:cc:dd:ee:ff'."""

    if not raw:
        return None
    raw = raw.strip()
    if len(raw) != 12:
        return raw
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2)).lower()


#: Whether control by Denon's apps is allowed on the receiver: the remote app
#: (NSARA) and the HEOS app (NSHEA). Denon's AVR Remote app asks both and
#: treats OFF as switched off; no protocol document here lists them.
APP_CONTROL = {"NSARA ": "remote_app_control", "NSHEA ": "heos_app_control"}
APP_CONTROL_QUERIES = ("NSARA ?", "NSHEA ?")

#: A sound mode list entry: two digits for its place, one for the selected
#: flag, then the name, which may itself start with a digit (7ch Stereo).
_SOUND_MODE_ENTRY = re.compile(r"^(\d{2})(\d)(.*)$")

#: The elapsed time on the on-screen list (NSE5): [h:]mm:ss, maybe a percentage.
_ELAPSED = re.compile(r"^(\d+):(\d{2})(?::(\d{2}))?(?:\s+(\d{1,3})%)?$")



def readable_sources(
    sources: list[SourceDescriptor], names: Mapping[str, str]
) -> list[SourceDescriptor]:
    """The sources, those without a real name given the profile's readable one.

    A source counts as unnamed when its name is its code or another bare code
    of the table: the stand-in NET was named NETWORK, and the snapshot of an
    earlier start holds the codes as names.
    """

    return [
        replace(source, name=names[source.code])
        if source.code in names and (source.name == source.code or source.name in names)
        else source
        for source in sources
    ]


# UPnP description local tag name -> the DeviceInfo attribute it fills. The AVR
# firmware and serial go on the device card; the AIOS (HEOS network module)
# version and build revision are captured for diagnostics.
_UPNP_TAGS: dict[str, str] = {
    "manufacturer": "manufacturer",
    "modelName": "model_name",
    "serialNumber": "serial_number",
    "firmware_version": "firmware_version",
    "modelNumber": "network_module_version",
    "firmwareRevision": "firmware_revision",
}


def parse_upnp_description(xml_text: str) -> dict[str, str]:
    """Extract identity/version fields from a UPnP device description.

    The document is namespaced and contains several sub devices, and the Denon
    firmware tag is in a vendor namespace, so match on the local tag name and
    take the first non empty value per field. Returns {} on any parse error.
    """

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    result: dict[str, str] = {}
    for element in root.iter():
        attr = _UPNP_TAGS.get(element.tag.rsplit("}", 1)[-1])
        if not attr or attr in result:
            continue
        text = (element.text or "").strip()
        if text:
            result[attr] = text
    return result


def parse_device_info(
    xml_text: str,
    feature_names: set[str] | None = None,
    enum_features: set[str] | None = None,
    receiver_type: dict[str, str] | None = None,
    value_ranges: set[str] | None = None,
) -> Discovery:
    """Parse the Deviceinfo XML into a Discovery object.

    `feature_names` is the set of receiver feature names (from the profile) whose
    display title and, for those also in `enum_features`, option labels should be
    read. Passing them keeps the wire grammar in the profile while the titles,
    labels, ranges and everything else come from the document.

    The parser is deliberately tolerant: any missing node simply leaves the
    corresponding piece of information unset. Nothing here is hardcoded per
    model; every name, range and option comes from the document.
    """

    discovery = Discovery()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        _LOGGER.warning("Could not parse Deviceinfo XML: %s", err)
        return discovery

    device = discovery.device
    # Some models report a region-variant marker '*' in the name; strip it, as
    # the official integration does, so the model reads cleanly.
    device.model_name = (root.findtext("ModelName") or "").strip().replace("*", "") or None
    device.mac_address = _format_mac(root.findtext("MacAddress"))
    comm_api = (root.findtext("CommApiVers") or "").strip()
    device.comm_api_version = int(comm_api) if comm_api.isdigit() else None
    brand = (root.findtext("BrandCode") or "").strip()
    device.brand_code = int(brand) if brand.isdigit() else None
    category = (root.findtext("ProductCategory") or "").strip()
    device.product_category = int(category) if category.isdigit() else None
    # Classify the receiver type (the device's hardware version) the way the
    # official library does: an AVR-X family device reached over the 8080
    # interface is "avr-x-2016", otherwise "avr". A device is AVR-X when its
    # CommApiVers OR its ModelName matches the library's patterns. This is read
    # from the equipment and works for any model (no per-model table).
    if receiver_type:
        commapi = (root.findtext("CommApiVers") or "").strip()
        model = device.model_name or ""
        is_avr_x = bool(
            (commapi and re.search(receiver_type.get("commapi_pattern", ""), commapi))
            or (model and re.search(receiver_type.get("avr_x_pattern", ""), model))
        )
        device.hardware_type = receiver_type.get(
            "avr_x_type" if is_avr_x else "default_type"
        )
    zones_text = root.findtext("DeviceZones")
    if zones_text and zones_text.strip().isdigit():
        device.zone_count = max(1, int(zones_text.strip()))

    # Build the zone list from the discovered count. The main zone (index 1)
    # uses the core control tokens; additional zones use the 'Z<index>' prefix.
    discovery.zones.append(
        ZoneDescriptor(id="main", index=1, name=None, is_main=True)
    )
    for index in range(2, device.zone_count + 1):
        discovery.zones.append(
            ZoneDescriptor(id=f"zone{index}", index=index, name=f"Zone {index}")
        )

    # Advertised features: every FuncName token anywhere in the tree.
    discovery.features = {
        (node.text or "").strip()
        for node in root.iter("FuncName")
        if node.text and node.text.strip()
    }

    # Channel list with codes, display names and per channel dB ranges, all
    # read from the receiver's Channel Level section.
    for ch in root.iter("Ch"):
        code = (ch.findtext("Name") or "").strip()
        name = (ch.findtext("DispName") or "").strip()
        if not code:
            continue
        # The receiver includes a synthetic "Reset" entry with a zero range;
        # skip anything that is not a real, adjustable channel.
        min_range = ch.findtext("MinRange")
        max_range = ch.findtext("MaxRange")
        if name.lower() == "reset" or min_range == max_range:
            continue
        discovery.channels.append(ChannelDescriptor(code=code, name=name or code))
        meta = _range_from_block(ch)
        if meta:
            discovery.numeric_meta[code] = meta

    # Tone control dB ranges, read from the receiver's tone control block.
    tone = root.find(".//ToneControlSet_AVR")
    if tone is not None:
        for feature in ("Bass", "Treble"):
            meta = _range_from_tone(tone, feature)
            if meta:
                discovery.numeric_meta[feature] = meta

    # Titles, option labels and numeric ranges for feature controls. For each
    # feature the profile knows, locate the matching block and read what the
    # receiver publishes: the title (first DispName), the ordered option labels
    # (for enum controls) and the numeric range (for level controls).
    enum_features = enum_features or set()
    for feature in feature_names or ():
        block = root.find(f".//{feature}")
        if block is None:
            continue
        labels = [
            (node.text or "").strip()
            for node in block.iter("DispName")
            if node.text and node.text.strip()
        ]
        if labels:
            discovery.feature_names[feature] = labels[0]
            if feature in enum_features and len(labels) >= 2:
                discovery.option_labels[feature] = labels[1:]
        meta = _range_from_block(block)
        if meta:
            discovery.numeric_meta[feature] = meta

    # Plain value ranges (MinValue/MaxValue/Step, not dB) for the controls
    # whose profile names where the receiver publishes them ("range_from").
    for path in value_ranges or ():
        block = root.find(f".//{path}")
        if block is None or path in discovery.numeric_meta:
            continue
        low, high = _num(block, "MinValue"), _num(block, "MaxValue")
        step = _num(block, "Step") or _num(block, "StepValue") or 1.0
        if low is not None and high is not None and low < high:
            discovery.numeric_meta[path] = {"min": low, "max": high, "step": step}

    # Sound mode genre names (Movie/Music/Game/Pure), read from the receiver's
    # SoundMode genre list. Keyed by the first three upper case letters so they
    # can be correlated with the 3 letter genre codes used by OPSMLALL.
    genre_list = root.find(".//SoundMode/Genre/List")
    if genre_list is not None:
        for value in genre_list.findall("Value"):
            disp = (value.findtext("DispName") or "").strip()
            if disp:
                discovery.sound_mode_genres[disp[:3].upper()] = disp.title()

    # Master volume scale, read from the receiver's Volume block: the absolute
    # maximum, the step, and the reference (the absolute value shown as 0.0 dB).
    volume = root.find(".//Volume")
    if volume is not None:
        scale: dict[str, float] = {}
        max_value = _num(volume, "MaxValue")
        step_value = _num(volume, "StepValue")
        if max_value is not None:
            scale["absolute_max"] = max_value
        if step_value is not None:
            scale["step"] = step_value
        # The reference is the Absolute value whose Relative label is "0.0dB".
        for param in volume.findall(".//Param"):
            relative = (param.findtext("Relative") or "").strip().lower()
            if relative in ("0.0db", "0db"):
                reference = _num(param, "Absolute")
                if reference is not None:
                    scale["reference"] = reference
                break
        if scale:
            discovery.volume = scale

    # Fallback source list from the InputSource tree (used only when the telnet
    # SSFUN/SSSOD introspection is unavailable). The path leaf is the name.
    for source in root.iter("Source"):
        path = source.findtext("SourcePath") or ""
        leaf = path.rsplit("/", 1)[-1].strip()
        if leaf and leaf.upper() != "SOURCE":
            if not any(s.code == leaf for s in discovery.sources):
                discovery.sources.append(SourceDescriptor(code=leaf, name=leaf))

    return discovery


def parse_main_zone(xml_text: str, listed_sound_modes: Mapping[str, str]) -> Discovery:
    """Parse the main zone document of a receiver without Deviceinfo.xml.

    Receivers from 2010 to 2012 describe their inputs only here, the way the
    denonavr library reads them (receiver type "avr"): the input codes
    (InputFuncList, the SI token), the user's names (RenameSource, padded
    with spaces) and whether each is in use (SourceDelete, USE or DEL), in
    the same order. Sound modes come from SurroundLists, whose `index` is
    the MS token; they are returned as the listed sound mode names of the
    profile (listed_sound_modes), like the function names of Deviceinfo.xml.
    Zones and the identity are not in this document.
    """

    discovery = Discovery()
    discovery.zones.append(ZoneDescriptor(id="main", index=1, name=None, is_main=True))
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        _LOGGER.warning("Could not parse the main zone XML: %s", err)
        return discovery

    def values(tag: str) -> list[str]:
        return [(node.text or "").strip() for node in root.findall(f"./{tag}/value")]

    names = values("RenameSource")
    used = values("SourceDelete")
    for index, code in enumerate(values("InputFuncList")):
        # SOURCE is "follow the main zone", for the other zones only.
        if not code or code == "SOURCE" or any(s.code == code for s in discovery.sources):
            continue
        name = names[index] if index < len(names) and names[index] else code
        visible = not (index < len(used) and used[index] == "DEL")
        discovery.sources.append(SourceDescriptor(code=code, name=name, visible=visible))
    wires = {
        (node.get("index") or "").strip() for node in root.findall("./SurroundLists/value")
    } - {""}
    discovery.features = {name for name, wire in listed_sound_modes.items() if wire in wires}
    return discovery


def _num(element: ET.Element, tag: str) -> float | None:
    """Read a numeric child value, or None when absent or not numeric."""

    text = element.findtext(tag)
    try:
        return float(text) if text is not None and text.strip() != "" else None
    except ValueError:
        return None


def _to_db_range(low: float, high: float, default: float, step: float) -> dict[str, float]:
    """Convert a raw min/max/default/step to a dB range around the default.

    The receiver expresses levels as a raw value where the default is 0 dB and
    each raw unit is `step` dB, so the dB offset is (raw - default) * step. This
    matches both the tone control scale (0..12, default 6, step 1 -> +/- 6 dB)
    and the channel/subwoofer scale (0..48, default 24, step 0.5 -> +/- 12 dB).
    """

    return {
        "min": (low - default) * step,
        "max": (high - default) * step,
        "step": step,
    }


def _range_from_block(block: ET.Element) -> dict[str, float] | None:
    """Derive a dB range from a control block that publishes MinRange etc."""

    low = _num(block, "MinRange")
    high = _num(block, "MaxRange")
    default = _num(block, "DefaultValue")
    step = _num(block, "Step")
    if low is None or high is None or default is None or step is None:
        return None
    return _to_db_range(low, high, default, step)


def _range_from_tone(tone: ET.Element, prefix: str) -> dict[str, float] | None:
    """Derive a dB range from the tone control block for bass or treble."""

    low = _num(tone, f"{prefix}Min")
    high = _num(tone, f"{prefix}Max")
    default = _num(tone, f"{prefix}Default")
    step = _num(tone, f"{prefix}Step")
    if low is None or high is None or default is None or step is None:
        return None
    return _to_db_range(low, high, default, step)


#: What follows Z2/Z3 in replies that are zone settings, not a source.
_ZONE_SETTINGS = ("QUICK", "SMART", "SLP", "STBY", "HPF", "CS", "CV", "PS", "HDA")


class TelnetParser:
    """Stateful parser that applies telnet lines to a Discovery and AvrState."""

    def __init__(self, profile: ProtocolProfile, discovery: Discovery) -> None:
        self._profile = profile
        # Per speaker group its latest value of each kind: "size" (presence or
        # size) and, for the count groups, "count".
        self._speaker_values: dict[str, dict[str, str]] = {}
        self._quick_select_prefix = str(
            profile.introspection.get("quick_select", {}).get("prefix", "MSQUICK")
        )
        self._discovery = discovery
        # How many digits the receiver itself writes a numeric enum value with,
        # where that differs from the profile (an AVC-X3900H reports the volume
        # limit as '70', the AVR-X3600H as '070'): control id -> digits.
        self.digits: dict[str, int] = {}
        # Accumulators for the OPSML (current context) and OPSMLALL (all groups)
        # responses; each is swapped into discovery when its 'END' line arrives,
        # so the published lists are always a clean, complete single response.
        self._opsml_pending: list[str] = []
        self._opsmlall_pending: list[str] = []
        # Per genre group accumulator (MOV/MUS/GAM/PUR -> modes), rebuilt each
        # OPSMLALL cycle so a signal change cannot leave a group with a mode the
        # receiver no longer offers.
        self._opsmlall_groups_pending: dict[str, list[str]] = {}
        # Precompute the generic feature and read only matchers, longest prefix
        # first so that specific tokens win over shorter ones.
        # Feature controls decode generically from their prefix. Core enum/on-off
        # settings that have no FuncName gate (volume scale/limit, muting level,
        # all-zone stereo, subwoofer mode) decode the same way, so they join the
        # generic matcher instead of each needing a bespoke dispatch branch. The
        # overloaded core controls (power/volume/mute/source/sound mode) keep
        # their explicit handling and are excluded here (kind or zone bound).
        self._feature_matchers = sorted(
            (
                (spec.prefix, spec)
                for spec in profile.controls.values()
                if spec.prefix
                and spec.kind != "channel_set"
                and (
                    spec.scope in ("feature", "answered")
                    or (
                        spec.scope == "core"
                        and spec.kind in ("enum", "onoff")
                        and not spec.zone
                    )
                )
            ),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        # Another spelling some receivers answer instead (AVC-X3900H: OPALSSET
        # for auto lip sync); the one answered is also the one written.
        self.variant_prefixes: dict[str, str] = {}
        variants = [
            (str(variant["prefix"]), spec)
            for spec in profile.controls.values()
            for variant in spec.get("variants") or ()
            if variant.get("prefix")
        ]
        self._feature_matchers = sorted(
            self._feature_matchers + variants, key=lambda item: len(item[0]), reverse=True
        )
        self._readonly_matchers = sorted(
            profile.readonly.items(),
            key=lambda item: len(item[1].get("prefix", "")),
            reverse=True,
        )

    def feed(self, line: str, state: AvrState) -> bool:
        """Apply a single telnet line to the state. Return True if it changed."""

        line = line.rstrip("\r\n")
        if not line:
            return False
        try:
            return self._dispatch(line, state)
        except (ValueError, IndexError) as err:
            _LOGGER.debug("Ignoring unparsable line %r: %s", line, err)
            return False

    # Dispatch order matters because several prefixes overlap. The core zone
    # controls and the overloaded/list style responses are handled explicitly;
    # everything else is decoded generically from the profile.
    def _dispatch(self, line: str, state: AvrState) -> bool:
        if line.startswith("PW"):
            return self._set_system_power(line[2:], state)
        if line.startswith("ZM"):
            on = line[2:].strip() == "ON"
            state.zone("main").power = on
            # Mirror into values so the (optional) main-power switch reads state.
            state.values["main_power"] = on
            return True
        if line.startswith("MVMAX"):
            return self._set_volume_max("main", line[5:], state)
        if line.startswith("MV") and not line.startswith(("MVUP", "MVDOWN")):
            return self._set_volume("main", line[2:], state)
        if line.startswith("MU"):
            on = line[2:].strip() == "ON"
            state.zone("main").muted = on
            # Mirror into values so the (optional) main-mute switch reads state.
            state.values["main_mute"] = on
            return True
        quick = self._quick_select_prefix
        if line.startswith(quick):
            # Current main zone quick select preset number (0 = none);
            # MSSMART on Marantz receivers.
            token = line[len(quick):].strip()
            if token.isdigit():
                state.values["quick_select"] = int(token)
                return True
            return False
        if line.startswith("MS"):
            return self._set_sound_mode(line[2:], state)
        if line.startswith("SI"):
            state.zone("main").source = line[2:].strip()
            return True
        if line.startswith("SV"):
            state.values["video_select"] = line[2:].strip()
            return True
        if line.startswith("SSLEV"):
            return self._set_channel(line[5:], state.channel_levels)
        if line.startswith("SSSDE"):
            return self._set_distance(line[5:], state.channel_distances)
        if line.startswith("SSCFR"):
            return self._set_crossover(line[5:], state)
        if line.startswith("CV"):
            return self._set_channel(line[2:], state.channel_trims)
        if len(line) >= 2 and line[0] == "Z" and line[1].isdigit():
            if self._set_zone(int(line[1]), line[2:], state):
                return True
            # A zone setting (Z2SLP, Z2CVFL ...): decoded by the matchers below.
        for prefix, key in APP_CONTROL.items():
            if line.startswith(prefix):
                answer = line[len(prefix) :].strip()
                if answer not in ("ON", "OFF"):
                    return False
                state.values[key] = answer == "ON"
                return True
        if line.startswith("NSE") and len(line) > 3 and line[3].isdigit():
            return self._set_netaudio(int(line[3]), line[4:], state)
        if line.startswith("TFANNAME"):
            state.values["tuner_station"] = line[8:].strip() or None
            return True
        if line.startswith("TFAN") and line[4:].strip().isdigit():
            state.values["tuner_frequency"] = int(line[4:].strip())
            return True
        if line.startswith("TPAN"):
            preset = line[4:].strip()
            if not preset.startswith("MEM"):
                # TPANMEM01 reports a preset being stored, not the one recalled.
                state.values["tuner_preset"] = None if preset in ("", "OFF") else preset
            return True
        if line.startswith("SSFUN"):
            return self._set_source_name(line[5:])
        if line.startswith("SSSOD"):
            return self._set_source_visibility(line[5:])
        if line.startswith("SSSMG"):
            # Current sound mode genre group (MOV/MUS/GAM/PUR), reported directly.
            group = line[5:].strip()
            if group and not group.startswith(self._profile.list_terminator):
                state.values["sound_mode_genre"] = group
                return True
            return False
        if line.startswith("SSQSNZMA"):
            return self._set_quick_select_name(line[8:])
        if line.startswith("SSQS") and line[4:5].isdigit():
            return self._set_quick_select_block(line[4:])
        if line.startswith("SSSPC"):
            return self._set_speaker_config(line[5:], state)
        # SSSWM (subwoofer mode), MNZST (all-zone stereo) and SSVCTZMA* (volume
        # scale/limit, muting level) are core settings with no FuncName gate;
        # they now decode through the generic feature matcher below.
        if line.startswith("OPSMLALL"):
            return self._set_sound_mode_list(line[8:], state=state)
        if line.startswith("OPSML"):
            return self._set_current_sound_mode(line[5:], state)

        audio_menu = self._profile.grammar.get("audio_menu_status", {})
        if (prefix := audio_menu.get("prefix")) and line.startswith(prefix):
            # One digit per Audio menu item (0 hidden, 1 grayed, 2 settable).
            digits = line[len(prefix):].strip()
            if not digits.isdigit():
                return False
            state.values["audio_menu_status"] = digits
            return True

        # Read only audio information.
        for control_id, spec in self._readonly_matchers:
            prefix = spec.get("prefix", "")
            if prefix and line.startswith(prefix):
                return self._set_readonly(control_id, spec, line[len(prefix):], state)

        # Generic feature controls.
        for prefix, control in self._feature_matchers:
            if line.startswith(prefix):
                remainder = line[len(prefix):]
                # A control with a short, overloaded prefix (e.g. picture mode
                # 'PV', shared with the video-adjust tokens PVBR/PVCN/...) opts
                # into exact matching: it only claims the line when the value is
                # one of its known enum tokens, so it never swallows a sibling.
                if control.get("exact_enum") and remainder.strip() not in control.values:
                    continue
                if prefix != control.prefix:
                    self.variant_prefixes[control.id] = prefix
                return self._set_feature(control, remainder, state)

        hdmi = self._profile.grammar.get("hdmi_setup", {})
        if (prefix := hdmi.get("prefix")) and line.startswith(prefix):
            # The HDMI setup as stored (SSHOSCONARC ON), also while the menu
            # shows a value HDMI Control imposes; after the matchers, which
            # take SSHOSALS (auto lip sync of older receivers).
            code, _, value = line[len(prefix):].partition(" ")
            if code in hdmi.get("items", {}) and value.strip():
                state.values[f"hdmi_setup_{code}"] = value.strip()
                return True
            return line[len(prefix):].strip() == self._profile.list_terminator

        return False

    def _set_netaudio(self, index: int, text: str, state: AvrState) -> bool:
        """One line of the on-screen list (NSE0-NSE8), for receivers without HEOS.

        Line 0 tells whether a track plays; lines 1-8 start with one flag byte.
        Text after a NUL is to be disregarded (protocol document).
        """

        grammar = self._profile.netaudio
        if not grammar:
            return False
        if index:
            text = text[1:]
        text = text.split("\x00", 1)[0].strip()
        now = state.now_playing
        if index == 0:
            if text.startswith(grammar.get("now_playing", "Now Playing")):
                if not now.active:
                    state.now_playing = replace(now, state="play")
                return True
            state.now_playing = NowPlaying(repeat=now.repeat, shuffle=now.shuffle)
            return True
        if str(index) == str(grammar.get("position_line")):
            return self._set_netaudio_position(text, state)
        field_name = grammar.get("lines", {}).get(str(index))
        if field_name is None:
            return False
        updated = replace(now)
        setattr(updated, str(field_name), text or None)
        if field_name == "title" and (text or None) != now.title:
            # Another track: the old one's time is no longer the position.
            updated.position = updated.duration = updated.position_at = None
        state.now_playing = updated
        return True

    def _set_netaudio_position(self, text: str, state: AvrState) -> bool:
        """The elapsed time line ('03:36', '1:02:03', '00:11 100%') of a playing track."""

        now = state.now_playing
        if not now.active:
            return False
        match = _ELAPSED.match(text)
        if match is None:
            # Blank for a moment at a track change: no position until it counts.
            if text or now.position is None:
                return False
            state.now_playing = replace(now, position=None, duration=None, position_at=None)
            return True
        first, second, third, percent = match.groups()
        # mm:ss, or h:mm:ss when the third part is there.
        parts = [int(first), int(second)] + ([int(third)] if third else [])
        position = 0
        for part in parts:
            position = position * 60 + part
        duration = now.duration
        if percent is not None and 0 < int(percent) <= 100 and position:
            duration = round(position * 100 / int(percent))
        if now.position == float(position) and now.duration == duration:
            # Paused, or asked again within the second: the time it was read stays.
            return False
        state.now_playing = replace(
            now, position=float(position), duration=duration, position_at=time.time()
        )
        return True

    def _set_system_power(self, remainder: str, state: AvrState) -> bool:
        state.system_power = remainder.strip() == "ON"
        return True

    def _set_volume(self, zone_id: str, remainder: str, state: AvrState) -> bool:
        value = decode_volume(remainder)
        if value is None:
            return False
        state.zone(zone_id).volume_raw = value
        return True

    def _set_volume_max(self, zone_id: str, remainder: str, state: AvrState) -> bool:
        value = decode_half_step(remainder)
        if value is None:
            return False
        state.zone(zone_id).volume_max_raw = value
        return True

    def _set_sound_mode(self, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        # MS is also the prefix for quick select and smart select echoes; those
        # are not sound modes, so ignore them here.
        if remainder.startswith(("QUICK", "SMART")):
            return False
        # This is the active mode's wire token (for example "DOLBY AUDIO-DSUR").
        state.values["sound_mode"] = remainder
        # All Zone Stereo is reflected as a sound mode ("ALL ZONE STEREO"), so
        # mirror it here to give the All Zone Stereo switch a known on/off state
        # (its own MNZST '?' query returns nothing; MS? is answered on resync).
        state.values["all_zone_stereo"] = remainder == "ALL ZONE STEREO"
        return True

    def _set_sound_mode_list(self, remainder: str, state: AvrState) -> bool:
        """Parse an OPSMLALL entry (all genre groups) into the flat mode set.

        Entries look like 'OPSMLALL MOV071Virtual': a 3 letter genre code, a two
        digit group, a one digit flag and the display name. This builds the full
        cross group set of modes and the per genre groups. The currently selected
        mode is taken from OPSML (current context), not here, because OPSMLALL
        omits context modes such as Auto. The flagged entry's genre is the
        current genre group: the receiver pushes OPSMLALL on a change but not
        SSSMG (an AVR-X2600H flagged 'MOV031Dolby Audio - DD + DSur' while its
        SSSMG answer from the start still said MUS).
        """

        remainder = remainder.strip()
        if remainder.startswith(self._profile.list_terminator):
            # End of the list: publish the accumulated set (receiver order) and
            # swap in the freshly rebuilt per group map so no stale modes remain.
            self._discovery.all_sound_modes = list(self._opsmlall_pending)
            self._discovery.sound_mode_groups = self._opsmlall_groups_pending
            self._opsmlall_pending = []
            self._opsmlall_groups_pending = {}
            return True
        if not remainder:
            return False
        genre = remainder[:3]
        match = _SOUND_MODE_ENTRY.match(remainder[3:])
        if not match:
            return False
        name = match.group(3).strip()
        if not name:
            return False
        if match.group(2) == "1":
            state.values["sound_mode_genre"] = genre
        if name not in self._opsmlall_pending:
            self._opsmlall_pending.append(name)
        modes = self._opsmlall_groups_pending.setdefault(genre, [])
        if name not in modes:
            modes.append(name)
        return True

    def _set_current_sound_mode(self, remainder: str, state: AvrState) -> bool:
        """Parse an OPSML entry (modes selectable in the CURRENT context/group).

        OPSML reflects exactly what the active group offers now, including modes
        OPSMLALL leaves out (such as Auto). Entries look like '011Direct'; the
        trailing flag digit marks the selected mode. The full response is
        accumulated and swapped in on the terminating 'OPSML END' line.
        """

        remainder = remainder.strip()
        if remainder.startswith(self._profile.list_terminator):
            # End of the list: publish the accumulated set as the current options.
            self._discovery.current_sound_modes = list(self._opsml_pending)
            self._opsml_pending = []
            return True
        match = _SOUND_MODE_ENTRY.match(remainder)
        if not match:
            return False
        flag, name = match.group(2), match.group(3).strip()
        if not name:
            return False
        # Once each: an AVR-X2600H left Stereo four times in the list.
        if name not in self._opsml_pending:
            self._opsml_pending.append(name)
        # The trailing digit is the "currently selected" flag; this is the mode
        # the user selected, which can differ from the resolved MS mode.
        if flag == "1":
            state.values["sound_mode_display"] = name
        return True

    def _set_channel(self, remainder: str, target: dict[str, float]) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder == self._profile.list_terminator:
            return False
        parts = remainder.split()
        if len(parts) != 2:
            return False
        code, raw = parts
        value = decode_half_step(raw)
        if value is None:
            return False
        target[code] = value - self._profile.level_reference
        return True

    def _set_distance(self, remainder: str, target: dict[str, float]) -> bool:
        """Parse an 'SSSDE<CODE> <value>' speaker distance line into `target`.

        The value is the distance times the profile divisor (e.g. '0310' at
        divisor 100 is 3.10 m). The 'STP' unit line and the 'END' terminator are
        ignored.
        """

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        parts = remainder.split()
        if len(parts) != 2:
            return False
        code, raw = parts
        if code == "STP" or not raw.isdigit():
            return False
        divisor = self._profile.distance.get("divisor", 100) or 100
        target[code] = int(raw) / divisor
        return True

    def _set_crossover(self, remainder: str, state: AvrState) -> bool:
        """Parse an 'SSCFR...' crossover line.

        Handles the per group 'SSCFR<GROUP> <Hz>' values (into state.crossovers),
        the speaker-selection mode 'SSCFR IDV'/'SSCFR ALL' and the 'SSCFRALL <Hz>'
        aggregate value (into state.values, driving the crossover speaker-
        selection and All-crossover selects). The 'END' terminator is ignored.
        """

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        parts = remainder.split()
        all_group = self._profile.introspection.get("crossover", {}).get(
            "all_group", "ALL"
        )
        if len(parts) == 1:
            # Speaker-selection mode: IDV (per-group) or ALL (one crossover).
            if parts[0] in ("IDV", "ALL"):
                state.values["crossover_speaker_selection"] = parts[0]
                return True
            return False
        if len(parts) != 2:
            return False
        group, raw = parts
        if not raw.isdigit():
            return False
        if group == all_group:
            state.values["crossover_all"] = int(raw)
            return True
        state.crossovers[group] = int(raw)
        return True

    def _set_zone(self, index: int, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        if not remainder:
            return False
        zone = state.zone(f"zone{index}")
        if remainder in ("ON", "OFF"):
            zone.power = remainder == "ON"
            return True
        if remainder.startswith("MU"):
            zone.muted = remainder[2:] == "ON"
            return True
        if remainder.isdigit():
            value = decode_volume(remainder)
            if value is not None:
                zone.volume_raw = value
                return True
            return False
        # Replies that share the zone prefix but are no source: quick and
        # smart select (Z2QUICK0 means "no quick select") and the zone's
        # settings (Z2SLPOFF, Z2CVFL 50 ...). A source code has no space.
        # Not checked against the receiver's source list: zone replies can
        # arrive before it.
        if " " in remainder or remainder.startswith(_ZONE_SETTINGS):
            return False
        zone.source = remainder
        return True

    def _set_source_name(self, remainder: str) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, name = remainder.partition(" ")
        self._upsert_source(code, name=name.strip() or None)
        return True

    def _set_source_visibility(self, remainder: str) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, flag = remainder.partition(" ")
        visible_token = self._profile.introspection["source_visibility"].get(
            "visible_token", "USE"
        )
        self._upsert_source(code, visible=flag.strip() == visible_token)
        return True

    def _upsert_source(
        self, code: str, *, name: str | None = None, visible: bool | None = None
    ) -> None:
        """Create or update a discovered source, keeping earlier attributes."""

        names = self._profile.source_names
        for source in self._discovery.sources:
            if source.code == code:
                if name is not None:
                    source.name = name
                elif code in names and (source.name == code or source.name in names):
                    # Named by its code, as an earlier start stored it.
                    source.name = names[code]
                if visible is not None:
                    source.visible = visible
                return
        self._discovery.sources.append(
            SourceDescriptor(
                code=code,
                name=name if name is not None else names.get(code, code),
                visible=visible if visible is not None else True,
            )
        )

    def _known_spelling(self, spec: ControlSpec, remainder: str) -> str:
        """The profile's value for a number the receiver writes its own way."""

        values = [str(value) for value in spec.get("values") or ()]
        if remainder in values or not remainder.isdigit():
            return remainder
        for value in values:
            if value.isdigit() and int(value) == int(remainder):
                self.digits[spec.id] = len(remainder)
                return value
        return remainder

    def _set_quick_select_block(self, remainder: str) -> bool:
        """A quick select as a block: 'SSQS2QSN <name>' names preset 2.

        Newer receivers (AVC-X3900H) answer per preset with one line per stored
        item (QSN name, SRC, VOL, SDM ...) and 'SSQS2 END'; only the name is
        used.
        """

        digits = ""
        for char in remainder:
            if not char.isdigit():
                break
            digits += char
        item = remainder[len(digits) :]
        if not digits or not item.startswith("QSN "):
            return bool(digits)
        name = item[4:].strip()
        if not name:
            return False
        self._discovery.quick_select_names[int(digits)] = name
        return True

    def _set_quick_select_name(self, remainder: str) -> bool:
        """Parse a 'QS<n> <name>' quick select entry into the discovery model."""

        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        code, _, name = remainder.partition(" ")
        name = name.strip()
        # code looks like 'QS1'; take the trailing digits as the preset number.
        digits = "".join(ch for ch in code if ch.isdigit())
        if not digits or not name:
            return False
        self._discovery.quick_select_names[int(digits)] = name
        return True

    def _set_speaker_config(self, remainder: str, state: AvrState) -> bool:
        remainder = remainder.strip()
        if not remainder or remainder.startswith(self._profile.list_terminator):
            return False
        group, _, value = remainder.partition(" ")
        value = value.strip()
        if not group or not value:
            return False
        # discovery.speaker_config holds a group's latest value of each kind (it
        # drives the configured-channel derivation): a count group answers its
        # presence and its count (SSSPCSBK NON, then SSSPCSBK 2SP), and a later
        # value replaces only the earlier one of its kind, so a speaker set up
        # or removed since counts. state.speaker_sizes keeps the latest single
        # value per group so the size selects have a live value.
        counts = self._profile.speakers.get("count_groups", {}).get(group, {})
        kind = "count" if value in counts.get("count_tokens", ()) else "size"
        values = self._speaker_values.setdefault(group, {})
        values[kind] = value
        self._discovery.speaker_config[group] = set(values.values())
        state.speaker_sizes[group] = value
        self._recompute_configured_channels()
        return True

    def _recompute_configured_channels(self) -> None:
        """Derive the configured channel codes from the SSSPC speaker config.

        A group counts as present only when none of its reported values is the
        absent token. The channel/count maps come from the profile (protocol
        grammar); which groups are present comes entirely from the receiver.
        """

        speakers = self._profile.speakers
        absent = set(speakers.get("absent_tokens") or [speakers.get("absent_token", "NON")])
        double = speakers.get("double_token", "2SP")
        groups = speakers.get("groups", {})
        count_groups = speakers.get("count_groups", {})

        configured: set[str] = set()
        for group, values in self._discovery.speaker_config.items():
            if absent & values:
                continue
            if group in count_groups:
                key = "double" if double in values else "single"
                configured.update(count_groups[group].get(key, []))
            elif group in groups:
                configured.update(groups[group])
        self._discovery.configured_channels = configured

    def _set_readonly(
        self, control_id: str, spec: dict[str, Any], remainder: str, state: AvrState
    ) -> bool:
        kind = spec.get("kind", "passthrough")
        if kind == "sample_rate":
            token = remainder.strip()
            state.readonly[control_id] = (
                None if token == self._profile.no_signal_token else token
            )
        elif kind == "trimmed":
            state.readonly[control_id] = remainder.rstrip() or None
        else:
            state.readonly[control_id] = remainder.strip() or None
        return True

    def _set_feature(self, spec: ControlSpec, remainder: str, state: AvrState) -> bool:
        kind = spec.kind
        remainder = remainder.strip()
        if kind == "onoff":
            on, off = spec.get("on", "ON"), spec.get("off", "OFF")
            # A prefix shared with other values (BTTX reports the output mode
            # too, SSTTR ends its list with END) claims only its own two.
            if spec.get("exact_onoff") and remainder not in (on, off):
                return False
            state.values[spec.id] = remainder == on
        elif kind == "enum":
            state.values[spec.id] = self._known_spelling(spec, remainder)
        elif kind == "level":
            value = decode_half_step(remainder)
            if value is None:
                return False
            state.values[spec.id] = value - self._profile.level_reference
        elif kind == "centered":
            value = decode_centered(remainder, int(spec.get("center", 0)))
            if value is None:
                return False
            state.values[spec.id] = value
        elif kind == "integer":
            try:
                state.values[spec.id] = int(remainder)
            except ValueError:
                return False
        elif kind == "attenuation":
            # Digits without a sign: 'PSLFE 10' is -10 dB.
            if not remainder.isdigit():
                return False
            state.values[spec.id] = -int(remainder)
        elif kind == "minutes":
            off = spec.get("off", "OFF")
            if remainder == off:
                state.values[spec.id] = 0
            elif remainder.isdigit():
                state.values[spec.id] = int(remainder)
            else:
                return False
        else:
            state.values[spec.id] = remainder
        return True
