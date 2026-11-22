"""Tests for avr/transport/goform.py: which port serves the receiver's goform API."""

from __future__ import annotations

import re

import pytest
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.denonavr_plus.avr.transport.goform import async_detect_port

from .conftest import deviceinfo

HOST = "192.0.2.10"
# The HTTP mock compares scheme, host and path but not the port, so match the
# full URL text instead (yarl drops the default port 80 from it).
URL_8080 = re.compile(r"^http://192\.0\.2\.10:8080/goform/Deviceinfo\.xml$")
URL_80 = re.compile(r"^http://192\.0\.2\.10/goform/Deviceinfo\.xml$")


async def test_2016_receiver_answers_on_8080(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL_8080, text=deviceinfo("AVC-X3700H-Deviceinfo-8080.xml"))
    port, text = await async_detect_port(async_get_clientsession(hass), HOST)
    assert port == 8080
    assert "AVC-X3700H" in text


@pytest.mark.parametrize(
    "recording",
    ["AVR-X2000-Deviceinfo.xml", "AVR-X1100W-Deviceinfo.xml", "AVR-X2100W-2-Deviceinfo.xml"],
)
async def test_older_receiver_answers_on_80(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, recording: str
) -> None:
    aioclient_mock.get(URL_8080, exc=ClientError())
    aioclient_mock.get(URL_80, text=deviceinfo(recording))
    port, _ = await async_detect_port(async_get_clientsession(hass), HOST)
    assert port == 80


async def test_waking_2016_receiver_is_not_stored_on_80(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A newer receiver whose 8080 is not up yet must not end up on port 80."""
    aioclient_mock.get(URL_8080, exc=ClientError())
    aioclient_mock.get(URL_80, text=deviceinfo("AVC-X3700H-Deviceinfo-8080.xml"))
    with pytest.raises(ConnectionError):
        await async_detect_port(async_get_clientsession(hass), HOST)


async def test_port_80_redirect_to_https_is_not_followed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Newer firmware redirects port 80 to HTTPS (official integration, #163759)."""
    aioclient_mock.get(URL_8080, exc=ClientError())
    aioclient_mock.get(URL_80, status=302, headers={"Location": f"https://{HOST}/"})
    with pytest.raises(ConnectionError):
        await async_detect_port(async_get_clientsession(hass), HOST)


async def test_unreachable_receiver_raises(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL_8080, exc=ClientError())
    aioclient_mock.get(URL_80, exc=ClientError())
    with pytest.raises(ConnectionError):
        await async_detect_port(async_get_clientsession(hass), HOST)
