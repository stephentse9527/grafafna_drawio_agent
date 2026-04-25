#!/usr/bin/env python3
"""
tools/validate_dashboard.py — Full validation of a generated Grafana dashboard JSON.

Runs all validation checks from the Step 7 gates in the agent instructions.

Usage:
    python tools/validate_dashboard.py output/APP_dashboard.json

Exit codes:
    0 — all checks pass
    1 — one or more checks fail (details printed to stdout)
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Gate 7-A: Mandatory panel layout
# ---------------------------------------------------------------------------

REQUIRED_GRID = [
    {"h": 3,  "w": 20, "x": 0,  "y": 0,  "slot": "Z1-A"},
    {"h": 3,  "w": 3,  "x": 21, "y": 0,  "slot": "Z1-B"},
    {"h": 1,  "w": 6,  "x": 0,  "y": 3,  "slot": "Z2-1"},
    {"h": 1,  "w": 6,  "x": 6,  "y": 3,  "slot": "Z2-2"},
    {"h": 1,  "w": 6,  "x": 12, "y": 3,  "slot": "Z2-3"},
    {"h": 1,  "w": 6,  "x": 18, "y": 3,  "slot": "Z2-4"},
    {"h": 4,  "w": 3,  "x": 0,  "y": 4,  "slot": "Z3-1"},
    {"h": 4,  "w": 3,  "x": 3,  "y": 4,  "slot": "Z3-2"},
    {"h": 4,  "w": 3,  "x": 6,  "y": 4,  "slot": "Z3-3"},
    {"h": 4,  "w": 3,  "x": 9,  "y": 4,  "slot": "Z3-4"},
    {"h": 4,  "w": 3,  "x": 12, "y": 4,  "slot": "Z3-5"},
    {"h": 4,  "w": 3,  "x": 15, "y": 4,  "slot": "Z3-6"},
    {"h": 4,  "w": 6,  "x": 18, "y": 4,  "slot": "Z3-7"},
    {"h": 6,  "w": 6,  "x": 0,  "y": 8,  "slot": "Z4-1"},
    {"h": 6,  "w": 6,  "x": 6,  "y": 8,  "slot": "Z4-2"},
    {"h": 6,  "w": 6,  "x": 12, "y": 8,  "slot": "Z4-3"},
    {"h": 6,  "w": 6,  "x": 18, "y": 8,  "slot": "Z4-4"},
    {"h": 18, "w": 18, "x": 0,  "y": 14, "slot": "Z5-MAIN"},
    {"h": 6,  "w": 6,  "x": 18, "y": 14, "slot": "Z5-R1"},
    {"h": 6,  "w": 6,  "x": 18, "y": 20, "slot": "Z5-R2"},
    {"h": 6,  "w": 6,  "x": 18, "y": 26, "slot": "Z5-R3"},
]

REQUIRED_SET = {(r["h"], r["w"], r["x"], r["y"]) for r in REQUIRED_GRID}

# Synthetic placeholder markers (produced by grafana_builder.py fallback paths)
PLACEHOLDER_MARKERS = [
    "[TITLE PANEL MISSING]",
    "[ALERT PANEL MISSING]",
]


def validate(path: Path) -> list[str]:
    """Return list of error strings. Empty = pass."""
    errors: list[str] = []

    # ---- parse ----
    try:
        raw = path.read_text(encoding="utf-8")
        d, _ = json.JSONDecoder().raw_decode(raw)
    except Exception as e:
        return [f"Cannot parse JSON: {e}"]

    panels = d.get("panels") or d.get("dashboard", {}).get("panels", [])

    # ------------------------------------------------------------------ 7-A
    # Panel count
    if len(panels) != 21:
        errors.append(f"[7-A] Expected 21 panels, found {len(panels)}")

    actual_grids = [
        {"h": p["gridPos"]["h"], "w": p["gridPos"]["w"],
         "x": p["gridPos"]["x"], "y": p["gridPos"]["y"]}
        for p in panels if "gridPos" in p
    ]

    for req in REQUIRED_GRID:
        matches = [g for g in actual_grids
                   if g["h"] == req["h"] and g["w"] == req["w"]
                   and g["x"] == req["x"] and g["y"] == req["y"]]
        if not matches:
            errors.append(
                f"[7-A] Missing slot {req['slot']} "
                f"(h={req['h']},w={req['w']},x={req['x']},y={req['y']})"
            )
        elif len(matches) > 1:
            errors.append(
                f"[7-A] Duplicate panels at slot {req['slot']} "
                f"(h={req['h']},w={req['w']},x={req['x']},y={req['y']})"
            )

    for p in panels:
        gp = p.get("gridPos", {})
        key = (gp.get("h"), gp.get("w"), gp.get("x"), gp.get("y"))
        if key not in REQUIRED_SET:
            errors.append(
                f"[7-A] Unexpected gridPos h={key[0]},w={key[1]},"
                f"x={key[2]},y={key[3]} — title='{p.get('title','?')}'"
            )

    # ------------------------------------------------------------------ 7-B  English titles
    for p in panels:
        title = p.get("title", "")
        if not title:
            errors.append(f"[7-B] Panel id={p.get('id','?')} has empty title")
        elif any(ord(c) > 127 for c in title):
            errors.append(f"[7-B] Non-English title: '{title}'")

    # ------------------------------------------------------------------ 7-C  Content validity
    slot_map: dict[tuple, dict] = {
        (p["gridPos"]["h"], p["gridPos"]["w"],
         p["gridPos"]["x"], p["gridPos"]["y"]): p
        for p in panels if "gridPos" in p
    }

    # Z1-A: must NOT contain synthetic placeholder
    z1a = slot_map.get((3, 20, 0, 0))
    if z1a:
        title_z1a = z1a.get("title", "")
        for marker in PLACEHOLDER_MARKERS:
            if marker in title_z1a:
                errors.append(
                    f"[7-C] Z1-A still has synthetic placeholder title: '{title_z1a}'. "
                    "title_panel.json was NOT used — ensure the file exists at "
                    ".github/agents/panel_templates/title_panel.json and re-run."
                )
    else:
        errors.append("[7-C] Z1-A panel (h=3,w=20,x=0,y=0) not found — cannot check content")

    # Z1-B: must NOT contain synthetic placeholder
    z1b = slot_map.get((3, 3, 21, 0))
    if z1b:
        title_z1b = z1b.get("title", "")
        for marker in PLACEHOLDER_MARKERS:
            if marker in title_z1b:
                errors.append(
                    f"[7-C] Z1-B still has synthetic placeholder title: '{title_z1b}'. "
                    "alert_panel.json was NOT used — ensure the file exists at "
                    ".github/agents/panel_templates/alert_panel.json and re-run."
                )
    else:
        errors.append("[7-C] Z1-B panel (h=3,w=3,x=21,y=0) not found — cannot check content")

    # Z5-MAIN: must have a non-trivial SVG embedded
    z5main = slot_map.get((18, 18, 0, 14))
    if z5main:
        fc = z5main.get("flowcharting", {})
        svg_val = fc.get("svg", "") or ""
        if len(svg_val) < 200:
            errors.append(
                f"[7-C] Z5-MAIN flowcharting.svg is missing or too short "
                f"({len(svg_val)} chars) — the DrawIO SVG was not embedded. "
                "Ensure --flow-svg points to the correct .svg file."
            )
        if svg_val and "mxGraphModel" not in svg_val:
            errors.append(
                "[7-C] Z5-MAIN flowcharting.svg does not contain mxGraphModel — "
                "this is not a valid DrawIO wrapper SVG. Use the .svg file from "
                "build_drawio.py, NOT the .drawio XML file."
            )
    else:
        errors.append("[7-C] Z5-MAIN panel (h=18,w=18,x=0,y=14) not found")

    return errors


def main() -> None:
    if len(sys.argv) < 2:
        # Auto-detect: look for *_dashboard.json in output/
        candidates = glob.glob("output/*_dashboard.json")
        if not candidates:
            print("Usage: python tools/validate_dashboard.py <path/to/dashboard.json>")
            print("No *_dashboard.json found in output/ either.")
            sys.exit(1)
        path = Path(candidates[0])
        print(f"Auto-detected: {path}")
    else:
        path = Path(sys.argv[1])

    if not path.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    errors = validate(path)

    if errors:
        print(f"VALIDATION FAILED — {len(errors)} error(s) in {path.name}:")
        for e in errors:
            print(f"  ✗ {e}")
        sys.exit(1)
    else:
        print(f"VALIDATION PASSED — {path.name}")
        print("  ✓ [7-A] 21 panels, all gridPos slots present")
        print("  ✓ [7-B] All titles are non-empty English")
        print("  ✓ [7-C] Z1-A and Z1-B are production panels (no synthetic placeholders)")
        print("  ✓ [7-C] Z5-MAIN has a valid DrawIO SVG embedded")


if __name__ == "__main__":
    main()
