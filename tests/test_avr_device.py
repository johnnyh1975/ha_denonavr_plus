"""Tests for avr/device.py."""

from __future__ import annotations

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
from custom_components.denonavr_plus.avr.errors import CommandNotSent
from custom_components.denonavr_plus.avr.transport import GoformClient
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .common import registry_entry
from .test_init import FakeTelnet, loaded_entry, receiver_network  # noqa: F401 -- fixtures

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
        result = await device.async_check_capabilities(progress.append)
    assert {command: item["result"] for command, item in result["queries"].items()} == {
        "PW?": "answered",
        "MV?": "answered",
        "PSFOO ?": "rejected",
        "SSINF ?": "no reply",
    }
    assert result["queries"]["MV?"]["lines"] == ["MV50", "MVMAX 80"]
    assert result["model"] == device.discovery.device.model_name
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
    await hass.services.async_call(
        "number", "set_value", {"entity_id": brightness, "value": 7}, blocking=True
    )
    assert "PVBR 07" in telnet.sent


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
    assert telnet.sent == ["TFAN?", "TFANNAME?"]
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
