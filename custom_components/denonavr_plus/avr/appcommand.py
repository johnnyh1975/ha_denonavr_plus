"""The sound state over AppCommand.xml and AppCommand0300.xml, while telnet is down.

Telnet brings every setting with push updates; without it (port 23 taken by
another app, blocked, or a receiver that refuses it) the StatusLite poll
reads only power, volume, mute and source. These requests add what the
official integration reads over HTTP: the sound mode and tone control
(AppCommand.xml, receivers from level 0210, 2013) and MultEQ, Dynamic EQ and
Dynamic Volume (AppCommand0300.xml, from level 0300, 2014). Writes already
go over formiPhoneAppDirect.xml as telnet commands.

Sources: the request and answer formats are those of the denonavr library
(appcommand.py, MIT); the GetToneControl and GetSurroundModeStatus answers are
recorded there (tests/fixtures/denonavr). The GetAudyssey answer and its
numbers (MultEQ 0-3, Dynamic Volume 0-3, as the library maps them) are not
recorded. The restorer is not read: no answer format is known.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

STATUS_PATH = "/goform/AppCommand.xml"
SETUP_PATH = "/goform/AppCommand0300.xml"
#: The CommApiVers level from which a receiver answers each page.
STATUS_FROM = 210
SETUP_FROM = 300

_HEADER = '<?xml version="1.0" encoding="utf-8"?>'
STATUS_REQUEST = (
    f"{_HEADER}<tx>"
    '<cmd id="1">GetSurroundModeStatus</cmd>'
    '<cmd id="1">GetToneControl</cmd>'
    "</tx>"
)
AUDYSSEY_REQUEST = (
    f"{_HEADER}<tx>"
    '<cmd id="3"><name>GetAudyssey</name><list>'
    '<param name="multeq"></param>'
    '<param name="dynamiceq"></param>'
    '<param name="dynamicvol"></param>'
    "</list></cmd></tx>"
)

#: The numbers AppCommand0300.xml uses, as telnet's tokens.
_MULTEQ = {"0": "OFF", "1": "FLAT", "2": "BYP.LR", "3": "AUDYSSEY"}
_DYNAMIC_VOLUME = {"0": "OFF", "1": "LIT", "2": "MED", "3": "HEV"}
_DB = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*dB$", re.IGNORECASE)


def _root(text: str | None) -> ET.Element | None:
    if not text:
        return None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None
    return root if root.tag == "rx" else None


def _text(root: ET.Element, path: str) -> str | None:
    node = root.find(path)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _db(text: str | None) -> float | None:
    match = _DB.match(text or "")
    return float(match.group(1)) if match else None


def parse_status(text: str | None) -> dict[str, object]:
    """The sound mode and tone control from the answer to STATUS_REQUEST.

    Keys are the state's value keys: sound_mode_display (the receiver's name
    for the mode), and tone_control, bass and treble (dB) while the receiver
    offers tone control (status 1).
    """

    root = _root(text)
    if root is None:
        return {}
    values: dict[str, object] = {}
    surround = _text(root, ".//cmd/surround")
    if surround is not None:
        values["sound_mode_display"] = surround
    if _text(root, ".//cmd/status") == "1":
        adjust = _text(root, ".//cmd/adjust")
        if adjust is not None:
            values["tone_control"] = adjust == "1"
        for key, tag in (("bass", "basslevel"), ("treble", "treblelevel")):
            level = _db(_text(root, f".//cmd/{tag}"))
            if level is not None:
                values[key] = level
    return values


def parse_audyssey(text: str | None) -> dict[str, object]:
    """MultEQ, Dynamic EQ and Dynamic Volume from the answer to AUDYSSEY_REQUEST."""

    root = _root(text)
    if root is None:
        return {}
    params = {
        node.get("name"): (node.text or "").strip() for node in root.iterfind(".//list/param")
    }
    values: dict[str, object] = {}
    if params.get("multeq") in _MULTEQ:
        values["multeq"] = _MULTEQ[params["multeq"]]
    if params.get("dynamiceq") in ("0", "1"):
        values["dynamic_eq"] = params["dynamiceq"] == "1"
    if params.get("dynamicvol") in _DYNAMIC_VOLUME:
        values["dynamic_volume"] = _DYNAMIC_VOLUME[params["dynamicvol"]]
    return values
