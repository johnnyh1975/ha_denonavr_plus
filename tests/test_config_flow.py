"""Tests for config_flow.py."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.denonavr_plus import config_flow, move
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.models import DeviceInfo
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .conftest import deviceinfo


HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X3600H"  # a model in KNOWN_MODELS; unknown ones are tested apart


pytestmark = pytest.mark.usefixtures("flow_without_setup")




_ANSWERS = "custom_components.denonavr_plus.config_flow.async_answers"


def _identity(mac: str | None = MAC, model: str | None = MODEL, **info: Any) -> AsyncMock:
    return AsyncMock(return_value=DeviceInfo(model_name=model, mac_address=mac, **info))


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: f" {HOST} "}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == MODEL
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == MAC
    # Where to go next: the options, linked players.
    assert result["description"] == "created"


async def test_user_flow_cannot_connect_then_recovers(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    failing = AsyncMock(side_effect=ConnectionError("unreachable"))
    with patch.object(DenonAvrDevice, "async_fetch_identity", failing):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured_updates_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: "192.0.2.99"})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST


async def test_user_flow_without_mac_matches_on_host(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: HOST}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity(mac=None)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


AIOS = "urn:schemas-denon-com:device:AiosDevice:1"
RENDERER = "urn:schemas-upnp-org:device:MediaRenderer:1"


def _ssdp(
    location: str = f"http://{HOST}:60006/upnp/desc/aios_device/aios_device.xml",
    *,
    manufacturer: str = "Denon",
    device_type: str = AIOS,
    model: str = MODEL,
) -> SsdpServiceInfo:
    return SsdpServiceInfo(
        ssdp_usn="uuid:00000000-0000-0000-0000-000000000000",
        ssdp_st=device_type,
        ssdp_location=location,
        upnp={"manufacturer": manufacturer, "deviceType": device_type, "modelName": model},
    )


def test_the_manifest_matches_every_spelling_and_device_type() -> None:
    manifest = json.loads(
        (Path(config_flow.__file__).parent / "manifest.json").read_text(encoding="utf-8")
    )
    matchers = {(item["deviceType"], item["manufacturer"]) for item in manifest["ssdp"]}
    assert matchers == {
        (device_type, manufacturer)
        for device_type in (AIOS, RENDERER, "urn:schemas-upnp-org:device:MediaServer:1")
        for manufacturer in config_flow.SSDP_MANUFACTURERS
    }


async def test_ssdp_finds_a_marantz_receiver(hass: HomeAssistant) -> None:
    """Marantz and pre-2016 units announce only a MediaRenderer."""
    discovery = _ssdp(manufacturer="Marantz", device_type=RENDERER, model="SR6015")
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=discovery
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ssdp_confirm"


@pytest.mark.parametrize(
    ("manufacturer", "model", "reason"),
    [
        ("Sonos, Inc.", "Play:1", "not_denon_marantz"),
        ("Denon", "HEOS 1", "not_a_receiver"),
        ("Denon", "Denon Home 150", "not_a_receiver"),
    ],
)
async def test_ssdp_ignores_other_devices(
    hass: HomeAssistant, manufacturer: str, model: str, reason: str
) -> None:
    identify = _identity()
    with patch.object(DenonAvrDevice, "async_fetch_identity", identify):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_SSDP},
            data=_ssdp(manufacturer=manufacturer, device_type=RENDERER, model=model),
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    identify.assert_not_awaited()


async def test_ssdp_announcements_at_the_same_moment_start_one_flow(
    hass: HomeAssistant,
) -> None:
    """Announcements of one receiver arrive together; Deviceinfo is read once."""
    started = asyncio.Event()

    async def slow_identity(self: DenonAvrDevice) -> DeviceInfo:
        started.set()
        await asyncio.sleep(0.05)
        return DeviceInfo(model_name=MODEL, mac_address=None)

    with patch.object(DenonAvrDevice, "async_fetch_identity", slow_identity):
        results = await asyncio.gather(
            *(
                hass.config_entries.flow.async_init(
                    DOMAIN,
                    context={"source": config_entries.SOURCE_SSDP},
                    data=_ssdp(device_type=device_type),
                )
                for device_type in (AIOS, RENDERER, "urn:schemas-upnp-org:device:MediaServer:1")
            )
        )
    assert sorted(result["type"] for result in results) == [
        FlowResultType.ABORT,
        FlowResultType.ABORT,
        FlowResultType.FORM,
    ]


async def test_ssdp_one_flow_per_receiver(hass: HomeAssistant) -> None:
    """The same receiver announced as AiosDevice and MediaRenderer starts one flow."""
    identify = _identity()
    with patch.object(DenonAvrDevice, "async_fetch_identity", identify):
        first = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp()
        )
        second = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_SSDP},
            data=_ssdp(device_type=RENDERER),
        )
    assert first["type"] is FlowResultType.FORM
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_in_progress"
    assert identify.await_count == 1


def _seen(*receivers: tuple[str, str, str, str]) -> AsyncMock:
    """The SSDP cache: (address, manufacturer, model, friendly name) per unit.

    Each unit is announced under every device type, as HEOS-era receivers do.
    """

    async def by_st(hass: HomeAssistant, device_type: str) -> list[SsdpServiceInfo]:
        return [
            SsdpServiceInfo(
                ssdp_usn=f"uuid:{host}",
                ssdp_st=device_type,
                ssdp_location=f"http://{host}:60006/upnp/desc/aios_device/aios_device.xml",
                upnp={"manufacturer": maker, "modelName": model, "friendlyName": name},
            )
            for host, maker, model, name in receivers
        ]

    return AsyncMock(side_effect=by_st)


LIVING = ("192.0.2.10", "Denon", MODEL, "Living room")
CINEMA = ("192.0.2.11", "Marantz", "SR6015", "Cinema")
KITCHEN = ("192.0.2.12", "Denon", "HEOS 1", "Kitchen")


async def _search(hass: HomeAssistant, cache: AsyncMock) -> Any:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(config_flow.ssdp, "async_get_discovery_info_by_st", cache):
        return await hass.config_entries.flow.async_configure(result["flow_id"], {})


async def test_an_empty_address_offers_the_receivers_found(hass: HomeAssistant) -> None:
    result = await _search(hass, _seen(LIVING, CINEMA, KITCHEN))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    options = result["data_schema"].schema["receiver"].config["options"]
    # The HEOS speaker is not offered, and each receiver once.
    assert options == [
        {"value": "192.0.2.10", "label": "Living room (192.0.2.10)"},
        {"value": "192.0.2.11", "label": "Cinema (192.0.2.11)"},
    ]

    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"receiver": "192.0.2.10"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "192.0.2.10"}


async def test_a_single_receiver_found_is_added_directly(hass: HomeAssistant) -> None:
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await _search(hass, _seen(LIVING, KITCHEN))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: "192.0.2.10"}


async def test_receivers_already_set_up_are_not_offered(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: "192.0.2.10"}
    ).add_to_hass(hass)
    result = await _search(hass, _seen(LIVING))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_receivers_found"}


async def test_a_picked_receiver_that_does_not_answer(hass: HomeAssistant) -> None:
    result = await _search(hass, _seen(LIVING, CINEMA))
    failing = AsyncMock(side_effect=ConnectionError("unreachable"))
    with patch.object(DenonAvrDevice, "async_fetch_identity", failing):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"receiver": "192.0.2.11"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_ssdp_discovery_and_confirm(hass: HomeAssistant) -> None:
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp()
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ssdp_confirm"
    assert result["description_placeholders"] == {"name": MODEL}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_HOST: HOST}
    assert result["result"].unique_id == MAC


async def test_ssdp_without_location_aborts(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp(location="")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_ssdp_unreachable_aborts(hass: HomeAssistant) -> None:
    failing = AsyncMock(side_effect=ConnectionError("unreachable"))
    with patch.object(DenonAvrDevice, "async_fetch_identity", failing):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp()
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_ssdp_known_receiver_updates_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: "192.0.2.99"})
    entry.add_to_hass(hass)
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp()
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST


async def test_ssdp_without_mac_matches_on_host(hass: HomeAssistant) -> None:
    """A receiver that reports no MAC is recognised by its address instead."""
    MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: HOST}).add_to_hass(hass)
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity(mac=None)):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_SSDP}, data=_ssdp()
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_requires_an_address(hass: HomeAssistant) -> None:
    """The empty-address search of the setup dialog does not apply here."""
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(result["flow_id"], {})


async def test_reconfigure_changes_host(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: "192.0.2.99"})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == HOST


async def test_reconfigure_refuses_a_different_receiver(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: "192.0.2.99"})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    with patch.object(
        DenonAvrDevice, "async_fetch_identity", _identity(mac="aa:bb:cc:dd:ee:01")
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"
    assert entry.data[CONF_HOST] == "192.0.2.99"


async def test_reconfigure_cannot_connect(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: "192.0.2.99"})
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    failing = AsyncMock(side_effect=ConnectionError("unreachable"))
    with patch.object(DenonAvrDevice, "async_fetch_identity", failing):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_older_receiver_is_set_up_on_port_80(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An AVR-X2000 answers on port 80 only; the entry remembers the port."""
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10:8080/goform/Deviceinfo\.xml$"), exc=ClientError()
    )
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10/goform/Deviceinfo\.xml$"),
        text=deviceinfo("AVR-X2000-Deviceinfo.xml"),
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    # A checked model: no check is offered.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AVR-X2000"
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: 80}
    assert result["result"].options == {}


