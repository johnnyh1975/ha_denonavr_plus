#!/usr/bin/env python3
"""Find out what a Denon or Marantz receiver can do, by asking it.

Works with any Denon or Marantz receiver or network amplifier that has a
network port: old models without HEOS, the AVR-X series, the 2016+ HEOS
models and Marantz SR, NR, AV and Cinema models. Python 3.10 or newer, nothing
else to install.

    python3 probe_receiver.py 192.0.2.10        (your receiver's address)
    python3 probe_receiver.py 192.0.2.10 --quick

The script only reads. Every telnet command it sends is a query (it ends in
"?", or is one of the read-only screen requests NSE, NSA and NSH). Every web
request is a GET of a status page, or a POST to AppCommand.xml of commands
whose names start with "Get". HEOS gets only "get_" requests and a heartbeat.
It never changes a setting, never switches anything on or off and never
stores a preset.

Besides what the receiver can do, it measures how fast it answers, whether
it drops queries sent close together, and whether it accepts a second
telnet session. It reads every page type of the setup interface (not with
--quick) and notes which zones were on, because a zone that is off answers
fewer queries.

Before you run it:

- Switch the receiver on (not standby); some answers exist only while on.
- The receiver accepts one telnet connection at a time. Disable any
  integration or app that holds it, for example the official "Denon AVR
  Network Receivers" integration in Home Assistant (Settings > Devices &
  services > the entry > three dots > Disable). Enable it again afterwards.

It writes two files next to the script:

- avr-probe-<model>.md: a readable summary, capability by capability.
- avr-probe-<model>.json: everything the receiver answered, for the
  developers (test data for Denon AVR Plus).

The address, MAC addresses, serial numbers, the names of the receiver and of
other HEOS speakers, Wi-Fi names, account names and any other private network
address are replaced by placeholders before the files are written. Read them
before you share them: the now-playing and preset lines can show what you
listen to.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2"
PLACEHOLDER_HOST = "192.0.2.10"
PLACEHOLDER_OTHER_HOST = "192.0.2.99"
PLACEHOLDER_MAC = "aa:bb:cc:dd:ee:ff"
PLACEHOLDER_SERIAL = "SERIAL0000000"
PLACEHOLDER_ACCOUNT = "user@example.com"
PLACEHOLDER_WIFI = "WLAN"
PLACEHOLDER_IPV6 = "2001:db8::10"

PORTS = {
    23: "telnet control",
    80: "web (goform before 2016, redirect on newer firmware)",
    8080: "web (goform from 2016)",
    1255: "HEOS command line",
    1256: "calibration (Audyssey app)",
    10443: "setup interface (HTTPS)",
    11080: "setup interface (HTTP, newer firmware)",
    60006: "UPnP description",
}


@dataclass(frozen=True)
class Query:
    """One telnet query: the command, what it tells, and how to read the reply."""

    command: str
    group: str
    what: str
    # Text the last line of a multi-line answer ends with.
    ends_with: str | None = None
    wait: float = 0.6


def _q(command: str, group: str, what: str, ends_with: str | None = None,
       wait: float = 0.6) -> Query:
    return Query(command, group, what, ends_with, wait)


# Sources: the Denon protocol documents (2012 to 2015), the 2016+ query set of
# Denon AVR Plus (its protocol profile) and the queries collected in
# docs/protocol. Unknown queries are harmless: the receiver ignores them or
# answers with an error, and the summary says so.
CATALOGUE: list[Query] = [
    # Power and zones
    _q("PW?", "Power and zones", "Power"),
    _q("ZM?", "Power and zones", "Main zone on/off"),
    _q("Z2?", "Power and zones", "Zone 2 power, source, volume", wait=1.2),
    _q("Z2MU?", "Power and zones", "Zone 2 mute"),
    _q("Z2CV?", "Power and zones", "Zone 2 channel levels", wait=1.0),
    _q("Z2CS?", "Power and zones", "Zone 2 stereo/mono"),
    _q("Z2HPF?", "Power and zones", "Zone 2 high-pass filter"),
    _q("Z2PSBAS ?", "Power and zones", "Zone 2 bass"),
    _q("Z2PSTRE ?", "Power and zones", "Zone 2 treble"),
    _q("Z2HDA?", "Power and zones", "Zone 2 HDMI audio"),
    _q("Z2SLP?", "Power and zones", "Zone 2 sleep timer"),
    _q("Z2STBY?", "Power and zones", "Zone 2 auto standby"),
    _q("Z2QUICK ?", "Power and zones", "Zone 2 quick select"),
    _q("Z3?", "Power and zones", "Zone 3 power, source, volume", wait=1.2),
    _q("Z3MU?", "Power and zones", "Zone 3 mute"),
    _q("Z3CV?", "Power and zones", "Zone 3 channel levels", wait=1.0),
    _q("Z3SLP?", "Power and zones", "Zone 3 sleep timer"),
    _q("Z3STBY?", "Power and zones", "Zone 3 auto standby"),
    _q("Z3QUICK ?", "Power and zones", "Zone 3 quick select"),
    _q("MNZST ?", "Power and zones", "All Zone Stereo"),
    _q("SR?", "Power and zones", "Record/zone 2 mode (older models)"),
    # Volume, input, timers
    _q("MV?", "Volume and input", "Master volume and maximum"),
    _q("MU?", "Volume and input", "Mute"),
    _q("CV?", "Volume and input", "Channel levels", ends_with="CVEND", wait=1.5),
    _q("SI?", "Volume and input", "Input source"),
    _q("SV?", "Volume and input", "Video select"),
    _q("SD?", "Volume and input", "Input mode (auto/HDMI/digital/analog)"),
    _q("DC?", "Volume and input", "Digital input mode"),
    _q("SLP?", "Volume and input", "Sleep timer"),
    _q("SLP ?", "Volume and input", "Sleep timer with a space (no answer on AVR-X2000, AVR-X2600H)"),
    _q("STBY?", "Volume and input", "Auto standby"),
    _q("ECO?", "Volume and input", "ECO mode"),
    _q("DIM ?", "Volume and input", "Display dimmer"),
    _q("TR?", "Volume and input", "12 V triggers"),
    _q("MNMEN?", "Volume and input", "Setup menu shown on screen"),
    # Surround modes
    _q("MS?", "Surround", "Sound mode"),
    _q("MSQUICK ?", "Surround", "Quick select"),
    _q("MSSMART ?", "Surround", "Smart select (Marantz)"),
    _q("OPSML ?", "Surround", "Sound modes for the current signal", ends_with="END", wait=1.5),
    _q("OPSMLALL ?", "Surround", "All sound modes", ends_with="END", wait=1.5),
    _q("SSSMG ?", "Surround", "Sound mode group"),
    _q("PSMODE: ?", "Surround", "Pro Logic / cinema-music mode (older models)"),
    _q("SYSMI ?", "Surround", "Sound mode as shown (undocumented)"),
    _q("SYSDA ?", "Surround", "Audio format (undocumented)"),
    # Audio and Audyssey
    _q("PSTONE CTRL ?", "Audio", "Tone control"),
    _q("PSBAS ?", "Audio", "Bass"),
    _q("PSTRE ?", "Audio", "Treble"),
    _q("PSDIL ?", "Audio", "Dialogue level"),
    _q("PSSWL ?", "Audio", "Subwoofer level"),
    _q("PSCINEMA EQ. ?", "Audio", "Cinema EQ"),
    _q("PSLOM ?", "Audio", "Loudness management"),
    _q("PSSP: ?", "Audio", "Speaker output (height/wide/back)"),
    _q("PSMULTEQ: ?", "Audio", "Audyssey MultEQ"),
    _q("PSDYNEQ ?", "Audio", "Audyssey Dynamic EQ"),
    _q("PSREFLEV ?", "Audio", "Reference level offset"),
    _q("PSDYNVOL ?", "Audio", "Dynamic volume"),
    _q("PSDYNSET ?", "Audio", "Dynamic volume (older models)"),
    _q("PSLFC ?", "Audio", "Audyssey LFC"),
    _q("PSCNTAMT ?", "Audio", "LFC containment amount"),
    _q("PSDSX ?", "Audio", "Audyssey DSX"),
    _q("PSSTW ?", "Audio", "Stage width"),
    _q("PSSTH ?", "Audio", "Stage height"),
    _q("PSGEQ ?", "Audio", "Graphic EQ on/off"),
    _q("PSHEQ ?", "Audio", "Headphone EQ"),
    _q("PSDRC ?", "Audio", "Dynamic compression"),
    _q("PSDCO ?", "Audio", "Dynamic compression (older models)"),
    _q("PSDIC ?", "Audio", "Dialog control"),
    _q("PSDEH ?", "Audio", "Dialogue enhancer"),
    _q("PSBSC ?", "Audio", "Bass sync"),
    _q("PSLFE ?", "Audio", "LFE level"),
    _q("PSLFL ?", "Audio", "LFE level for external input"),
    _q("PSEFF ?", "Audio", "Effect level"),
    _q("PSDEL ?", "Audio", "Effect delay"),
    _q("PSPAN ?", "Audio", "Panorama"),
    _q("PSDIM ?", "Audio", "Dimension"),
    _q("PSCEN ?", "Audio", "Center width"),
    _q("PSCEI ?", "Audio", "Center image"),
    _q("PSCEG ?", "Audio", "Center gain"),
    _q("PSCES ?", "Audio", "Center spread"),
    _q("PSNEURAL ?", "Audio", "DTS Neural:X"),
    _q("PSSWR ?", "Audio", "Subwoofer on/off"),
    _q("PSRSZ ?", "Audio", "Room size"),
    _q("PSDELAY ?", "Audio", "Audio delay (lip sync)"),
    _q("PSRSTR ?", "Audio", "Audio restorer"),
    _q("PSFRONT?", "Audio", "Front speaker A/B"),
    _q("PSSB: ?", "Audio", "Surround back (older models)"),
    _q("PSATT ?", "Audio", "Attenuation (older models)"),
    _q("PSPHG ?", "Audio", "Pro Logic height gain"),
    _q("PSSPV ?", "Audio", "Speaker virtualizer"),
    _q("PSAUROPR ?", "Audio", "Auro-Matic preset"),
    _q("PSAUROST ?", "Audio", "Auro-Matic strength"),
    _q("PSAUROMODE ?", "Audio", "Auro-3D mode"),
    _q("PSDACFIL ?", "Audio", "DAC filter (Marantz)"),
    _q("PSMDAX ?", "Audio", "M-DAX (Marantz)"),
    _q("PSIMAX ?", "Audio", "IMAX"),
    _q("PSIMAXAUD ?", "Audio", "IMAX audio settings"),
    _q("PSIMAXHPF ?", "Audio", "IMAX high-pass filter"),
    _q("PSIMAXLPF ?", "Audio", "IMAX low-pass filter"),
    _q("PSIMAXSWM ?", "Audio", "IMAX subwoofer mode"),
    _q("PSIMAXSWO ?", "Audio", "IMAX subwoofer output"),
    _q("PSDIRAC ?", "Audio", "Dirac Live filter"),
    _q("SPPR ?", "Audio", "Speaker preset"),
    # Video
    _q("VSASP ?", "Video", "Aspect ratio"),
    _q("VSMONI ?", "Video", "HDMI monitor output"),
    _q("VSSC ?", "Video", "Resolution"),
    _q("VSSCH ?", "Video", "HDMI resolution"),
    _q("VSAUDIO ?", "Video", "HDMI audio output"),
    _q("VSVPM ?", "Video", "Video processing mode"),
    _q("VSVST ?", "Video", "Vertical stretch"),
    _q("PV?", "Video", "Picture mode"),
    _q("PVCN ?", "Video", "Contrast"),
    _q("PVBR ?", "Video", "Brightness"),
    _q("PVST ?", "Video", "Saturation"),
    _q("PVHUE ?", "Video", "Hue"),
    _q("PVDNR ?", "Video", "Noise reduction"),
    _q("PVENH ?", "Video", "Enhancer"),
    _q("SYSDVIN ?", "Video", "Video input signal (undocumented)"),
    _q("SYSDVOUT ?", "Video", "Video output signal (undocumented)"),
    # Tuner
    _q("TFAN?", "Tuner", "Frequency"),
    _q("TFANNAME?", "Tuner", "RDS station name"),
    _q("TPAN?", "Tuner", "Preset"),
    _q("TMAN?", "Tuner", "Band and tuning mode", wait=1.0),
    _q("TFHD?", "Tuner", "HD Radio frequency (North America)"),
    _q("TPHD?", "Tuner", "HD Radio preset (North America)"),
    _q("HD?", "Tuner", "HD Radio status (North America)", wait=1.0),
    # Network audio
    _q("NSE", "Network audio", "On-screen text, UTF-8 (now playing)", ends_with="NSE8", wait=2.0),
    _q("NSA", "Network audio", "On-screen text, ASCII", ends_with="NSA8", wait=2.0),
    _q("NSH", "Network audio", "Network preset names", wait=2.0),
    _q("NSFRN ?", "Network audio", "Friendly name"),
    # Signal information and setup data (2016+)
    _q("SSINF ?", "Signal and setup", "Signal information", wait=1.0),
    _q("SSINFAISSIG ?", "Signal and setup", "Input signal"),
    _q("SSINFAISFSV ?", "Signal and setup", "Sample rate"),
    _q("SSINFAISFOR ?", "Signal and setup", "Audio format"),
    _q("SSOUTFAISFOR ?", "Signal and setup", "Output format"),
    _q("SSFUN ?", "Signal and setup", "Sources and their names", ends_with="END", wait=2.0),
    _q("SSSOD ?", "Signal and setup", "Sources shown or hidden", ends_with="END", wait=2.0),
    _q("SSLEV ?", "Signal and setup", "Speaker levels", ends_with="END", wait=1.5),
    _q("SSSDE ?", "Signal and setup", "Speaker distances", ends_with="END", wait=1.5),
    _q("SSSPC ?", "Signal and setup", "Speaker configuration", ends_with="END", wait=1.5),
    _q("SSCFR ?", "Signal and setup", "Crossovers", ends_with="END", wait=1.5),
    _q("SSQSNZMA ?", "Signal and setup", "Quick select names", ends_with="END", wait=1.5),
    _q("SSVCTZMA ?", "Signal and setup", "Volume limits", wait=1.0),
    _q("SSSWM ?", "Signal and setup", "Subwoofer mode"),
    _q("SSLFL ?", "Signal and setup", "LPF for LFE"),
    _q("SSHOS ?", "Signal and setup", "HDMI output settings", wait=1.0),
    _q("SSHOSALS ?", "Signal and setup", "Auto lip sync"),
    _q("SSPAA ?", "Signal and setup", "Amp assign", wait=1.0),
    _q("SSALS ?", "Signal and setup", "Auto lip sync (other form)"),
    _q("SSANA ?", "Signal and setup", "Analog input settings", wait=1.0),
    _q("SSAUDIO ?", "Signal and setup", "Audio settings", wait=1.0),
    _q("SSTTR ?", "Signal and setup", "Tactile transducer"),
    _q("BTTX ?", "Signal and setup", "Bluetooth transmitter"),
    _q("ILB ?", "Signal and setup", "Unknown event seen on 2026 firmware"),
    _q("RM ?", "Signal and setup", "Remote maintenance mode"),
]

QUICK = {
    "PW?", "ZM?", "Z2?", "Z3?", "MNZST ?", "MV?", "MU?", "CV?", "SI?", "MS?",
    "OPSMLALL ?", "PSMULTEQ: ?", "PSDYNEQ ?", "PSBAS ?", "PSTRE ?", "VSSCH ?",
    "PV?", "TPAN?", "NSE", "SSINF ?", "SSFUN ?",
}

# The only commands without "?" that are sent: requests for screen text.
READ_ONLY_REQUESTS = {"NSE", "NSA", "NSH"}

HEOS_PER_PLAYER = [
    "player/get_player_info",
    "player/get_now_playing_media",
    "player/get_play_state",
    "player/get_play_mode",
    "player/get_volume",
    "player/get_mute",
    "player/get_quickselects",
    "player/check_update",
]
HEOS_GLOBAL = ["system/heart_beat", "group/get_groups", "browse/get_music_sources"]

# goform status pages, read on port 80 and on port 8080.
GOFORM_PAGES = [
    "Deviceinfo.xml",
    "formMainZone_MainZoneXml.xml",
    "formMainZone_MainZoneXmlStatus.xml",
    "formMainZone_MainZoneXmlStatusLite.xml",
    "formZone2_Zone2XmlStatus.xml",
    "formZone2_Zone2XmlStatusLite.xml",
    "formZone3_Zone3XmlStatus.xml",
    "formZone3_Zone3XmlStatusLite.xml",
    "formNetAudio_StatusXml.xml",
    "formTuner_TunerXml.xml",
]

# Read-only AppCommand.xml commands (as the denonavr library sends them, at
# most five per request). Only names starting with "Get" are ever sent.
APPCOMMANDS = [
    "GetFriendlyName", "GetAllZonePowerStatus", "GetAllZoneSource",
    "GetAllZoneVolume", "GetAllZoneMuteStatus", "GetRenameSource",
    "GetDeletedSource", "GetSurroundModeStatus", "GetToneControl",
    "GetAutoStandby", "GetDimmer", "GetECO",
]
APPCOMMAND0300 = ("GetAudyssey", ["dynamiceq", "reflevoffset", "dynamicvol", "multeq"])

SETUP_PAGES = [
    "/ajax/globals/get_config?type=1",
    "/ajax/audio/get_config?type=1",
    # The pages Denon AVR Plus reads: graphic EQ (channel 0), the video
    # menus (HDMI setup, on-screen display, 4K format, TV format) and amp
    # assign.
    "/ajax/audio/get_config?type=10&opt1=0&opt2=0",
    "/ajax/video/get_config?type=1",
    "/ajax/video/get_config?type=3",
    "/ajax/video/get_config?type=7",
    "/ajax/video/get_config?type=8",
    "/ajax/video/get_config?type=9",
    "/ajax/speakers/get_config?type=1",
    "/ajax/speakers/get_config?type=2",
    "/ajax/control/get_config?type=1",
    "/ajax/control/get_config?type=2",
    "/ajax/info/get_config?type=1",
    "/ajax/info/get_config?type=3",
]

# The full scan of the setup interface: every section, type 1 to 20, read
# only. Shows what a model offers there beyond what Denon AVR Plus uses.
SETUP_SECTIONS = ["globals", "audio", "video", "speakers", "control", "info"]
SETUP_TYPES = range(1, 21)


def setup_scan(base: str) -> dict[str, dict[str, dict[str, object]]]:
    """GET every section and type of the setup interface; record what answers."""

    scan: dict[str, dict[str, dict[str, object]]] = {}
    for section in SETUP_SECTIONS:
        scan[section] = {}
        for type_id in SETUP_TYPES:
            result = http_get(f"{base}/ajax/{section}/get_config?type={type_id}")
            body = str(result.get("body", ""))
            root = re.search(r"<([A-Za-z][\w.-]*)", re.sub(r"<\?.*?\?>", "", body))
            result["root"] = root.group(1) if root else None
            result["bytes"] = len(body)
            scan[section][str(type_id)] = result
        answered = sum(
            1 for r in scan[section].values() if r.get("status") == 200 and r["bytes"]
        )
        print(f"  {section:9s} {answered} of {len(SETUP_TYPES)} types answered")
    return scan


def zone_states(telnet: dict[str, object]) -> dict[str, str]:
    """Whether each zone was on during the probe, from the ZM?, Z2? and Z3? answers."""

    states: dict[str, str] = {}
    for zone, query, prefix in (("main", "ZM?", "ZM"), ("zone 2", "Z2?", "Z2"),
                                ("zone 3", "Z3?", "Z3")):
        entry = telnet.get(query)
        lines = entry.get("lines", []) if isinstance(entry, dict) else []
        if f"{prefix}ON" in lines:
            states[zone] = "on"
        elif f"{prefix}OFF" in lines:
            states[zone] = "off"
    return states


# Network helpers -----------------------------------------------------------


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def http_get(url: str) -> dict[str, object]:
    """GET a URL without following redirects; never raises."""

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # the receiver uses a self-signed cert

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=context), NoRedirect()
    )
    try:
        with opener.open(url, timeout=5) as response:
            body = response.read(300_000).decode("utf-8", errors="replace")
            return {"status": response.status, "body": body}
    except urllib.error.HTTPError as err:
        return {"status": err.code, "location": err.headers.get("Location")}
    except (urllib.error.URLError, OSError) as err:
        return {"error": str(getattr(err, "reason", err))}


def appcommand_body(names: list[str]) -> bytes:
    """A read-only AppCommand.xml request; refuses anything but Get commands."""

    for name in names:
        if not name.startswith("Get"):
            raise ValueError(f"refusing to send a non-read AppCommand: {name}")
    cmds = "".join(f'<cmd id="1">{name}</cmd>' for name in names)
    return f'<?xml version="1.0" encoding="utf-8"?><tx>{cmds}</tx>'.encode()


def appcommand0300_body(name: str, params: list[str]) -> bytes:
    if not name.startswith("Get"):
        raise ValueError(f"refusing to send a non-read AppCommand: {name}")
    items = "".join(f'<param name="{param}"></param>' for param in params)
    return (
        f'<?xml version="1.0" encoding="utf-8"?><tx><cmd id="3"><name>{name}</name>'
        f"<list>{items}</list></cmd></tx>"
    ).encode()


def http_post(url: str, body: bytes) -> dict[str, object]:
    """POST a read-only AppCommand request; never raises."""

    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "text/xml"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            text = response.read(300_000).decode("utf-8", errors="replace")
            return {"status": response.status, "body": text}
    except urllib.error.HTTPError as err:
        return {"status": err.code}
    except (urllib.error.URLError, OSError) as err:
        return {"error": str(getattr(err, "reason", err))}


def ssdp_search(host: str, wait: float = 3.0) -> list[dict[str, str]]:
    """The receiver's own answers to an SSDP search (what discovery sees)."""

    found: list[dict[str, str]] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.settimeout(0.3)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        for target in ("ssdp:all", "upnp:rootdevice"):
            message = (
                "M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {target}\r\n\r\n'
            )
            sock.sendto(message.encode(), ("239.255.255.250", 1900))
    except OSError as err:
        return [{"error": str(err)}]
    end = time.monotonic() + wait
    seen: set[str] = set()
    while time.monotonic() < end:
        try:
            data, (address, _) = sock.recvfrom(65536)
        except TimeoutError:
            continue
        except OSError:
            break
        if address != host:
            continue
        headers: dict[str, str] = {}
        for line in data.decode("utf-8", errors="replace").split("\r\n")[1:]:
            key, sep, value = line.partition(":")
            if sep:
                headers[key.strip().upper()] = value.strip()
        key = headers.get("USN", "") + headers.get("ST", "")
        if key not in seen:
            seen.add(key)
            found.append(headers)
    sock.close()
    return found


