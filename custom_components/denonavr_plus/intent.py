"""Intents: start an activity, choose the sound mode and ask why a zone is
silent, by voice or LLM.

Home Assistant sets this platform up with its intent integration. The
default conversation agent reaches the intents through sentences the user
adds (docs/USER_GUIDE.md has examples in English and German); an LLM agent
reaches them as tools (llm.py). Speech clarity needs no intent of its own:
its switch answers "turn on speech clarity" like any switch.

A zone is its media player: the name and area slots choose among this
integration's players exposed to the assistant; without them, the zone that
has the activity, preferring the area of the voice satellite.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import DATA_COMPONENT
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent

from . import no_sound
from .const import DOMAIN
from .entity import unique_id

INTENT_START_ACTIVITY = "DenonAvrStartActivity"
INTENT_WHY_NO_SOUND = "DenonAvrWhyNoSound"
INTENT_SET_SOUND_MODE = "DenonAvrSetSoundMode"

_TARGET_SLOTS = {
    vol.Optional("name"): cv.string,
    vol.Optional("area"): cv.string,
    vol.Optional("floor"): cv.string,
    vol.Optional("preferred_area_id"): cv.string,
    vol.Optional("preferred_floor_id"): cv.string,
}


async def async_setup_intents(hass: HomeAssistant) -> None:
    """Register the intents (called by Home Assistant's intent integration)."""

    intent.async_register(hass, StartActivityHandler())
    intent.async_register(hass, WhyNoSoundHandler())
    intent.async_register(hass, SetSoundModeHandler())


def _value(slots: dict[str, Any], name: str) -> Any:
    return slots.get(name, {}).get("value")


def _players(hass: HomeAssistant, intent_obj: intent.Intent, slots: dict[str, Any]) -> list[Any]:
    """This integration's media players the slots and the assistant allow."""

    constraints = intent.MatchTargetsConstraints(
        name=_value(slots, "name"),
        area_name=_value(slots, "area"),
        floor_name=_value(slots, "floor"),
        domains={"media_player"},
        assistant=intent_obj.assistant,
        allow_duplicate_names=True,
    )
    result = intent.async_match_targets(hass, constraints)
    if not result.is_match:
        raise intent.MatchFailedError(result=result, constraints=constraints)
    registry = er.async_get(hass)
    component = hass.data.get(DATA_COMPONENT)
    found = []
    for state in result.states:
        item = registry.async_get(state.entity_id)
        if item is None or item.platform != DOMAIN or component is None:
            continue
        if (entity := component.get_entity(state.entity_id)) is not None:
            found.append(entity)
    return found


def _area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    item = er.async_get(hass).async_get(entity_id)
    if item is None:
        return None
    if item.area_id:
        return item.area_id
    device = dr.async_get(hass).async_get(item.device_id) if item.device_id else None
    return device.area_id if device else None


def _prefer_area(hass: HomeAssistant, players: list[Any], slots: dict[str, Any]) -> list[Any]:
    """Of several players, those in the satellite's area, if any are."""

    area = _value(slots, "preferred_area_id")
    if len(players) < 2 or not area:
        return players
    near = [player for player in players if _area_id(hass, player.entity_id) == area]
    return near or players


async def _texts(intent_obj: intent.Intent) -> dict[str, str]:
    """The texts in the language of the request (a German pipeline on an English system)."""

    return await no_sound.async_texts(intent_obj.hass, intent_obj.language)


def _main_players(hass: HomeAssistant, players: list[Any]) -> list[Any]:
    """The main zones of the players' receivers (Zone 2 named means its receiver)."""

    component = hass.data.get(DATA_COMPONENT)
    found: list[Any] = []
    for player in players:
        main = player
        if player.zone_id != "main" and component is not None:
            entity_id = er.async_get(hass).async_get_entity_id(
                "media_player", DOMAIN, unique_id(player.coordinator.device, "media_player_main")
            )
            main = component.get_entity(entity_id) if entity_id else None
        if main is not None and main not in found:
            found.append(main)
    return found


def _say(texts: dict[str, str], key: str, **placeholders: str) -> str:
    text = texts.get(key, key)
    try:
        return text.format(**placeholders)
    except (KeyError, IndexError, ValueError):
        return text


def _named(state: State | None, fallback: str) -> str:
    return state.name if state is not None else fallback


def _one(hass: HomeAssistant, texts: dict[str, str], players: list[Any]) -> Any:
    """The one player meant; says so when there is none or there are several."""

    if not players:
        raise intent.IntentHandleError(_say(texts, "intent_no_zone"), "no_zone")
    if len(players) > 1:
        raise intent.IntentHandleError(
            _say(
                texts,
                "intent_which_zone",
                zones=", ".join(_named(hass.states.get(p.entity_id), p.entity_id) for p in players),
            ),
            "several_zones",
        )
    return players[0]


class StartActivityHandler(intent.IntentHandler):
    """Start an activity of a zone (activities.py)."""

    intent_type = INTENT_START_ACTIVITY
    description = (
        "Starts an activity of a Denon or Marantz receiver zone, such as Film or "
        "Music: switches the zone on and sets its input, sound mode, volume and "
        "speech clarity as stored for the activity."
    )
    slot_schema = {vol.Required("activity"): cv.string, **_TARGET_SLOTS}
    platforms = {"media_player"}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        name = str(_value(slots, "activity"))
        texts = await _texts(intent_obj)
        allowed = _players(hass, intent_obj, slots)
        players = _prefer_area(
            hass, [player for player in allowed if player.activity(name) is not None], slots
        )
        if not players:
            known = sorted({item.name for player in allowed for item in player.zone_activities})
            raise intent.IntentHandleError(
                _say(texts, "intent_no_activity", activity=name, options=", ".join(known) or "–"),
                "no_activity",
            )
        if len(players) > 1:
            raise intent.IntentHandleError(
                _say(
                    texts,
                    "intent_activity_several",
                    activity=name,
                    zones=", ".join(_named(hass.states.get(p.entity_id), p.entity_id) for p in players),
                ),
                "several_zones",
            )
        player = players[0]
        activity = player.activity(name)
        assert activity is not None
        try:
            await player.async_start_activity(activity.name, intent_obj.context)
        except HomeAssistantError as err:
            # Said, not logged as an error of the intent: an input refused.
            raise intent.IntentHandleError(str(err), "not_started") from err
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        response.async_set_results(
            success_results=[
                intent.IntentResponseTarget(
                    type=intent.IntentResponseTargetType.ENTITY,
                    name=_named(hass.states.get(player.entity_id), player.entity_id),
                    id=player.entity_id,
                )
            ]
        )
        response.async_set_speech(_say(texts, "intent_activity_started", activity=activity.name))
        response.async_set_speech_slots({"activity": activity.name})
        return response


class WhyNoSoundHandler(intent.IntentHandler):
    """Tell why a zone may be silent (no_sound.py), and fix what is safe if asked."""

    intent_type = INTENT_WHY_NO_SOUND
    description = (
        "Checks a zone of a Denon or Marantz receiver for what may keep it silent "
        "(off, muted, volume, HDMI audio to the TV, speakers B, no input signal) "
        "and says it in plain words. With fix true, switches the zone on, unmutes "
        "and sends HDMI audio to the receiver where that was the cause."
    )
    slot_schema = {vol.Optional("fix"): cv.boolean, **_TARGET_SLOTS}
    platforms = {"media_player"}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        texts = await _texts(intent_obj)
        players = _prefer_area(hass, _players(hass, intent_obj, slots), slots)
        if not slots.get("name") and not slots.get("area") and len(players) > 1:
            # Without a name: the main zones only, unless a zone is in the area.
            main = [player for player in players if player.zone_id == "main"]
            players = main or players
        player = _one(hass, texts, players)
        result = await player.async_why_no_sound(bool(_value(slots, "fix")), intent_obj.language)
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.QUERY_ANSWER
        response.async_set_speech(" ".join(item["message"] for item in result))
        response.async_set_speech_slots({"findings": result})
        return response


class SetSoundModeHandler(intent.IntentHandler):
    """Choose the sound mode of a main zone, by the name its list shows."""

    intent_type = INTENT_SET_SOUND_MODE
    description = (
        "Sets the sound mode of a Denon or Marantz receiver, such as Dolby Surround, "
        "Stereo, Pure Direct or Multichannel Stereo. Takes the names of the main "
        "zone's sound mode list; an unknown name is answered with the names it has."
    )
    slot_schema = {vol.Required("sound_mode"): cv.string, **_TARGET_SLOTS}
    platforms = {"media_player"}

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        hass = intent_obj.hass
        slots = self.async_validate_slots(intent_obj.slots)
        mode = str(_value(slots, "sound_mode"))
        texts = await _texts(intent_obj)
        # Only the main zone has sound modes; Zone 2 named means its receiver.
        players = _main_players(hass, _players(hass, intent_obj, slots))
        player = _one(hass, texts, _prefer_area(hass, players, slots))
        shown = player.shown_sound_mode(mode)
        try:
            await player.async_select_sound_mode(mode)
        except HomeAssistantError as err:
            raise intent.IntentHandleError(str(err), "not_set") from err
        response = intent_obj.create_response()
        response.response_type = intent.IntentResponseType.ACTION_DONE
        response.async_set_results(
            success_results=[
                intent.IntentResponseTarget(
                    type=intent.IntentResponseTargetType.ENTITY,
                    name=_named(hass.states.get(player.entity_id), player.entity_id),
                    id=player.entity_id,
                )
            ]
        )
        response.async_set_speech(_say(texts, "intent_sound_mode_set", sound_mode=shown))
        response.async_set_speech_slots({"sound_mode": shown})
        return response