async def test_an_unknown_model_can_be_checked_after_setup(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity(model="SR6015")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["step_id"] == "unknown_model"
    # On by default; the audible part needs someone at the TV, not offered here.
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults == {"test": True}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"test": True})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {
        "capability_check_pending": True,
        "command_test_pending": ["quiet"],
    }
    assert result["description"] == "created"


def _dhcp(ip: str, mac: str = "aabbccddeeff") -> DhcpServiceInfo:
    return DhcpServiceInfo(ip=ip, hostname="denon-avr", macaddress=mac)


async def test_dhcp_follows_a_new_address(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, unique_id=MAC, data={CONF_HOST: "192.0.2.99"}
    )
    entry.add_to_hass(hass)
    identify = _identity()
    with patch.object(DenonAvrDevice, "async_fetch_identity", identify):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp(HOST)
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST
    # Matched by the MAC alone; nothing was asked of the receiver.
    identify.assert_not_awaited()


async def test_dhcp_for_an_unknown_unit_checks_it_like_ssdp(hass: HomeAssistant) -> None:
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp(HOST)
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "ssdp_confirm"


async def test_dhcp_for_a_unit_that_does_not_answer(hass: HomeAssistant) -> None:
    failing = AsyncMock(side_effect=ConnectionError("unreachable"))
    with patch.object(DenonAvrDevice, "async_fetch_identity", failing):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp(HOST)
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


