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

from custom_components.denonavr_plus import config_flow, move, receiver_test
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.avr.models import DeviceInfo
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .conftest import deviceinfo
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X3600H"  # a model in KNOWN_MODELS; unknown ones are tested apart


MANIFEST_VERSION = json.loads(
    (Path(__file__).parents[1] / "custom_components/denonavr_plus/manifest.json").read_text()
)["version"]

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
    with (
        patch("custom_components.denonavr_plus.async_setup_entry", return_value=True),
        # The receiver answers on its control connection, unless a test says not.
        patch(_ANSWERS, AsyncMock(return_value=True)),
    ):
        yield
        await hass.async_block_till_done()


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
        "volume", "zones", "sources", "sound_modes", "playback", "activities", "connection", "entities", "tests_start"
    ]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_sources_need_the_receiver(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, minor_version=CONFIG_MINOR_VERSION, data={CONF_HOST: "192.0.2.10"})
    entry.add_to_hass(hass)
    for step in ("sources", "sound_modes", "playback"):
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
        result["flow_id"], {"DVD": "Player", "TUNER": "Tuner"}
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
    # Readable names are on unless switched off.
    assert loaded_entry.options["readable_sound_modes"] is True


async def test_a_stored_source_no_longer_offered_does_not_block_the_step(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The form's defaults were the stored list, and submitting them failed."""
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "sources": {"main": ["DVD", "GONE"]}}
    )
    await hass.async_block_till_done()
    result = await _options_step(hass, loaded_entry, "sources")
    # The form's own defaults, as a user who changes nothing sends them.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"main": {}, "zone2": {}}
    )
    assert result["step_id"] == "source_names"
    assert [str(key) for key in result["data_schema"].schema][0] == "DVD"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Kept for when the receiver offers it again.
    assert loaded_entry.options["sources"]["main"] == ["DVD", "GONE"]


async def test_a_stored_sound_mode_no_longer_reported_does_not_block_the_step(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    import asyncio

    from .test_init import FakeTelnet

    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "sound_modes": ["Stereo", "Gone"]}
    )
    await hass.async_block_till_done()
    FakeTelnet.instances[-1].push(
        "OPSMLALL MOV071Dolby Surround", "OPSMLALL MUS011Stereo", "OPSMLALL END"
    )
    await asyncio.sleep(0.15)
    result = await _options_step(hass, loaded_entry, "sound_modes")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "sound_mode_names"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert loaded_entry.options["sound_modes"] == ["Stereo", "Gone"]


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
    assert loaded_entry.options["settings_turn_on"] is False
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
            "zone2_channel_level_fl": {"group": "quiet", "result": "confirmed", "restored": False},
        },
        "audible": {
            "main_mute": {"group": "audible", "result": "confirmed", "restored": True},
            "setup_menu": {"group": "audible", "result": "skipped", "reason": "no_alternative"},
            "volume_limit": {"group": "audible", "result": "interrupted", "reason": "receiver_silent"},
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
    expected = ("4", "1", "2", "1", "2") if audible else ("3", "1", "1", "0", "2")
    assert (
        placeholders["confirmed"],
        placeholders["no_effect"],
        placeholders["skipped"],
        placeholders["interrupted"],
        placeholders["not_restored"],
    ) == expected
    # Named as their entities are, with the device where the name says little.
    assert placeholders["not_restored_list"] == "Dimmer, Zone 2: Front Left Level"
    # A receiver report in Discussions, with this integration's version.
    assert "/discussions/new?category=receiver-reports" in placeholders["url"]
    assert "version=" + MANIFEST_VERSION in placeholders["url"]
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


async def test_the_move_is_undone_from_the_options(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    old = MockConfigEntry(domain=move.OFFICIAL, data={CONF_HOST: HOST}, title="Wohnzimmer")
    old.add_to_hass(hass)
    await move.async_move(hass, loaded_entry, old)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["menu_options"][-1] == "move_back"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "move_back"}
    )
    assert result["step_id"] == "move_back"
    assert result["description_placeholders"] == {"titles": "Wohnzimmer", "count": "0"}
    with patch.object(move, "async_move_back", AsyncMock()) as back:
        result = await hass.config_entries.options.async_configure(result["flow_id"], {})
        await hass.async_block_till_done()
    assert (result["type"], result["reason"]) == (FlowResultType.ABORT, "moved_back")
    back.assert_awaited_once_with(hass, loaded_entry)


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


