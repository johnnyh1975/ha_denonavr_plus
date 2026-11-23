"""Tests for __init__.py: setting up and unloading a receiver.

The receiver's network is replaced at its edges only: the Deviceinfo and
status documents are served from recordings, and the telnet and HEOS
transports are fakes built through the same constructor signature as the
real ones (AGENTS.md, rule 3). Everything between -- discovery, the parser,
the coordinator and the entities -- is the real code.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus.avr import DenonAvrDevice
from custom_components.denonavr_plus.avr.transport import (
    GoformClient,
    UpnpClient,
    WebConfigClient,
)
from custom_components.denonavr_plus.const import CONFIG_MINOR_VERSION, DOMAIN

from .conftest import deviceinfo

HOST = "192.0.2.10"
RECORDING = "AVC-X3700H-Deviceinfo-8080.xml"


class FakeTelnet:
    """Stands in for TelnetClient: records commands, lets a test push lines."""

    instances: list[FakeTelnet] = []

    def __init__(
        self,
        host: str,
        on_line: Callable[[str], None],
        on_connected: Callable[[], Awaitable[None]] | None = None,
        on_availability: Callable[[bool], None] | None = None,
        port: int = 23,
    ) -> None:
        self.host = host
        self._on_line = on_line
        self._on_connected = on_connected
        self._on_availability = on_availability
        self.connected = False
        self.sent: list[str] = []
        self.response_time: float | None = 0.042
        self.queued = 0
        FakeTelnet.instances.append(self)

    async def async_start(self) -> None:
        self.connected = True
        if self._on_availability is not None:
            self._on_availability(True)
        if self._on_connected is not None:
            await self._on_connected()

    async def async_stop(self) -> None:
        self.connected = False

    async def async_send(self, command: str) -> None:
        self.sent.append(command)

    def push(self, *lines: str) -> None:
        for line in lines:
            self._on_line(line)


class FakeHeos:
    """Stands in for HeosClient: records play mode commands, lets a test push now playing."""

    instances: list[FakeHeos] = []

    def __init__(self, host: str, on_update: Callable[[dict[str, Any]], None], port: int = 1255) -> None:
        self.host = host
        self.on_update = on_update
        self.calls: list[tuple[str, Any]] = []
        self.connected = False
        self.command_results: dict[str, dict[str, str]] = {}
        FakeHeos.instances.append(self)

    async def async_set_repeat(self, repeat: str) -> None:
        self.calls.append(("repeat", repeat))

    async def async_set_shuffle(self, shuffle: bool) -> None:
        self.calls.append(("shuffle", shuffle))

    async def async_play_url(self, url: str) -> bool:
        self.calls.append(("url", url))
        return True

    async def async_start(self) -> None:
        return None

    async def async_stop(self) -> None:
        return None


_ORIGINAL_AWAIT_READY = DenonAvrDevice.async_await_ready


async def _await_ready_without_waiting(self: DenonAvrDevice, timeout: float = 8.0) -> None:
    # The real method waits up to 8 s for the first resync; the fakes answer
    # nothing, so skip the wait but keep the fallback logic it runs.
    await _ORIGINAL_AWAIT_READY(self, timeout=0)


@pytest.fixture
def receiver_network() -> Any:
    """Patch the receiver's network edges; yields the patch context."""
    FakeTelnet.instances.clear()
    FakeHeos.instances.clear()
    with (
        patch("custom_components.denonavr_plus.avr.device.TelnetClient", FakeTelnet),
        patch("custom_components.denonavr_plus.avr.device.HeosClient", FakeHeos),
        patch("custom_components.denonavr_plus.avr.device.async_probe", AsyncMock()),
        patch.object(
            GoformClient, "async_get_device_info", AsyncMock(return_value=deviceinfo(RECORDING))
        ),
        patch.object(GoformClient, "async_get_status", AsyncMock(return_value=None)),
        patch.object(UpnpClient, "async_get_description", AsyncMock(return_value=None)),
        patch.object(WebConfigClient, "async_get", AsyncMock(return_value=None)),
        patch.object(DenonAvrDevice, "async_await_ready", _await_ready_without_waiting),
    ):
        yield


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, receiver_network: Any
) -> AsyncIterator[MockConfigEntry]:
    # Created at the current version; tests/test_migrations.py covers older ones.
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: HOST}, minor_version=CONFIG_MINOR_VERSION
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry


def _main_player(hass: HomeAssistant, entry: MockConfigEntry) -> str:
    registry = er.async_get(hass)
    for item in er.async_entries_for_config_entry(registry, entry.entry_id):
        if item.domain == "media_player" and item.unique_id.endswith("_media_player_main"):
            return item.entity_id
    raise AssertionError("no main zone media player")


async def _settle(hass: HomeAssistant) -> None:
    # Device updates are debounced by 0.1 s before they reach the coordinator.
    await asyncio.sleep(0.15)
    await hass.async_block_till_done()


