"""Logbook texts for the events of Denon AVR Plus.

Without them the logbook shows the raw event name. The logbook writes its
texts in English for every integration; these follow that.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import (
    LOGBOOK_ENTRY_ENTITY_ID,
    LOGBOOK_ENTRY_MESSAGE,
    LOGBOOK_ENTRY_NAME,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN
from .linked_players import EVENT_INPUT_FOLLOWED


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    """Describe the events this integration fires."""

    @callback
    def describe_input_followed(event: Event) -> dict[str, Any]:
        data = event.data
        zone = data.get(ATTR_ENTITY_ID)
        zone_state = hass.states.get(zone) if zone else None
        player = data.get("player")
        player_state = hass.states.get(player) if player else None
        message = f"switched to {data.get('source')}"
        if player:
            message += f" because {player_state.name if player_state else player} started"
        return {
            LOGBOOK_ENTRY_NAME: zone_state.name if zone_state else (zone or DOMAIN),
            LOGBOOK_ENTRY_MESSAGE: message,
            LOGBOOK_ENTRY_ENTITY_ID: zone,
        }

    async_describe_event(DOMAIN, EVENT_INPUT_FOLLOWED, describe_input_followed)
