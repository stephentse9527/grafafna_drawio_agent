#!/usr/bin/env python3
"""
End-to-end pipeline test for the Grafana Dashboard Agent.

Simulates the complete 7-step workflow exactly as the agent would execute it
when a user sends the following prompt:

  "Generate a Grafana dashboard for the CCMS application.
   Use CCMS as the APP_SPACE, CCMSRCA as the RCA_SPACE, and
   .github/agents/grafana_json_standar/standar.json as the reference dashboard."

Steps 1-3 (Confluence) are simulated with realistic CCMS mock data because
no live Confluence is available in this environment. Steps 4-7 are fully
executed using the actual Python tools. Every step applies the identical
Validation Gate defined in .github/agents/grafana-dashboard-agent.md.

Usage
-----
  python tools/e2e_pipeline_test.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
OUTPUT_DIR = ROOT / "output" / "e2e_test"
STANDAR_JSON = ROOT / ".github" / "agents" / "grafana_json_standar" / "standar.json"
SVGS_DIR = ROOT / ".github" / "agents" / "svgs"
KNOWLEDGE_PATH = OUTPUT_DIR / "knowledge.json"
DRAWIO_PATH = OUTPUT_DIR / "ccms_flow.drawio"
SVG_PATH = OUTPUT_DIR / "ccms_flow.svg"
DASHBOARD_PATH = OUTPUT_DIR / "ccms_dashboard.json"

# ---------------------------------------------------------------------------
# REQUIRED_GRID contract (must match grafana-dashboard-agent.md Step 7-A)
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

FLOW_PANEL_TYPES = {"agenty-flowcharting-panel", "nline-flow-panel"}

# ---------------------------------------------------------------------------
# Mock Confluence data — what the agent would extract from real Confluence pages
# ---------------------------------------------------------------------------

MOCK_PAGE_LIST = [
    {"id": "11001", "title": "CCMS Architecture Overview"},
    {"id": "11002", "title": "CCMS Integration Points"},
    {"id": "11003", "title": "CCMS Business Functions"},
    {"id": "11004", "title": "CCMS Monitoring Metrics"},
    {"id": "11005", "title": "CCMS Release Notes v2.4"},
    {"id": "11006", "title": "CCMS Team Contact List"},
]

MOCK_ARCHITECTURE_BODY = """
CCMS (Central Clearing Management System) is the core platform that processes
DDI, eDDA, D3, and MYRPP payment clearing flows.

Upstreams:
- SCPAY connects via Solace messaging (Retail Channel group)
- HSBC connects via Solace messaging (Bank Channel group)
- GCG connects via Solace messaging (Retail Channel group)
- CCMS internal self-loop via IBM MQ (Internal group)

Downstreams:
- TSAAS receives notifications via Solace
- EBBS core banking updates via Solace
- PAIMI payment instructions via IBM MQ

Infrastructure:
- Solace PubSub+ messaging middleware
- IBM MQ for legacy integration
- Oracle DB for transaction persistence
- HashiCorp Vault for secret management
- Hazelcast in-memory cache
- NAS file transfer for batch reports
"""

MOCK_METRICS_BODY = """
Business metrics monitored in CCMS:

DDI Metrics:
- DDI Req Count: Total DDI debit requests received (instant count)
- DDI Resp Count: Total DDI responses sent back (instant count)

eDDA Metrics:
- eDDA Req Count: Total eDDA authorisation requests (instant count)
- eDDA Resp Count: Total eDDA authorisation responses (instant count)

D3 Metrics:
- D3 Req Count: DuitNow debit request volume (instant count)
- D3 Resp Count: DuitNow response volume (instant count)

MYRPP Metrics:
- MYRPP Req Count: Malaysia RTP request count (instant count)
- MYRPP Resp Count: Malaysia RTP response count (instant count)
"""

MOCK_RCA_BODY = """
RCA-2024-031: CCMS DDI Processing Outage
Date: 2024-03-15
Root cause: Solace queue depth exceeded threshold causing message loss.
Impact: DDI Req Count dropped to zero for 45 minutes.
Detection: DDI Req Count metric on Grafana showed sudden drop.
Resolution: Increased Solace queue capacity, added dead letter queue monitoring.

