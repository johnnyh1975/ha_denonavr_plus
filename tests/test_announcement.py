"""Tests for announcement.py: an announcement plays, then the zone is put back."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import volume
from custom_components.denonavr_plus.announcement import Phase
from custom_components.denonavr_plus.avr.errors import CommandNotSent

from .common import entity_id
from .fakes import FakeTelnet
from .test_media_player import _eventually, _heos_state, _push


async def test_an_announcement_puts_the_zone_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Input, volume and mute are kept, the announcement plays loud enough, then all is back."""
    from .fakes import FakeHeos

    hass.config_entries.async_update_entry(
        loaded_entry, options={"main": {volume.OPT_ANNOUNCE_VOLUME: -10.0}}
    )
    await hass.async_block_till_done()
    await _push(hass, "PWON", "ZMON", "SITV", "MV50", "MUON")
    heos = FakeHeos.instances[-1]
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]

    def phase() -> Phase:
        player = hass.data["entity_components"]["media_player"].get_entity(main)
        return player._announcement.phase

    def restored() -> list[str]:
        # The receiver's own queries may run in between (a resync after the input change).
        return [c for c in telnet.sent if c[:2] in ("SI", "MV", "MU", "ZM", "PW")]

    async def announce(extra: dict[str, Any] | None = None) -> None:
        data = {
            "entity_id": main,
            "media_content_type": "music",
            "media_content_id": "http://example.com/tts.mp3",
            "announce": True,
        }
        if extra:
            data["extra"] = extra
        await hass.services.async_call(
            "media_player", "play_media", data, blocking=True
        )

    telnet.sent.clear()
    with (
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_POLL", 0.01),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_STOP_GRACE", 0.5),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_SETTLE", 0.05),
    ):
        assert phase() is Phase.IDLE
        await announce()
        assert phase() is Phase.PLAYING
        # Loud enough, and a muted zone is unmuted for it.
        assert telnet.sent == ["MV70", "MUOFF"]
        assert heos.calls[-1] == ("url", "http://example.com/tts.mp3")
        await _push(hass, "SINET", "MV70", "MUOFF")
        heos.on_update(_heos_state("play"))
        # A second announcement while the first plays keeps the first snapshot.
        await announce({"volume": 0.5})
        # HEOS stops the first stream, then plays the second: a play before
        # that stop is still the first one.
        heos.on_update(_heos_state("stop"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.05)
        telnet.sent.clear()
        # A short stop while HEOS switches is no end.
        heos.on_update(_heos_state("stop"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.6)
        assert restored() == []
        # Meanwhile its input changes are no one's choice (linked players).
        assert loaded_entry.runtime_data.announcing == {"main"}
        heos.on_update(_heos_state("stop"))
        await _eventually(lambda: restored() == ["SITV", "MV50", "MUON"])
        await _eventually(lambda: phase() is Phase.SETTLING)
        # A moment after the restore, the input is someone's choice again.
        await _eventually(lambda: not loaded_entry.runtime_data.announcing)
        assert phase() is Phase.IDLE


async def test_an_announcement_that_does_not_start_puts_the_zone_back_at_once(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """HEOS refuses the URL: the zone is put back before the error is reported."""
    from .fakes import FakeHeos

    await _push(hass, "PWON", "ZMOFF", "SITV", "MV50")
    heos = FakeHeos.instances[-1]
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    telnet.sent.clear()

    async def answer() -> None:
        await _eventually(lambda: "ZMON" in telnet.sent)
        telnet.push("ZMON")

    task = asyncio.create_task(answer())
    with (
        patch.object(heos, "async_play_url", AsyncMock(return_value=False)),
        patch("custom_components.denonavr_plus.media_player.TURN_ON_SETTLE", 0),
        pytest.raises(HomeAssistantError),
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
    await task
    assert telnet.sent[0] == "ZMON" and telnet.sent[-1] == "ZMOFF"

    # Without a HEOS link nothing is switched at all.
    heos.connected = False
    telnet.sent.clear()
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
    assert telnet.sent == []


async def test_an_announcement_that_never_starts_still_puts_the_zone_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """No volume option: the volume stays; HEOS never reports playing; a failing restore is logged."""
    await _push(hass, "PWON", "ZMON", "SITV", "MV50")
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    device = loaded_entry.runtime_data.device
    with patch(
        "custom_components.denonavr_plus.announcement.ANNOUNCE_START_WAIT", 0.05
    ):
        telnet.sent.clear()
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
        assert telnet.sent == []
        await _push(hass, "SINET")
        await asyncio.sleep(0.3)
        await hass.async_block_till_done()
        assert "SITV" in telnet.sent

        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
        with patch.object(
            type(device), "async_restore", AsyncMock(side_effect=CommandNotSent("gone"))
        ) as restore:
            await asyncio.sleep(0.3)
            await hass.async_block_till_done()
        assert restore.await_count == 1

    from homeassistant.exceptions import HomeAssistantError as _Error

    from custom_components.denonavr_plus.const import DOMAIN

    await hass.services.async_call(
        DOMAIN, "snapshot", {"entity_id": main}, blocking=True
    )
    with (
        patch.object(
            type(device), "async_restore", AsyncMock(side_effect=CommandNotSent("gone"))
        ),
        pytest.raises(_Error),
    ):
        await hass.services.async_call(
            DOMAIN, "restore", {"entity_id": main}, blocking=True
        )


async def test_an_announcement_without_heos_leaves_no_window(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """Refused before it starts: the linked players are not held off afterwards."""
    from .fakes import FakeHeos

    FakeHeos.instances[-1].connected = False
    await _push(hass, "PWON", "ZMON", "SICD")
    main = entity_id(hass, loaded_entry, "media_player_main")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
    assert loaded_entry.runtime_data.announcing == set()


async def test_a_second_announcement_keeps_its_window(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The first one's settle time ends in the middle of the second: the window stays."""
    from .fakes import FakeHeos

    await _push(hass, "PWON", "ZMON", "SICD")
    heos = FakeHeos.instances[-1]
    main = entity_id(hass, loaded_entry, "media_player_main")
    announcing = loaded_entry.runtime_data.announcing

    async def announce() -> None:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )

    with (
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_POLL", 0.01),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_STOP_GRACE", 0.05),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_SETTLE", 0.3),
    ):
        await announce()
        await _push(hass, "SINET")
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("stop"))
        # Restored; the window closes 0.3 s later unless another one comes.
        await _eventually(lambda: "SICD" in FakeTelnet.instances[-1].sent)
        await _push(hass, "SICD")
        await announce()
        await _push(hass, "SINET")
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.5)
        assert announcing == {"main"}
        heos.on_update(_heos_state("stop"))
        await _eventually(lambda: announcing == set())


async def test_an_announcement_during_the_restore_still_restores(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A doorbell while the zone is put back after the first one: the zone still comes back."""
    from .fakes import FakeHeos

    await _push(hass, "PWON", "ZMON", "SICD")
    heos = FakeHeos.instances[-1]
    device = loaded_entry.runtime_data.device
    main = entity_id(hass, loaded_entry, "media_player_main")
    restoring = asyncio.Event()
    restored: list[Any] = []
    original = device.async_restore

    async def slow_restore(zone_id: str, snapshot: Any) -> None:
        if not restored:
            restored.append(None)
            restoring.set()
            await asyncio.sleep(10)
        restored.append(snapshot)
        await original(zone_id, snapshot)

    async def announce() -> None:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )

    with (
        patch.object(device, "async_restore", slow_restore),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_POLL", 0.01),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_STOP_GRACE", 0.05),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_SETTLE", 0.05),
    ):
        await announce()
        await _push(hass, "SINET")
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("stop"))
        await asyncio.wait_for(restoring.wait(), 3)
        await announce()
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("stop"))
        await _eventually(lambda: len(restored) == 2)
    assert restored[1] is not None and restored[1].source == "CD"


async def test_an_announcement_right_after_a_restore_puts_back_the_first_state(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """The receiver has not yet reported the restored input: the first snapshot holds."""
    from .fakes import FakeHeos

    await _push(hass, "PWON", "ZMON", "SICD")
    heos = FakeHeos.instances[-1]
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]

    async def announce() -> None:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )

    with (
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_POLL", 0.01),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_STOP_GRACE", 0.05),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_SETTLE", 0.3),
    ):
        await announce()
        await _push(hass, "SINET")
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("stop"))
        await _eventually(lambda: "SICD" in telnet.sent)
        # No echo yet: the zone still reads NET.
        telnet.sent.clear()
        await announce()
        heos.on_update(_heos_state("play"))
        await asyncio.sleep(0.05)
        heos.on_update(_heos_state("stop"))
        await _eventually(lambda: "SICD" in telnet.sent)
        await _eventually(lambda: loaded_entry.runtime_data.announcing == set())


async def test_turning_off_during_an_announcement_puts_nothing_back(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    """A restore after the turn off would switch the zone on again."""
    from .fakes import FakeHeos

    await _push(hass, "PWON", "ZMON", "SICD")
    heos = FakeHeos.instances[-1]
    main = entity_id(hass, loaded_entry, "media_player_main")
    telnet = FakeTelnet.instances[-1]
    with (
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_POLL", 0.01),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_STOP_GRACE", 0.05),
        patch("custom_components.denonavr_plus.announcement.ANNOUNCE_SETTLE", 0.05),
    ):
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": main,
                "media_content_type": "music",
                "media_content_id": "http://example.com/tts.mp3",
                "announce": True,
            },
            blocking=True,
        )
        await _push(hass, "SINET")
        heos.on_update(_heos_state("play"))
        await hass.services.async_call("media_player", "turn_off", {"entity_id": main}, blocking=True)
        await _push(hass, "ZMOFF")
        telnet.sent.clear()
        heos.on_update(_heos_state("stop"))
        await asyncio.sleep(0.3)
    assert [c for c in telnet.sent if c[:2] in ("SI", "ZM", "PW")] == []
    assert loaded_entry.runtime_data.announcing == set()
