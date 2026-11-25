"""Tests for avr/device.py."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.denonavr_plus.avr import capability_check
from custom_components.denonavr_plus.avr.errors import (
    CommandNotSent,
    SettingNotTaken,
    SourceNotTaken,
)
from custom_components.denonavr_plus.avr.transport import GoformClient
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .common import registry_entry
from .test_init import FakeHeos, FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


async def test_a_raw_command_to_an_unreachable_receiver_raises(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    # The media player is unavailable then, so the action never gets here;
    # the device still refuses rather than dropping the command silently.
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    send = AsyncMock(return_value=True)
    with (
        patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)),
        patch.object(GoformClient, "async_send_command", send),
    ):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        with pytest.raises(CommandNotSent) as err:
            await loaded_entry.runtime_data.device.async_send_command("PSDYNEQ ON")
    assert err.value.reason == "unreachable"
    send.assert_not_awaited()


async def test_an_own_query_is_dropped_quietly_without_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Queries are the integration's own; only a command asked for raises."""
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    device = loaded_entry.runtime_data.device
    with patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        await device._send("MV?")
        with pytest.raises(CommandNotSent):
            await device._send("MVUP")


async def test_the_capability_check_asks_every_query(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    device = loaded_entry.runtime_data.device
    answers = {"PW?": ["PWON"], "MV?": ["MV50", "MVMAX 80"], "PSFOO ?": ["?"]}

    async def answer(command: str) -> None:
        telnet.sent.append(command)
        if command in answers:
            hass.loop.call_soon(telnet.push, *answers[command])

    progress: list[float] = []
    queries = (("PW?", "Power"), ("MV?", "Volume"), ("PSFOO ?", "Test"), ("SSINF ?", "Test"))
    with (
        patch.object(telnet, "async_send", answer),
        patch.object(capability_check, "QUERIES", queries),
        patch("custom_components.denonavr_plus.avr.device.CHECK_REPLY_FIRST", 0.02),
        patch("custom_components.denonavr_plus.avr.device.REPLY_COLLECT_QUIET", 0.02),
    ):
        groups: list[str] = []
        result = await device.async_check_capabilities(progress.append, groups.append)
    # Each part is named once, as it starts.
    assert groups == ["Power", "Volume", "Test"]
    assert {command: item["result"] for command, item in result["queries"].items()} == {
        "PW?": "answered",
        "MV?": "answered",
        "PSFOO ?": "rejected",
        "SSINF ?": "no reply",
    }
    assert result["queries"]["MV?"]["lines"] == ["MV50", "MVMAX 80"]
    assert result["model"] == device.discovery.device.model_name
    assert result["generation"] == "2016"
    assert progress == [0.25, 0.5, 0.75, 1.0]


async def test_the_capability_check_needs_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    with pytest.raises(CommandNotSent):
        await loaded_entry.runtime_data.device.async_check_capabilities()


ANSWERS = {
    "STBY?": ["STBY15M"],
    "TR?": ["TR1 ON", "TR2 OFF"],
    "BTTX ?": ["BTTX OFF", "BTTX SP"],
    "SSTTR ?": ["SSTTR ON", "SSTTRLEV 52", "SSTTRLPF 080", "SSTTR END"],
}


async def _probe_answering(
    host: str, queries: list[str], on_line: Any, is_complete: Any, **kwargs: Any
) -> None:
    for query in queries:
        for line in ANSWERS.get(query, []):
            on_line(line)


#: As an AVR-X2000 answered (tests/fixtures/capability_check/AVR-X2000.json).
X2000_ANSWERS = {
    "PSMULTEQ: ?": ["PSMULTEQ:OFF"],
    "PSDYNEQ ?": ["PSDYNEQ ON"],
    "PSDYNVOL ?": ["PSDYNVOL MED"],
    "PSBAS ?": ["PSBAS 50"],
    "PVBR ?": ["PVBR 00"],
    "PW?": ["PWON"],
    "SSFUN ?": ["SSFUNDVD DVD", "SSFUNCD CD"],
    "SSSOD ?": ["SSSODDVD USE", "SSSODCD USE", "SSSODIRADIO USE"],
    "SSHOS ?": ["SSHOSCON ON", "SSHOSALS ON", "SSHOSIPM AUT"],
    "Z2SLP?": ["Z2SLPOFF"],
    "Z2QUICK ?": ["Z2QUICK0"],
    "SSQSNZMA ?": [f"SSQSNZMAQS{n} Quick Select {n}" for n in range(1, 5)],
    "Z2CV?": ["Z2CVFL 50", "Z2CVFR 50"],
    "CV?": ["CVFL 51", "CVFR 50", "CVC 50", "CVSW 38", "CVSBL 50"],
    "SSSPC ?": ["SSSPCFRO SMA", "SSSPCCEN SMA", "SSSPCSWF YES", "SSSPCSBK NON"],
}


X2000_PROBES: list[dict[str, Any]] = []


async def _x2000_answering(
    host: str, queries: list[str], on_line: Any, is_complete: Any, **kwargs: Any
) -> None:
    X2000_PROBES.append({**kwargs, "queries": queries})
    for query in queries:
        for line in X2000_ANSWERS.get(query, []):
            on_line(line)


async def test_features_an_older_receiver_does_not_name_appear_when_they_answer(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """An AVR-X2000 names few features in Deviceinfo but answers them over telnet.

    Its diagnostics (0.3.2) showed MultEQ, Dynamic EQ, Dynamic Volume and tone
    values from the receiver, yet no entity for any of them.
    """
    from homeassistant.const import CONF_PORT
    from homeassistant.helpers import entity_registry as er

    from .conftest import deviceinfo

    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _x2000_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("AVR-X2000-Deviceinfo.xml")),
        ),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.11", CONF_PORT: 80},
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    discovery = entry.runtime_data.device.discovery
    # It drops queries sent while it still answers: its probe waits for each.
    assert X2000_PROBES[-1]["reply_wait"] > 0
    assert not discovery.supports("MultEq")
    assert {"multeq", "dynamic_eq", "dynamic_volume", "bass"} <= discovery.answered
    # A feature it neither names nor answers stays away.
    assert "cinema_eq" not in discovery.answered
    # Its picture levels use another scale: brightness 00-12, not 000-100.
    assert "brightness" in discovery.answered
    assert entry.runtime_data.device.state.values["brightness"] == 0
    # Auto lip sync comes in its HDMI setup list; SSHOSALS ? gets no answer.
    assert "auto_lip_sync" in discovery.answered
    # It plays from the network as SINET but lists only the network services.
    assert "NET" in {source.code for source in discovery.sources}
    unique_ids = {
        item.unique_id.split("_", 1)[1]
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    assert {"select_multeq", "switch_dynamic_eq", "select_dynamic_volume"} <= unique_ids
    assert "switch_cinema_eq" not in unique_ids
    # Zone 2 settings, on the zone 2 device; no auto standby, it did not answer.
    registry = er.async_get(hass)
    zone2 = {
        item.unique_id.split("_", 1)[1]: item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.unique_id.endswith(("zone2_sleep_timer", "zone2_channel_level_fl"))
    }
    assert set(zone2) == {"number_zone2_sleep_timer", "number_zone2_channel_level_fl"}
    media_player = registry.async_get(
        registry.async_get_entity_id("media_player", DOMAIN, f"{item_id(entry)}_media_player_zone2")
    )
    assert {item.device_id for item in zone2.values()} == {media_player.device_id}
    assert "select_zone2_auto_standby" not in unique_ids
    # As many zone 2 quick selects as the main zone has: four, not five.
    quick = registry.async_get_entity_id(
        "select", DOMAIN, f"{item_id(entry)}_select_zone2_quick_select"
    )
    assert quick is not None
    assert registry.async_get(quick).capabilities["options"] == [
        f"quick_select_{n}" for n in range(1, 5)
    ]
    # No SSLEV on this model: the channel levels come from CV?, the
    # configured ones enabled; no distances.
    trims = {key: key for key in unique_ids if key.startswith("number_channel_trim_")}
    assert {"number_channel_trim_FL", "number_channel_trim_SW", "number_channel_trim_SBL"} <= set(trims)
    assert not any(key.startswith("number_channel_distance_") for key in unique_ids)


@pytest.mark.assumed(
    "the formats of TR and SSTTR come from the denonavr library and are what its "
    "parser expects; STBY and BTTX are as an AVR-X2600H answered "
    "(tests/fixtures/capability_check)"
)
async def test_controls_without_a_feature_name_appear_when_they_answer(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    with patch("custom_components.denonavr_plus.avr.device.async_probe", _probe_answering):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    device = entry.runtime_data.device
    assert device.discovery.answered == {
        "auto_standby",
        "trigger_1",
        "trigger_2",
        "bluetooth_transmitter",
        "tactile_transducer",
        "tactile_transducer_level",
        "tactile_transducer_lpf",
    }
    values = device.state.values
    assert values["auto_standby"] == "15M"
    assert values["trigger_1"] is True and values["trigger_2"] is False
    # BTTX SP (the output mode) and SSTTR END do not overwrite the switches.
    assert values["bluetooth_transmitter"] is False
    assert values["tactile_transducer"] is True
    assert values["tactile_transducer_level"] == 2.0  # 52: 50 is 0 dB
    assert values["tactile_transducer_lpf"] == "080"
    # They are asked again after every reconnect; the others are not.
    queries = device._resync_queries()
    assert "STBY?" in queries and "TR?" in queries
    assert "PSIMAX ?" not in queries
    for key in ("select_auto_standby", "switch_trigger_1", "number_tactile_transducer_level"):
        assert registry_entry(hass, entry, key)
    with pytest.raises(AssertionError):
        registry_entry(hass, entry, "select_imax")


async def test_the_resync_asks_what_the_receivers_answer(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Seen on an AVR-X2000 and an AVR-X2600H: both answer SLP? and Z2MU?, not SLP ?.

    Without them the sleep timer and the zone 2 mute stayed unknown until
    they changed (tests/fixtures/capability_check).
    """
    queries = loaded_entry.runtime_data.device._resync_queries()
    assert "SLP?" in queries and "SLP ?" not in queries
    assert "Z2MU?" in queries


async def test_the_setup_probe_ends_at_the_first_pause(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """A model without these controls stays silent; setup must not wait for them."""
    calls: list[tuple[list[str], Any]] = []

    async def probe(host: str, queries: list[str], on_line: Any, is_complete: Any, **kwargs: Any) -> None:
        calls.append((queries, is_complete))

    with patch("custom_components.denonavr_plus.avr.device.async_probe", probe):
        entry = MockConfigEntry(
            domain=DOMAIN, data={CONF_HOST: "192.0.2.10"}, minor_version=CONFIG_MINOR_VERSION
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    queries, is_complete = next(call for call in calls if "STBY?" in call[0])
    # Nothing has answered, yet the probe may end as soon as the line is quiet.
    assert is_complete() is True


def item_id(entry: MockConfigEntry) -> str:
    """The identifier the entities of an entry start with."""
    return entry.runtime_data.device.identifier


async def test_a_later_start_does_not_ask_again(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """The setup answers are kept: an AVR-X2000 takes 10-15 s to give them."""
    from homeassistant.const import CONF_PORT

    from .conftest import deviceinfo

    X2000_PROBES.clear()
    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _x2000_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("AVR-X2000-Deviceinfo.xml")),
        ),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.11", CONF_PORT: 80},
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        asked = [probe for probe in X2000_PROBES if "PSMULTEQ: ?" in probe["queries"]]
        assert len(asked) == 1
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    asked = [probe for probe in X2000_PROBES if "PSMULTEQ: ?" in probe["queries"]]
    assert len(asked) == 1
    assert "multeq" in entry.runtime_data.device.discovery.answered


async def test_an_older_receiver_gets_its_own_picture_scale(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """AVR-X2000 protocol v03: contrast 44-56 (50 is 0), brightness 00-12."""
    from homeassistant.const import CONF_PORT
    from homeassistant.helpers import entity_registry as er

    from .conftest import deviceinfo

    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _x2000_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("AVR-X2000-Deviceinfo.xml")),
        ),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.11", CONF_PORT: 80},
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        brightness = registry.async_get_entity_id(
            "number", DOMAIN, f"{item_id(entry)}_number_brightness"
        )
        assert brightness is not None
        registry.async_update_entity(brightness, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(brightness)
    assert state is not None
    assert (state.attributes["min"], state.attributes["max"]) == (0, 12)
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    telnet.echo = True
    await hass.services.async_call(
        "number", "set_value", {"entity_id": brightness, "value": 7}, blocking=True
    )
    assert "PVBR 07" in telnet.sent


async def test_a_2014_receiver_on_port_80_keeps_the_newer_picture_scale(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """AVR-X2100W (CommApiVers 0300, port 80): the 2014 and 2015 documents give
    picture levels as 000-100 like the newer receivers, not the AVR-X2000 scale."""
    from homeassistant.const import CONF_PORT
    from homeassistant.helpers import entity_registry as er

    from .conftest import deviceinfo

    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _x2000_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("AVR-X2100W-2-Deviceinfo.xml")),
        ),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.12", CONF_PORT: 80},
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        brightness = registry.async_get_entity_id(
            "number", DOMAIN, f"{item_id(entry)}_number_brightness"
        )
        assert brightness is not None
        registry.async_update_entity(brightness, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    state = hass.states.get(brightness)
    assert state is not None
    assert (state.attributes["min"], state.attributes["max"]) == (-50, 50)
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    telnet.echo = True
    await hass.services.async_call(
        "number", "set_value", {"entity_id": brightness, "value": 7}, blocking=True
    )
    assert "PVBR 057" in telnet.sent
    assert entry.runtime_data.device.profile.control("dialog_level").query == "PSDIL ?"


async def _x2000_entry(hass: HomeAssistant) -> MockConfigEntry:
    from homeassistant.const import CONF_PORT

    from .conftest import deviceinfo

    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _x2000_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("AVR-X2000-Deviceinfo.xml")),
        ),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.11", CONF_PORT: 80},
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.assumed(
    "NS9A-NS9E and NSE are documented for the AVR-X2000 generation; not yet "
    "tried on the user's AVR-X2000"
)
async def test_an_older_receiver_plays_over_telnet(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """No HEOS on an AVR-X2000: now playing is asked with NSE, transport is NS9x."""
    entry = await _x2000_entry(hass)
    device = entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.connected = True
    assert device.telnet_media and device.transport_available
    telnet.push("PWON", "ZMON", "SINET")
    telnet.sent.clear()
    await device.async_poll_media()
    assert telnet.sent == ["NSE"]

    telnet.push("NSE0Now Playing Internet Radio", "NSE1\x01Song")
    assert device.state.now_playing.title == "Song"
    url = device.media_art_url()
    assert url is not None and url.startswith("http://192.0.2.11:80/NetAudio/art.asp-jpg?")

    await device.async_media_pause()
    assert telnet.sent[-1] == "NS9B"
    assert device.state.now_playing.state == "pause"

    # The tuner: frequency and name are asked instead.
    telnet.push("SITUNER")
    telnet.sent.clear()
    await device.async_poll_media()
    assert telnet.sent == ["TFAN?", "TFANNAME?", "TPAN?"]
    # Off the network source, what played is cleared.
    assert device.state.now_playing.state is None


_X2000_DESCRIPTION = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0"><device>
<manufacturer>Denon</manufacturer><modelName>*AVR-X2000</modelName>
<modelNumber>X2000</modelNumber><serialNumber>0000000000000</serialNumber>
</device></root>"""


@pytest.mark.assumed(
    "description.xml on port 8080 for receivers of the AVR-X2000 generation comes "
    "from the denonavr library (const.DESCRIPTION_TYPES); the layout here is the "
    "UPnP standard, not a recording"
)
async def test_an_older_receiver_names_its_maker_and_serial(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    from custom_components.denonavr_plus.avr.transport import UpnpClient

    async def description(self: UpnpClient) -> str | None:
        return _X2000_DESCRIPTION if ":8080/description.xml" in self._url else None

    with patch.object(UpnpClient, "async_get_description", description):
        entry = await _x2000_entry(hass)
    device = entry.runtime_data.device.discovery.device
    assert (device.manufacturer, device.serial_number) == ("Denon", "0000000000000")
    # Its model number is no network module version.
    assert device.network_module_version is None


async def test_an_older_receiver_refuses_a_url(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """No HEOS on an AVR-X2000: a URL cannot play, and the user learns why."""
    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import entity_registry as er

    entry = await _x2000_entry(hass)
    main = er.async_get(hass).async_get_entity_id(
        "media_player", DOMAIN, f"{item_id(entry)}_media_player_main"
    )
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": main, "media_content_type": "music", "media_content_id": "http://example.com/a.mp3"},
            blocking=True,
        )
    assert err.value.translation_key == "play_url_unsupported"


@pytest.mark.assumed(
    "the AVR-X2000/E400 document names the tuner presets A1-G8; the AVR-X2000 "
    "answered TPANOFF, so its own form is unconfirmed"
)
async def test_zone_2_on_the_tuner_and_the_older_preset_names(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """The tuner is asked while zone 2 plays it; an AVR-X2000 names its presets A1-G8."""
    entry = await _x2000_entry(hass)
    device = entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.connected = True
    telnet.push("PWON", "ZMON", "SICD", "Z2ON", "Z2SOURCE")
    assert device.zone_source("zone2") == "CD"
    telnet.push("Z2TUNER")
    telnet.sent.clear()
    await device.async_poll_media()
    assert telnet.sent == ["TFAN?", "TFANNAME?", "TPAN?"]

    codes = device.tuner_preset_codes
    assert (codes[0], codes[7], codes[8], codes[-1], len(codes)) == ("A1", "A8", "B1", "G8", 56)
    telnet.sent.clear()
    await device.async_tuner_preset("zone2", "B3")
    assert telnet.sent == ["TPANB3"]
    with pytest.raises(ValueError):
        await device.async_tuner_preset("zone2", "01")
    telnet.sent.clear()
    await device.async_store_tuner_preset("B1")
    assert telnet.sent == ["TPANMEMB1"]
    with pytest.raises(ValueError):
        await device.async_store_tuner_preset("01")


async def test_a_preset_switches_to_the_tuner_first(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """Preset commands work only on the tuner: switch, wait for it, then recall."""
    entry = await _x2000_entry(hass)
    device = entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.connected = True
    telnet.push("PWON", "ZMON", "SICD")
    telnet.sent.clear()

    async def answer() -> None:
        await asyncio.sleep(0.05)
        telnet.push("SITUNER")

    task = asyncio.create_task(answer())
    await device.async_tuner_preset("main", "A2")
    await task
    assert telnet.sent == ["SITUNER", "TPANA2"]

    # A receiver that does not switch in time still gets the recall.
    telnet.push("SICD")
    telnet.sent.clear()
    with patch("custom_components.denonavr_plus.avr.device.TUNER_SWITCH_WAIT", 0.05):
        await device.async_tuner_preset("main", "A3")
    assert telnet.sent == ["SITUNER", "TPANA3"]


async def test_media_transport_goes_over_heos_where_there_is_heos(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    for action in (
        device.async_media_play,
        device.async_media_pause,
        device.async_media_stop,
        device.async_media_next,
        device.async_media_previous,
    ):
        await action()
    assert [call for call, _ in FakeHeos.instances[-1].calls] == [
        "play",
        "pause",
        "stop",
        "next",
        "previous",
    ]


async def test_settings_are_encoded_for_their_kind(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    await device.async_set_control("lfe", -5)
    await device.async_set_control("auro_matic_strength", 7)
    await device.async_set_control("zone2_sleep_timer", 0)
    await device.async_set_control("zone2_sleep_timer", 30)
    await device.async_set_control("does_not_exist", 1)
    await device.async_select_sound_mode_group("PUR")
    await device.async_volume_step("zone2", up=True)

    # LFE as the documents give it: -5 dB is 05 (an AVR-X2600H ignored -1).
    assert telnet.sent[:5] == ["PSLFE 05", "PSAUROST 07", "Z2SLPOFF", "Z2SLP030", "MSPURE DIRECT"]


async def test_receiver_facts_for_the_ui(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    assert device.host == "192.0.2.10"
    assert device.volume_display == "Relative"
    assert device.setup_url == "https://192.0.2.10:10443/"


async def test_a_command_over_http_reads_the_zones_again(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    status = AsyncMock(return_value={"power": True, "volume_db": -30.0})
    send = AsyncMock(return_value=True)
    with (
        patch.object(GoformClient, "async_get_status", status),
        patch.object(GoformClient, "async_send_command", send),
        patch("custom_components.denonavr_plus.avr.device._HTTP_COMMAND_SETTLE", 0),
    ):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        reads = status.await_count
        await loaded_entry.runtime_data.device.async_send_command("PSDYNEQ ON")
        # The read runs as a task after a settle time, here zero.
        for _ in range(5):
            await asyncio.sleep(0)
        await hass.async_block_till_done()
    send.assert_awaited_once_with("PSDYNEQ ON")
    assert status.await_count > reads


async def test_channel_trims_and_distances_are_sent_in_wire_units(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    await device.async_set_channel_trim("FL", -1.5)
    await device.async_set_channel_distance("FL", 3.45)

    assert telnet.sent[:2] == ["CVFL 485", "SSSDEFL 0345"]


async def test_a_source_zone_2_does_not_take(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An AVR-X2600H gave no reply to Z2AUX2 and kept its source."""
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.push("Z2ON", "Z2NET")
    with (
        patch("custom_components.denonavr_plus.avr.device.ZONE_SOURCE_WAIT", 0.05),
        pytest.raises(SourceNotTaken),
    ):
        await device.async_choose_source("zone2", "DVD")
    assert device.refused_sources("zone2") == {"DVD"}

    # Taken: the receiver echoes it, and a later success clears the mark.
    async def echo(command: str) -> None:
        telnet.sent.append(command)
        telnet.push(command)

    with patch.object(telnet, "async_send", echo):
        await device.async_choose_source("zone2", "DVD")
    assert device.refused_sources("zone2") == frozenset()
    assert device.zone_source("zone2") == "DVD"


async def test_a_repeated_signal_line_starts_no_new_query(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An AVR-X2600H repeats its signal lines after every OPSML query.

    Re-reading the sound mode lists on each repeat brought the next repeat:
    two queries every 3.6 s for as long as the receiver was on.
    """
    telnet = FakeTelnet.instances[-1]
    # The answers to the resync, then a change of signal.
    telnet.push("SSINFAISSIG 03", "SYSDA DOLBY")
    telnet.push("SSINFAISSIG 17", "SYSDA PCM")
    await asyncio.sleep(1.1)
    assert "OPSMLALL ?" in telnet.sent
    telnet.sent.clear()
    telnet.push("SSINFAISSIG 17", "SYSDA PCM")
    await asyncio.sleep(1.1)
    assert "OPSMLALL ?" not in telnet.sent


async def test_the_answers_to_the_resync_start_no_new_query(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """AVR-X2600H diagnostics (0.9.4): in its first five seconds the sound mode
    list was asked for five times and the full list twice; the first answers
    after connecting counted as changes."""
    telnet = FakeTelnet.instances[-1]
    asked = telnet.sent.count("OPSML ?"), telnet.sent.count("OPSMLALL ?")
    telnet.push("SSINFAISSIG 12", "SSINFAISFSV NON", "SYSDA Unknown", "SIAUX2", "MSDOLBY AUDIO-DD+DSUR")
    # The end of the answer to the resync's own OPSMLALL query.
    telnet.push("OPSMLALL MOV010Stereo", "OPSMLALL END")
    await asyncio.sleep(1.2)
    assert (telnet.sent.count("OPSML ?"), telnet.sent.count("OPSMLALL ?")) == asked
    # A pushed list right after (a change of genre) still brings the OPSML
    # query: each own query takes one END, not every END for a while.
    telnet.push("OPSMLALL MUS080Stereo", "OPSMLALL END")
    await asyncio.sleep(0.05)
    assert telnet.sent.count("OPSML ?") == asked[0] + 1


async def test_a_marantz_recalls_and_stores_smart_select(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """SR6012 (BrandCode 1): MSSMART<n> and Z2SMART<n>, as Marantz's 2019 document gives them."""
    from homeassistant.const import CONF_PORT

    from .conftest import deviceinfo

    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _x2000_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("SR6012-Deviceinfo-8080.xml")),
        ),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.13", CONF_PORT: 8080},
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    device = entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await device.async_select_quick_select(2)
    await device.async_select_quick_select(3, "zone2")
    await device.async_save_quick_select(4)
    assert "MSSMART2" in telnet.sent
    assert "Z2SMART3" in telnet.sent
    assert "MSSMART4 MEMORY" in telnet.sent
    assert not any(line.startswith(("MSQUICK", "Z2QUICK")) for line in telnet.sent)


async def test_a_marantz_gets_no_restorer_for_its_m_dax(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """SR6012: Deviceinfo names M-DAX Restorer; Marantz's 2019 document has no PSRSTR."""
    from homeassistant.const import CONF_PORT

    from .conftest import deviceinfo

    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _x2000_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("SR6012-Deviceinfo-8080.xml")),
        ),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.13", CONF_PORT: 8080},
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    device = entry.runtime_data.device
    assert device.discovery.supports("Restorer")
    spec = device.profile.control("restorer")
    assert spec is not None
    assert not device.discovery.has_control(spec.scope, spec.feature, spec.id)


#: A 2011 receiver without Deviceinfo.xml, as its document (AVR-1912/2112CI,
#: v7.4.0) gives the answers; it has no zone 3.
LEGACY_ANSWERS = {
    "Z2?": ["Z2ON", "Z2SOURCE", "Z250"],
    "PW?": ["PWON"],
    "PSRSTR ?": ["PSRSTR MODE2"],
    "PSDYNVOL ?": ["PSDYNVOL EVE"],
    "PSDYNEQ ?": ["PSDYNEQ ON"],
}
LEGACY_PROBES: list[dict[str, Any]] = []


async def _legacy_answering(
    host: str, queries: list[str], on_line: Any, is_complete: Any, **kwargs: Any
) -> None:
    LEGACY_PROBES.append({**kwargs, "queries": queries})
    for query in queries:
        for line in LEGACY_ANSWERS.get(query, []):
            on_line(line)


async def test_a_receiver_without_deviceinfo_is_set_up_from_its_main_zone(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    """AVR-1912 (2011): main zone document recorded by the denonavr library."""
    from homeassistant.const import CONF_PORT

    from custom_components.denonavr_plus.avr.transport import UpnpClient

    from .conftest import deviceinfo

    description = (
        '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
        "<manufacturer>Denon</manufacturer><modelName>*AVR-1912</modelName>"
        "<serialNumber>SN0000000000</serialNumber></device></root>"
    )
    with (
        patch("custom_components.denonavr_plus.avr.device.async_probe", _legacy_answering),
        patch.object(
            GoformClient,
            "async_get_device_info",
            AsyncMock(return_value=deviceinfo("AVR-1912-Deviceinfo.xml")),
        ),
        patch.object(
            GoformClient,
            "async_get_main_zone",
            AsyncMock(return_value=deviceinfo("AVR-1912-formMainZone_MainZoneXml.xml")),
        ),
        patch.object(UpnpClient, "async_get_description", AsyncMock(return_value=description)),
    ):
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.0.2.12", CONF_PORT: 80},
            unique_id="SN0000000000",
            minor_version=CONFIG_MINOR_VERSION,
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    device = entry.runtime_data.device
    discovery = device.discovery
    assert discovery.device.model_name == "AVR-1912"
    assert discovery.device.serial_number == "SN0000000000"
    # Zone 2 answered, zone 3 did not.
    assert [zone.id for zone in discovery.zones] == ["main", "zone2"]
    assert LEGACY_PROBES[0]["queries"] == ["Z2?", "Z3?"]
    # Inputs from the main zone document, with the user's names.
    names = {source.code: source.name for source in discovery.sources}
    assert names["DVD"] == "Chrome"
    # The 2011 encodings: restorer MODE1-3, Dynamic Volume NGT/EVE/DAY.
    assert {"restorer", "dynamic_volume", "dynamic_eq"} <= discovery.answered
    assert device.state.values["restorer"] == "MODE2"
    assert device.state.values["dynamic_volume"] == "EVE"
    assert "MULTI CH STEREO" in device.fallback_sound_modes
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()
    await device.async_set_control("restorer", "MODE1")
    assert "PSRSTR MODE1" in telnet.sent


async def test_a_setting_the_receiver_keeps_is_reported_with_its_reason(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The echo confirms a setting; without one, its query answers the old value."""
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.push("ZMON", "PSSWR ON", "PSDYNEQ ON")
    with patch("custom_components.denonavr_plus.avr.device.CONFIRM_WAIT", 0.05):
        telnet.echo = True
        telnet.sent.clear()
        await device.async_set_control_confirmed("subwoofer_switch", False)
        assert telnet.sent == ["PSSWR OFF"]

        # Outside Direct and Stereo the receiver keeps the subwoofer on.
        telnet.echo = False
        telnet.push("PSSWR ON")
        telnet.sent.clear()
        with pytest.raises(SettingNotTaken) as err:
            await device.async_set_control_confirmed("subwoofer_switch", False)
        assert (err.value.control_id, err.value.reason) == ("subwoofer_switch", "two_channel")
        assert telnet.sent == ["PSSWR OFF", "PSSWR ?"]

        # In standby any main-zone setting may be kept.
        telnet.push("ZMOFF")
        with pytest.raises(SettingNotTaken) as err:
            await device.async_set_control_confirmed("dynamic_eq", False)
        assert err.value.reason == "standby"

        # Taken anywhere, or the value it already has: only sent.
        telnet.push("ZMON")
        telnet.sent.clear()
        await device.async_set_control_confirmed("dynamic_eq", False)
        await device.async_set_control_confirmed("subwoofer_switch", True)
        await device.async_set_control_confirmed("no_such_control", True)
        assert telnet.sent == ["PSDYNEQ OFF", "PSSWR ON"]


async def test_a_setting_over_http_is_not_confirmed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """With telnet down nothing comes back to confirm by: sent, not waited for."""
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.push("ZMOFF", "PSSWR ON")
    telnet.connected = False
    with patch.object(type(device), "async_set_control", AsyncMock()) as send:
        await device.async_set_control_confirmed("subwoofer_switch", False)
    send.assert_awaited_once_with("subwoofer_switch", False)


_TONE_AND_MODE = (
    "<rx><cmd><surround>Multi Ch Stereo  </surround></cmd>"
    "<cmd><status>1</status><adjust>1</adjust><basslevel>+2dB</basslevel>"
    "<treblelevel>-1dB</treblelevel></cmd></rx>"
)
_AUDYSSEY = (
    '<rx><cmd><list><param name="multeq">1</param><param name="dynamiceq">0</param>'
    '<param name="dynamicvol">2</param></list></cmd></rx>'
)


def _answers(path: str, body: str) -> str | None:
    return _AUDYSSEY if path.endswith("AppCommand0300.xml") else _TONE_AND_MODE


async def test_without_telnet_the_sound_state_is_read_over_http(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Sound mode, tone and Audyssey over AppCommand(0300).xml until telnet is back."""
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    assert telnet._on_availability is not None
    telnet._on_availability(False)
    post = AsyncMock(side_effect=_answers)
    status = {"power": True, "source": "CD", "volume_db": -40.0, "muted": False}
    with (
        patch.object(GoformClient, "async_get_status", AsyncMock(return_value=status)),
        patch.object(GoformClient, "async_post", post),
    ):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    values = device.state.values
    assert values["sound_mode_display"] == "Multi Ch Stereo"
    assert (values["tone_control"], values["bass"], values["treble"]) == (True, 2.0, -1.0)
    assert (values["multeq"], values["dynamic_eq"], values["dynamic_volume"]) == (
        "FLAT",
        False,
        "MED",
    )
    assert device.http_values == {
        "sound_mode_display", "tone_control", "bass", "treble",
        "multeq", "dynamic_eq", "dynamic_volume",
    }
    telnet.connected = True
    telnet._on_availability(True)
    assert device.http_values == frozenset()


@pytest.mark.parametrize(("level", "posts"), [(None, 0), (220, 1)])
async def test_older_receivers_are_asked_only_what_their_level_has(
    hass: HomeAssistant, loaded_entry: MockConfigEntry, level: int | None, posts: int
) -> None:
    """No AppCommand.xml before level 0210, no AppCommand0300.xml before 0300."""
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    post = AsyncMock(side_effect=_answers)
    identity = replace(device.discovery.device, comm_api_version=level)
    with (
        patch.object(device.discovery, "device", identity),
        patch.object(GoformClient, "async_get_status", AsyncMock(return_value={"power": True})),
        patch.object(GoformClient, "async_post", post),
    ):
        await device.async_poll()
    assert post.await_count == posts


async def test_values_read_over_http_follow_the_page_and_yield_to_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Tone control off the list leaves bass and treble unknown; telnet back wins."""
    device = loaded_entry.runtime_data.device
    telnet = FakeTelnet.instances[-1]
    telnet.connected = False
    status = AsyncMock(return_value={"power": True})
    post = AsyncMock(side_effect=_answers)
    with (
        patch.object(GoformClient, "async_get_status", status),
        patch.object(GoformClient, "async_post", post),
    ):
        await device.async_poll()
        assert device.state.values["bass"] == 2.0
        no_tone = "<rx><cmd><surround>Direct</surround></cmd><cmd><status>0</status></cmd></rx>"
        post.side_effect = lambda path, body: (
            _AUDYSSEY if path.endswith("AppCommand0300.xml") else no_tone
        )
        await device.async_poll()
        assert "bass" not in device.state.values
        assert "bass" not in device.http_values
        assert device.state.values["sound_mode_display"] == "Direct"

        # Telnet came back while a page was on its way: telnet's values stand.
        telnet.push("PSBAS 47")

        async def answer_late(path: str, body: str) -> str:
            telnet.connected = True
            return _TONE_AND_MODE

        post.side_effect = answer_late
        await device.async_poll()
    assert device.state.values["bass"] == -3.0
