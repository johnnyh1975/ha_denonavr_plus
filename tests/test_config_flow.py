"""Tests for config_flow.py."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.denonavr_plus import config_flow, receiver_test
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.avr.models import DeviceInfo
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .conftest import deviceinfo
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X3600H"  # a model in KNOWN_MODELS; unknown ones are tested apart


@pytest.fixture(autouse=True)
async def no_setup(
    request: pytest.FixtureRequest, hass: HomeAssistant
) -> AsyncIterator[None]:
    """The flow is tested on its own; never set the entry up for real.

    Except for the options tests that need a loaded receiver (its sources and
    sound modes come from it). A flow that creates or reconfigures an entry
    schedules its setup or reload; it is awaited here, while setup is still
    patched, or it would run during teardown against no receiver at all.
    """
    if "loaded_entry" in request.fixturenames:
        yield
        return
    with patch(
        "custom_components.denonavr_plus.async_setup_entry", return_value=True
    ):
        yield
        await hass.async_block_till_done()


def _identity(mac: str | None = MAC, model: str | None = MODEL) -> AsyncMock:
    return AsyncMock(return_value=DeviceInfo(model_name=model, mac_address=mac))


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
    # A model not checked before: the check is offered first.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "unknown_model"
    assert result["description_placeholders"] == {"model": "AVR-X2000"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"test": False}
    )
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


async def test_options_keep_the_markers_they_do_not_show(hass: HomeAssistant) -> None:
    from homeassistant.helpers import entity_registry as er

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION,
        options={"channels_initialized": True},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for zone in ("main", "zone2"):
        registry.async_get_or_create(
            "media_player", DOMAIN, f"aa:bb:cc:dd:ee:ff_media_player_{zone}", config_entry=entry
        )

    result = await _options_step(hass, entry, "volume")
    assert result["type"] is FlowResultType.FORM
    # One section per zone of the receiver, read from the registry.
    fields = [str(key) for key in result["data_schema"].schema]
    assert fields == ["official_volume_scale", "main", "zone2"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"official_volume_scale": True, "main": {"max_volume": -10.0}, "zone2": {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        "channels_initialized": True,
        "official_volume_scale": True,
        "main": {"max_volume": -10.0},
        "zone2": {},
    }


async def test_options_refuse_a_turn_on_volume_above_the_maximum(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.10"})
    entry.add_to_hass(hass)
    result = await _options_step(hass, entry, "volume")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"official_volume_scale": False, "main": {"max_volume": -30.0, "turn_on_volume": -20.0}},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "turn_on_above_max"}


async def _options_step(hass: HomeAssistant, entry: MockConfigEntry, step: str) -> Any:
    """Open the options and pick a step from the menu."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == [
        "volume", "zones", "sources", "sound_modes", "connection", "entities", "tests_start"
    ]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_sources_need_the_receiver(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.10"})
    entry.add_to_hass(hass)
    for step in ("sources", "sound_modes"):
        result = await _options_step(hass, entry, step)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "not_connected"


async def test_sources_are_chosen_per_zone_and_renamed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "sources")
    assert result["step_id"] == "sources"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"main": {"sources": ["DVD", "TUNER"]}, "zone2": {"sources": ["TUNER"]}},
    )
    assert result["step_id"] == "source_names"
    # Only the chosen sources are offered for renaming.
    assert [str(key) for key in result["data_schema"].schema] == ["DVD", "TUNER"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"DVD": "Player", "TUNER": "TUNER"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["sources"] == {"main": ["DVD", "TUNER"], "zone2": ["TUNER"]}
    # An unchanged name is not stored.
    assert loaded_entry.options["source_names"] == {"DVD": "Player"}


async def test_choosing_every_source_means_all(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    every = [s.code for s in loaded_entry.runtime_data.device.discovery.visible_sources()]
    result = await _options_step(hass, loaded_entry, "sources")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {"sources": every}, "zone2": {"sources": every}}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert loaded_entry.options["sources"] == {"main": [], "zone2": []}


async def test_two_sources_cannot_share_a_name(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "sources")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {"sources": ["DVD", "CD"]}, "zone2": {"sources": []}}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"DVD": "Disc", "CD": "Disc"}
    )
    assert result["errors"] == {"base": "duplicate_name"}