def reply_prefix(command: str) -> str:
    """The text a reply to this query starts with (the command without '?')."""

    return command.rstrip("?").rstrip()


def classify(command: str, lines: list[str]) -> str:
    prefix = reply_prefix(command)
    if any(line.startswith(prefix) for line in lines):
        return "answered"
    if any(line in ("?", "ERR", "ERROR") for line in lines):
        return "rejected"
    if lines:
        return "other lines only"
    return "no reply"


class Telnet:
    """One telnet session; sends a query and collects what comes back."""

    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 23), timeout=3)
        self.sock.settimeout(0.05)
        self.buffer = b""
        self.events: list[str] = []
        # When each line arrived, to measure how fast an answer comes.
        self.arrivals: list[tuple[float, str]] = []
        self.last_ms: int | None = None

    def close(self) -> None:
        self.sock.close()

    def _read(self, until: float, done: Callable[[list[str]], bool] | None) -> list[str]:
        lines: list[str] = []
        while time.monotonic() < until:
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError:
                chunk = b""
            except OSError:
                break
            if chunk:
                now = time.monotonic()
                self.buffer += chunk
                *parts, self.buffer = self.buffer.split(b"\r")
                new = [p.decode("utf-8", errors="replace").strip() for p in parts if p.strip()]
                self.arrivals += [(now, line) for line in new]
                lines += new
                if done is not None and done(lines):
                    break
        return lines

    def query(self, query: Query) -> list[str]:
        if not query.command.endswith("?") and query.command not in READ_ONLY_REQUESTS:
            raise ValueError(f"refusing to send a non-query command: {query.command}")
        self.events += self._read(time.monotonic() + 0.05, None)
        self.arrivals = []
        self.sock.sendall(query.command.encode("ascii") + b"\r")
        prefix = reply_prefix(query.command)

        def done(lines: list[str]) -> bool:
            if query.ends_with:
                return any(line.endswith(query.ends_with) for line in lines)
            return any(line.startswith(prefix) for line in lines)

        sent = time.monotonic()
        lines = self._read(sent + query.wait, done)
        # Time to the first line of the answer, for the pacing of Denon AVR Plus.
        answer = next((at for at, line in self.arrivals if line.startswith(prefix)), None)
        self.last_ms = None if answer is None else round((answer - sent) * 1000)
        if not query.ends_with and any(line.startswith(prefix) for line in lines):
            lines += self._read(time.monotonic() + 0.15, None)  # companion lines
        return lines

    def burst(self, commands: list[str], spacing: float, wait: float = 1.5) -> list[str]:
        """Send queries close together without waiting; return what came back."""

        for command in commands:
            if not command.endswith("?"):
                raise ValueError(f"refusing to send a non-query command: {command}")
        self.events += self._read(time.monotonic() + 0.3, None)
        for command in commands:
            self.sock.sendall(command.encode("ascii") + b"\r")
            if spacing:
                time.sleep(spacing)
        return self._read(time.monotonic() + wait, None)