@pytest.mark.parametrize(("choice", "pending"), [("move_yes", True), ("move_no", False)])
async def test_the_receiver_in_another_integration_is_offered_for_the_move(
    hass: HomeAssistant, choice: str, pending: bool
) -> None:
    old = MockConfigEntry(domain=move.OFFICIAL, data={CONF_HOST: HOST}, title="Wohnzimmer")
    old.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "move_offer"
    assert result["menu_options"] == ["move_yes", "move_no"]
    assert result["description_placeholders"] == {
        "model": MODEL,
        "title": "Wohnzimmer",
        "integration": "Denon AVR Network Receivers",
    }
    answers = AsyncMock(return_value=False)
    with patch(_ANSWERS, answers):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": choice}
        )
    if not pending:
        # Not taken over: the old integration may hold the session; asked.
        assert result["step_id"] == "telnet_unavailable"
        answers.assert_awaited_once()
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "telnet_continue"}
        )
    else:
        # Taken over: it lets go once it is unloaded; not asked.
        answers.assert_not_awaited()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    if pending:
        # Off at once, so this integration gets the telnet session; the
        # entities move once they exist here.
        assert result["result"].options == {move.OPT_MOVE_PENDING: [old.entry_id]}
        assert old.disabled_by is config_entries.ConfigEntryDisabler.USER
    else:
        assert result["result"].options == {}
        assert old.disabled_by is None


