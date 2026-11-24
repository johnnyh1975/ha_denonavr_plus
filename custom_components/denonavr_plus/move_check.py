"""What automations and scripts use that the moved receiver no longer offers.

After a move from the official Denon AVR Network Receivers integration
(`denonavr`), some actions, attributes and sound mode names have another
form here. This looks through the configurations of automations and scripts
(as Home Assistant loaded them) and lists each place with what replaces it.
Nothing is changed. Entity ids need no check: the moved entities keep them.

The findings are kept free of language (ids, names, arrows), so the repair
can show them in every language; its text says what the arrows mean.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

#: The sound mode groups the official integration took as sound modes;
#: here they are options of the Sound Mode Group select.
GENRE_GROUPS = frozenset({"MOVIE", "MUSIC", "GAME"})

_ACTION_KEYS = ("action", "service", "perform_action")
_ACTION = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
_ATTRIBUTE_TEMPLATES = (
    re.compile(r"state_attr\(\s*['\"]([a-z_]+\.[a-z0-9_]+)['\"]\s*,\s*['\"]([a-z_]+)['\"]"),
    re.compile(r"([a-z_]+\.[a-z0-9_]+)\.attributes\.([a-z_]+)"),
    re.compile(r"([a-z_]+\.[a-z0-9_]+)\.attributes\[\s*['\"]([a-z_]+)['\"]"),
)
#: Nothing replaces it: the action is not needed any more, or there is none.
NONE = "–"


@dataclass(frozen=True)
class Replacements:
    """The entities that take over what the official integration had elsewhere."""

    moved: frozenset[str]
    sound_mode_sensor: str | None = None
    dynamic_eq_switch: str | None = None
    sound_mode_group_select: str | None = None
    sound_modes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Finding:
    """One place: where (an automation or script), what, and what instead."""

    owner: str
    old: str
    new: str

    def line(self) -> str:
        return f"{self.owner}: {self.old} → {self.new}"


def findings(configs: list[tuple[str, Any]], replace: Replacements) -> list[Finding]:
    """The findings for (owner, configuration) pairs, each place once, in order."""

    found: list[Finding] = []
    for owner, config in configs:
        for node in _walk(config):
            if isinstance(node, str):
                found += _template(owner, node, replace)
            else:
                found += _action(owner, node, replace)
                found += _attribute_condition(owner, node, replace)
    return list(dict.fromkeys(found))


def _walk(node: Any) -> Iterator[Any]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)
    elif isinstance(node, str):
        yield node


def _targets(node: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for holder in (node, node.get("target"), node.get("data"), node.get("data_template")):
        if isinstance(holder, dict):
            value = holder.get("entity_id")
            if isinstance(value, str):
                found += [part.strip() for part in value.split(",")]
            elif isinstance(value, list):
                found += [str(part) for part in value]
    return found


def _data(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def _action(owner: str, node: dict[str, Any], replace: Replacements) -> list[Finding]:
    action = next(
        (
            node[key]
            for key in _ACTION_KEYS
            if isinstance(node.get(key), str) and _ACTION.match(node[key])
        ),
        None,
    )
    if action is None:
        return []
    if action == "denonavr.get_command":
        return [Finding(owner, action, "denonavr_plus.send_command")]
    if action == "denonavr.set_dynamic_eq":
        switch = replace.dynamic_eq_switch or "switch"
        return [Finding(owner, action, f"switch.turn_on / switch.turn_off {switch}")]
    if action.startswith("denonavr."):
        return [Finding(owner, action, NONE)]
    if action != "media_player.select_sound_mode":
        return []
    if not any(target in replace.moved for target in _targets(node)):
        return []
    mode = str(_data(node).get("sound_mode", "")).strip()
    if not mode or mode.casefold() in {known.casefold() for known in replace.sound_modes}:
        return []
    old = f"{action} {mode}"
    if mode.upper() in GENRE_GROUPS and replace.sound_mode_group_select:
        return [Finding(owner, old, f"select.select_option {replace.sound_mode_group_select}")]
    return [Finding(owner, old, "sound_mode_list")]


def _attribute(owner: str, entity: str, attribute: str, replace: Replacements) -> list[Finding]:
    if entity not in replace.moved:
        return []
    old = f"{entity}.attributes.{attribute}"
    if attribute == "sound_mode_raw":
        return [Finding(owner, old, replace.sound_mode_sensor or NONE)]
    if attribute == "dynamic_eq":
        return [Finding(owner, old, replace.dynamic_eq_switch or NONE)]
    return []


def _attribute_condition(
    owner: str, node: dict[str, Any], replace: Replacements
) -> list[Finding]:
    attribute = node.get("attribute")
    if not isinstance(attribute, str):
        return []
    return [
        finding
        for entity in _targets(node)
        for finding in _attribute(owner, entity, attribute, replace)
    ]


def _template(owner: str, text: str, replace: Replacements) -> list[Finding]:
    if "{{" not in text and "{%" not in text:
        return []
    return [
        finding
        for pattern in _ATTRIBUTE_TEMPLATES
        for entity, attribute in pattern.findall(text)
        for finding in _attribute(owner, entity, attribute, replace)
    ]