# Probes --------------------------------------------------------------------


# Queries every receiver answers, for the spacing test.
BURST = ["PW?", "MV?", "MU?", "SI?", "MS?", "ZM?"]
BURST_SPACINGS = (0.0, 0.02, 0.05, 0.08)


def spacing_test(telnet: Telnet) -> list[dict[str, object]]:
    """Which of six queries sent close together get an answer."""

    results = []
    for spacing in BURST_SPACINGS:
        lines = telnet.burst(BURST, spacing)
        answered = [c for c in BURST if any(line.startswith(reply_prefix(c)) for line in lines)]
        results.append({"spacing_ms": round(spacing * 1000), "sent": len(BURST),
                        "answered": len(answered), "lines": lines})
        print(f"  {round(spacing * 1000):3d} ms apart: {len(answered)} of {len(BURST)} answered")
    return results


def second_session_test(host: str, first: Telnet) -> dict[str, object]:
    """Whether the receiver accepts and answers a second telnet session."""

    result: dict[str, object] = {}
    try:
        second = Telnet(host)
    except OSError as err:
        return {"second_connects": False, "error": str(err)}
    result["second_connects"] = True
    try:
        lines = second.query(_q("PW?", "", ""))
        result["second_answers"] = any(line.startswith("PW") for line in lines)
        lines = first.query(_q("PW?", "", ""))
        result["first_still_answers"] = any(line.startswith("PW") for line in lines)
    finally:
        second.close()
    print(f"  second session: {result}")
    return result


