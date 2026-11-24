#!/usr/bin/env python3
"""Locale-slug guard: entity ids stay English in every language.

Ported in spirit from Roomba+ (johnnyh1975/ha_roomba_plus), where entity ids
in the user's language caused 15 regressions. Home Assistant builds entity ids
from the entity name in the user's language for many languages, German and
Dutch among them (NATIVE_ENTITY_IDS). This integration builds them from the
English name instead (`DenonAvrEntity.suggested_object_id` in entity.py). The
runtime test is tests/test_entity.py; this script checks, without Home
Assistant, the source rules that keep it working:

1. `DenonAvrEntity` defines `suggested_object_id`.
2. Every entity class inherits from `DenonAvrEntity`, or defines
   `suggested_object_id` itself.
3. No entity sets `_attr_name` to anything but None. Names go through
   `translation_key`, with placeholders for parts that come from the
   receiver. A hard-coded name is English everywhere and bypasses the
   translations. Exempt only names that read the same in every language.
4. No `DeviceInfo` has a `translation_key`: a device name is part of every
   entity id of its entities, so a translated device name would put the
   user's language back into the ids.
5. Every entity translation key used in the source, literal or through the
   protocol profile, has a name in strings.json and in every translation.
6. Every translated name has the same placeholders as the English one.

Exit code 0 = all rules hold. Exit code 1 = violations, printed to stdout.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "denonavr_plus"
BASE_CLASS = "DenonAvrEntity"

#: Classes allowed to set `_attr_name`, with the reason.
NAME_EXEMPT = {
    # The band label is a frequency such as "63 Hz", the same in every language.
    "DenonAvrEqBand",
}

#: Profile control kinds and the platform their entity lands on
#: (select.py, switch.py and number.py).
_KIND_PLATFORM = {
    "enum": "select",
    "onoff": "switch",
    "level": "number",
    "attenuation": "number",
    "minutes": "number",
    "integer": "number",
    "centered": "number",
}
#: Setup-API controls (grammar.video_web / speakers_web) by their kind.
_WEB_KIND_PLATFORM = {"select": "select", "switch": "switch"}

_PLACEHOLDER = re.compile(r"\{\w+\}")


def _base_names(node: ast.ClassDef) -> set[str]:
    names = set()
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.add(base.id)
        elif isinstance(base, ast.Attribute):
            names.add(base.attr)
        elif isinstance(base, ast.Subscript) and isinstance(base.value, ast.Name):
            names.add(base.value.id)
    return names


def _classes(trees: dict[str, ast.Module]) -> dict[str, tuple[str, ast.ClassDef]]:
    return {
        node.name: (fname, node)
        for fname, tree in trees.items()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def _defines(node: ast.ClassDef, name: str) -> bool:
    return any(
        isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
        for item in node.body
    )


def _family(classes: dict[str, tuple[str, ast.ClassDef]]) -> set[str]:
    """Every class that inherits DenonAvrEntity, directly or not."""
    family = {BASE_CLASS}
    changed = True
    while changed:
        changed = False
        for name, (_, node) in classes.items():
            if name not in family and _base_names(node) & family:
                family.add(name)
                changed = True
    return family


def _is_entity_class(node: ast.ClassDef) -> bool:
    return any(base.endswith("Entity") for base in _base_names(node))


def _name_assignments(node: ast.ClassDef) -> list[ast.AST]:
    """Values assigned to `_attr_name`, at class level or as `self._attr_name`."""
    values: list[ast.AST] = []
    for sub in ast.walk(node):
        if isinstance(sub, (ast.Assign, ast.AnnAssign)):
            targets = sub.targets if isinstance(sub, ast.Assign) else [sub.target]
            for target in targets:
                name = (
                    target.id
                    if isinstance(target, ast.Name)
                    else target.attr
                    if isinstance(target, ast.Attribute)
                    else None
                )
                if name == "_attr_name" and sub.value is not None:
                    values.append(sub.value)
    return values


def _literal_translation_keys(node: ast.ClassDef) -> set[str]:
    keys = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            for target in sub.targets:
                name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
                if (
                    name == "_attr_translation_key"
                    and isinstance(sub.value, ast.Constant)
                    and isinstance(sub.value.value, str)
                ):
                    keys.add(sub.value.value)
    return keys


def _platform_of(fname: str) -> str:
    return Path(fname).stem


def _device_info_translation(trees: dict[str, ast.Module]) -> list[str]:
    found = []
    for fname, tree in trees.items():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", getattr(node.func, "attr", None)) == "DeviceInfo"
                and any(kw.arg == "translation_key" for kw in node.keywords)
            ):
                found.append(f"{fname}:{node.lineno}: DeviceInfo has a translation_key")
    return found


def _profile_keys() -> set[tuple[str, str]]:
    profile = json.loads((PACKAGE / "avr" / "protocol_profile.json").read_text(encoding="utf-8"))
    keys = {
        (_KIND_PLATFORM[spec["kind"]], control_id)
        for control_id, spec in profile["controls"].items()
        if spec.get("kind") in _KIND_PLATFORM
    }
    for section in ("video_web", "speakers_web"):
        for control in profile["grammar"].get(section, {}).get("controls", []):
            platform = _WEB_KIND_PLATFORM.get(control.get("kind", ""))
            if platform:
                keys.add((platform, control["id"]))
    return keys


def check() -> list[str]:
    trees = {
        path.name: ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
        for path in sorted(PACKAGE.glob("*.py"))
    }
    classes = _classes(trees)
    problems: list[str] = []

    # Rule 1
    base = classes.get(BASE_CLASS)
    if base is None or not _defines(base[1], "suggested_object_id"):
        problems.append(f"entity.py: {BASE_CLASS} must define suggested_object_id")

    family = _family(classes)
    used_keys: set[tuple[str, str]] = set()
    for name, (fname, node) in classes.items():
        if not _is_entity_class(node) or name == BASE_CLASS:
            continue
        # Rule 2
        if name not in family and not _defines(node, "suggested_object_id"):
            problems.append(
                f"{fname}:{name}: an entity class must inherit {BASE_CLASS} "
                "or define suggested_object_id"
            )
        # Rule 3
        if name not in NAME_EXEMPT:
            for value in _name_assignments(node):
                if not (isinstance(value, ast.Constant) and value.value is None):
                    problems.append(
                        f"{fname}:{name}: line {value.lineno} sets _attr_name; "
                        "use a translation_key (with placeholders) instead"
                    )
        used_keys |= {(_platform_of(fname), key) for key in _literal_translation_keys(node)}

    # Rule 4
    problems += _device_info_translation(trees)

    # Rules 5 and 6
    used_keys |= _profile_keys()
    english = json.loads((PACKAGE / "strings.json").read_text(encoding="utf-8"))["entity"]
    files = {"strings.json": english} | {
        f"translations/{path.name}": json.loads(path.read_text(encoding="utf-8")).get("entity", {})
        for path in sorted((PACKAGE / "translations").glob("*.json"))
    }
    for platform, key in sorted(used_keys):
        source = english.get(platform, {}).get(key, {}).get("name")
        for fname, entity in files.items():
            name = entity.get(platform, {}).get(key, {}).get("name")
            if not name:
                problems.append(f"{fname}: entity.{platform}.{key}.name is missing")
            elif source and set(_PLACEHOLDER.findall(name)) != set(_PLACEHOLDER.findall(source)):
                problems.append(
                    f"{fname}: entity.{platform}.{key}.name has other placeholders "
                    "than strings.json"
                )
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    if problems:
        print(f"FAILED: {len(problems)} locale-slug rule violation(s)")
        return 1
    print("OK: entity ids stay English in every language")
    return 0


if __name__ == "__main__":
    sys.exit(main())
