"""Tests for logbook.py: the logbook tells why the main zone switched."""

from __future__ import annotations

from typing import Any

from homeassistant.core import Event, HomeAssistant

from custom_components.denonavr_plus import logbook
from custom_components.denonavr_plus.const import DOMAIN
from custom_components.denonavr_plus.linked_players import EVENT_INPUT_FOLLOWED


async def test_following_a_player_reads_as_a_sentence(hass: HomeAssistant) -> None:
    described: dict[str, Any] = {}

    def register(domain: str, event_type: str, describe: Any) -> None:
        described[event_type] = (domain, describe)

    logbook.async_describe_events(hass, register)
    domain, describe = described[EVENT_INPUT_FOLLOWED]
    assert domain == DOMAIN
    hass.states.async_set("media_player.avr", "on", {"friendly_name": "Living room receiver"})
    hass.states.async_set("media_player.apple_tv", "playing", {"friendly_name": "Apple TV"})
    entry = describe(
        Event(
            EVENT_INPUT_FOLLOWED,
            {
                "entity_id": "media_player.avr",
                "source": "Apple TV",
                "previous_source": None,
                "player": "media_player.apple_tv",
            },
        )
    )
    assert entry == {
        "name": "Living room receiver",
        "message": "switched to Apple TV because Apple TV started",
        "entity_id": "media_player.avr",
    }
    # A player gone since: its id stands in.
    entry = describe(
        Event(EVENT_INPUT_FOLLOWED, {"entity_id": "media_player.avr", "source": "CD", "player": "media_player.gone"})
    )
    assert entry["message"] == "switched to CD because media_player.gone started"
    # Without a player named (an older event): just where it switched.
    entry = describe(Event(EVENT_INPUT_FOLLOWED, {"entity_id": "media_player.gone", "source": "CD"}))
    assert entry["message"] == "switched to CD"
    assert entry["name"] == "media_player.gone"