def probe_telnet(host: str, catalogue: list[Query]) -> dict[str, object]:
    try:
        telnet = Telnet(host)
    except OSError as err:
        return {"error": f"telnet connection failed: {err}"}
    results: dict[str, object] = {}
    try:
        first = telnet.query(catalogue[0])
        if not first:
            return {
                "error": "connected, but no reply to PW?. Another program probably "
                "holds the receiver's only telnet session."
            }
        results[catalogue[0].command] = {
            "result": classify("PW?", first), "lines": first, "ms": telnet.last_ms
        }
        if any(line.startswith("PWSTANDBY") for line in first):
            print("  The receiver is in standby: many answers will be missing. "
                  "Switch it on and run the probe again.")
        for query in catalogue[1:]:
            lines = telnet.query(query)
            result = classify(query.command, lines)
            results[query.command] = {"result": result, "lines": lines, "ms": telnet.last_ms}
            print(f"  {query.command:16s} {result}")
        print("Telnet spacing")
        results["_spacing"] = spacing_test(telnet)
        print("Telnet sessions")
        results["_sessions"] = second_session_test(host, telnet)
        results["_unrelated_events"] = telnet.events
    finally:
        telnet.close()
    return results


def probe_heos(host: str) -> dict[str, object]:
    results: dict[str, object] = {}
    try:
        sock = socket.create_connection((host, 1255), timeout=3)
    except OSError as err:
        return {"error": str(err)}
    sock.settimeout(3)
    buffer = b""

    def ask(command: str) -> object:
        nonlocal buffer
        sock.sendall(f"heos://{command}\r\n".encode())
        while b"\r\n" not in buffer:
            try:
                chunk = sock.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buffer += chunk
        line, _, buffer = buffer.partition(b"\r\n")
        try:
            return json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            return line.decode("utf-8", errors="replace")

    try:
        players = ask("player/get_players")
        results["player/get_players"] = players
        payload = players.get("payload", []) if isinstance(players, dict) else []
        for index, player in enumerate(payload):
            for command in HEOS_PER_PLAYER:
                results[f"{command} (player {index})"] = ask(f"{command}?pid={player.get('pid')}")
        for command in HEOS_GLOBAL:
            results[command] = ask(command)
    finally:
        sock.close()
    return results


