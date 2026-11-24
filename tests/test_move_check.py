"""Tests for move_check.py: what automations use that the official integration had."""

from __future__ import annotations

from custom_components.denonavr_plus.move_check import NONE, Finding, Replacements, findings

PLAYER = "media_player.wohnzimmer"
REPLACE = Replacements(
    moved=frozenset({PLAYER}),
    sound_mode_sensor="sensor.avr_sound_mode",
    dynamic_eq_switch="switch.avr_audio_dynamic_eq",
    sound_mode_group_select="select.avr_sound_mode_group",
    sound_modes=frozenset({"Stereo", "Multi Ch Stereo"}),
)


def _lines(config: object, replace: Replacements = REPLACE) -> list[str]:
    return [finding.line() for finding in findings([("automation.x", config)], replace)]


def test_actions_of_the_official_integration() -> None:
    config = {
        "actions": [
            {"action": "denonavr.get_command", "data": {"command": "/goform/x"}},
            {"service": "denonavr.set_dynamic_eq", "data": {"dynamic_eq": True}},
            {"action": "denonavr.update_audyssey"},
            {"action": "light.turn_on", "target": {"entity_id": "light.x"}},
        ]
    }
    assert _lines(config) == [
        "automation.x: denonavr.get_command → denonavr_plus.send_command",
        "automation.x: denonavr.set_dynamic_eq → switch.turn_on / switch.turn_off "
        "switch.avr_audio_dynamic_eq",
        f"automation.x: denonavr.update_audyssey → {NONE}",
    ]


def test_attributes_that_are_entities_now() -> None:
    config = {
        "conditions": [
            {"condition": "state", "entity_id": PLAYER, "attribute": "dynamic_eq", "state": True},
            {"condition": "state", "entity_id": "media_player.tv", "attribute": "dynamic_eq"},
        ],
        "actions": [
            {"action": "notify.x", "data": {"message": "{{ state_attr('%s', 'sound_mode_raw') }}" % PLAYER}},
            {"action": "notify.x", "data": {"message": "{{ states.%s.attributes.volume_level }}" % PLAYER}},
            {"action": "notify.x", "data": {"message": "no template: state_attr('%s', 'sound_mode_raw')" % PLAYER}},
        ],
    }
    assert _lines(config) == [
        f"automation.x: {PLAYER}.attributes.dynamic_eq → switch.avr_audio_dynamic_eq",
        f"automation.x: {PLAYER}.attributes.sound_mode_raw → sensor.avr_sound_mode",
    ]


def test_sound_modes_by_their_old_names() -> None:
    def select(mode: str, entity: str = PLAYER) -> dict[str, object]:
        return {
            "action": "media_player.select_sound_mode",
            "target": {"entity_id": entity},
            "data": {"sound_mode": mode},
        }

    config = [select("MOVIE"), select("DOLBY DIGITAL"), select("stereo"), select("MOVIE", "media_player.tv")]
    assert _lines(config) == [
        "automation.x: media_player.select_sound_mode MOVIE → select.select_option "
        "select.avr_sound_mode_group",
        "automation.x: media_player.select_sound_mode DOLBY DIGITAL → sound_mode_list",
    ]


def test_without_a_replacement_entity() -> None:
    bare = Replacements(moved=frozenset({PLAYER}))
    config = [
        {"action": "denonavr.set_dynamic_eq"},
        {"condition": "state", "entity_id": [PLAYER], "attribute": "sound_mode_raw"},
        {
            "action": "media_player.select_sound_mode",
            "entity_id": f"{PLAYER}, media_player.tv",
            "data": {"sound_mode": "MUSIC"},
        },
    ]
    assert _lines(config, bare) == [
        "automation.x: denonavr.set_dynamic_eq → switch.turn_on / switch.turn_off switch",
        f"automation.x: {PLAYER}.attributes.sound_mode_raw → {NONE}",
        "automation.x: media_player.select_sound_mode MUSIC → sound_mode_list",
    ]


def test_each_place_once() -> None:
    action = {"action": "denonavr.get_command"}
    found = findings([("script.a", [action, action]), ("script.b", action)], REPLACE)
    assert found == [
        Finding("script.a", "denonavr.get_command", "denonavr_plus.send_command"),
        Finding("script.b", "denonavr.get_command", "denonavr_plus.send_command"),
    ]
