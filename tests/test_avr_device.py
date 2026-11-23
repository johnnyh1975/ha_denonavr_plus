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
}


X2000_PROBES: list[dict[str, Any]] = []


async def _x2000_answering(
    host: str, queries: list[str], on_line: Any, is_complete: Any, **kwargs: Any
) -> None:
    X2000_PROBES.append(kwargs)
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
    # Its picture levels use another scale (brightness 00-12, not 000-100).
    assert "brightness" not in discovery.answered
    unique_ids = {
        item.unique_id.split("_", 1)[1]
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    }
    assert {"select_multeq", "switch_dynamic_eq", "select_dynamic_volume"} <= unique_ids
    assert "switch_cinema_eq" not in unique_ids


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
