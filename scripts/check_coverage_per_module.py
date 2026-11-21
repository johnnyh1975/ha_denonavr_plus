#!/usr/bin/env python3
"""Line coverage per module: 95 % is the rule, recorded floors the bridge.

Ported from Roomba+ (johnnyh1975/ha_roomba_plus), where every module is at
95 % and the check is a plain gate. This project started with the code of
denon_avr 1.1.2 and no tests at all, so a plain gate would be red for a
long time and nobody would read it. Instead it is a RATCHET, the same idea
as scripts/check_typing_baseline.py:

- A module listed in scripts/coverage_floors.json may not drop below its
  recorded floor.
- When a listed module rises more than one percentage point above its
  floor, the floor has to be raised in the same commit. Otherwise the
  gain could be given back later without anyone noticing.
- A listed module that reaches 95 % has to be removed from the file.
- A module that is NOT listed must be at 95 % -- new code starts at the
  rule, not at a floor.

The goal is an empty coverage_floors.json; then this is Roomba+'s gate.

Counted exactly from the per-line hits in coverage.xml, never from the
rounded terminal report (which prints 94.6 % as 95 %). Branch coverage is
printed for information only.

Usage:
    python scripts/check_coverage_per_module.py coverage.xml
Without an argument it looks for ./coverage.xml and, when there is none,
reports that it had nothing to check and passes, so it can sit among the
other check_*.py scripts. CI passes the path explicitly, and then a
missing file is an error.
"""

from __future__ import annotations

import json
import pathlib
import sys
import xml.etree.ElementTree as ET

THRESHOLD = 0.95
#: A floor this far below the measured value has to be raised.
SLACK = 0.01
FLOORS_PATH = pathlib.Path(__file__).resolve().parent / "coverage_floors.json"


def per_module(path: pathlib.Path) -> list[tuple[str, int, int, float | None]]:
    """(file, covered lines, statements, branch rate or None) per module."""
    rows = []
    for cls in ET.parse(path).getroot().iter("class"):
        lines = list(cls.iter("line"))
        total = len(lines)
        covered = sum(1 for ln in lines if int(ln.get("hits", "0")) > 0)
        branch = cls.get("branch-rate")
        rows.append((cls.get("filename", "?"), covered, total,
                     float(branch) if branch is not None else None))
    return rows


def load_floors() -> dict[str, float]:
    if not FLOORS_PATH.exists():
        return {}
    data = json.loads(FLOORS_PATH.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in data.get("floors", {}).items()}


def evaluate(
    rows: list[tuple[str, int, int, float | None]], floors: dict[str, float]
) -> list[str]:
    """Every problem as one message; empty when the ratchet holds."""
    problems: list[str] = []
    seen = set()
    for name, covered, total, _branch in rows:
        seen.add(name)
        rate = covered / total if total else 1.0
        floor = floors.get(name)
        if floor is None:
            if rate < THRESHOLD:
                need = int(total * THRESHOLD + 0.999999) - covered
                problems.append(
                    f"{name}: {rate:.2%} is below 95 % and the module has no "
                    f"recorded floor -- {need} more line(s) needed"
                )
            continue
        if rate >= THRESHOLD:
            problems.append(
                f"{name}: reached {rate:.2%} -- remove it from "
                "scripts/coverage_floors.json"
            )
        elif rate + 1e-9 < floor:
            problems.append(
                f"{name}: {rate:.2%} is below its recorded floor {floor:.2%}"
            )
        elif rate - floor > SLACK:
            problems.append(
                f"{name}: {rate:.2%} is more than one point above its floor "
                f"{floor:.2%} -- raise the floor to {rate:.4f}"
            )
    for name in sorted(set(floors) - seen):
        problems.append(
            f"{name}: listed in scripts/coverage_floors.json but not measured "
            "-- remove the entry"
        )
    return problems


def main(argv: list[str]) -> int:
    explicit = len(argv) > 1
    path = pathlib.Path(argv[1] if explicit else "coverage.xml")
    if not path.exists():
        if explicit:
            print(f"::error::{path} not found -- run the tests with --cov-report=xml first")
            return 1
        print("check_coverage_per_module: no coverage.xml here, nothing to check")
        return 0
    rows = per_module(path)
    floors = load_floors()
    for name, covered, total, branch in sorted(rows):
        rate = covered / total if total else 1.0
        mark = ""
        if name in floors:
            mark = f"  (floor {floors[name]:.2%})"
        elif rate < THRESHOLD:
            mark = "  SHORT"
        branch_text = f"  branches {branch:.0%}" if branch is not None else ""
        print(f"{rate:7.2%}  {name}{branch_text}{mark}")
    problems = evaluate(rows, floors)
    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        return 1
    at_rule = sum(1 for n, c, t, _b in rows if (c / t if t else 1.0) >= THRESHOLD)
    print(f"{at_rule} of {len(rows)} modules at 95 %; "
          f"{len(floors)} still on a recorded floor")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
