"""The capability check: the read-only telnet queries of tools/probe_receiver.py.

Run from inside the integration (options, repair, or after setting up a model
not checked before), over the integration's own telnet connection, so it does
not need a second session. Only queries: every entry ends with "?", and the
receiver changes nothing for them. The list is the probe script's telnet
catalogue without its three screen-text requests (NSE, NSA, NSH);
tests/test_avr_capability_check.py fails when the two drift apart.

The result goes into the diagnostics file, which the user attaches to a
device-support issue.
"""

from __future__ import annotations

import re

#: Bump when the queries or the result format change.
CHECK_VERSION = 1

#: Models whose capabilities have been checked on a receiver. Others get the
#: repair that asks for a check. Add a model once its check has been reviewed.
KNOWN_MODELS = frozenset({"AVR-X3600H"})

#: (query, group) in the probe script's order.
QUERIES: tuple[tuple[str, str], ...] = (
    ("PW?", "Power and zones"),
    ("ZM?", "Power and zones"),
    ("Z2?", "Power and zones"),
    ("Z2MU?", "Power and zones"),
    ("Z2CV?", "Power and zones"),
    ("Z2CS?", "Power and zones"),
    ("Z2HPF?", "Power and zones"),
    ("Z2PSBAS ?", "Power and zones"),
    ("Z2PSTRE ?", "Power and zones"),
    ("Z2HDA?", "Power and zones"),
    ("Z2SLP?", "Power and zones"),
    ("Z2STBY?", "Power and zones"),
    ("Z2QUICK ?", "Power and zones"),
    ("Z3?", "Power and zones"),
    ("Z3MU?", "Power and zones"),
    ("Z3CV?", "Power and zones"),
    ("Z3SLP?", "Power and zones"),
    ("Z3STBY?", "Power and zones"),
    ("Z3QUICK ?", "Power and zones"),
    ("MNZST ?", "Power and zones"),
    ("SR?", "Power and zones"),
    ("MV?", "Volume and input"),
    ("MU?", "Volume and input"),
    ("CV?", "Volume and input"),
    ("SI?", "Volume and input"),
    ("SV?", "Volume and input"),
    ("SD?", "Volume and input"),
    ("DC?", "Volume and input"),
    ("SLP?", "Volume and input"),
    ("SLP ?", "Volume and input"),
    ("STBY?", "Volume and input"),
    ("ECO?", "Volume and input"),
    ("DIM ?", "Volume and input"),
    ("TR?", "Volume and input"),
    ("MNMEN?", "Volume and input"),
    ("MS?", "Surround"),
    ("MSQUICK ?", "Surround"),
    ("MSSMART ?", "Surround"),
    ("OPSML ?", "Surround"),
    ("OPSMLALL ?", "Surround"),
    ("SSSMG ?", "Surround"),
    ("PSMODE: ?", "Surround"),
    ("SYSMI ?", "Surround"),
    ("SYSDA ?", "Surround"),
    ("PSTONE CTRL ?", "Audio"),
    ("PSBAS ?", "Audio"),
    ("PSTRE ?", "Audio"),
    ("PSDIL ?", "Audio"),
    ("PSSWL ?", "Audio"),
    ("PSCINEMA EQ. ?", "Audio"),
    ("PSLOM ?", "Audio"),
    ("PSSP: ?", "Audio"),
    ("PSMULTEQ: ?", "Audio"),
    ("PSDYNEQ ?", "Audio"),
    ("PSREFLEV ?", "Audio"),
    ("PSDYNVOL ?", "Audio"),
    ("PSDYNSET ?", "Audio"),
    ("PSLFC ?", "Audio"),
    ("PSCNTAMT ?", "Audio"),
    ("PSDSX ?", "Audio"),
    ("PSSTW ?", "Audio"),
    ("PSSTH ?", "Audio"),
    ("PSGEQ ?", "Audio"),
    ("PSHEQ ?", "Audio"),
    ("PSDRC ?", "Audio"),
    ("PSDCO ?", "Audio"),
    ("PSDIC ?", "Audio"),
    ("PSDEH ?", "Audio"),
    ("PSBSC ?", "Audio"),
    ("PSLFE ?", "Audio"),
    ("PSLFL ?", "Audio"),
    ("PSEFF ?", "Audio"),
    ("PSDEL ?", "Audio"),
    ("PSPAN ?", "Audio"),
    ("PSDIM ?", "Audio"),
    ("PSCEN ?", "Audio"),
    ("PSCEI ?", "Audio"),
    ("PSCEG ?", "Audio"),
    ("PSCES ?", "Audio"),
    ("PSNEURAL ?", "Audio"),
    ("PSSWR ?", "Audio"),
    ("PSRSZ ?", "Audio"),
    ("PSDELAY ?", "Audio"),
    ("PSRSTR ?", "Audio"),
    ("PSFRONT?", "Audio"),
    ("PSSB: ?", "Audio"),
    ("PSATT ?", "Audio"),
    ("PSPHG ?", "Audio"),
    ("PSSPV ?", "Audio"),
    ("PSAUROPR ?", "Audio"),
    ("PSAUROST ?", "Audio"),
    ("PSAUROMODE ?", "Audio"),
    ("PSDACFIL ?", "Audio"),
    ("PSMDAX ?", "Audio"),
    ("PSIMAX ?", "Audio"),
    ("PSIMAXAUD ?", "Audio"),
    ("PSIMAXHPF ?", "Audio"),
    ("PSIMAXLPF ?", "Audio"),
    ("PSIMAXSWM ?", "Audio"),
    ("PSIMAXSWO ?", "Audio"),
    ("PSDIRAC ?", "Audio"),
    ("SPPR ?", "Audio"),
    ("VSASP ?", "Video"),
    ("VSMONI ?", "Video"),
    ("VSSC ?", "Video"),
    ("VSSCH ?", "Video"),
    ("VSAUDIO ?", "Video"),
    ("VSVPM ?", "Video"),
    ("VSVST ?", "Video"),
    ("PV?", "Video"),
    ("PVCN ?", "Video"),
    ("PVBR ?", "Video"),
    ("PVST ?", "Video"),
    ("PVHUE ?", "Video"),
    ("PVDNR ?", "Video"),
    ("PVENH ?", "Video"),
    ("SYSDVIN ?", "Video"),
    ("SYSDVOUT ?", "Video"),
    ("TFAN?", "Tuner"),
    ("TFANNAME?", "Tuner"),
    ("TPAN?", "Tuner"),
    ("TMAN?", "Tuner"),
    ("TFHD?", "Tuner"),
    ("TPHD?", "Tuner"),
    ("HD?", "Tuner"),
    ("NSFRN ?", "Network audio"),
    ("SSINF ?", "Signal and setup"),
    ("SSINFAISSIG ?", "Signal and setup"),
    ("SSINFAISFSV ?", "Signal and setup"),
    ("SSINFAISFOR ?", "Signal and setup"),
    ("SSOUTFAISFOR ?", "Signal and setup"),
    ("SSFUN ?", "Signal and setup"),
    ("SSSOD ?", "Signal and setup"),
    ("SSLEV ?", "Signal and setup"),
    ("SSSDE ?", "Signal and setup"),
    ("SSSPC ?", "Signal and setup"),
    ("SSCFR ?", "Signal and setup"),
    ("SSQSNZMA ?", "Signal and setup"),
    ("SSVCTZMA ?", "Signal and setup"),
    ("SSSWM ?", "Signal and setup"),
    ("SSLFL ?", "Signal and setup"),
    ("SSHOS ?", "Signal and setup"),
    ("SSHOSALS ?", "Signal and setup"),
    ("SSPAA ?", "Signal and setup"),
    ("SSALS ?", "Signal and setup"),
    ("SSANA ?", "Signal and setup"),
    ("SSAUDIO ?", "Signal and setup"),
    ("SSTTR ?", "Signal and setup"),
    ("BTTX ?", "Signal and setup"),
    ("ILB ?", "Signal and setup"),
    ("RM ?", "Signal and setup"),
)


def reply_prefix(command: str) -> str:
    """The text a reply to this query starts with (the query without "?")."""

    return command.rstrip("?").rstrip()


def classify(command: str, lines: list[str]) -> str:
    """answered, rejected, other lines only, or no reply; as the probe script."""

    prefix = reply_prefix(command)
    if any(line.startswith(prefix) for line in lines):
        return "answered"
    if any(line in ("?", "ERR", "ERROR") for line in lines):
        return "rejected"
    if lines:
        return "other lines only"
    return "no reply"


_MAC = re.compile(r"(?i)(?<![0-9a-f:\-])(?:[0-9a-f]{2}[:\-]){5}[0-9a-f]{2}(?![0-9a-f:\-])")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def anonymise(line: str) -> str:
    """Replace MACs, IPv4 addresses and e-mail addresses in a reply line."""

    line = _MAC.sub("aa:bb:cc:dd:ee:ff", line)
    line = _IPV4.sub("192.0.2.10", line)
    return _EMAIL.sub("user@example.com", line)