# Anonymisation ----------------------------------------------------------------


def find_identity(
    texts: list[str], host: str
) -> tuple[set[str], set[str], dict[str, str], str]:
    """MACs, serial numbers, names (with their replacement) and the model."""

    macs: set[str] = set()
    serials: set[str] = set()
    names: dict[str, str] = {}
    model = "receiver"
    for text in texts:
        macs.update(re.findall(r"<MacAddress>\s*([0-9A-Fa-f:\-]{12,17})\s*<", text))
        serials.update(re.findall(r"<(?:SerialNumber|serialNumber)>\s*([^<\s]+)\s*<", text))
        serials.update(re.findall(r'"serial"\s*:\s*"([^"]+)"', text))
        for name in re.findall(r"<[Ff]riendlyName>\s*(?:<value>)?\s*([^<]+?)\s*<", text):
            names[name] = "Denon AVR"
        for name in re.findall(r"NSFRN ([^\"\r\n]+)", text):
            names[name.strip()] = "Denon AVR"
        found = re.findall(r"<ModelName>\s*\*?([^<\s]+)\s*<", text)
        if found and model == "receiver":
            model = found[0]
    # HEOS: the receiver's own player and every other speaker and group in the
    # home carry the names their owner gave them ("Kitchen").
    for text in texts:
        try:
            data = json.loads(text)
        except ValueError:
            continue
        players = data.get("player/get_players", {}) if isinstance(data, dict) else {}
        payload = players.get("payload", []) if isinstance(players, dict) else []
        for index, player in enumerate(payload):
            if isinstance(player, dict) and player.get("name"):
                own = player.get("ip") == host
                names[str(player["name"])] = "Denon AVR" if own else f"HEOS speaker {index + 1}"
        groups = data.get("group/get_groups", {}) if isinstance(data, dict) else {}
        for index, group in enumerate(groups.get("payload", []) if isinstance(groups, dict) else []):
            if isinstance(group, dict) and group.get("name"):
                names[str(group["name"])] = f"HEOS group {index + 1}"
    names.pop("", None)
    return macs, serials, names, model


