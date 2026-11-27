"""Tests for llm.py."""

from __future__ import annotations

from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import llm as ha_llm
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.denonavr_plus import llm
from custom_components.denonavr_plus.intent import (
    INTENT_SET_SOUND_MODE,
    INTENT_START_ACTIVITY,
    INTENT_WHY_NO_SOUND,
)

from .common import entity_id
from .test_init import loaded_entry, receiver_network  # noqa: F401 -- fixtures

__all__ = ["loaded_entry", "receiver_network"]


def _context(assistant: str | None = "conversation") -> ha_llm.LLMContext:
    return ha_llm.LLMContext(
        platform="test",
        context=Context(),
        language="en",
        assistant=assistant,
        device_id=None,
    )


async def test_the_intents_are_tools_while_a_zone_is_exposed(
    hass: HomeAssistant, loaded_entry: MockConfigEntry
) -> None:
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "intent", {})
    await hass.async_block_till_done()
    for key in ("media_player_main", "media_player_zone2"):
        async_expose_entity(hass, "conversation", entity_id(hass, loaded_entry, key), True)
    found = llm.async_get_tools(hass, _context(), ha_llm.LLM_API_ASSIST)
    assert found is not None
    intents = sorted(getattr(tool, "intent_type", tool.name) for tool in found.tools)
    assert intents == sorted([INTENT_SET_SOUND_MODE, INTENT_START_ACTIVITY, INTENT_WHY_NO_SOUND])
    # The descriptions are what the LLM reads to pick a tool.
    assert all("Denon or Marantz" in tool.description for tool in found.tools)

    # Not for another API, without an assistant, or with no zone exposed.
    assert llm.async_get_tools(hass, _context(), "other_api") is None
    assert llm.async_get_tools(hass, _context(None), ha_llm.LLM_API_ASSIST) is None
    for key in ("media_player_main", "media_player_zone2"):
        async_expose_entity(hass, "conversation", entity_id(hass, loaded_entry, key), False)
    assert llm.async_get_tools(hass, _context(), ha_llm.LLM_API_ASSIST) is None