RCA-2024-047: eDDA Response Timeout
Date: 2024-04-22
Root cause: Oracle DB connection pool exhausted under high load.
Impact: eDDA Resp Count fell behind Req Count by >1000 for 20 minutes.
Detection: eDDA Req/Resp count divergence visible on dashboard.
Resolution: DB connection pool increased, slow queries optimised.
"""

MOCK_KNOWLEDGE = {
    "app_name": "CCMS",
    "app_description": "Central Clearing Management System — handles DDI, eDDA, D3 and MYRPP payment flows.",
    "upstreams": [
        {"name": "SCPAY", "channel_group": "Retail Channel", "connection_middleware": "Solace", "notes": None},
        {"name": "CCMS",  "channel_group": "Internal",       "connection_middleware": "MQ",     "notes": "self-loop via IBM MQ"},
        {"name": "HSBC",  "channel_group": "Bank Channel",   "connection_middleware": "Solace", "notes": None},
        {"name": "GCG",   "channel_group": "Retail Channel", "connection_middleware": "Solace", "notes": None},
    ],
    "downstreams": [
        {"name": "TSAAS", "category": "Notification", "connection_middleware": "Solace", "notes": None},
        {"name": "EBBS",  "category": "Core Banking",  "connection_middleware": "Solace", "notes": None},
        {"name": "PAIMI", "category": "Payments",      "connection_middleware": "MQ",     "notes": None},
    ],
    "business_functions": [
        {"name": "DDI",   "description": "Direct Debit Instruction processing"},
        {"name": "eDDA",  "description": "Electronic Direct Debit Authorisation"},
        {"name": "D3",    "description": "Direct Debit via DuitNow"},
        {"name": "MYRPP", "description": "Malaysia Real-time Payment Processing"},
    ],
    "business_metrics": [
        {"name": "DDI Req Count",   "group": "DDI",   "description": "Total DDI debit requests",       "is_instant": True,  "common_issues": ["DDI Req Count dropped to zero — check Solace queue depth"]},
        {"name": "DDI Resp Count",  "group": "DDI",   "description": "Total DDI responses returned",   "is_instant": True,  "common_issues": ["Debit resp not received"]},
        {"name": "eDDA Req Count",  "group": "eDDA",  "description": "Total eDDA authorisation requests", "is_instant": True, "common_issues": []},
        {"name": "eDDA Resp Count", "group": "eDDA",  "description": "Total eDDA responses",           "is_instant": True,  "common_issues": ["eDDA Req/Resp divergence — check DB connection pool"]},
        {"name": "D3 Req Count",    "group": "D3",    "description": "DuitNow debit request volume",   "is_instant": True,  "common_issues": []},
        {"name": "D3 Resp Count",   "group": "D3",    "description": "DuitNow response volume",        "is_instant": True,  "common_issues": []},
        {"name": "MYRPP Req Count", "group": "MYRPP", "description": "Malaysia RTP requests",          "is_instant": True,  "common_issues": []},
        {"name": "MYRPP Resp Count","group": "MYRPP", "description": "Malaysia RTP responses",         "is_instant": True,  "common_issues": []},
    ],
    "middleware_components": [
        {"name": "Solace",    "component_type": "messaging",      "svg_provided": False, "svg_content": None},
        {"name": "MQ",        "component_type": "messaging",      "svg_provided": False, "svg_content": None},
        {"name": "DB",        "component_type": "database",       "svg_provided": False, "svg_content": None},
        {"name": "HashiCorp", "component_type": "secret",         "svg_provided": False, "svg_content": None},
        {"name": "Hazelcast", "component_type": "cache",          "svg_provided": False, "svg_content": None},
        {"name": "NAS",       "component_type": "file_transfer",  "svg_provided": False, "svg_content": None},
    ],
    "upstream_groups": {
        "Retail Channel": ["SCPAY", "GCG"],
        "Bank Channel":   ["HSBC"],
        "Internal":       ["CCMS"],
    },
    "downstream_groups": {
        "Notification": ["TSAAS"],
        "Core Banking":  ["EBBS"],
        "Payments":      ["PAIMI"],
    },
}

# ---------------------------------------------------------------------------
# Step result tracking
# ---------------------------------------------------------------------------

class StepResult:
    def __init__(self, step: str, name: str):
        self.step = step
        self.name = name
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.output_summary: str = ""

    def fail(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def report(self) -> None:
        status = "✓ PASS" if self.passed else "✗ FAIL"
        print(f"\n{'─'*60}")
        print(f"  {self.step}: {self.name}  [{status}]")
        print(f"{'─'*60}")
        if self.output_summary:
            print(f"  Output: {self.output_summary}")
        for e in self.errors:
            print(f"  ERROR: {e}")
        for w in self.warnings:
            print(f"  WARN:  {w}")
        if self.passed and not self.warnings:
            print("  All checks passed.")


def sep(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# Step 1 — Confluence page list (simulated)
# ---------------------------------------------------------------------------

def run_step1() -> StepResult:
    r = StepResult("Step 1", "Confluence Page List  [SIMULATED — no live Confluence]")

    pages = MOCK_PAGE_LIST
    r.output_summary = str(len(pages)) + " pages returned"

    # Validation Gate — Step 1
    if not isinstance(pages, list):
        r.fail("Output is not a JSON array")
        return r
    if len(pages) == 0:
        r.fail("Empty array — space key wrong or token lacks permission")
        return r
    for p in pages:
        if "id" not in p or "title" not in p:
            r.fail("Entry missing 'id' or 'title': " + str(p))

    # At least 1 architecture-relevant title
    arch_keywords = {"architecture", "integration", "overview", "monitoring", "metrics", "business"}
    relevant = [p for p in pages if any(kw in p["title"].lower() for kw in arch_keywords)]
    if not relevant:
        r.fail("No architecture/integration/monitoring pages found — re-check space key")
    else:
        r.output_summary += " | " + str(len(relevant)) + " relevant pages identified: " + \
                            ", ".join(p["title"] for p in relevant[:3])

    return r


# ---------------------------------------------------------------------------
# Step 2 — Read architecture pages (simulated)
# ---------------------------------------------------------------------------

def run_step2() -> StepResult:
    r = StepResult("Step 2", "Architecture Page Parsing  [SIMULATED]")

    pages_to_read = [
        ("11001", "CCMS Architecture Overview",  MOCK_ARCHITECTURE_BODY),
        ("11004", "CCMS Monitoring Metrics",      MOCK_METRICS_BODY),
    ]

    extracted: Dict[str, Any] = {
        "app_name": None, "upstreams": [], "downstreams": [],
        "business_functions": [], "middleware_components": [],
    }

    for page_id, title, body in pages_to_read:
        # Validation Gate — Step 2 per page
        if not title:
            r.fail("Page " + page_id + " has empty title")
            continue
        body_clean = body.strip()
        if len(body_clean) < 50:
            r.fail("Page '" + title + "' body too short (" + str(len(body_clean)) + " chars) — skipped")
            continue

        # Extract from architecture page
        if "Architecture" in title:
            extracted["app_name"] = "CCMS"
            extracted["upstreams"]  = ["SCPAY", "HSBC", "GCG", "CCMS (internal)"]
            extracted["downstreams"] = ["TSAAS", "EBBS", "PAIMI"]
            extracted["middleware_components"] = ["Solace", "IBM MQ", "Oracle DB",
                                                  "HashiCorp Vault", "Hazelcast", "NAS"]
        if "Metrics" in title or "Monitoring" in title:
            extracted["business_functions"] = ["DDI", "eDDA", "D3", "MYRPP"]

    if not extracted["app_name"]:
        r.fail("Could not identify app_name from any page")
    if not extracted["upstreams"]:
        r.fail("No upstreams extracted")
    if not extracted["downstreams"]:
        r.fail("No downstreams extracted")
    if not extracted["business_functions"]:
        r.fail("No business functions extracted")

    r.output_summary = ("app=" + str(extracted["app_name"]) +
                        " | " + str(len(extracted["upstreams"])) + " upstreams" +
                        " | " + str(len(extracted["downstreams"])) + " downstreams" +
                        " | " + str(len(extracted["business_functions"])) + " functions")
    return r


# ---------------------------------------------------------------------------
# Step 3 — Read RCA pages (simulated)
# ---------------------------------------------------------------------------

def run_step3() -> StepResult:
    r = StepResult("Step 3", "RCA Page Parsing  [SIMULATED]")

    rca_pages = [("20001", "CCMS Incident Log 2024", MOCK_RCA_BODY)]

    metrics_enriched: List[str] = []
    for page_id, title, body in rca_pages:
        # Validation Gate — Step 3 per page
        incident_keywords = {"rca", "incident", "outage", "root cause", "impact", "resolution"}
        if not any(kw in body.lower() for kw in incident_keywords):
            r.warn("Page '" + title + "' has no incident content — skipped")
            continue

        # Identify impactful metrics
        if "DDI Req Count" in body:
            metrics_enriched.append("DDI Req Count")
        if "eDDA" in body:
            metrics_enriched.append("eDDA Resp Count")

    if not metrics_enriched:
        r.warn("No metrics enriched from RCA pages — common_issues will be empty")

    r.output_summary = str(len(metrics_enriched)) + " metrics enriched from RCA: " + \
                       ", ".join(metrics_enriched)
    return r


# ---------------------------------------------------------------------------
# Step 4 — Middleware SVG icons
# ---------------------------------------------------------------------------

def run_step4() -> StepResult:
    r = StepResult("Step 4", "Middleware SVG Icons Validation")

    builtin = {"Solace", "FileIT", "MQ", "REST API"}
    needed_in_knowledge = [mw["name"] for mw in MOCK_KNOWLEDGE["middleware_components"]]

    # These require user SVGs (not in built-in list)
    non_builtin = [m for m in needed_in_knowledge if m not in builtin]

    svg_extensions = {".svg", ".png", ".drawio"}
    found: List[str] = []
    missing: List[str] = []

    for component in non_builtin:
        hit = False
        for ext in svg_extensions:
            p = SVGS_DIR / (component + ext)
            # Also check case-insensitive equivalents
            if not p.exists():
                # Try lowercase
                p = SVGS_DIR / (component.lower() + ext)
            if p.exists() and p.stat().st_size > 0:
                hit = True
                found.append(component)
                break
        if not hit:
            missing.append(component)

    # Validate built-in Solace SVG
    solace_svg = SVGS_DIR / "solace.svg"
    if not solace_svg.exists():
        r.fail("Built-in solace.svg not found in .github/agents/svgs/ — Step 4 BLOCKED")
    elif solace_svg.stat().st_size == 0:
        r.fail("solace.svg is empty — replace with valid SVG")

    if missing:
        for m in missing:
            r.warn("No SVG/PNG found for '" + m + "' — will use built-in shape fallback")

    r.output_summary = ("Built-in SVGs OK | Non-builtin checked: " +
                        str(len(non_builtin)) + " | Found: " + str(len(found)) +
                        " | Using fallback: " + str(len(missing)))
    return r


# ---------------------------------------------------------------------------
# Step 5 — Write and validate knowledge.json
# ---------------------------------------------------------------------------

def run_step5() -> StepResult:
    r = StepResult("Step 5", "knowledge.json Creation & Validation")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_PATH.write_text(
        json.dumps(MOCK_KNOWLEDGE, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Run the exact inline validation script from the agent instructions (Step 5 gate)
    validation_script = r"""
