"""Tests for config_flow.py."""

from __future__ import annotations

import json
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
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.ssdp import SsdpServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.denonavr_plus import config_flow
from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.models import DeviceInfo
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .conftest import deviceinfo
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

HOST = "192.0.2.10"
MAC = "aa:bb:cc:dd:ee:ff"
MODEL = "AVR-X2600H"


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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AVR-X2000"
    assert result["data"] == {CONF_HOST: HOST, CONF_PORT: 80}


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
    assert result["menu_options"] == ["volume", "zones", "sources", "sound_modes"]
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
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"zones": []})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert loaded_entry.options["zones"] == []
    # A change of zones reloads the entry; let it finish inside the test, or
    # it runs during teardown without the fake receiver.
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert loaded_entry.runtime_data.shown_zones == frozenset({"main"})
