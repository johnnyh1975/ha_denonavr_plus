#!/usr/bin/env python3
"""A simulated Denon receiver for trying the integration without one.

It serves what Denon AVR Plus needs to set a receiver up and run it:

* telnet (port 23): the answers of a recorded capability check
  (tests/fixtures/capability_check/), so every query gets what a real
  receiver said; a command that sets something changes the simulated state
  and is echoed, as a receiver does;
* HTTP (port 8080): a recorded Deviceinfo.xml (tests/fixtures/denonavr/),
  which names the model, zones and functions.

The setup API (HTTPS 10443) and HEOS (1255) are not simulated: the graphic
EQ, the video settings that need them and network audio are missing.

    python3 tools/simulate_receiver.py --host 127.0.0.10

Then add the receiver in Home Assistant with that address. Ports below 1024
need root; --telnet-port changes it (the integration always uses 23).
The recorded names of the sources are replaced by neutral ones.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK = ROOT / "tests" / "fixtures" / "capability_check" / "AVR-X2600H-3.139.175.json"
DEVICEINFO = ROOT / "tests" / "fixtures" / "denonavr" / "AVC-X3700H-Deviceinfo-8080.xml"

#: Source names as a user may have named them: neutral ones for screenshots.
RENAMED = {
    "Wohnen": "Media Player",
    "Quick Select 1": "TV",
    "Quick Select 2": "Music",
    "Quick Select 3": "Radio",
    "Quick Select 4": "Game",
}

#: Families whose value follows without a space (MV325, SLP120, ECOAUTO).
FAMILIES = (
    "PW", "ZM", "MU", "SI", "SV", "SD", "DC", "MS", "MV", "CV", "SLP", "STBY", "ECO",
    "VSASP", "VSMONI", "VSSCH", "VSVPM", "PV", "TFAN", "TPAN", "TMAN", "MSQUICK",
)
ZONE2 = re.compile(r"^Z2(.*)$")
_LOGGER = logging.getLogger("simulated receiver")


class Receiver:
    """The state of the simulated receiver: recorded answers, changed by writes."""

    def __init__(self, queries: dict[str, list[str]]) -> None:
        self.queries = queries
        #: Lines written since the start, by the setting they are about.
        self.changed: dict[str, str] = {}
        zone2 = queries.get("Z2?", [])
        self.zone2 = {
            "power": "ON" if "Z2ON" in zone2 else "OFF",
            "source": next((line[2:] for line in zone2[1:2]), "SOURCE"),
            "volume": next((line[2:] for line in zone2[2:3]), "30"),
            "mute": "OFF",
        }
        # It starts on, so the dashboard shows something.
        self.set("PWON")
        self.set("ZMON")

    @staticmethod
    def key(line: str) -> str:
        """Which setting a line is about: the part before its value."""
        if line.startswith("MSQUICK"):
            return "MSQUICK"
        if line.startswith(("MS", "SI")):
            return line[:2]
        if line.startswith("PS") and ":" in line:
            return line[: line.index(":") + 1]
        if " " in line:
            return line[: line.index(" ") + 1]
        for family in sorted(FAMILIES, key=len, reverse=True):
            if line.startswith(family):
                return family
        return line

    def set(self, line: str) -> list[str]:
        self.changed[self.key(line)] = line
        return [line]

    def answer(self, command: str) -> list[str]:
        """What the receiver sends back for one command."""
        command = command.strip()
        if match := ZONE2.match(command):
            return self.zone_two(match.group(1))
        if command.endswith("?"):
            return [self.changed.get(self.key(line), line) for line in self.queries.get(command, [])]
        if command in ("MVUP", "MVDOWN"):
            now = self.changed.get("MV") or next(iter(self.queries.get("MV?", [])), "MV50")
            step = 1 if command == "MVUP" else -1
            return self.set(f"MV{max(0, min(98, int(now[2:4]) + step)):02d}")
        return self.set(command)

    def zone_two(self, rest: str) -> list[str]:
        state = self.zone2
        if rest == "?":
            return [f"Z2{state['power']}", f"Z2{state['source']}", f"Z2{state['volume']}"]
        if rest in ("ON", "OFF"):
            state["power"] = rest
            return [f"Z2{rest}"]
        if rest == "MU?":
            return [f"Z2MU{state['mute']}"]
        if rest.startswith("MU"):
            state["mute"] = rest[2:]
            return [f"Z2{rest}"]
        if rest.isdigit():
            state["volume"] = rest
            return [f"Z2{rest}"]
        if rest in ("UP", "DOWN"):
            volume = int(state["volume"][:2]) + (1 if rest == "UP" else -1)
            state["volume"] = f"{volume:02d}"
            return [f"Z2{state['volume']}"]
        if rest.endswith("?"):
            query = f"Z2{rest}"
            return [self.changed.get(self.key(line), line) for line in self.queries.get(query, [])]
        if rest.startswith(("CV", "SLP", "STBY", "QUICK", "PS", "HPF", "CS", "HDA")):
            return self.set(f"Z2{rest}")
        # A source: Zone 2 switches to it and comes on.
        state["source"], state["power"] = rest, "ON"
        return ["Z2ON", f"Z2{rest}"]


def load(check: Path) -> dict[str, list[str]]:
    data = json.loads(check.read_text(encoding="utf-8"))
    queries: dict[str, list[str]] = {}
    for query, item in data["queries"].items():
        lines = []
        for line in item.get("lines", []):
            for old, new in RENAMED.items():
                # Names are padded to a fixed width: keep it.
                line = line.replace(old.ljust(len(new)), new.ljust(len(old)))
            lines.append(line)
        queries[query] = lines
    return queries


async def serve_telnet(receiver: Receiver, host: str, port: int) -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        _LOGGER.info("telnet connected")
        try:
            while True:
                raw = await reader.readuntil(b"\r")
                command = raw.decode("utf-8", "replace").strip("\r\n")
                lines = receiver.answer(command)
                _LOGGER.debug("%s -> %s", command, lines)
                for line in lines:
                    writer.write(line.encode() + b"\r")
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)


async def serve_http(deviceinfo: bytes, host: str, port: int) -> asyncio.Server:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        path = request.split(b" ", 2)[1].decode(errors="replace")
        if path.startswith("/goform/Deviceinfo.xml"):
            status, body = b"200 OK", deviceinfo
        elif path.startswith("/goform/formiPhoneAppDirect.xml"):
            status, body = b"200 OK", b""
        else:
            status, body = b"404 Not Found", b""
        writer.write(
            b"HTTP/1.1 " + status + b"\r\nContent-Type: text/xml\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(handle, host, port)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--telnet-port", type=int, default=23)
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--check", type=Path, default=CHECK)
    parser.add_argument("--deviceinfo", type=Path, default=DEVICEINFO)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)
    receiver = Receiver(load(args.check))
    servers = [
        await serve_telnet(receiver, args.host, args.telnet_port),
        await serve_http(args.deviceinfo.read_bytes(), args.host, args.http_port),
    ]
    _LOGGER.info("simulated receiver on %s (telnet %s, http %s)", args.host, args.telnet_port, args.http_port)
    await asyncio.gather(*(server.serve_forever() for server in servers))


if __name__ == "__main__":
    asyncio.run(main())
