"""
Grafana dashboard JSON builder — strict 21-panel layout.

The output dashboard ALWAYS has exactly 21 panels whose gridPos values
100% match the mandatory layout contract (derived from standar.json).

Zone layout
-----------
  Z1-A               h=3  w=20 x=0  y=0   Main overview timeseries
  Z1-B               h=3  w=3  x=21 y=0   Auxiliary small timeseries
  Z2-1 … Z2-4        h=1  w=6  y=3        KPI stat strip (4 panels)
  Z3-1 … Z3-7        h=4  w=3  y=4        Narrow timeseries strip (7 panels)
  Z3-7 (wide)        h=4  w=6  x=18 y=4   Wide timeseries (rightmost of Zone 3)
  Z4-1 … Z4-4        h=6  w=6  y=8        Medium chart row (4 panels)
  Z5-MAIN            h=18 w=18 x=0  y=14  DrawIO flow diagram (always)
  Z5-R1 / R2 / R3    h=6  w=6  x=18 y=14/20/26  Right-side stacked panels

Design rules
------------
1. gridPos is a HARD CONTRACT — never calculated, always from REQUIRED_LAYOUT.
2. Panel type + visual config (fieldConfig, options) are CLONED from standar.json.
3. Panel CONTENT (title, targets) is injected from AppKnowledge.
4. Z5-MAIN is always replaced with the DrawIO flow diagram panel regardless of
   what standar.json has in that slot.
5. The slot→content mapping function (_map_content) is the ONLY place where
   "which business metric goes where" logic lives. It will be extended once the
   user provides official per-slot content rules.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List, Optional, Tuple

from agent.state import AppKnowledge

# ---------------------------------------------------------------------------
# Layout contract — immutable; must match standar.json exactly
# ---------------------------------------------------------------------------

# (slot_name, h, w, x, y)
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

assert len(REQUIRED_LAYOUT) == 21, "REQUIRED_LAYOUT must have exactly 21 slots"

# Quick lookup: (h, w, x, y) → slot name
GRID_TO_SLOT: Dict[Tuple[int, int, int, int], str] = {
    (h, w, x, y): slot for slot, h, w, x, y in REQUIRED_LAYOUT
}

# ---------------------------------------------------------------------------
# Panel type constants
# ---------------------------------------------------------------------------

FLOWCHARTING_PANEL_TYPE = "agenty-flowcharting-panel"
FLOW_PANEL_TYPES = {FLOWCHARTING_PANEL_TYPE, "nline-flow-panel"}

# TestData datasource (slots from standar.json already use grafana-testdata-datasource)
TESTDATA_DS = {"type": "grafana-testdata-datasource", "uid": "-- TestData DB --"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_uid() -> str:
    return uuid.uuid4().hex[:9]


def _make_testdata_target(ref_id: str = "A", label: str = "") -> Dict[str, Any]:
    t: Dict[str, Any] = {
        "datasource": TESTDATA_DS,
        "refId": ref_id,
        "scenarioId": "random_walk",
    }
    if label:
        t["alias"] = label
    return t


def _load_template_panels(
    template_json: Dict[str, Any],
) -> Dict[Tuple[int, int, int, int], Dict[str, Any]]:
    """Index template panels by (h, w, x, y) for O(1) slot lookup."""
    result: Dict[Tuple[int, int, int, int], Dict[str, Any]] = {}
    for p in template_json.get("panels", []):
        gp = p.get("gridPos", {})
        key = (gp.get("h"), gp.get("w"), gp.get("x"), gp.get("y"))
        if None not in key:
            result[key] = p
    return result


# ---------------------------------------------------------------------------
# Slot → content mapping
# ---------------------------------------------------------------------------

SlotContent = Dict[str, Any]  # {"title": str, "targets": list}


def _map_content(
    knowledge: AppKnowledge,
    rca_analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, SlotContent]:
    """
    Map AppKnowledge + RCA analysis onto the 19 non-user-provided panel slots.

    Slots Z1-A and Z1-B are NOT mapped here — they are injected directly
    from user-provided JSONs in build_dashboard().

    RCA analysis structure expected:
      {
        "top_business_metrics": [
          {"title": "DDI", "metrics": ["DDI Req Count", "DDI Resp Count"], "rca_source": ..},
          ...
        ],  # exactly 3 items
        "system_metrics": [
          {"name": "Solace Queue Depth", "description": "..."},
          ...
        ]   # exactly 5 items
      }

    Falls back gracefully to knowledge.business_functions / business_metrics
    when rca_analysis is None or incomplete.
    """
    rca = rca_analysis or {}
    top3_bm = rca.get("top_business_metrics", [])
    sys_metrics = rca.get("system_metrics", [])

    # ---- Fallback: build top3_bm from knowledge when rca not provided ----
    if len(top3_bm) < 3:
        # Group metrics by business function name
        fn_names = [f.name for f in knowledge.business_functions[:3]]
        for fn_name in fn_names[len(top3_bm):]:
            fn_metrics = [
                m.name for m in knowledge.business_metrics
                if m.group == fn_name
            ]
            top3_bm.append({"title": fn_name, "metrics": fn_metrics, "rca_source": None})
        # Still short? pad with placeholders
        for i in range(len(top3_bm), 3):
            top3_bm.append({"title": f"BM{i+1}", "metrics": [], "rca_source": None})

    # ---- Fallback: build system_metrics from knowledge when rca not provided ----
    _default_sys = [
        {"name": "Solace Queue Depth", "description": "Solace queue utilization"},
        {"name": "DB Connections",     "description": "Oracle connection pool usage"},
        {"name": "CPU Usage",          "description": "Application CPU"},
        {"name": "Memory Usage",       "description": "JVM heap"},
        {"name": "Error Rate",         "description": "5xx/exception rate"},
    ]
    if len(sys_metrics) < 5:
        sys_metrics = list(sys_metrics) + _default_sys[len(sys_metrics):]

    content: Dict[str, SlotContent] = {}

    # ---- Zone 2 — section header labels ----
    # Displayed via stat panel textMode="name": the panel title is EMPTY,
    # and the target alias becomes the visible text (from standar.json pattern).
    # Color is fixed to #6d786b66 — baked into template fieldConfig; must NOT override.
    # Only the target alias changes (= BM short title or "System Metrics").
    content["Z2-1"] = {"title": "", "targets": [_make_testdata_target("A", top3_bm[0]["title"])]}
    content["Z2-2"] = {"title": "", "targets": [_make_testdata_target("A", top3_bm[1]["title"])]}
    content["Z2-3"] = {"title": "", "targets": [_make_testdata_target("A", top3_bm[2]["title"])]}
    content["Z2-4"] = {"title": "", "targets": [_make_testdata_target("A", "System Metrics")]}

    # ---- Zone 3 — instant stat panels: 2 per BM, then Z3-7 wide system metric ----
    z3_slots = ["Z3-1", "Z3-2", "Z3-3", "Z3-4", "Z3-5", "Z3-6"]
    for bm_i in range(3):
        bm = top3_bm[bm_i]
        bm_metrics = bm["metrics"] if isinstance(bm["metrics"], list) else []
        for metric_j in range(2):
            slot = z3_slots[bm_i * 2 + metric_j]
            if metric_j < len(bm_metrics):
                m_name = bm_metrics[metric_j]
            elif len(bm_metrics) == 1:
                m_name = bm_metrics[0] + " (2)"
            else:
                m_name = bm["title"] + f" Metric {metric_j+1}"
            content[slot] = {
                "title": m_name,
                "targets": [_make_testdata_target("A", m_name)],
            }

    # Z3-7: first system metric as timeseries (wide)
    sys0_name = sys_metrics[0]["name"]
    content["Z3-7"] = {
        "title": sys0_name,
        "targets": [_make_testdata_target("A", sys0_name)],
    }

    # ---- Zone 4 — timeseries per BM + one system metric ----
    for bm_i, slot in enumerate(["Z4-1", "Z4-2", "Z4-3"]):
        bm = top3_bm[bm_i]
        bm_metrics = bm["metrics"] if isinstance(bm["metrics"], list) else []
        targets = [
            _make_testdata_target(chr(65 + j), m)
            for j, m in enumerate(bm_metrics)
        ] or [_make_testdata_target("A", bm["title"])]
        content[slot] = {"title": bm["title"], "targets": targets}

    sys1_name = sys_metrics[1]["name"]
    content["Z4-4"] = {
        "title": sys1_name,
        "targets": [_make_testdata_target("A", sys1_name)],
    }

    # ---- Zone 5 MAIN — SVG injected by build_dashboard ----
    content["Z5-MAIN"] = {
        "title": (knowledge.app_name or "Application") + " Architecture",
        "targets": [_make_testdata_target()],
    }

    # ---- Zone 5 Right — remaining system metric stats ----
    for i, slot in enumerate(["Z5-R1", "Z5-R2", "Z5-R3"]):
        sm = sys_metrics[2 + i]
        content[slot] = {
            "title": sm["name"],
            "targets": [_make_testdata_target("A", sm["name"])],
        }

    return content


# ---------------------------------------------------------------------------
# Panel assemblers
# ---------------------------------------------------------------------------

def _clone_panel(
    slot: str,
    h: int, w: int, x: int, y: int,
    template_panel: Optional[Dict[str, Any]],
    content: SlotContent,
    panel_id: int,
) -> Dict[str, Any]:
    """
    Clone the template panel for this slot, enforce gridPos + content.

    Invariants:
    - gridPos is ALWAYS (h, w, x, y) from REQUIRED_LAYOUT — template value ignored.
    - title is ALWAYS set from content["title"].
    - targets is ALWAYS set from content["targets"].
    - type, fieldConfig, options come from template (preserving visual style).
    """
    if template_panel is not None:
        p = copy.deepcopy(template_panel)
    else:
        p = {
            "type": "timeseries",
            "fieldConfig": {
                "defaults": {
                    "color": {"mode": "palette-classic"},
                    "custom": {"lineWidth": 1, "fillOpacity": 10},
                },
                "overrides": [],
            },
            "options": {"legend": {"displayMode": "list", "placement": "bottom"}},
            "datasource": TESTDATA_DS,
        }

    p["id"] = panel_id
    p["gridPos"] = {"h": h, "w": w, "x": x, "y": y}   # IMMUTABLE CONTRACT
    p["title"] = content["title"]
    p["targets"] = content.get("targets") or [_make_testdata_target()]
    return p


def _build_flow_panel(
    h: int, w: int, x: int, y: int,
    drawio_svg: str,
    panel_id: int,
    app_name: str,
    template_panel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the Z5-MAIN DrawIO flow diagram panel."""
    if template_panel is not None and template_panel.get("type") in FLOW_PANEL_TYPES:
        p = copy.deepcopy(template_panel)
        p["id"] = panel_id
        p["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
        p["title"] = f"{app_name} Architecture"
        if "flowcharting" in p:
            p["flowcharting"]["svg"] = drawio_svg
            if "source" in p["flowcharting"]:
                p["flowcharting"]["source"]["content"] = drawio_svg
        return p

    return {
        "id": panel_id,
        "type": FLOWCHARTING_PANEL_TYPE,
        "title": f"{app_name} Architecture",
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": TESTDATA_DS,
        "targets": [_make_testdata_target()],
        "options": {},
        "flowcharting": {
            "version": "1.0.0e",
            "svg": drawio_svg,
            "source": {"language": "xml", "content": drawio_svg},
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_dashboard(
    app_knowledge: AppKnowledge,
    template_json: Dict[str, Any],
    drawio_svg: str,
    title_panel_json: Optional[Dict[str, Any]] = None,
    alert_panel_json: Optional[Dict[str, Any]] = None,
    rca_analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a Grafana dashboard JSON with exactly 21 panels in the mandatory layout.

    Parameters
    ----------
    app_knowledge : AppKnowledge
        Parsed knowledge.json
    template_json : dict
        Parsed standar.json — provides panel type, fieldConfig, options per slot.
        gridPos values from this template are IGNORED; REQUIRED_LAYOUT is used.
    drawio_svg : str
        DrawIO SVG content to embed in Z5-MAIN.
    title_panel_json : dict, optional
        User-provided Z1-A panel JSON. REQUIRED in production — if None, a
        synthetic placeholder is used (agent must warn). The ONLY permitted
        modification is replacing the literal string "XXXX" in the title field
        with app_knowledge.app_name.
    alert_panel_json : dict, optional
        User-provided Z1-B panel JSON. Deep-copied with zero modifications.
    rca_analysis : dict, optional
        RCA analysis output (top_business_metrics + system_metrics). When
        provided, overrides knowledge-based fallback in _map_content().

    Returns
    -------
    dict
        Complete Grafana dashboard JSON (21 panels, import-ready).
    """
    app_name = app_knowledge.app_name or "Application"
    template_panels = _load_template_panels(template_json)
    content_map = _map_content(app_knowledge, rca_analysis)

    panels: List[Dict[str, Any]] = []
    panel_id = 1

    for slot, h, w, x, y in REQUIRED_LAYOUT:
        gp_key = (h, w, x, y)
        tmpl = template_panels.get(gp_key)
        content = content_map.get(slot, {"title": slot, "targets": [_make_testdata_target()]})

        if slot == "Z1-A":
            if title_panel_json is not None:
                p = copy.deepcopy(title_panel_json)
                # ONLY permitted change: replace "XXXX" placeholder in title
                if "title" in p and "XXXX" in str(p["title"]):
                    p["title"] = str(p["title"]).replace("XXXX", app_name)
            else:
                # Synthetic fallback — warns that user panel is missing
                p = _clone_panel(slot, h, w, x, y, tmpl, {
                    "title": f"[TITLE PANEL MISSING] {app_name}",
                    "targets": [_make_testdata_target()],
                }, panel_id)
            p["id"] = panel_id
        elif slot == "Z1-B":
            if alert_panel_json is not None:
                p = copy.deepcopy(alert_panel_json)
            else:
                p = _clone_panel(slot, h, w, x, y, tmpl, {
                    "title": "[ALERT PANEL MISSING]",
                    "targets": [_make_testdata_target()],
                }, panel_id)
            p["id"] = panel_id
        elif slot == "Z5-MAIN":
            p = _build_flow_panel(h, w, x, y, drawio_svg, panel_id, app_name, tmpl)
        else:
            p = _clone_panel(slot, h, w, x, y, tmpl, content, panel_id)

        # Final safety: no code path can deviate from the contract
        p["gridPos"] = {"h": h, "w": w, "x": x, "y": y}
        panels.append(p)
        panel_id += 1

    assert len(panels) == 21, f"BUG: generated {len(panels)} panels, expected 21"

    return {
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 0,
        "id": None,
        "links": [],
        "liveNow": False,
        "panels": panels,
        "refresh": "30s",
        "schemaVersion": 38,
        "style": "dark",
        "tags": ["generated", "sre", app_name.lower().replace(" ", "-")],
        "templating": {"list": []},
        "time": {"from": "now-3h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "title": f"{app_name} Observability Dashboard",
        "uid": _new_uid(),
        "version": 1,
    }
