"""Tests for avr/transport/upnp.py."""

from __future__ import annotations

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.denonavr_plus.avr.transport.upnp import UpnpClient

URL = "http://192.0.2.10:60006/upnp/desc/aios_device/aios_device.xml"


async def test_the_description_is_returned_as_text(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL, text="<root/>")
    client = UpnpClient(async_get_clientsession(hass), "192.0.2.10")
    assert await client.async_get_description() == "<root/>"


async def test_an_error_page_gives_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL, status=404)
    client = UpnpClient(async_get_clientsession(hass), "192.0.2.10")
    assert await client.async_get_description() is None


async def test_an_unreachable_receiver_gives_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(URL, exc=ClientError())
    client = UpnpClient(async_get_clientsession(hass), "192.0.2.10")
    assert await client.async_get_description() is None
