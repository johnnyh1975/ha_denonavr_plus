"""Live signal information from the setup interface (/ajax/info).

Receivers with the setup interface (10443 or 11080) report what they receive
and send on HDMI and which channels play, next to the setup menus:

    /ajax/info/get_config?type=2 -> <Audio>: InputSignal and ActiveSpeaker,
        each a list of <Speaker index="n">0|1|2</Speaker> (2: present/playing)
    /ajax/info/get_config?type=3 -> <Video>: Resolution, HDR, ColorSpace,
        Colorimetry, PixelDepth, FRLRate, each with <Input> and <Output>;
        "---" for no value

Sourced from TheFab21/ha-denon-marantz-avr (webapi.py), which reads them on
current AVR-X receivers; the channel index tables come from the receiver's
own web app as that integration took them. Not yet seen on an AVR-X2600H,
which is why the tests mark them assumed. Receivers without the setup
interface (the AVR-X2000) have none of this.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

AUDIO_TYPE = 2
VIDEO_TYPE = 3

#: The receiver's placeholder for "no value".
_NO_VALUE = "---"
#: A speaker entry with this value plays (or is present in the signal).
_ACTIVE = "2"

#: Channel index -> label in the input signal list.
INPUT_CHANNELS = {
    0: "FL", 1: "FR", 2: "C", 3: "LFE", 5: "SL", 6: "SR", 7: "SBL", 8: "SBR",
    9: "SB", 10: "FWL", 11: "FWR", 12: "FHL", 13: "FHR", 24: "SHL", 25: "SHR",
    30: "CH", 31: "TS", 35: "EXT",
}
#: Channel index -> label in the active speaker list (more positions).
SPEAKER_CHANNELS = {
    0: "FL", 1: "FR", 2: "C", 3: "SW", 5: "SL", 6: "SR", 7: "SBL", 8: "SBR",
    9: "SB", 10: "FWL", 11: "FWR", 12: "FHL", 13: "FHR", 14: "FDL", 15: "FDR",
    16: "TFL", 17: "TFR", 18: "TML", 19: "TMR", 20: "SDL", 21: "SDR",
    22: "TRL", 23: "TRR", 24: "SHL", 25: "SHR", 26: "RHL", 27: "RHR",
    28: "BDL", 29: "BDR", 30: "CH", 31: "TS",
}

#: The video fields: key -> (section tag, Input or Output).
VIDEO_FIELDS = {
    "video_input_resolution": ("Resolution", "Input"),
    "video_output_resolution": ("Resolution", "Output"),
    "video_input_hdr": ("HDR", "Input"),
    "video_output_hdr": ("HDR", "Output"),
    "video_input_color_space": ("ColorSpace", "Input"),
    "video_input_pixel_depth": ("PixelDepth", "Input"),
    "video_input_frl": ("FRLRate", "Input"),
}
AUDIO_FIELDS = ("audio_input_channels", "audio_active_speakers")
FIELDS = (*VIDEO_FIELDS, *AUDIO_FIELDS)


def _root(text: str | None, tag: str) -> ET.Element | None:
    if not text:
        return None
    try:
        root = ET.fromstring(text)  # noqa: S314 - the receiver on the local network
    except ET.ParseError:
        return None
    return root if root.tag == tag else None


def _clean(value: str | None) -> str | None:
    text = (value or "").strip()
    return None if not text or text == _NO_VALUE else text


def parse_video(text: str | None) -> dict[str, str | None] | None:
    """The video fields from a type 3 answer, or None when it is no <Video>."""

    root = _root(text, "Video")
    if root is None:
        return None
    values: dict[str, str | None] = {}
    for key, (section, side) in VIDEO_FIELDS.items():
        element = root.find(f"{section}/{side}")
        values[key] = _clean(element.text) if element is not None else None
    return values


def _channels(parent: ET.Element | None, names: dict[int, str]) -> str | None:
    if parent is None:
        return None
    active = [
        names[int(index)]
        for item in parent.findall("List/Speaker")
        if (index := item.get("index", "")).isdigit()
        and (item.text or "").strip() == _ACTIVE
        and int(index) in names
    ]
    return " ".join(active) or None


def parse_audio(text: str | None) -> dict[str, str | None] | None:
    """The playing channels from a type 2 answer, or None when it is no <Audio>."""

    root = _root(text, "Audio")
    if root is None:
        return None
    return {
        "audio_input_channels": _channels(root.find("InputSignal"), INPUT_CHANNELS),
        "audio_active_speakers": _channels(root.find("ActiveSpeaker"), SPEAKER_CHANNELS),
    }
