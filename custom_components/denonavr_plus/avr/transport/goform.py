"""The goform HTTP transport (port 8080, or 80 on older receivers).

The goform HTTP API is used for two things only:

* Discovery: fetch the Deviceinfo XML once at setup so the receiver can describe
  its identity and capabilities.
* Reconciliation: poll the compact StatusLite endpoints so the integration can
  confirm the receiver is reachable and recover core state if the telnet push
  channel is temporarily down.

The heavy lifting (control and real time updates) is done over telnet; this is
the stateless safety net. This module owns its own port and discovery path; no
other transport shares them.

Receivers from 2016 on (CommApiVers 0301) serve the API on port 8080. Older
ones (CommApiVers 0300 and below, or none at all) serve it on port 80. The port
is chosen from what the receiver reports, never by falling back after a failed
attempt: newer firmware redirects port 80 to HTTPS, and a newer receiver that
is still waking up must not be stored on the wrong port.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import aiohttp

from ..const import HTTP_TIMEOUT
from ..errors import UnsupportedReceiver

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8080
LEGACY_PORT = 80
_DEVICEINFO_PATH = "/goform/Deviceinfo.xml"
# Takes a telnet command as its query string (docs/protocol/02, "HTTP").
_COMMAND_PATH = "/goform/formiPhoneAppDirect.xml"
# CommApiVers from which the receiver serves goform on port 8080 (2016 on).
_PORT_8080_FROM = 301
_COMMAPI_RE = re.compile(r"<CommApiVers>\s*(\d+)\s*</CommApiVers>")
# The answer of a 2010-2012 receiver, which has no Deviceinfo.xml (recorded
# AVR-1912, AVR-2312CI, AVR-3311CI, AVR-3312, AVR-4810, M-CR603).
_NO_DEVICEINFO = "Deviceinfo.xml is not defined"


def _comm_api_version(text: str) -> int | None:
    match = _COMMAPI_RE.search(text)
    return int(match.group(1)) if match else None


async def async_detect_port(
    session: aiohttp.ClientSession, host: str
) -> tuple[int, str]:
    """Find the goform port from what the receiver reports.

    Returns the port and the Deviceinfo document read from it. Raises
    ConnectionError when no port gives an answer that fits: port 80 only
    counts for a receiver that reports an older CommApiVers (or none), so a
    newer receiver whose port 8080 is not up yet is retried later instead.
    Raises UnsupportedReceiver when port 80 says it has no Deviceinfo.xml
    (receivers from 2010 to 2012).
    """

    text = await GoformClient(session, host, DEFAULT_PORT).async_get_device_info()
    if text and "<Device_Info" in text:
        return DEFAULT_PORT, text
    text = await GoformClient(session, host, LEGACY_PORT).async_get_device_info()
    if text and "<Device_Info" in text:
        version = _comm_api_version(text)
        if version is None or version < _PORT_8080_FROM:
            return LEGACY_PORT, text
        _LOGGER.debug(
            "%s answers on port 80 with CommApiVers %s; waiting for port 8080",
            host,
            version,
        )
    elif text and _NO_DEVICEINFO in text:
        raise UnsupportedReceiver(f"{host} has no Deviceinfo.xml (2010-2012 generation)")
    raise ConnectionError(f"No Deviceinfo response from {host}")


class GoformClient:
    """Minimal async client for the goform HTTP API."""

    def __init__(
        self, session: aiohttp.ClientSession, host: str, port: int = DEFAULT_PORT
    ) -> None:
        self._session = session
        self._base = f"http://{host}:{port}"
        self.port = port

    async def async_get_device_info(self) -> str | None:
        """Fetch the raw Deviceinfo XML, or None on failure."""

        return await self._get(_DEVICEINFO_PATH)

    async def async_get_status(self, path: str) -> dict[str, object] | None:
        """Fetch and parse the StatusLite snapshot at the given path.

        Returns a dict with any of the keys 'power', 'source', 'volume_db' and
        'muted', or None when the endpoint is unavailable.
        """

        text = await self._get(path)
        if text is None:
            return None
        return self._parse_status(text)

    async def async_send_command(self, command: str) -> bool:
        """Send a telnet command over HTTP; True when the receiver accepted it.

        The fallback while the telnet link is down. Spaces are sent as %20,
        as in the commands of the denonavr library.
        """

        return await self._get(f"{_COMMAND_PATH}?{quote(command, safe='')}") is not None

    async def _get(self, path: str) -> str | None:
        """Perform a GET against the goform base and return the body text."""

        url = f"{self._base}{path}"
        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            # Never follow a redirect: newer firmware sends port 80 to HTTPS.
            async with self._session.get(
                url, timeout=timeout, allow_redirects=False
            ) as response:
                if response.status != 200:
                    _LOGGER.debug("GET %s returned HTTP %s", url, response.status)
                    return None
                return await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("GET %s failed: %s", url, err)
            return None

    @staticmethod
    def _parse_status(text: str) -> dict[str, object] | None:
        """Parse a StatusLite XML document into a partial state dict."""

        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None

        def value_of(tag: str) -> str | None:
            node = root.find(f"{tag}/value")
            return node.text.strip() if node is not None and node.text else None

        result: dict[str, object] = {}
        power = value_of("Power")
        if power is not None:
            result["power"] = power.upper() == "ON"
        display = value_of("VolumeDisplay")
        if display is not None:
            result["volume_display"] = display
        source = value_of("InputFuncSelect")
        if source is not None:
            result["source"] = source
        mute = value_of("Mute")
        if mute is not None:
            result["muted"] = mute.lower() == "on"
        volume = value_of("MasterVolume")
        if volume is not None:
            try:
                # StatusLite reports the volume relative to the 0 dB reference.
                result["volume_db"] = float(volume)
            except ValueError:
                pass
        return result
