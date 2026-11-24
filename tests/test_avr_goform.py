"""Tests for avr/transport/goform.py: which port serves the receiver's goform API."""

from __future__ import annotations

import re

import pytest
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.denonavr_plus.avr.transport.goform import (
    GoformClient,
    async_detect_port,
)

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


async def test_a_command_goes_over_http_with_spaces_encoded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    url = re.compile(
        r"^http://192\.0\.2\.10:8080/goform/formiPhoneAppDirect\.xml\?PSBAS%2050$"
    )
    aioclient_mock.get(url, text="")
    client = GoformClient(async_get_clientsession(hass), HOST)
    assert await client.async_send_command("PSBAS 50")
    assert aioclient_mock.call_count == 1


async def test_a_refused_command_reports_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        re.compile(r"formiPhoneAppDirect\.xml\?MVUP$"), exc=ClientError()
    )
    client = GoformClient(async_get_clientsession(hass), HOST)
    assert not await client.async_send_command("MVUP")


STATUS = "/goform/formMainZone_MainZoneXmlStatusLite.xml"


async def test_status_lite_is_read_into_a_partial_state(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"http://{HOST}:8080{STATUS}",
        text=(
            "<item><Power><value>ON</value></Power>"
            "<InputFuncSelect><value>TV Audio</value></InputFuncSelect>"
            "<VolumeDisplay><value>Absolute</value></VolumeDisplay>"
            "<MasterVolume><value>-35.5</value></MasterVolume>"
            "<Mute><value>off</value></Mute></item>"
        ),
    )
    client = GoformClient(async_get_clientsession(hass), HOST, 8080)
    assert await client.async_get_status(STATUS) == {
        "power": True,
        "source": "TV Audio",
        "volume_display": "Absolute",
        "volume_db": -35.5,
        "muted": False,
    }


async def test_status_lite_tolerates_gaps_and_junk(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        f"http://{HOST}:8080{STATUS}",
        text="<item><MasterVolume><value>--</value></MasterVolume><Mute><value/></Mute></item>",
    )
    client = GoformClient(async_get_clientsession(hass), HOST, 8080)
    assert await client.async_get_status(STATUS) == {}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"http://{HOST}:8080{STATUS}", text="<item")
    assert await client.async_get_status(STATUS) is None


async def test_status_lite_on_an_error_page_gives_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(f"http://{HOST}:8080{STATUS}", status=500)
    client = GoformClient(async_get_clientsession(hass), HOST, 8080)
    assert await client.async_get_status(STATUS) is None


async def test_a_command_over_http_is_url_encoded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(re.compile(r"^http://192\.0\.2\.10:8080/goform/formiPhoneAppDirect\.xml"))
    client = GoformClient(async_get_clientsession(hass), HOST, 8080)

    assert await client.async_send_command("MV 50")

    assert str(aioclient_mock.mock_calls[-1][1]).endswith("formiPhoneAppDirect.xml?MV%2050")