_ANY_MAC = re.compile(r"(?i)(?<![0-9a-f:\-])(?:[0-9a-f]{2}[:\-]){5}[0-9a-f]{2}(?![0-9a-f:\-])")
_IPV6 = re.compile(r"(?i)\b(?:fe80|fd[0-9a-f]{2}|2[0-9a-f]{3}):[0-9a-f:]{2,}[0-9a-f]\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WIFI = re.compile(r"(?i)(<(\w*ssid\w*)>)([^<]+)(</\2>)")
_WIFI_JSON = re.compile(r'(?i)("\w*ssid\w*"\s*:\s*")([^"]+)(")')
_ACCOUNT_JSON = re.compile(r'(?i)("(?:service_username|username|user)"\s*:\s*")([^"]+)(")')


def anonymise(
    text: str, host: str, macs: set[str], serials: set[str], names: dict[str, str]
) -> str:
    text = text.replace(host, PLACEHOLDER_HOST)
    for mac in macs:
        plain = re.sub(r"[^0-9A-Fa-f]", "", mac).lower()
        for form in (plain, ":".join(plain[i:i + 2] for i in range(0, 12, 2)),
                     "-".join(plain[i:i + 2] for i in range(0, 12, 2))):
            text = re.sub(re.escape(form), PLACEHOLDER_MAC, text, flags=re.IGNORECASE)
    # Any other MAC, for example the Wi-Fi one on the setup pages.
    text = _ANY_MAC.sub(PLACEHOLDER_MAC, text)
    for serial in serials:
        text = text.replace(serial, PLACEHOLDER_SERIAL)
    for name in sorted(names, key=len, reverse=True):
        text = text.replace(name, names[name])
    text = _WIFI.sub(lambda m: m.group(1) + PLACEHOLDER_WIFI + m.group(4), text)
    text = _WIFI_JSON.sub(lambda m: m.group(1) + PLACEHOLDER_WIFI + m.group(3), text)
    text = _ACCOUNT_JSON.sub(lambda m: m.group(1) + PLACEHOLDER_ACCOUNT + m.group(3), text)
    text = _EMAIL.sub(PLACEHOLDER_ACCOUNT, text)
    text = _IPV6.sub(PLACEHOLDER_IPV6, text)
    return re.sub(
        r"\b(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}\b",
        PLACEHOLDER_OTHER_HOST,
        text,
    )


# Summary ---------------------------------------------------------------------


def comm_api_version(texts: list[str]) -> str | None:
    for text in texts:
        found = re.search(r"<CommApiVers>\s*(\d+)\s*<", text)
        if found:
            return found.group(1)
    return None


def generation(version: str | None, ports: dict[int, bool]) -> str:
    if version is None:
        return "before 2013 (no CommApiVers)" if ports.get(80) else "unknown"
    if int(version) >= 301:
        return f"2016 or later (CommApiVers {version}): goform on port 8080"
    return f"2013 to 2015 (CommApiVers {version}): goform on port 80"


def summary_markdown(report: dict[str, object], catalogue: list[Query]) -> str:
    raw_ports = report["ports"]
    ports = {int(k): bool(v) for k, v in raw_ports.items()} if isinstance(raw_ports, dict) else {}
    telnet = report.get("telnet")
    telnet = telnet if isinstance(telnet, dict) else {}
    lines = [
        f"# Probe of {report['model']}",
        "",
        f"Recorded {report['recorded']} with probe_receiver.py version {VERSION}.",
        f"Generation: {report['generation']}.",
        "",
    ]
    if report.get("power") == "standby":
        lines += ["**The receiver was in standby.** Many answers exist only while it is "
                  "on; run the probe again with the receiver switched on.", ""]
    zones = report.get("zones")
    if isinstance(zones, dict) and zones:
        lines += ["Zones during the probe: "
                  + ", ".join(f"{zone} {state}" for zone, state in zones.items())
                  + ". A zone that is off answers fewer queries.", ""]
    lines += [
        "## Network interfaces",
        "",
        "| Port | Interface | Open |",
        "|---|---|---|",
    ]
    lines += [f"| {port} | {what} | {'yes' if ports.get(port) else 'no'} |"
              for port, what in PORTS.items()]
    heos = report.get("heos")
    if isinstance(heos, dict) and isinstance(heos.get("player/get_players"), dict):
        count = len(heos["player/get_players"].get("payload", []))
        lines += ["", f"HEOS: {count} player(s) in the HEOS system."]
    ssdp = report.get("ssdp")
    if isinstance(ssdp, list):
        servers = sorted({h.get("SERVER", "") for h in ssdp if isinstance(h, dict)} - {""})
        lines += ["", f"SSDP: {len(ssdp)} answer(s) from the receiver"
                  + (f" ({'; '.join(servers)})" if servers else "") + "."]
    http = report.get("http")
    if isinstance(http, dict) and http:
        lines += ["", "## Web pages", "", "| Page | Result |", "|---|---|"]
        for page, entry in http.items():
            if isinstance(entry, dict):
                state = entry.get("status", entry.get("error", "?"))
                lines.append(f"| `{page}` | {state} |")
    if "error" in telnet:
        lines += ["", f"Telnet: {telnet['error']}"]
        return "\n".join(lines) + "\n"

    lines += ["", "## Telnet capabilities"]
    group = None
    answered = 0
    for query in catalogue:
        entry = telnet.get(query.command)
        if not isinstance(entry, dict):
            continue
        if query.group != group:
            group = query.group
            lines += ["", f"### {group}", "",
                      "| Capability | Query | Result | Reply |", "|---|---|---|---|"]
        replies: list[str] = entry["lines"]
        reply = "; ".join(replies[:3])
        if len(replies) > 3:
            reply += f"; … ({len(replies)} lines)"
        reply = reply.replace("|", "\\|")[:120]
        result = "yes" if entry["result"] == "answered" else entry["result"]
        answered += entry["result"] == "answered"
        lines.append(f"| {query.what} | `{query.command}` | {result} | {reply} |")
    lines += ["", f"{answered} of {len(catalogue)} queries answered."]

    scan = report.get("setup_scan")
    if isinstance(scan, dict):
        lines += ["", f"## Setup interface, all types (port {scan.get('port')})", "",
                  "Each cell: the size of the answer in bytes and its root element; "
                  "– means no answer.", "",
                  "| Type | " + " | ".join(SETUP_SECTIONS) + " |",
                  "|---|" + "---|" * len(SETUP_SECTIONS)]
        for type_id in SETUP_TYPES:
            cells = []
            for section in SETUP_SECTIONS:
                entry = scan.get(section, {}).get(str(type_id), {})
                if entry.get("status") == 200 and entry.get("bytes"):
                    cells.append(f"{entry['bytes']} `{entry.get('root')}`")
                else:
                    cells.append("–")
            lines.append(f"| {type_id} | " + " | ".join(cells) + " |")

    times = sorted(e["ms"] for e in telnet.values()
                   if isinstance(e, dict) and isinstance(e.get("ms"), int))
    if times:
        lines += ["", "## Timing", "",
                  f"Reply time over {len(times)} answered queries: fastest {times[0]} ms, "
                  f"median {times[len(times) // 2]} ms, slowest {times[-1]} ms."]
    spacing = telnet.get("_spacing")
    if isinstance(spacing, list) and spacing:
        lines += ["", "Six queries sent close together, without waiting for answers:", "",
                  "| Gap | Answered |", "|---|---|"]
        lines += [f"| {s['spacing_ms']} ms | {s['answered']} of {s['sent']} |" for s in spacing]
    sessions = telnet.get("_sessions")
    if isinstance(sessions, dict):
        if not sessions.get("second_connects"):
            verdict = "refuses a second telnet session"
        elif sessions.get("second_answers") and sessions.get("first_still_answers"):
            verdict = "accepts a second telnet session and answers both"
        elif sessions.get("second_answers"):
            verdict = "accepts a second session, which takes over from the first"
        else:
            verdict = "accepts a second connection but does not answer it"
        lines += ["", "## Telnet sessions", "", f"The receiver {verdict}."]
    return "\n".join(lines) + "\n"


# Main ------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("host", help="the receiver's IP address")
    parser.add_argument("--quick", action="store_true", help="only the core queries (about 20 s)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent,
                        help="folder for the result files (default: next to the script)")
    args = parser.parse_args()
    host: str = args.host
    catalogue = [q for q in CATALOGUE if q.command in QUICK] if args.quick else CATALOGUE

    print(f"Probing {host} (read-only) ...")
    report: dict[str, object] = {
        "tool": f"Denon AVR Plus probe_receiver.py {VERSION}",
        "recorded": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

    print("Ports")
    ports = {port: port_open(host, port) for port in PORTS}
    report["ports"] = {str(port): state for port, state in ports.items()}

    print("Web pages")
    http: dict[str, object] = {}
    for port in (80, 8080):
        if not ports.get(port):
            continue
        base = f"http://{host}" if port == 80 else f"http://{host}:{port}"
        http[f"{port} /"] = http_get(f"{base}/")
        for page in GOFORM_PAGES:
            http[f"{port} /goform/{page}"] = http_get(f"{base}/goform/{page}")
        if port == 8080:
            http["8080 /description.xml"] = http_get(f"{base}/description.xml")
        for start in range(0, len(APPCOMMANDS), 5):
            names = APPCOMMANDS[start:start + 5]
            http[f"{port} AppCommand.xml {', '.join(names)}"] = http_post(
                f"{base}/goform/AppCommand.xml", appcommand_body(names))
        name, params = APPCOMMAND0300
        http[f"{port} AppCommand0300.xml {name}"] = http_post(
            f"{base}/goform/AppCommand0300.xml", appcommand0300_body(name, params))
    if ports.get(60006):
        http["60006 aios_device.xml"] = http_get(
            f"http://{host}:60006/upnp/desc/aios_device/aios_device.xml")
    scanned = False
    for port, scheme in ((10443, "https"), (11080, "http")):
        if ports.get(port):
            for page in SETUP_PAGES:
                http[f"{port} {page}"] = http_get(f"{scheme}://{host}:{port}{page}")
            if not scanned and not args.quick:
                print(f"Setup interface, all types (port {port})")
                report["setup_scan"] = {"port": port, **setup_scan(f"{scheme}://{host}:{port}")}
                scanned = True
    report["http"] = http

    print("SSDP discovery")
    ssdp = ssdp_search(host)
    report["ssdp"] = ssdp
    for location in sorted({h.get("LOCATION", "") for h in ssdp if isinstance(h, dict)}):
        if location.startswith(f"http://{host}:") or location.startswith(f"http://{host}/"):
            http[f"SSDP {location.replace(host, PLACEHOLDER_HOST)}"] = http_get(location)

    if ports.get(1255):
        print("HEOS")
        report["heos"] = probe_heos(host)

    print("Telnet")
    time.sleep(1)  # the port check above briefly took the only telnet session
    report["telnet"] = probe_telnet(host, catalogue) if ports.get(23) else {"error": "port 23 closed"}
    if isinstance(report["telnet"], dict):
        report["zones"] = zone_states(report["telnet"])
    power = report["telnet"].get("PW?") if isinstance(report["telnet"], dict) else None
    if isinstance(power, dict):
        report["power"] = (
            "standby" if any(str(line).startswith("PWSTANDBY") for line in power["lines"])
            else "on"
        )

    texts = [str(v.get("body", "")) for v in http.values() if isinstance(v, dict)]
    scan = report.get("setup_scan")
    if isinstance(scan, dict):
        texts += [str(r.get("body", "")) for s in scan.values() if isinstance(s, dict)
                  for r in s.values() if isinstance(r, dict)]
    texts.append(json.dumps(report.get("heos", {})))
    texts.append(json.dumps(report.get("telnet", {})))
    macs, serials, names, model = find_identity(texts, host)
    version = comm_api_version(texts)
    report["model"] = model
    report["comm_api_version"] = version
    report["generation"] = generation(version, ports)

    raw = json.dumps(report, indent=2, ensure_ascii=False)
    summary = summary_markdown(report, catalogue)
    args.out.mkdir(parents=True, exist_ok=True)
    json_path = args.out / f"avr-probe-{model}.json"
    md_path = args.out / f"avr-probe-{model}.md"
    json_path.write_text(anonymise(raw, host, macs, serials, names), encoding="utf-8")
    md_path.write_text(anonymise(summary, host, macs, serials, names), encoding="utf-8")
    print(f"\nWritten:\n  {md_path}\n  {json_path}")
    print("Please read them before sharing; now-playing and preset lines show what you listen to.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