async def test_sound_modes_are_chosen_and_renamed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from .test_init import FakeTelnet

    FakeTelnet.instances[-1].push(
        "OPSMLALL MOV071Dolby Surround", "OPSMLALL MUS011Stereo", "OPSMLALL END"
    )
    await asyncio.sleep(0.15)
    result = await _options_step(hass, loaded_entry, "sound_modes")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"sound_modes": ["Stereo"]}
    )
    assert result["step_id"] == "sound_mode_names"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"Stereo": "Zwei Kanäle"}
    )
    assert loaded_entry.options["sound_modes"] == ["Stereo"]
    assert loaded_entry.options["sound_mode_names"] == {"Stereo": "Zwei Kanäle"}


async def test_zones_can_be_switched_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "zones")
    assert result["step_id"] == "zones"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"zones": [], "main_off_standby": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["zones"] == []
    assert loaded_entry.options["main_off_standby"] is True
    # A change of zones reloads the entry; let it finish inside the test, or
    # it runs during teardown without the fake receiver.
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert loaded_entry.runtime_data.shown_zones == frozenset({"main"})


async def test_connection_options_open_without_the_receiver(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.10"})
    entry.add_to_hass(hass)
    result = await _options_step(hass, entry, "connection")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "connection"
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema
    }
    assert defaults == {"poll_interval": 60, "heos": True, "setup_refresh": 15}


async def test_connection_options_reload_the_entry(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "connection")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_interval": 120.0, "heos": False, "setup_refresh": 0.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["poll_interval"] == 120
    assert loaded_entry.options["setup_refresh"] == 0
    # A change of the connection reloads the entry; let it finish here.
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    coordinator = loaded_entry.runtime_data
    assert coordinator.connection == {"poll_interval": 120, "heos": False, "setup_refresh": 0}
    assert coordinator.update_interval == timedelta(seconds=120)


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


CHECK = {"model": "AVC-X3700H", "firmware": "1.2.3", "queries": {
    "PW?": {"group": "Power", "result": "answered", "lines": ["PWON"]},
    "SR?": {"group": "Power", "result": "no reply", "lines": []},
}}


def _command_test(groups: list[str]) -> dict[str, Any]:
    tests = {
        "quiet": {
            "bass": {"group": "quiet", "result": "confirmed", "restored": True},
            "treble": {"group": "quiet", "result": "query_only", "restored": True},
            "dimmer": {"group": "quiet", "result": "no_effect", "restored": False},
            "lfe": {"group": "quiet", "result": "no_value"},
        },
        "audible": {
            "main_mute": {"group": "audible", "result": "confirmed", "restored": True},
            "setup_menu": {"group": "audible", "result": "skipped", "reason": "no_alternative"},
        },
    }
    found: dict[str, Any] = {}
    for group in groups:
        found |= tests[group]
    return {"model": "AVC-X3700H", "firmware": "1.2.3", "groups": groups, "tests": found}


async def _through_progress(hass: HomeAssistant, result: Any) -> Any:
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    await hass.async_block_till_done()
    return await hass.config_entries.options.async_configure(result["flow_id"])


@pytest.mark.parametrize("audible", [True, False])
async def test_the_receiver_test_runs_its_parts_in_sequence(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, audible: bool
) -> None:
    async def command_test(self: Any, groups: list[str], on_progress: Any = None, on_current: Any = None) -> Any:
        on_progress(0.5)
        return _command_test(groups)

    check = AsyncMock(return_value=dict(CHECK))
    options_before = dict(loaded_entry.options)
    shown: list[float] = []
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", check),
        patch.object(DenonAvrDevice, "async_command_test", command_test),
        patch(
            "homeassistant.config_entries.OptionsFlow.async_update_progress",
            lambda self, progress: shown.append(progress),
        ),
    ):
        # A start page says what happens; Start begins.
        result = await _options_step(hass, loaded_entry, "tests_start")
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "tests_start"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        assert (result["step_id"], result["progress_action"]) == ("tests_check", "test_part_1")
        result = await _through_progress(hass, result)
        # The quiet part follows by itself.
        assert (result["step_id"], result["progress_action"]) == ("tests_quiet", "test_part_2")
        result = await _through_progress(hass, result)
        # Then the question, with a button each.
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "tests_ask"
        assert result["menu_options"] == ["tests_audible", "tests_done"]
        choice = "tests_audible" if audible else "tests_done"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": choice}
        )
        if audible:
            assert result["progress_action"] == "test_part_3"
            result = await _through_progress(hass, result)
    check.assert_awaited_once()
    # One progress over the whole test, not starting again for every part.
    expected_shares = [0.0, 0.25, 0.5] + ([0.75, 0.875] if audible else [])
    assert shown == expected_shares
    # A setting did not come back: the result says so in its title.
    assert result["step_id"] == "tests_done_check"
    placeholders = result["description_placeholders"]
    assert (placeholders["answered"], placeholders["total"]) == ("1", "2")
    expected = ("3", "1", "2", "1") if audible else ("2", "1", "1", "1")
    assert (
        placeholders["confirmed"],
        placeholders["no_effect"],
        placeholders["skipped"],
        placeholders["not_restored"],
    ) == expected
    assert placeholders["not_restored_list"] == "dimmer"
    assert "template=device_support.yml" in placeholders["url"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Changes no option; both parts are kept for the diagnostics.
    assert dict(loaded_entry.options) == options_before
    kept = loaded_entry.runtime_data.command_test.result
    assert ("main_mute" in kept["tests"]) is audible
    assert "bass" in kept["tests"]


async def test_everything_put_back_is_the_result_title(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    tested = {"model": "AVC-X3700H", "groups": ["quiet"], "tests": {
        "bass": {"group": "quiet", "result": "confirmed", "restored": True},
        "dynamic_eq": {"group": "quiet", "result": "precondition", "reason": "audyssey_off"},
    }}
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(CHECK))),
        patch.object(DenonAvrDevice, "async_command_test", AsyncMock(return_value=tested)),
    ):
        result = await _options_step(hass, loaded_entry, "tests_start")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        result = await _through_progress(hass, result)
        result = await _through_progress(hass, result)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "tests_done"}
        )
    assert result["step_id"] == "tests_done"
    placeholders = result["description_placeholders"]
    assert (placeholders["confirmed"], placeholders["precondition"]) == ("1", "1")
    assert (placeholders["not_restored"], placeholders["not_restored_list"]) == ("0", "-")