async def test_setup_creates_receiver_and_zones(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    registry = er.async_get(hass)
    players = [
        e for e in er.async_entries_for_config_entry(registry, loaded_entry.entry_id)
        if e.domain == "media_player"
    ]
    # The recording reports two zones.
    assert len(players) == 2


async def test_setup_resynchronises_on_connect(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    assert telnet.host == HOST
    # The full state is queried after connecting, power and volume among it.
    assert "PW?" in telnet.sent
    assert "MV?" in telnet.sent


async def test_pushed_state_reaches_the_media_player(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    entity_id = _main_player(hass, loaded_entry)
    FakeTelnet.instances[-1].push("PWON", "ZMON", "MVMAX 98", "MV49", "MUOFF", "SITV")
    await _settle(hass)
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_ON
    assert state.attributes["volume_level"] == pytest.approx(49 / 98)
    assert state.attributes["is_volume_muted"] is False


async def test_media_player_commands_reach_telnet(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    entity_id = _main_player(hass, loaded_entry)
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON", "MVMAX 98", "MV40")
    await _settle(hass)
    telnet.sent.clear()

    await hass.services.async_call(
        "media_player", "volume_set", {"entity_id": entity_id, "volume_level": 0.5}, blocking=True
    )
    await hass.services.async_call(
        "media_player", "turn_off", {"entity_id": entity_id}, blocking=True
    )
    assert telnet.sent == ["MV49", "ZMOFF"]


async def test_lost_link_makes_entities_unavailable(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    entity_id = _main_player(hass, loaded_entry)
    telnet = FakeTelnet.instances[-1]
    telnet.push("PWON", "ZMON")
    await _settle(hass)
    assert hass.states.get(entity_id).state == STATE_ON

    assert telnet._on_availability is not None
    telnet._on_availability(False)
    await _settle(hass)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_unload_stops_the_link(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    telnet = FakeTelnet.instances[-1]
    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED
    assert telnet.connected is False


async def test_unreachable_receiver_retries_setup(
    hass: HomeAssistant, receiver_network: Any
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HOST: HOST})
    entry.add_to_hass(hass)
    with patch.object(GoformClient, "async_get_device_info", AsyncMock(return_value=None)):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key == "cannot_reach"


async def test_setup_remembers_the_goform_port(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """An entry created without a port learns it at its first setup."""
    assert loaded_entry.data[CONF_PORT] == 8080


async def test_a_zone_switched_off_loses_its_entities(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    registry = er.async_get(hass)

    def zone2_entities() -> list[str]:
        return [
            item.unique_id
            for item in er.async_entries_for_config_entry(registry, loaded_entry.entry_id)
            if item.unique_id.endswith("_zone2")
        ]

    assert len(zone2_entities()) == 3  # media player, volume, source sensor
    # Switching zone 2 off reloads the entry and removes its entities.
    hass.config_entries.async_update_entry(loaded_entry, options={"zones": []})
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert zone2_entities() == []
    # Switching it on again brings them back.
    hass.config_entries.async_update_entry(loaded_entry, options={"zones": ["zone2"]})
    await hass.async_block_till_done()
    assert len(zone2_entities()) == 3


async def test_the_device_shows_no_protocol_family_as_hardware(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """hardware_type is the protocol family (avr-x-2016), not the hardware."""
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), loaded_entry.entry_id)
    assert devices
    assert all(device.hw_version is None for device in devices)


async def test_sub_devices_follow_the_receivers_room(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Seen on an AVR-X2600H: only the receiver was in its room, not Audio, Video..."""
    areas = ar.async_get(hass)
    living, kitchen, cellar = (areas.async_create(name).id for name in ("Living", "Kitchen", "Cellar"))
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, loaded_entry.entry_id)
    main = next(device for device in devices if device.via_device_id is None)
    subs = [
        device
        for device in devices
        if device.via_device_id == main.id and not device.name.endswith("Zone 2")
    ]
    zone2 = next(device for device in devices if device.name.endswith("Zone 2"))
    assert len(subs) >= 2

    registry.async_update_device(main.id, area_id=living)
    await hass.async_block_till_done()
    assert {registry.async_get(sub.id).area_id for sub in subs} == {living}
    # Zone 2 plays in a room of its own: it does not follow.
    assert registry.async_get(zone2.id).area_id is None

    # One the user puts elsewhere stays there when the receiver moves.
    registry.async_update_device(subs[0].id, area_id=cellar)
    registry.async_update_device(main.id, area_id=kitchen)
    await hass.async_block_till_done()
    assert registry.async_get(subs[0].id).area_id == cellar
    assert {registry.async_get(sub.id).area_id for sub in subs[1:]} == {kitchen}

    # A sub-device without a room takes the receiver's at the next start.
    registry.async_update_device(subs[1].id, area_id=None)
    await hass.config_entries.async_reload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get(subs[1].id).area_id == kitchen


async def test_zone_2_has_a_device_of_its_own(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Zone 2 usually plays in another room, so it gets a device with its own room."""
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    main = next(
        device
        for device in dr.async_entries_for_config_entry(devices, loaded_entry.entry_id)
        if device.via_device_id is None
    )
    on_zone2 = {
        entity_id: entities.async_get(entity_id).device_id
        for entity_id in (
            "media_player.avc_x3700h_zone_2",
            "number.avc_x3700h_zone_2_volume",
            "sensor.avc_x3700h_zone_2_source",
        )
    }
    (device_id,) = set(on_zone2.values())
    zone2 = devices.async_get(device_id)
    assert zone2.name == "AVC-X3700H Zone 2"
    assert zone2.via_device_id == main.id
    assert entities.async_get("media_player.avc_x3700h").device_id == main.id

    # Left out in the options, the zone loses its device with its entities.
    hass.config_entries.async_update_entry(loaded_entry, options={"zones": []})
    await hass.async_block_till_done()
    assert devices.async_get(device_id) is None
