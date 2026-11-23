"""Setup-UI configuration transport (HTTPS port 10443, or HTTP 11080).

The receiver's setup web interface reads and writes audio configuration through
a small config API that is distinct from telnet and goform: a GET of
``/ajax/<section>/get_config?type=<id>`` returns an XML document, and a GET of
``/ajax/<section>/set_config?type=<id>&data=<url-encoded XML>`` applies a
change (the receiver rejects a POST with HTTP 400). Sections are ``audio`` and ``globals``; each config
``type`` selects one settings group (for example the graphic equaliser).

Neither the read nor the write enters a calibration session, so both are
non-disruptive and safe during playback. This transport owns that HTTPS endpoint
only; it does not know the meaning of any config id or XML tag (that lives with
the caller). The endpoint uses a self-signed certificate, so verification is
disabled. Every failure is swallowed and surfaced as ``None``/``False`` so a
setup-UI problem can never affect the telnet control path.

Newer firmware serves the same API on plain HTTP port 11080 with 10443
closed. The client tries 10443 first and 11080 when 10443 does not accept a
connection, and keeps the port that answered until it stops answering.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import aiohttp

from ..const import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

# The setup web interface listens on this HTTPS port with a self-signed cert;
# newer firmware serves it on plain HTTP 11080 instead.
DEFAULT_PORT = 10443
HTTP_PORT = 11080


class WebConfigClient:
    """Minimal async client for the setup-UI /ajax config API."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        self._session = session
        self._candidates = {
            DEFAULT_PORT: f"https://{host}:{DEFAULT_PORT}",
            HTTP_PORT: f"http://{host}:{HTTP_PORT}",
        }
        # The port that answered last; None until one has, or after it stopped.
        self._port: int | None = None

    @property
    def port(self) -> int | None:
        """The port the setup interface answered on last, for the diagnostics."""

        return self._port

    async def async_get(
        self, section: str, type_id: int, extra: dict[str, str] | None = None
    ) -> str | None:
        """Read one config group as raw XML text, or None on any failure.

        ``extra`` adds query parameters, e.g. ``{"opt1": "1", "opt2": "0"}`` to
        select the graphic-EQ channel to read.
        """

        params = {"type": type_id}
        if extra:
            params.update(extra)
        return await self._get(f"/ajax/{section}/get_config", params)

    async def async_set(self, section: str, type_id: int, xml: str) -> bool:
        """Apply an XML fragment for one config group. Return True on HTTP 200.

        Both reads and writes are GETs on this interface (the setup UI's jQuery
        call defaults to GET); a POST is rejected with HTTP 400. The XML is just
        another query parameter, so this shares the same request path as a read.
        """

        return await self._get(
            f"/ajax/{section}/set_config", {"type": type_id, "data": xml}
        ) is not None

    async def _get(self, path: str, params: dict[str, object]) -> str | None:
        """Issue a GET and return the body text, or None on error/non-200.

        The one request path for both reads and writes. Parameters are
        percent-encoded fully (encodeURIComponent-equivalent, ``safe=''``), which
        the setup interface expects for the XML payload and is harmless for the
        simple read parameters.
        """

        query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params.items())
        ports = [self._port] if self._port is not None else list(self._candidates)
        for port in ports:
            url = f"{self._candidates[port]}{path}?{query}"
            try:
                async with self._session.get(
                    url, ssl=False, timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
                ) as response:
                    self._port = port
                    if response.status != 200:
                        _LOGGER.debug("%s returned HTTP %s", path, response.status)
                        return None
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                _LOGGER.debug("GET %s on port %s failed: %s", path, port, err)
        # Nothing answered: try every port again next time.
        self._port = None
        return None
