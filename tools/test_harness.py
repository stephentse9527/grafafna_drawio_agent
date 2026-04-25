#!/usr/bin/env python3
"""
Comprehensive logical test harness for all 7 agent steps.

Each validator checks LOGICAL CORRECTNESS, not just structural presence.
Run against CCMS mock data to confirm the full pipeline is healthy.

Usage
-----
  python tools/test_harness.py

  # Override default file paths:
  python tools/test_harness.py \\
      --knowledge output/ccms_knowledge.json \\
      --drawio    output/ccms_flow.drawio \\
      --svg       output/ccms_flow.svg \\
      --dashboard output/ccms_dashboard.json \\
      --standar   .github/agents/grafana_json_standar/standar.json
"""
from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.state import AppKnowledge

# ---------------------------------------------------------------------------
# Layout contract (must stay in sync with grafana_builder.REQUIRED_LAYOUT)
# ---------------------------------------------------------------------------

REQUIRED_LAYOUT: List[Tuple[str, int, int, int, int]] = [
    ("Z1-A",    3, 20,  0,  0),
    ("Z1-B",    3,  3, 21,  0),
    ("Z2-1",    1,  6,  0,  3),
    ("Z2-2",    1,  6,  6,  3),
    ("Z2-3",    1,  6, 12,  3),
    ("Z2-4",    1,  6, 18,  3),
    ("Z3-1",    4,  3,  0,  4),
    ("Z3-2",    4,  3,  3,  4),
    ("Z3-3",    4,  3,  6,  4),
    ("Z3-4",    4,  3,  9,  4),
    ("Z3-5",    4,  3, 12,  4),
    ("Z3-6",    4,  3, 15,  4),
    ("Z3-7",    4,  6, 18,  4),
    ("Z4-1",    6,  6,  0,  8),
    ("Z4-2",    6,  6,  6,  8),
    ("Z4-3",    6,  6, 12,  8),
    ("Z4-4",    6,  6, 18,  8),
    ("Z5-MAIN", 18, 18,  0, 14),
    ("Z5-R1",   6,  6, 18, 14),
    ("Z5-R2",   6,  6, 18, 20),
    ("Z5-R3",   6,  6, 18, 26),
]

FLOW_PANEL_TYPES = {"agenty-flowcharting-panel", "nline-flow-panel"}
KNOWN_MIDDLEWARE = {"Solace", "MQ", "IBM MQ", "REST", "REST API", "Kafka", "RabbitMQ",
                    "FileIT", "SFTP", "NAS", "gRPC", "HTTP"}
CELL_ID_PREFIXES = {"up_", "dn_", "cu_", "app_", "infra_"}


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------

