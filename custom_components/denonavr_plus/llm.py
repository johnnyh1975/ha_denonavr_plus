"""LLM tools: this integration's intents (intent.py) for an LLM agent.

Home Assistant gives an LLM agent the intents each integration names in its
llm.py, here while one of this integration's media players is exposed to the
assistant. From Home Assistant 2026.10 a tool is named after the integration
and carries a title; 2026.8 names it after the intent.
"""

from __future__ import annotations

import inspect

from homeassistant.components.homeassistant import async_should_expose
from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers.llm import LLM_API_ASSIST, IntentTool, LLMContext, Tool

from .const import DOMAIN
from .intent import INTENT_START_ACTIVITY, INTENT_WHY_NO_SOUND

#: The intents offered, with their tool titles.
LLM_INTENTS = {
    INTENT_START_ACTIVITY: "Start receiver activity",
    INTENT_WHY_NO_SOUND: "Why no sound",
}

_NAMESPACED = "title" in inspect.signature(IntentTool).parameters


def _tool(handler: intent.IntentHandler) -> Tool:
    if not _NAMESPACED:
        return IntentTool(handler.intent_type, handler)
    return IntentTool(  # type: ignore[call-arg,unused-ignore]
        f"{DOMAIN}__{handler.intent_type}",
        handler,
        title=LLM_INTENTS[handler.intent_type],
        integration=DOMAIN,
    )


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: LLMContext, api_id: str
) -> LLMTools | None:
    """The intents as tools while one of this integration's players is exposed."""

    if api_id != LLM_API_ASSIST or not llm_context.assistant:
        return None
    registry = er.async_get(hass)
    if not any(
        (item := registry.async_get(state.entity_id)) is not None
        and item.platform == DOMAIN
        and async_should_expose(hass, llm_context.assistant, state.entity_id)
        for state in hass.states.async_all("media_player")
    ):
        return None
    tools = [_tool(handler) for handler in intent.async_get(hass) if handler.intent_type in LLM_INTENTS]
    return LLMTools(tools=tools) if tools else None
