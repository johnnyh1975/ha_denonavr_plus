"""The UPnP/AIOS description transport (port 60006) for the client library.

The receiver's UPnP (AIOS) device description carries the firmware version and
serial number, which the goform Deviceinfo document omits. It is read once at
setup. This module owns its own port and path; it does no parsing (the caller
parses the returned XML), it only fetches.
"""

from __future__ import annotations

import logging

import aiohttp

from ..const import HTTP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_PORT = 60006
_DESCRIPTION_PATH = "/upnp/desc/aios_device/aios_device.xml"
# Receivers before the AIOS module (AVR-X2000, goform on port 80) describe
# themselves here instead (denonavr library, const.DESCRIPTION_TYPES).
OLDER_PORT = 8080
OLDER_DESCRIPTION_PATH = "/description.xml"


class UpnpClient:
    """Minimal async client for the UPnP/AIOS device description (port 60006)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = _PORT,
        path: str = _DESCRIPTION_PATH,
    ) -> None:
        self._session = session
        self._url = f"http://{host}:{port}{path}"

    async def async_get_description(self) -> str | None:
        """Fetch the UPnP device description XML, or None on failure."""

        try:
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            async with self._session.get(self._url, timeout=timeout) as response:
                if response.status != 200:
                    _LOGGER.debug(
                        "GET %s returned HTTP %s", self._url, response.status
                    )
                    return None
                return await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            _LOGGER.debug("GET %s failed: %s", self._url, err)
            return None