async def test_the_move_and_the_tests_for_an_unknown_model(hass: HomeAssistant) -> None:
    old = MockConfigEntry(domain=move.LASERGURUGUY, unique_id=MAC, data={CONF_HOST: "192.0.2.99"})
    old.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity(model="AVR-X9999")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "move_yes"}
    )
    assert result["step_id"] == "unknown_model"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"test": True})
    assert result["result"].options == {
        move.OPT_MOVE_PENDING: [old.entry_id],
        "capability_check_pending": True,
        "command_test_pending": ["quiet"],
    }


@pytest.mark.parametrize(
    ("model", "level"), [("AVR-X4100W", 300), ("SR6012", 301), ("AVR-X1100W", 250)]
)
async def test_a_described_generation_is_added_without_the_tests(
    hass: HomeAssistant, model: str, level: int
) -> None:
    """Models not tested here, but of a generation the integration describes."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    identity = _identity(model=model, comm_api_version=level, product_category=1)
    with patch.object(DenonAvrDevice, "async_fetch_identity", identity):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {}


async def test_a_system_is_offered_the_tests(hass: HomeAssistant) -> None:
    """ProductCategory 2 (M-CR510): not an AV receiver, so not described."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    identity = _identity(model="M-CR510", comm_api_version=220, product_category=2)
    with patch.object(DenonAvrDevice, "async_fetch_identity", identity):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["step_id"] == "unknown_model"


LEGACY_DESCRIPTION = (
    '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
    "<manufacturer>Denon</manufacturer><modelName>*AVR-1912</modelName>"
    "<serialNumber>SN0000000000</serialNumber></device></root>"
)


async def test_a_receiver_before_2013_is_set_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An AVR-1912 has no Deviceinfo.xml: identity from its UPnP description.

    The error page and the main zone document are recordings of the denonavr
    library; the serial number is the unique id, as there is no MAC.
    """
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10:8080/goform/Deviceinfo\.xml$"), exc=ClientError()
    )
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10/goform/Deviceinfo\.xml$"),
        text=deviceinfo("AVR-1912-Deviceinfo.xml"),
    )
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10/goform/formMainZone_MainZoneXml\.xml$"),
        text=deviceinfo("AVR-1912-formMainZone_MainZoneXml.xml"),
    )
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10:8080/description\.xml$"), text=LEGACY_DESCRIPTION
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    # A described generation: no test is offered.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AVR-1912"
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: 80}
    assert result["result"].unique_id == "SN0000000000"
    assert result["result"].options == {}


async def test_an_error_page_without_main_zone_is_no_receiver(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10:8080/goform/Deviceinfo\.xml$"), exc=ClientError()
    )
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10/goform/Deviceinfo\.xml$"),
        text=deviceinfo("AVR-1912-Deviceinfo.xml"),
    )
    aioclient_mock.get(
        re.compile(r"^http://192\.0\.2\.10/goform/formMainZone_MainZoneXml\.xml$"),
        status=404,
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_a_known_model_is_added_without_the_tests(hass: HomeAssistant) -> None:
    """Both tests stay in the options; setting up a known model asks nothing."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch.object(DenonAvrDevice, "async_fetch_identity", _identity()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].options == {}




async def test_a_receiver_without_its_control_connection_says_why(hass: HomeAssistant) -> None:
    """HTTP answers, telnet does not: the setup names the causes; check again or go on."""
    answers = AsyncMock(side_effect=[False, False, True])
    with (
        patch.object(DenonAvrDevice, "async_fetch_identity", _identity()),
        patch(_ANSWERS, answers),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.0.2.10"}
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "telnet_unavailable"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "telnet_retry"}
        )
        assert result["step_id"] == "telnet_unavailable"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "telnet_retry"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    answers.assert_awaited_with("192.0.2.10")


async def test_the_setup_goes_on_without_the_control_connection(hass: HomeAssistant) -> None:
    with (
        patch.object(DenonAvrDevice, "async_fetch_identity", _identity()),
        patch(_ANSWERS, AsyncMock(return_value=False)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.0.2.10"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "telnet_continue"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