import json, sys
with open(r'""" + str(KNOWLEDGE_PATH).replace("\\", "\\\\") + r"""', encoding='utf-8') as f:
    k = json.load(f)
errors = []
if not k.get("app_name"): errors.append("app_name is empty")
if not k.get("upstreams") and not k.get("downstreams"): errors.append("both upstreams and downstreams are empty")
if not k.get("business_functions"): errors.append("business_functions is empty")
if not k.get("business_metrics"): errors.append("business_metrics is empty")
for u in k.get("upstreams", []):
    if not u.get("name"): errors.append("upstream missing name: " + str(u))
    if not u.get("connection_middleware"): errors.append("upstream missing connection_middleware: " + u.get("name","?"))
for d in k.get("downstreams", []):
    if not d.get("name"): errors.append("downstream missing name: " + str(d))
    if not d.get("connection_middleware"): errors.append("downstream missing connection_middleware: " + d.get("name","?"))
all_up_names = {u["name"] for u in k.get("upstreams", [])}
grouped_up = {m for members in k.get("upstream_groups", {}).values() for m in members}
missing_up = all_up_names - grouped_up
if missing_up: errors.append("upstreams not in any upstream_group: " + str(missing_up))
all_dn_names = {d["name"] for d in k.get("downstreams", [])}
grouped_dn = {m for members in k.get("downstream_groups", {}).values() for m in members}
missing_dn = all_dn_names - grouped_dn
if missing_dn: errors.append("downstreams not in any downstream_group: " + str(missing_dn))
valid_types = {"messaging", "database", "file_transfer", "cache", "secret"}
for mc in k.get("middleware_components", []):
    if mc.get("component_type") not in valid_types:
        errors.append("invalid component_type for " + mc["name"] + ": " + str(mc.get("component_type")))