async def test_the_receiver_test_needs_the_receiver(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.10"}
    )
    entry.add_to_hass(hass)
    result = await _options_step(hass, entry, "tests_start")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "test_not_connected"


@pytest.mark.parametrize("error", [CommandNotSent("query_needs_telnet"), RuntimeError("bug")])
async def test_the_receiver_test_ends_when_a_part_fails(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, error: Exception
) -> None:
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", AsyncMock(return_value=dict(CHECK))),
        patch.object(DenonAvrDevice, "async_command_test", AsyncMock(side_effect=error)),
    ):
        result = await _options_step(hass, loaded_entry, "tests_start")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        result = await _through_progress(hass, result)
        result = await _through_progress(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "test_not_connected"


async def test_the_progress_names_what_runs(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The step is reloaded with the name of each part of the receiver."""
    keys = ("audio", "speakers", "zone2", "receiver", "something_new")
    gates = {key: asyncio.Event() for key in ("check", *keys)}

    async def check(self: Any, on_progress: Any = None, on_current: Any = None) -> Any:
        on_current("Signal and setup")
        await gates["check"].wait()
        return dict(CHECK)

    async def command_test(
        self: Any, groups: list[str], on_progress: Any = None, on_current: Any = None
    ) -> Any:
        for index, key in enumerate(keys):
            on_current(key)
            await gates[key].wait()
            on_progress((index + 1) / len(keys))
        return _command_test(groups)

    changed: list[Any] = []
    hass.bus.async_listen("data_entry_flow_progressed", changed.append)

    async def shown(flow_id: str) -> str:
        # What the frontend does on the event: load the step again.
        for _ in range(5):
            await asyncio.sleep(0)
        result = await hass.config_entries.options.async_configure(flow_id)
        return str(result["description_placeholders"]["current"])

    names: list[str] = []
    sent: list[float] = []
    with (
        patch.object(DenonAvrDevice, "async_check_capabilities", check),
        patch.object(DenonAvrDevice, "async_command_test", command_test),
        patch.object(receiver_test, "PROGRESS_AGAIN", 0),
        patch(
            "homeassistant.config_entries.OptionsFlow.async_update_progress",
            lambda self, progress: sent.append(round(progress, 2)),
        ),
    ):
        result = await _options_step(hass, loaded_entry, "tests_start")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        flow_id = result["flow_id"]
        names.append(await shown(flow_id))
        gates["check"].set()
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_configure(flow_id)
        assert result["step_id"] == "tests_quiet"
        for key in keys:
            names.append(await shown(flow_id))
            gates[key].set()
    # The part's name, and a readable key for anything new.
    assert names == [
        "Signal and setup",
        "Audio",
        "Speakers",
        "Zone 2",
        "Receiver",
        "Something new",
    ]
    # After each reload the percentage comes again, so the circle keeps it.
    assert sent[:3] == [0.0, 0.0, 0.25]
    assert sent.count(0.35) == 2
    assert changed and all(event.data["refresh"] for event in changed)