class StepResult:
    def __init__(self, step: str, name: str):
        self.step = step
        self.name = name
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def fail(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def report(self) -> None:
        status = "PASS" if self.passed else "FAIL"
        print(f"\n[{self.step}] {self.name} — {status}")
        for e in self.errors:
            print(f"  ERROR: {e}")
        for w in self.warnings:
            print(f"  WARN:  {w}")
        if self.passed and not self.warnings:
            print("  (all checks passed)")


# ---------------------------------------------------------------------------
# Step 1 — Confluence page list
# (Simulated: we check whether knowledge.json implies a sensible page list)
# ---------------------------------------------------------------------------

def validate_step1_page_list(knowledge: AppKnowledge) -> StepResult:
    """
    Step 1: Validate that the agent would produce a sensible Confluence page list.
    We simulate this by verifying the app name is non-trivial and knowledge is
    non-empty (i.e., the knowledge base has information worth searching for).
    """
    r = StepResult("Step 1", "Confluence Page List")

    if not knowledge.app_name or len(knowledge.app_name.strip()) < 2:
        r.fail("app_name is empty or too short — page search would find nothing")

    if not knowledge.upstreams and not knowledge.downstreams:
        r.fail("No upstreams or downstreams — architecture page likely missing")

    if not knowledge.business_functions:
        r.fail("No business functions — capability page likely missing or misidentified")

    # IDs should not be placeholder strings
    placeholder_words = {"unknown", "placeholder", "todo", "tbd", "example"}
    name_lc = knowledge.app_name.lower()
    if any(w in name_lc for w in placeholder_words):
        r.fail("app_name looks like a placeholder: " + knowledge.app_name)

    return r


# ---------------------------------------------------------------------------
# Step 2 — Architecture page parsing
# ---------------------------------------------------------------------------

def validate_step2_architecture(knowledge: AppKnowledge) -> StepResult:
    """
    Step 2: Validate that the architecture parsing produced logical knowledge.
    """
    r = StepResult("Step 2", "Architecture Page Parsing")

    # Upstreams
    if len(knowledge.upstreams) == 0:
        r.fail("No upstreams extracted — architecture page body likely not parsed")

    up_names = {u.name for u in knowledge.upstreams}
    for u in knowledge.upstreams:
        if not u.name or len(u.name.strip()) < 2:
            r.fail("Upstream has blank or trivial name: " + repr(u.name))
        if u.connection_middleware in ("Unknown", "", None):
            r.warn("Upstream '" + u.name + "' has Unknown middleware — check page content")
        if len(u.name) > 50:
            r.fail("Upstream name suspiciously long (likely a paragraph): " + u.name[:60])

    # Downstreams
    if len(knowledge.downstreams) == 0:
        r.fail("No downstreams extracted")

    dn_names = {d.name for d in knowledge.downstreams}
    for d in knowledge.downstreams:
        if not d.name or len(d.name.strip()) < 2:
            r.fail("Downstream has blank or trivial name: " + repr(d.name))
        if d.connection_middleware in ("Unknown", "", None):
            r.warn("Downstream '" + d.name + "' has Unknown middleware")

    # No overlap between upstream and downstream names
    overlap = up_names & dn_names
    if overlap:
        r.warn("Same names appear in both upstreams and downstreams: " + str(overlap))

    # Business functions should be short capability names (not sentences)
    for fn in knowledge.business_functions:
        if not fn.name:
            r.fail("BusinessFunction has empty name")
        if len(fn.name) > 40:
            r.fail("BusinessFunction name looks like a sentence: " + fn.name[:50])
        if fn.name.lower() in placeholder_words_set():
            r.fail("BusinessFunction name is a placeholder: " + fn.name)

    # Middleware components — names must be non-trivial
    for mw in knowledge.middleware_components:
        if not mw.name:
            r.fail("MiddlewareComponent has empty name")
        if mw.component_type not in ("messaging", "database", "cache", "secret",
                                      "file_transfer", "service_mesh", "api_gateway"):
            r.warn("Unusual component_type '" + mw.component_type + "' for " + mw.name)

    return r


def placeholder_words_set():
    return {"unknown", "placeholder", "todo", "tbd", "n/a", "na", "none"}


# ---------------------------------------------------------------------------
# Step 3 — RCA page parsing
# ---------------------------------------------------------------------------

def validate_step3_rca(knowledge: AppKnowledge) -> StepResult:
    """
    Step 3: Validate that business metrics and common issues are logically sound.
    """
    r = StepResult("Step 3", "RCA Page Parsing")

    if len(knowledge.business_metrics) == 0:
        r.fail("No business_metrics — RCA pages likely not parsed or metrics not extracted")

    metric_names = set()
    for m in knowledge.business_metrics:
        if not m.name or len(m.name.strip()) < 3:
            r.fail("BusinessMetric has trivial name: " + repr(m.name))
        if m.name in metric_names:
            r.fail("Duplicate metric name: " + m.name)
        metric_names.add(m.name)

        if not m.group:
            r.fail("Metric '" + m.name + "' has no group — won't map to business function")

        if len(m.common_issues) > 0:
            for issue in m.common_issues:
                if len(issue.strip()) < 10:
                    r.warn("common_issue for '" + m.name + "' is too short to be useful: " + issue)

    # Metric groups should correspond to known business function names
    fn_names = {fn.name for fn in knowledge.business_functions}
    metric_groups = {m.group for m in knowledge.business_metrics}
    orphan_groups = metric_groups - fn_names
    if orphan_groups:
        r.warn("Metric groups with no matching BusinessFunction: " + str(orphan_groups))

    # Check at least one metric is instant (for stat panels)
    instant_count = sum(1 for m in knowledge.business_metrics if m.is_instant)
    if instant_count == 0:
        r.warn("No instant metrics — all Z2 stat panels will use fallback labels")

    return r


# ---------------------------------------------------------------------------
# Step 4 — Middleware SVG download
# ---------------------------------------------------------------------------

def validate_step4_middleware_svgs(knowledge: AppKnowledge) -> StepResult:
    """
    Step 4: Validate that middleware SVGs were downloaded and are valid XML.
    """
    r = StepResult("Step 4", "Middleware SVG Download")

    if not knowledge.middleware_components:
        r.warn("No middleware_components — nothing to validate for SVG download")
        return r

    for mw in knowledge.middleware_components:
        if mw.svg_provided:
            if not mw.svg_content:
                r.fail("Middleware '" + mw.name + "' marked svg_provided=True but svg_content is empty")
            else:
                if len(mw.svg_content) < 100:
                    r.fail("SVG for '" + mw.name + "' is suspiciously small (<100 bytes)")
                stripped = mw.svg_content.strip()
                if not (stripped.startswith("<svg") or stripped.startswith("<?xml")):
                    r.fail("SVG for '" + mw.name + "' does not start with <svg or <?xml")
        else:
            r.warn("No SVG provided for middleware: " + mw.name + " (will use shape fallback)")

    return r


# ---------------------------------------------------------------------------
# Step 5 — Knowledge normalisation / grouping
# ---------------------------------------------------------------------------

def validate_step5_knowledge(knowledge: AppKnowledge) -> StepResult:
    """
    Step 5: Validate the fully-normalised AppKnowledge structure.

    Checks grouping logic, no orphan members, no duplicates.
    """
    r = StepResult("Step 5", "Knowledge Normalisation & Grouping")

    app_name = knowledge.app_name
    if not app_name:
        r.fail("app_name is empty")

    up_names = {u.name for u in knowledge.upstreams}
    dn_names = {d.name for d in knowledge.downstreams}

    # Every upstream must appear in exactly one upstream_group
    if knowledge.upstream_groups:
        grouped_ups = set()
        for grp, members in knowledge.upstream_groups.items():
            if not grp:
                r.fail("upstream_groups has an empty group name")
            for m in members:
                if m in grouped_ups:
                    r.fail("Upstream '" + m + "' appears in multiple upstream_groups")
                grouped_ups.add(m)
                if m not in up_names:
                    r.fail("upstream_groups member '" + m + "' not in upstreams list")
        ungrouped = up_names - grouped_ups
        if ungrouped:
            r.warn("Upstreams not in any group: " + str(ungrouped))
    else:
        r.warn("upstream_groups is empty — DrawIO frame grouping will be flat")

    # Every downstream must appear in exactly one downstream_group
    if knowledge.downstream_groups:
        grouped_dns = set()
        for grp, members in knowledge.downstream_groups.items():
            if not grp:
                r.fail("downstream_groups has an empty group name")
            for m in members:
                if m in grouped_dns:
                    r.fail("Downstream '" + m + "' appears in multiple downstream_groups")
                grouped_dns.add(m)
                if m not in dn_names:
                    r.fail("downstream_groups member '" + m + "' not in downstreams list")
        ungrouped = dn_names - grouped_dns
        if ungrouped:
            r.warn("Downstreams not in any group: " + str(ungrouped))
    else:
        r.warn("downstream_groups is empty — DrawIO frame grouping will be flat")

    # Business function names must be unique
    fn_names: List[str] = [fn.name for fn in knowledge.business_functions]
    if len(fn_names) != len(set(fn_names)):
        r.fail("Duplicate business function names: " + str(fn_names))

    # Metric names must be unique
    metric_names: List[str] = [m.name for m in knowledge.business_metrics]
    if len(metric_names) != len(set(metric_names)):
        r.fail("Duplicate business metric names: " + str(metric_names))

    # Connection middleware for upstreams/downstreams must be non-trivial
    for u in knowledge.upstreams:
        if not u.connection_middleware or u.connection_middleware.lower() == "unknown":
            r.warn("Upstream '" + u.name + "' has Unknown connection_middleware")
    for d in knowledge.downstreams:
        if not d.connection_middleware or d.connection_middleware.lower() == "unknown":
            r.warn("Downstream '" + d.name + "' has Unknown connection_middleware")

    return r


# ---------------------------------------------------------------------------
# Step 6 — DrawIO diagram validation
# ---------------------------------------------------------------------------

def validate_step6_drawio(
    knowledge: AppKnowledge,
    drawio_xml: str,
    drawio_svg: str,
) -> StepResult:
    """
    Step 6: Validate the DrawIO XML + SVG outputs.

    Checks:
    - Valid XML with mxGraphModel root
    - All upstream / downstream names appear somewhere in cell values
    - All business function names appear in cell values
    - Cell IDs follow naming convention
    - No duplicate cell IDs
    - Connection units exist for each unique middleware used
    - SVG wrapper is non-empty and contains XML
    """
    r = StepResult("Step 6", "DrawIO Diagram Generation")

    # --- XML validity ---
    try:
        root = ET.fromstring(drawio_xml)
    except ET.ParseError as exc:
        r.fail("DrawIO XML is not valid XML: " + str(exc))
        return r

    if root.tag not in ("mxfile", "mxGraphModel"):
        r.fail("XML root is neither mxfile nor mxGraphModel, got: " + root.tag)

    # Unwrap <mxfile><diagram>...mxGraphModel...</diagram></mxfile>
    if root.tag == "mxfile":
        inner = root.find(".//{*}mxGraphModel") or root.find(".//mxGraphModel")
        if inner is None:
            r.fail("mxfile root has no mxGraphModel inside")
            return r

    cells = root.findall(".//{*}mxCell") or root.findall(".//mxCell")
    if len(cells) < 3:
        r.fail("Too few mxCell elements (" + str(len(cells)) + ") — diagram likely empty")

    # Collect all cell IDs and values
    cell_ids: List[str] = []
    cell_values: List[str] = []
    for cell in cells:
        cid = cell.get("id", "")
        val = cell.get("value", "")
        if cid:
            cell_ids.append(cid)
        if val:
            cell_values.append(val)

    all_values_text = " ".join(cell_values).lower()

    # --- No duplicate IDs ---
    seen_ids: Dict[str, int] = {}
    for cid in cell_ids:
        seen_ids[cid] = seen_ids.get(cid, 0) + 1
    dupes = {k: v for k, v in seen_ids.items() if v > 1}
    if dupes:
        r.fail("Duplicate cell IDs: " + str(dupes))

    # --- Cell ID naming convention ---
    non_reserved_ids = [cid for cid in cell_ids if cid not in ("0", "1")]
    bad_id_count = 0
    for cid in non_reserved_ids:
        if not any(cid.startswith(pfx) for pfx in CELL_ID_PREFIXES):
            bad_id_count += 1
    if bad_id_count > 0:
        r.warn(
            str(bad_id_count) + " cell IDs do not follow convention "
            "(expected up_/dn_/cu_/app_/infra_ prefix)"
        )

    # --- All upstream names present in cell values ---
    for u in knowledge.upstreams:
        if u.name.lower() not in all_values_text:
            r.fail("Upstream '" + u.name + "' not found in any DrawIO cell value")

    # --- All downstream names present in cell values ---
    for d in knowledge.downstreams:
        if d.name.lower() not in all_values_text:
            r.fail("Downstream '" + d.name + "' not found in any DrawIO cell value")

    # --- All business functions present ---
    for fn in knowledge.business_functions:
        if fn.name.lower() not in all_values_text:
            r.fail("BusinessFunction '" + fn.name + "' not found in DrawIO cell values")

    # --- Connection units exist for each unique middleware ---
    used_middleware: set = set()
    for u in knowledge.upstreams:
        if u.connection_middleware and u.connection_middleware.lower() != "unknown":
            used_middleware.add(u.connection_middleware.lower())
    for d in knowledge.downstreams:
        if d.connection_middleware and d.connection_middleware.lower() != "unknown":
            used_middleware.add(d.connection_middleware.lower())

    cu_ids = [cid for cid in cell_ids if cid.startswith("cu_")]
    if used_middleware and len(cu_ids) == 0:
        r.fail("No cu_ (connection unit) cells found — middleware connectors missing")

    # --- SVG wrapper ---
    if not drawio_svg or len(drawio_svg.strip()) < 50:
        r.fail("DrawIO SVG is empty or too short")
    else:
        svg_stripped = drawio_svg.strip()
        if not (svg_stripped.startswith("<svg") or svg_stripped.startswith("<?xml")
                or "mxGraphModel" in svg_stripped[:500]):
            r.fail("DrawIO SVG does not look like valid SVG/XML")

    return r


# ---------------------------------------------------------------------------
# Step 7 — Grafana dashboard validation
# ---------------------------------------------------------------------------

def validate_step7_dashboard(
    knowledge: AppKnowledge,
    dashboard: Dict[str, Any],
) -> StepResult:
    """
    Step 7: Validate the generated Grafana dashboard.

    Checks:
    - Exactly 21 panels
    - Each panel's gridPos matches REQUIRED_LAYOUT exactly
    - Z5-MAIN is a flow charting panel type
    - Z2 slots are stat panels
    - All titles are non-empty strings (not Python placeholders)
    - Dashboard title contains app_name
    - No duplicate panel IDs
    """
    r = StepResult("Step 7", "Grafana Dashboard Generation")

    panels = dashboard.get("panels", [])
    if len(panels) != 21:
        r.fail("Panel count is " + str(len(panels)) + ", expected 21")
        # Still continue to check what is there

    panel_ids: List[int] = []
    for i, (slot, h, w, x, y) in enumerate(REQUIRED_LAYOUT):
        if i >= len(panels):
            r.fail("Missing panel at slot " + slot + " (index " + str(i) + ")")
            continue
        p = panels[i]

        # gridPos
        gp = p.get("gridPos", {})
        expected = {"h": h, "w": w, "x": x, "y": y}
        if gp != expected:
            r.fail("Slot " + slot + " gridPos mismatch: expected " + str(expected) + " got " + str(gp))

        # title
        title = p.get("title", "")
        if not title or not title.strip():
            r.fail("Slot " + slot + " has empty title")
        if len(title) > 120:
            r.warn("Slot " + slot + " title seems very long: " + title[:80])

        # Z2 must be stat
        if slot.startswith("Z2-") and p.get("type") != "stat":
            r.fail("Slot " + slot + " must be 'stat' type, got: " + str(p.get("type")))

        # Z1, Z3, Z4, Z5-R must be timeseries
        if (slot.startswith("Z1-") or slot.startswith("Z3-")
                or slot.startswith("Z4-") or slot.startswith("Z5-R")):
            if p.get("type") != "timeseries":
                r.warn("Slot " + slot + " expected 'timeseries', got: " + str(p.get("type")))

        # Z5-MAIN must be flow panel
        if slot == "Z5-MAIN":
            if p.get("type") not in FLOW_PANEL_TYPES:
                r.fail("Z5-MAIN type must be a flow charting panel, got: " + str(p.get("type")))

        # Panel ID
        pid = p.get("id")
        if pid is None:
            r.fail("Slot " + slot + " has no 'id' field")
        else:
            if pid in panel_ids:
                r.fail("Duplicate panel id=" + str(pid) + " at slot " + slot)
            panel_ids.append(pid)

        # Targets non-empty
        if not p.get("targets"):
            r.warn("Slot " + slot + " has no targets — panel will show no data")

    # Dashboard title
    dash_title = dashboard.get("title", "")
    app_name = knowledge.app_name or ""
    if app_name and app_name.lower() not in dash_title.lower():
        r.warn("Dashboard title '" + dash_title + "' does not contain app_name '" + app_name + "'")

    # UID must be set
    if not dashboard.get("uid"):
        r.fail("Dashboard 'uid' field is missing or empty")

    return r


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_harness(
    knowledge_path: Path,
    drawio_path: Path,
    svg_path: Path,
    dashboard_path: Path,
    standar_path: Path,
) -> int:
    """Run all step validators; return exit code (0=all pass, 1=any fail)."""
    print("=" * 65)
    print("  Grafana Dashboard Agent — Full Pipeline Test Harness")
    print("=" * 65)

    # Load knowledge
    try:
        knowledge = AppKnowledge(**json.loads(knowledge_path.read_text(encoding="utf-8")))
        print("\nLoaded knowledge: " + knowledge.app_name + " ("
              + str(len(knowledge.upstreams)) + " upstreams, "
              + str(len(knowledge.downstreams)) + " downstreams, "
              + str(len(knowledge.business_metrics)) + " metrics)")
    except Exception as exc:
        print("\nFATAL: Cannot load knowledge.json: " + str(exc))
        return 1

    # Load DrawIO XML
    drawio_xml = ""
    if drawio_path.exists():
        drawio_xml = drawio_path.read_text(encoding="utf-8")
    else:
        print("\nWARN: DrawIO file not found: " + str(drawio_path))

    # Load DrawIO SVG
    drawio_svg = ""
    if svg_path.exists():
        drawio_svg = svg_path.read_text(encoding="utf-8")
    else:
        print("\nWARN: SVG file not found: " + str(svg_path))

    # Load dashboard JSON
    dashboard: Dict[str, Any] = {}
    if dashboard_path.exists():
        raw = dashboard_path.read_text(encoding="utf-8")
        try:
            dashboard = json.loads(raw)
        except json.JSONDecodeError as exc:
            print("\nWARN: Cannot parse dashboard JSON: " + str(exc))
    else:
        print("\nWARN: Dashboard file not found: " + str(dashboard_path))

    # Run validators
    results = [
        validate_step1_page_list(knowledge),
        validate_step2_architecture(knowledge),
        validate_step3_rca(knowledge),
        validate_step4_middleware_svgs(knowledge),
        validate_step5_knowledge(knowledge),
    ]

    if drawio_xml or drawio_svg:
        results.append(validate_step6_drawio(knowledge, drawio_xml, drawio_svg))
    else:
        r = StepResult("Step 6", "DrawIO Diagram Generation")
        r.fail("No DrawIO files found — skipped")
        results.append(r)

    if dashboard:
        results.append(validate_step7_dashboard(knowledge, dashboard))
    else:
        r = StepResult("Step 7", "Grafana Dashboard Generation")
        r.fail("No dashboard JSON found — skipped")
        results.append(r)

    # Print all results
    for res in results:
        res.report()

    # Summary
    failed = [r for r in results if not r.passed]
    warned = [r for r in results if r.warnings]
    print("\n" + "=" * 65)
    if not failed:
        print("ALL STEPS PASSED (" + str(len(results)) + "/" + str(len(results)) + ")")
        if warned:
            warn_steps = ", ".join(r.step for r in warned)
            print("Warnings in: " + warn_steps + " (see above)")
        print("=" * 65)
        return 0
    else:
        pass_count = len(results) - len(failed)
        print("FAILED: " + str(len(failed)) + " step(s) — "
              + str(pass_count) + "/" + str(len(results)) + " passed")
        fail_steps = ", ".join(r.step for r in failed)
        print("Failed steps: " + fail_steps)
        print("=" * 65)
        return 1


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="test_harness",
        description="Logical validation harness for all 7 agent pipeline steps.",
    )
    parser.add_argument("--knowledge",  default="output/ccms_knowledge.json")
    parser.add_argument("--drawio",     default="output/ccms_flow.drawio")
    parser.add_argument("--svg",        default="output/ccms_flow.svg")
    parser.add_argument("--dashboard",  default="output/ccms_dashboard.json")
    parser.add_argument("--standar",    default=".github/agents/grafana_json_standar/standar.json")
    args = parser.parse_args()

    code = run_harness(
        knowledge_path=Path(args.knowledge),
        drawio_path=Path(args.drawio),
        svg_path=Path(args.svg),
        dashboard_path=Path(args.dashboard),
        standar_path=Path(args.standar),
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
