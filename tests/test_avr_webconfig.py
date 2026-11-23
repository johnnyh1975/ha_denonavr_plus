"""Tests for avr/transport/webconfig.py: the setup interface on 10443 or 11080."""

from __future__ import annotations

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.denonavr_plus.avr.transport.webconfig import WebConfigClient

HOST = "192.0.2.10"
HTTPS = f"https://{HOST}:10443/ajax/audio/get_config"
HTTP = f"http://{HOST}:11080/ajax/audio/get_config"
XML = "<listGeq><value>0</value></listGeq>"


def _client(hass: HomeAssistant) -> WebConfigClient:
    return WebConfigClient(async_get_clientsession(hass), HOST)


async def test_reads_on_10443(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(HTTPS, text=XML)
    client = _client(hass)
    assert await client.async_get("audio", 10) == XML
    assert client.port == 10443


@pytest.mark.assumed(
    "that newer firmware serves the same /ajax API on plain HTTP 11080 (community "
    "reports from a 2022-era Marantz); a receiver with 10443 closed would settle it"
)
async def test_falls_back_to_11080_and_stays_there(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(HTTPS, exc=aiohttp.ClientConnectionError("refused"))
    aioclient_mock.get(HTTP, text=XML)
    client = _client(hass)
    assert await client.async_get("audio", 10) == XML
    assert client.port == 11080
    # Once found, 11080 is asked directly: one more call, not two.
    calls = aioclient_mock.call_count
    assert await client.async_get("audio", 10) == XML
    assert aioclient_mock.call_count == calls + 1


async def test_an_error_status_keeps_the_port(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refusal comes from a listening port; 11080 is not tried."""
    aioclient_mock.get(HTTPS, status=400)
    client = _client(hass)
    assert await client.async_get("audio", 10) is None
    assert client.port == 10443
    assert aioclient_mock.call_count == 1


async def test_nothing_answers(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    for path in ("get_config", "set_config"):
        aioclient_mock.get(HTTPS.replace("get_config", path), exc=TimeoutError())
        aioclient_mock.get(
            HTTP.replace("get_config", path), exc=aiohttp.ClientConnectionError("refused")
        )
    client = _client(hass)
    assert await client.async_get("audio", 10) is None
    assert not await client.async_set("audio", 10, XML)
    assert client.port is None


async def test_a_port_that_stops_answering_is_searched_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(HTTPS, text=XML)
    client = _client(hass)
    assert await client.async_get("audio", 10) == XML

    aioclient_mock.clear_requests()
    aioclient_mock.get(HTTPS, exc=aiohttp.ClientConnectionError("gone"))
    assert await client.async_get("audio", 10) is None
    assert client.port is None
    aioclient_mock.clear_requests()
    aioclient_mock.get(HTTPS, exc=aiohttp.ClientConnectionError("gone"))
    aioclient_mock.get(HTTP, text=XML)
    assert await client.async_get("audio", 10) == XML
    assert client.port == 11080