if errors:
    print("VALIDATION FAILED:")
    for e in errors: print("  - " + e)
    sys.exit(1)
else:
    print("VALIDATION PASSED")
"""

    python_exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    result = subprocess.run(
        [python_exe, "-c", validation_script],
        capture_output=True, text=True, cwd=str(ROOT)
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0 or "VALIDATION FAILED" in stdout:
        r.fail("knowledge.json validation script failed:\n" + stdout + "\n" + stderr)
    else:
        r.output_summary = "Written to " + str(KNOWLEDGE_PATH) + " | " + stdout

    return r


# ---------------------------------------------------------------------------
# Step 6 — Build DrawIO diagram
# ---------------------------------------------------------------------------

def run_step6() -> StepResult:
    r = StepResult("Step 6", "DrawIO Diagram Generation")

    python_exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    build_drawio = str(ROOT / "tools" / "build_drawio.py")
    output_stem = str(OUTPUT_DIR / "ccms_flow.drawio")

    cmd = [
        python_exe, build_drawio,
        "--knowledge", str(KNOWLEDGE_PATH),
        "--example",   str(STANDAR_JSON),
        "--output",    output_stem,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        r.fail("build_drawio.py exited " + str(result.returncode) + ":\n" + result.stderr)
        return r

    # Validation Gate — Step 6 (exact script from agent instructions)
    for path, label in [(DRAWIO_PATH, ".drawio"), (SVG_PATH, ".svg")]:
        if not path.exists():
            r.fail("Output file not found: " + str(path))
            continue
        content = path.read_text(encoding="utf-8")
        if len(content) < 200:
            r.fail(label + " file too small — likely empty or generation failed")
        if "<mxGraphModel" not in content and "mxGraphModel" not in content:
            r.fail(label + ": missing mxGraphModel — not valid DrawIO XML/SVG")
        if "<mxCell" not in content and "mxCell" not in content:
            r.fail(label + ": no cells found")

    # Extra: verify all system names appear in the DrawIO
    if DRAWIO_PATH.exists():
        drawio_text = DRAWIO_PATH.read_text(encoding="utf-8").lower()
        expected_names = (
            [u["name"].lower() for u in MOCK_KNOWLEDGE["upstreams"]] +
            [d["name"].lower() for d in MOCK_KNOWLEDGE["downstreams"]] +
            [fn["name"].lower() for fn in MOCK_KNOWLEDGE["business_functions"]]
        )
        for name in expected_names:
            if name not in drawio_text:
                r.warn("'" + name + "' not found in DrawIO cell values")

    if r.passed:
        drawio_size = DRAWIO_PATH.stat().st_size if DRAWIO_PATH.exists() else 0
        svg_size = SVG_PATH.stat().st_size if SVG_PATH.exists() else 0
        r.output_summary = (
            str(DRAWIO_PATH.name) + " " + str(drawio_size // 1024) + "KB | " +
            str(SVG_PATH.name) + " " + str(svg_size // 1024) + "KB"
        )

    return r


# ---------------------------------------------------------------------------
# Step 7 — Build Grafana dashboard + validate
# ---------------------------------------------------------------------------

def run_step7() -> StepResult:
    r = StepResult("Step 7", "Grafana Dashboard Generation")

    python_exe = str(ROOT / ".venv" / "Scripts" / "python.exe")
    build_dashboard = str(ROOT / "tools" / "build_dashboard.py")

    cmd = [
        python_exe, build_dashboard,
        "--knowledge", str(KNOWLEDGE_PATH),
        "--example",   str(STANDAR_JSON),
        "--flow-xml",  str(SVG_PATH),
        "--output",    str(OUTPUT_DIR),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if result.returncode != 0:
        r.fail("build_dashboard.py exited " + str(result.returncode) + ":\n" + result.stderr)
        return r

    if not DASHBOARD_PATH.exists():
        r.fail("Dashboard file not created: " + str(DASHBOARD_PATH))
        return r

    # Load dashboard JSON
    raw = DASHBOARD_PATH.read_text(encoding="utf-8")
    try:
        decoder = json.JSONDecoder()
        d, _ = decoder.raw_decode(raw)
    except Exception as exc:
        r.fail("Cannot parse dashboard JSON: " + str(exc))
        return r

    panels = d.get("panels") or d.get("dashboard", {}).get("panels", [])

    # --- Validation Gate 7-B: exact implementation from agent instructions ---
    errors: List[str] = []

    if len(panels) != 21:
        errors.append("expected 21 panels, found " + str(len(panels)))

    actual_grids = [
        {"h": p["gridPos"]["h"], "w": p["gridPos"]["w"],
         "x": p["gridPos"]["x"], "y": p["gridPos"]["y"]}
        for p in panels if "gridPos" in p
    ]

    for req in REQUIRED_GRID:
        match = [g for g in actual_grids
                 if g["h"] == req["h"] and g["w"] == req["w"]
                 and g["x"] == req["x"] and g["y"] == req["y"]]
        if len(match) == 0:
            errors.append(
                "missing slot " + req["slot"] +
                " (h=" + str(req["h"]) + ",w=" + str(req["w"]) +
                ",x=" + str(req["x"]) + ",y=" + str(req["y"]) + ")"
            )
        elif len(match) > 1:
            errors.append(
                "duplicate panels at slot " + req["slot"]
            )

    required_set = {(r2["h"], r2["w"], r2["x"], r2["y"]) for r2 in REQUIRED_GRID}
    for p in panels:
        gp = p.get("gridPos", {})
        key = (gp.get("h"), gp.get("w"), gp.get("x"), gp.get("y"))
        if key not in required_set:
            errors.append(
                "unexpected panel gridPos h=" + str(key[0]) +
                ",w=" + str(key[1]) + ",x=" + str(key[2]) + ",y=" + str(key[3]) +
                " title='" + p.get("title", "?") + "'"
            )

    for p in panels:
        title = p.get("title", "")
        if not title:
            errors.append("panel id=" + str(p.get("id", "?")) + " has empty title")
        elif any(ord(c) > 127 for c in title):
            errors.append("non-English title: '" + title + "'")

    # Z5-MAIN must be flow panel type
    flow_main = next(
        (p for p in panels if p.get("gridPos", {}) == {"h": 18, "w": 18, "x": 0, "y": 14}),
        None
    )
    if flow_main is None:
        errors.append("Z5-MAIN panel not found")
    elif flow_main.get("type") not in FLOW_PANEL_TYPES:
        errors.append("Z5-MAIN type is '" + str(flow_main.get("type")) + "', expected flow panel type")

    # Z2 slots must be stat panels
    for p in panels:
        gp = p.get("gridPos", {})
        if gp.get("y") == 3 and gp.get("h") == 1:
            if p.get("type") != "stat":
                errors.append(
                    "Z2 panel at x=" + str(gp.get("x")) +
                    " must be 'stat', got '" + str(p.get("type")) + "'"
                )

    for e in errors:
        r.fail(e)

    if r.passed:
        r.output_summary = (
            str(DASHBOARD_PATH.name) +
            " | 21 panels | all gridPos match mandatory template | " +
            "Z5-MAIN=" + (flow_main.get("type") if flow_main else "?")
        )

    return r


# ---------------------------------------------------------------------------
# Step 8 — Final report
# ---------------------------------------------------------------------------

def run_step8(results: List[StepResult]) -> None:
    sep("Step 8 — Final Report")
    print("\n  Output files:")
    for p in [KNOWLEDGE_PATH, DRAWIO_PATH, SVG_PATH, DASHBOARD_PATH]:
        size = (str(p.stat().st_size // 1024) + "KB") if p.exists() else "NOT GENERATED"
        print("    " + p.name + " — " + size)

    print("\n  Import dashboard into Grafana:")
    print("    Dashboards → Import → Upload JSON file → select " + str(DASHBOARD_PATH))

    print("\n  Middleware icon usage:")
    for mw in MOCK_KNOWLEDGE["middleware_components"]:
        builtin_set = {"Solace", "FileIT", "MQ", "REST API"}
        src = "built-in SVG" if mw["name"] in builtin_set else "built-in shape (fallback)"
        print("    " + mw["name"] + " → " + src)

    print("\n  Step audit trail:")
    for res in results:
        status = "PASS (1st attempt)" if res.passed else "FAIL"
        print("    " + res.step + ": " + status)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main() -> int:
    sep("Grafana Dashboard Agent — E2E Pipeline Test")
    print("\n  User prompt (simulated):")
    print('  "Generate a Grafana dashboard for the CCMS application.')
    print('   APP_SPACE=CCMS, RCA_SPACE=CCMSRCA,')
    print('   reference=.github/agents/grafana_json_standar/standar.json"')
    print()
    print("  NOTE: Confluence steps 1-3 use MOCK data (no live Confluence available).")
    print("        Steps 4-7 execute real Python tools against real file system.")

    # Clean prior E2E test outputs
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True)

    step_runners = [
        run_step1,
        run_step2,
        run_step3,
        run_step4,
        run_step5,
        run_step6,
        run_step7,
    ]

    results: List[StepResult] = []
    failed_step: Optional[str] = None

    for i, runner in enumerate(step_runners, start=1):
        result = runner()
        result.report()
        results.append(result)

        if not result.passed:
            failed_step = result.step
            print(
                "\n  ❌ Validation gate FAILED at " + result.step +
                " — pipeline halted per agent policy."
            )
            print("  Downstream steps will NOT run.\n")
            break   # agent policy: do not proceed past a failed gate

    # Run remaining steps as "skipped" if we stopped early
    if failed_step:
        remaining = step_runners[len(results):]
        for runner in remaining:
            dummy = runner.__name__.replace("run_", "").replace("step", "Step ")
            print("\n  ⏭  " + dummy.upper() + " — SKIPPED (upstream gate failed)")
    else:
        run_step8(results)

    # Summary
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_run = len(results)
    total_steps = len(step_runners)

    sep("SUMMARY")
    print()
    if failed == 0:
        print("  ✅ ALL STEPS PASSED (" + str(passed) + "/" + str(total_steps) + ")")
        print("  Pipeline complete — dashboard ready for Grafana import.")
    else:
        print("  ❌ FAILED: " + str(failed) + " step(s)  |  "
              + str(passed) + "/" + str(total_run) + " ran steps passed")
        if failed_step:
            print("  Blocked at: " + failed_step)
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