async def test_a_receiver_with_one_zone_gets_the_power_settings(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    from custom_components.denonavr_plus.avr.models import ZoneDescriptor

    discovery = loaded_entry.runtime_data.device.discovery
    with patch.object(discovery, "zones", [ZoneDescriptor(id="main", index=1)]):
        result = await _options_step(hass, loaded_entry, "zones")
        assert [str(key) for key in result["data_schema"].schema] == [
            "main_off_standby",
            "settings_turn_on",
        ]
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"main_off_standby": False, "settings_turn_on": True}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["settings_turn_on"] is True
    assert "zones" not in loaded_entry.options


async def test_a_player_is_linked_to_an_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    hass.states.async_set("media_player.apple_tv", "idle", {"friendly_name": "Apple TV"})
    result = await _options_step(hass, loaded_entry, "playback")
    assert result["step_id"] == "playback"
    inputs = next(
        field for field in result["data_schema"].schema if str(field) == "input"
    )
    offered = [
        option["value"]
        for option in result["data_schema"].schema[inputs].config["options"]
    ]
    # Not the network player and the tuner: they have their own information.
    # Not the tuner; the network input for Music Assistant's player.
    assert "DVD" in offered and "NETWORK" in offered and "TUNER" not in offered
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"input": "DVD", "signal_state": True}
    )
    assert result["step_id"] == "linked_input"
    # The receiver's own players are not offered.
    player = next(field for field in result["data_schema"].schema if str(field) == "player")
    own = entity_id_of_main(hass, loaded_entry)
    assert own in result["data_schema"].schema[player].config["exclude_entities"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"player": "media_player.apple_tv", "follow": True, "full_volume": False, "power": True},
    )
    # Another input or save: saving is a deliberate step.
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "playback_next"
    assert result["menu_options"] == ["playback", "playback_save"]
    assert "linked_players" not in loaded_entry.options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "playback"}
    )
    inputs = next(field for field in result["data_schema"].schema if str(field) == "input")
    labels = {
        option["value"]: option["label"]
        for option in result["data_schema"].schema[inputs].config["options"]
    }
    assert labels["DVD"].endswith("→ Apple TV")
    signal = next(field for field in result["data_schema"].schema if str(field) == "signal_state")
    assert signal.default() is True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"input": "CD", "signal_state": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"player": "media_player.streamer", "follow": False, "full_volume": True, "power": False},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "playback_save"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["signal_state"] is True
    assert loaded_entry.options["linked_players"] == {
        "DVD": {"player": "media_player.apple_tv", "follow": True, "full_volume": False, "power": True},
        "CD": {"player": "media_player.streamer", "follow": False, "full_volume": True, "power": False},
    }

    # Clearing the player unlinks the input; its switches have no meaning then.
    result = await _options_step(hass, loaded_entry, "playback")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"input": "DVD", "signal_state": True}
    )
    player = next(field for field in result["data_schema"].schema if str(field) == "player")
    assert player.description == {"suggested_value": "media_player.apple_tv"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"follow": True, "full_volume": False}
    )
    # Or back to the form and submit it without an input.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "playback"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal_state": True}
    )
    assert loaded_entry.options["linked_players"] == {
        "CD": {"player": "media_player.streamer", "follow": False, "full_volume": True, "power": False}
    }


async def test_the_playback_step_saves_without_an_input(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "playback")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal_state": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["signal_state"] is True


async def test_pausing_the_linked_player_on_turn_off_is_on_and_can_be_turned_off(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "playback")
    field = next(field for field in result["data_schema"].schema if str(field) == "pause_on_off")
    assert field.default() is True
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal_state": False, "pause_on_off": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["pause_on_off"] is False


async def test_a_player_of_the_receiver_cannot_be_linked(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "playback")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"input": "DVD", "signal_state": False}
    )
    with pytest.raises(InvalidData):
        # The selector already refuses it.
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"player": entity_id_of_main(hass, loaded_entry), "follow": False, "full_volume": False},
        )


