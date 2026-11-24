"""Every flow screen the tests reach must have its texts, in every language.

The frontend shows what a flow step names: a form's title, description and
field labels, a menu's entries, a progress message, an abort reason, an
error. When a key is missing, the frontend shows the bare key or nothing;
when a text uses a placeholder the step does not provide, it shows an error
instead of the text. Neither is caught by comparing the translation files
with strings.json, which is what scripts/check_translations.py does.

So conftest.py wraps the flow managers: each step result produced in any
test is checked here against strings.json and every translations/*.json.
"""

from __future__ import annotations

import json
import string
from functools import cache
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.repairs import RepairsFlow
from homeassistant.data_entry_flow import FlowResultType, section

BASE = Path(__file__).parent.parent / "custom_components" / "denonavr_plus"

#: The fixable issue a repair flow without an issue id belongs to.
REPAIR_ISSUE = "capability_check"

#: Steps whose fields are named after what the receiver reports (a source
#: code, a sound mode); the frontend shows that name as the label.
NAMED_BY_RECEIVER = {("options", "source_names"), ("options", "sound_mode_names")}


@cache
def _languages() -> dict[str, dict[str, Any]]:
    files = {"strings": BASE / "strings.json"}
    files |= {path.stem: path for path in sorted((BASE / "translations").glob("*.json"))}
    return {name: json.loads(path.read_text(encoding="utf-8")) for name, path in files.items()}


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


def _get(tree: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = tree
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _category(flow: Any) -> tuple[str, ...] | None:
    if isinstance(flow, config_entries.ConfigFlow):
        return ("config",)
    if isinstance(flow, config_entries.OptionsFlow):
        return ("options",)
    if isinstance(flow, RepairsFlow):
        # Issue ids are "<translation key>_<entry id>".
        issue = str(getattr(flow, "issue_id", "") or REPAIR_ISSUE).rsplit("_", 1)[0]
        return ("issues", issue, "fix_flow")
    return None


def problems(flow: Any, result: dict[str, Any]) -> list[str]:
    """What a screen would show wrongly, per language."""

    category = _category(flow)
    if category is None:
        return []
    kind = result.get("type")
    step = result.get("step_id")
    provided = set(result.get("description_placeholders") or {})
    needed: list[tuple[tuple[str, ...], bool]] = []  # (key path, required)
    if kind is FlowResultType.FORM:
        needed.append(((*category, "step", step, "description"), False))
        if (*category, step) not in NAMED_BY_RECEIVER:
            needed += _fields(category, step, result.get("data_schema"))
        for code in (result.get("errors") or {}).values():
            needed.append(((*category, "error", code), True))
    elif kind is FlowResultType.MENU:
        needed.append(((*category, "step", step, "description"), False))
        options = result.get("menu_options") or []
        names = options if isinstance(options, list) else list(options)
        for name in names:
            needed.append(((*category, "step", step, "menu_options", name), True))
            needed.append(((*category, "step", step, "menu_option_descriptions", name), False))
    elif kind is FlowResultType.SHOW_PROGRESS:
        needed.append(((*category, "progress", result.get("progress_action")), True))
    elif kind is FlowResultType.ABORT:
        needed.append(((*category, "abort", result.get("reason")), True))
    found: list[str] = []
    for language, tree in _languages().items():
        for path, required in needed:
            text = _get(tree, path)
            if text is None:
                if required:
                    found.append(f"{language}: missing {'.'.join(map(str, path))}")
                continue
            if not isinstance(text, str):
                found.append(f"{language}: {'.'.join(map(str, path))} is not a text")
                continue
            if text.startswith("[%key:"):
                continue
            missing = _placeholders(text) - provided
            if missing:
                found.append(
                    f"{language}: {'.'.join(map(str, path))} uses {sorted(missing)} "
                    "that the step does not provide"
                )
    return found


def _fields(
    category: tuple[str, ...], step: str | None, schema: vol.Schema | None
) -> list[tuple[tuple[str, ...], bool]]:
    """Every field's label (required) and help text (optional), sections included."""

    if schema is None:
        return []
    base = (*category, "step", str(step))
    paths: list[tuple[tuple[str, ...], bool]] = []
    for key, value in schema.schema.items():
        name = str(key)
        if isinstance(value, section):
            inner_base = (*base, "sections", name)
            paths += [((*inner_base, "name"), True), ((*inner_base, "description"), False)]
            for inner in value.schema.schema:
                paths += [
                    ((*inner_base, "data", str(inner)), True),
                    ((*inner_base, "data_description", str(inner)), False),
                ]
        else:
            paths += [((*base, "data", name), True), ((*base, "data_description", name), False)]
    return paths
