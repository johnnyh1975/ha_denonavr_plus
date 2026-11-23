#!/usr/bin/env python3
"""Icon check: every entity gets its icon from icons.json.

Home Assistant's quality scale asks for icon translations: an entity names
its icon through its `translation_key` in icons.json, with state icons where
the state changes the picture (mute on or off), instead of setting
`_attr_icon` in code. This script checks, without Home Assistant:

1. Every icon in icons.json is `mdi:<name>`, as a default and per state.
2. Every entity translation key in strings.json has an icon, except those
   whose icon comes from their device class (DEVICE_CLASS_ICONS).
3. icons.json has no icon for a translation key strings.json does not have.
4. No entity sets `_attr_icon` in code, and the protocol profile names no
   icon: both would bypass icons.json.

Exit code 0 = all rules hold. Exit code 1 = violations, printed to stdout.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "custom_components" / "denonavr_plus"

#: (platform, translation key) whose icon comes from the device class.
DEVICE_CLASS_ICONS = {
    ("binary_sensor", "connectivity"),
    ("media_player", "zone"),
}

_ICON = re.compile(r"^mdi:[a-z0-9]+(-[a-z0-9]+)*$")
_ATTR_ICON = re.compile(r"^\s*(self\.)?_attr_icon\s*[:=]", re.MULTILINE)


def _check_icon(where: str, value: object, problems: list[str]) -> None:
    if not isinstance(value, str) or not _ICON.match(value):
        problems.append(f"{where}: {value!r} is not an mdi:<name> icon")


def check(package: Path) -> list[str]:
    problems: list[str] = []
    icons_path = package / "icons.json"
    if not icons_path.exists():
        return ["icons.json is missing"]
    icons = json.loads(icons_path.read_text(encoding="utf-8")).get("entity", {})
    strings = json.loads((package / "strings.json").read_text(encoding="utf-8"))
    names = strings.get("entity", {})

    for platform, keys in icons.items():
        for key, entry in keys.items():
            where = f"icons.json entity.{platform}.{key}"
            if key not in names.get(platform, {}):
                problems.append(f"{where}: strings.json has no such entity")
            if not isinstance(entry, dict) or "default" not in entry:
                problems.append(f"{where}: needs a default icon")
                continue
            _check_icon(f"{where}.default", entry["default"], problems)
            for state, icon in entry.get("state", {}).items():
                _check_icon(f"{where}.state.{state}", icon, problems)

    for platform, keys in names.items():
        for key in keys:
            if (platform, key) in DEVICE_CLASS_ICONS:
                continue
            if key not in icons.get(platform, {}):
                problems.append(f"entity.{platform}.{key}: no icon in icons.json")

    for source in sorted(package.rglob("*.py")):
        if _ATTR_ICON.search(source.read_text(encoding="utf-8")):
            rel = source.relative_to(package)
            problems.append(f"{rel}: sets _attr_icon; name the icon in icons.json")

    profile = package / "avr" / "protocol_profile.json"
    controls = json.loads(profile.read_text(encoding="utf-8")).get("controls", {})
    for control_id, spec in controls.items():
        if "icon" in spec:
            problems.append(f"protocol_profile.json {control_id}: names an icon; use icons.json")
    return problems


def main() -> int:
    problems = check(PACKAGE)
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print("OK: every entity gets its icon from icons.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