def entity_id_of_main(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    from homeassistant.helpers import entity_registry as er

    return next(
        item.entity_id
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.unique_id.endswith("_media_player_main")
    )


async def test_a_rename_during_the_playback_dialog_is_kept(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Saving writes the inputs edited in the dialog, not an old copy of the rest."""
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "linked_players": {"CD": {"player": "media_player.streamer"}},
        },
    )
    result = await _options_step(hass, loaded_entry, "playback")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"input": "DVD", "signal_state": False}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"player": "media_player.apple_tv", "follow": False, "full_volume": False, "power": False}
    )
    # The streamer is renamed meanwhile (linked_players.py rewrites the options).
    hass.config_entries.async_update_entry(
        loaded_entry,
        options={
            **loaded_entry.options,
            "linked_players": {
                "CD": {"player": "media_player.kitchen", "follow": False, "full_volume": False, "power": False}
            },
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "playback_save"}
    )
    assert loaded_entry.options["linked_players"] == {
        "CD": {"player": "media_player.kitchen", "follow": False, "full_volume": False, "power": False},
        "DVD": {"player": "media_player.apple_tv", "follow": False, "full_volume": False, "power": False},
    }


async def test_the_network_input_has_no_full_volume(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Music Assistant's player may have the receiver itself as its volume."""
    result = await _options_step(hass, loaded_entry, "playback")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"input": "NETWORK", "signal_state": False}
    )
    assert result["step_id"] == "linked_input"
    assert [str(field) for field in result["data_schema"].schema] == ["player", "follow", "power"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"player": "media_player.music_assistant", "follow": True}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "playback_save"}
    )
    assert loaded_entry.options["linked_players"] == {
        "NETWORK": {"player": "media_player.music_assistant", "follow": True, "full_volume": False, "power": False}
    }


@pytest.mark.assumed(
    "Music Assistant's Home Assistant MediaPlayers provider names its player by "
    "the entity id it wraps, which its Home Assistant entity takes as unique id "
    "(the integration says 'unique_id is the MA player_id'); a Music Assistant "
    "with that provider would settle it"
)
async def test_music_assistants_player_made_of_this_receiver_is_not_offered(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Linked to the network input it would send play back and forth."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    own = entity_id_of_main(hass, loaded_entry)
    wrapped = registry.async_get_or_create("media_player", "music_assistant", own)
    other = registry.async_get_or_create("media_player", "music_assistant", "a1b2c3d4e5f6")
    result = await _options_step(hass, loaded_entry, "playback")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"input": "NETWORK"}
    )
    player = next(field for field in result["data_schema"].schema if str(field) == "player")
    excluded = result["data_schema"].schema[player].config["exclude_entities"]
    assert own in excluded and wrapped.entity_id in excluded
    assert other.entity_id not in excluded


async def test_a_player_named_like_the_input_is_suggested(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An input renamed after its device; Music Assistant's player of the receiver."""
    from homeassistant.helpers import entity_registry as er

    hass.states.async_set("media_player.living_room_apple_tv", "idle", {"friendly_name": "Living Room Apple TV"})
    hass.states.async_set("media_player.kitchen", "idle", {"friendly_name": "Kitchen"})
    hass.config_entries.async_update_entry(
        loaded_entry, options={**loaded_entry.options, "source_names": {"DVD": "Apple TV"}}
    )
    await hass.async_block_till_done()

    async def suggested(code: str) -> Any:
        result = await _options_step(hass, loaded_entry, "playback")
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"input": code})
        player = next(field for field in result["data_schema"].schema if str(field) == "player")
        return player.description["suggested_value"]

    assert await suggested("DVD") == "media_player.living_room_apple_tv"
    # A default name says nothing about the device.
    assert await suggested("CD") is None

    registry = er.async_get(hass)
    model = loaded_entry.runtime_data.device.discovery.device.model_name
    ma = registry.async_get_or_create("media_player", "music_assistant", "ma_receiver", suggested_object_id="ma_receiver")
    hass.states.async_set(ma.entity_id, "idle", {"friendly_name": f"Denon {model}"})
    assert await suggested("NETWORK") == ma.entity_id
    # Two that fit: no suggestion.
    other = registry.async_get_or_create("media_player", "music_assistant", "ma_other", suggested_object_id="ma_other")
    hass.states.async_set(other.entity_id, "idle", {"friendly_name": f"{model} (AirPlay)"})
    assert await suggested("NETWORK") is None


