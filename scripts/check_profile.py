#!/usr/bin/env python3
"""The protocol profile fits its schema, with every variant applied.

The profile (custom_components/denonavr_plus/avr/protocol_profile.json) is
checked when it is loaded too; this runs the same check without Home
Assistant, for every receiver generation and brand, so a misspelt key or a
wrong value type fails the CI instead of reading as a missing function.

Exit 0 = the profile fits. Exit 1 = what does not, printed to stdout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
AVR = ROOT / "custom_components" / "denonavr_plus" / "avr"

#: One receiver per combination of variants (port, CommApiVers, brand, model).
RECEIVERS = (
    (8080, 301, 0, "AVR-X2600H"),
    (8080, 301, 1, "SR6013"),
    (80, 300, 0, "AVR-X2100W"),
    (80, 220, 0, "AVR-X2000"),
    (80, 220, 0, "AVR-2113CI"),
    (80, None, 0, "AVR-1912"),
)


def _load(name: str) -> ModuleType:
    """A module of the avr package, without Home Assistant or the package itself."""

    spec = importlib.util.spec_from_file_location(f"denonavr_plus_avr.{name}", AVR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    # profile.py imports its schema relatively; give it a package to do so.
    package = ModuleType("denonavr_plus_avr")
    package.__path__ = [str(AVR)]
    sys.modules["denonavr_plus_avr"] = package
    schema = _load("profile_schema")
    profile = _load("profile")
    raw = json.loads((AVR / "protocol_profile.json").read_text(encoding="utf-8"))
    problems = [f"base: {problem}" for problem in schema.problems(raw)]
    if not problems:
        base = profile.ProtocolProfile(raw)
        for port, comm_api, brand, model in RECEIVERS:
            applied = profile.profile_for_receiver(base, port, comm_api, brand, model)
            problems += [
                f"{model} ({', '.join(applied.variants) or 'no variant'}): {problem}"
                for problem in schema.problems(applied._raw)  # noqa: SLF001
            ]
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print(f"protocol profile fits its schema ({len(RECEIVERS)} receiver variants)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