def _field(result: Any, name: str) -> Any:
    return next(field for field in result["data_schema"].schema if str(field) == name)


async def test_activities_are_added_changed_and_deleted(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    result = await _options_step(hass, loaded_entry, "activities")
    assert result["step_id"] == "activities"
    # None yet: nothing to pick, straight to a new one.
    assert result["data_schema"].schema == {}
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["step_id"] == "activity"
    zones = result["data_schema"].schema[_field(result, "zone")].config["options"]
    assert zones == ["main", "zone2"]
    sources = [
        option["value"]
        for option in result["data_schema"].schema[_field(result, "source")].config["options"]
    ]
    assert "DVD" in sources
    # The recorded receiver has the HDMI output select; no delete for a new one.
    hdmi = result["data_schema"].schema[_field(result, "hdmi_output")].config["options"]
    assert [option["value"] for option in hdmi] == ["AUTO", "1", "2"]
    assert "delete" not in {str(field) for field in result["data_schema"].schema}
    # The audio delay in the receiver's range; anything that can be switched on, not our players.
    delay = result["data_schema"].schema[_field(result, "audio_delay")].config
    assert (delay["min"], delay["max"]) == (0.0, 500.0)
    wake = result["data_schema"].schema[_field(result, "wake_entity")].config
    assert wake["domain"] == ["media_player", "remote", "switch"]
    assert entity_id_of_main(hass, loaded_entry) in wake["exclude_entities"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "  Film ",
            "zone": "main",
            "source": "DVD",
            "volume_db": -30.0,
            "audio_delay": 40.0,
            "clarity": "on",
            "wake_player": False,
            "wake_entity": "media_player.tv",
        },
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "activities_next"
    assert result["menu_options"] == ["activities", "activities_save"]
    assert result["description_placeholders"] == {"activity": "Film"}
    # A second one with the same name is refused.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "activities"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "film", "zone": "zone2", "source": "CD", "clarity": "keep", "wake_player": False},
    )
    assert result["errors"] == {"base": "duplicate_name"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "   ", "zone": "zone2", "source": "CD", "clarity": "keep", "wake_player": False},
    )
    assert result["errors"] == {"base": "activity_incomplete"}
    # What only the main zone has is not kept for Zone 2.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Music",
            "zone": "zone2",
            "source": "CD",
            "sound_mode": "Stereo",
            "audio_delay": 20.0,
            "clarity": "on",
            "wake_player": False,
        },
    )
    assert "activities" not in loaded_entry.options
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "activities_save"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["activities"] == [
        {
            "name": "Film",
            "zone": "main",
            "source": "DVD",
            "volume_db": -30.0,
            "clarity": "on",
            "audio_delay": 40,
            "wake_entity": "media_player.tv",
        },
        {"name": "Music", "zone": "zone2", "source": "CD"},
    ]

    # Picking one opens it with its values; delete removes it.
    result = await _options_step(hass, loaded_entry, "activities")
    assert result["description_placeholders"] == {"count": "2"}
    assert result["data_schema"].schema[_field(result, "activity")].config["options"] == [
        "Film",
        "Music",
    ]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"activity": "Music"}
    )
    assert _field(result, "zone").default() == "zone2"
    assert _field(result, "delete").default() is False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "name": "Music",
            "zone": "zone2",
            "source": "CD",
            "clarity": "keep",
            "wake_player": False,
            "delete": True,
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "activities_save"}
    )
    assert [item["name"] for item in loaded_entry.options["activities"]] == ["Film"]


async def test_the_activity_steps_need_the_receiver(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    with patch.object(config_flow.DenonAvrOptionsFlow, "_discovery", return_value=None):
        result = await _options_step(hass, loaded_entry, "activities")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_connected"
