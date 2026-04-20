"""
Grafana dashboard JSON builder.

Takes the example dashboard JSON (as a structural template) plus the extracted
app knowledge and flow SVG, then produces a complete Grafana dashboard JSON
ready for import.

Rules
-----
- Panel grid positions and sizes are COPIED EXACTLY from the example dashboard.
- Only panel content (queries, titles, the SVG source) changes.
- All panels use "-- TestData DB --" in the first version so the dashboard can
  be reviewed without a live datasource.
"""
from __future__ import annotations

import copy
import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from agent.state import AppKnowledge, BusinessMetric, LayoutInfo, PanelLayout

# Grafana TestData datasource reference
TESTDATA_DS = {"type": "datasource", "uid": "-- TestData DB --"}

# Panel types used by the FlowCharting / Flow plugin
FLOWCHARTING_PANEL_TYPE = "agenty-flowcharting-panel"
FLOW_PANEL_TYPES = {FLOWCHARTING_PANEL_TYPE, "nline-flow-panel", "marcusolsson-dynamictext-panel"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_uid() -> str:
    return uuid.uuid4().hex[:9]


def _make_testdata_target(ref_id: str = "A", scenario: str = "random_walk") -> Dict[str, Any]:
    return {
        "datasource": TESTDATA_DS,
        "refId": ref_id,
        "scenarioId": scenario,
        "alias": "",
    }


def _clone_panel(panel: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-copy a panel and assign a new id."""
    cloned = copy.deepcopy(panel)
    cloned["id"] = int(uuid.uuid4().int % 10000)
    return cloned


# ---------------------------------------------------------------------------
# Panel builders
# ---------------------------------------------------------------------------

def build_text_banner(title: str, grid_pos: Dict[str, int], panel_id: int) -> Dict[str, Any]:
    """A simple text panel used as a section banner."""
    return {
        "id": panel_id,
        "type": "text",
        "title": "",
        "gridPos": grid_pos,
        "options": {
            "content": f"<div style='text-align:center;font-size:16px;font-weight:bold;color:#ffffff'>"
                       f"{title}</div>",
            "mode": "html",
        },
        "datasource": TESTDATA_DS,
        "targets": [],
    }


def build_stat_panel(
    title: str,
    grid_pos: Dict[str, int],
    panel_id: int,
    unit: str = "short",
) -> Dict[str, Any]:
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "gridPos": grid_pos,
        "datasource": TESTDATA_DS,
        "targets": [_make_testdata_target("A", "random_walk")],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "red", "value": 1},
                    ],
                },
                "mappings": [],
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "orientation": "auto",
            "textMode": "auto",
            "colorMode": "value",
            "graphMode": "none",
            "justifyMode": "auto",
        },
    }


def build_timeseries_panel(
    title: str,
    grid_pos: Dict[str, int],
    panel_id: int,
    legend_labels: Optional[List[str]] = None,
) -> Dict[str, Any]:
    targets = []
    for i, label in enumerate(legend_labels or ["Series A"]):
        targets.append({
            **_make_testdata_target(chr(65 + i), "random_walk"),
            "alias": label,
        })
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": title,
        "gridPos": grid_pos,
        "datasource": TESTDATA_DS,
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "custom": {
                    "lineWidth": 2,
                    "fillOpacity": 10,
                    "showPoints": "never",
                },
                "color": {"mode": "palette-classic"},
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom"},
        },
    }


def build_flow_panel(
    drawio_xml: str,
    grid_pos: Dict[str, int],
    panel_id: int,
    reference_panel: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build a FlowCharting panel with the DrawIO XML embedded.

    If a reference panel is provided, copies its structure and only updates
    the SVG / XML source so that all FlowCharting-specific options are preserved.
    """
    if reference_panel:
        panel = copy.deepcopy(reference_panel)
        panel["id"] = panel_id
        panel["gridPos"] = grid_pos
        # Inject the new XML into the known option paths
        _inject_drawio_xml(panel, drawio_xml)
        return panel

    # Fallback: construct a minimal FlowCharting panel
    return {
        "id": panel_id,
        "type": FLOWCHARTING_PANEL_TYPE,
        "title": "Application Overview",
        "gridPos": grid_pos,
        "datasource": TESTDATA_DS,
        "targets": [_make_testdata_target()],
        "options": {},
        "flowcharting": {
            "version": "1.0.0e",
            "svg": drawio_xml,
            "source": {
                "language": "xml",
                "content": drawio_xml,
            },
        },
    }


def _inject_drawio_xml(panel: Dict[str, Any], xml: str) -> None:
    """Try known paths to inject DrawIO XML into a copied panel."""
    # agenty-flowcharting-panel
    if "flowcharting" in panel:
        panel["flowcharting"]["svg"] = xml
        if "source" in panel["flowcharting"]:
            panel["flowcharting"]["source"]["content"] = xml
        return
    # Custom or newer panel schema
    for path in [
        ["options", "svg"],
        ["options", "content"],
        ["options", "source"],
    ]:
        node = panel
        for key in path[:-1]:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if node is not None and isinstance(node, dict):
            node[path[-1]] = xml
            return


# ---------------------------------------------------------------------------
# Layout-aware dashboard assembler
# ---------------------------------------------------------------------------

class GrafanaDashboardBuilder:
    """
    Assembles a full Grafana dashboard JSON from:
    - the example dashboard (used for layout / panel sizing)
    - extracted app knowledge
    - the generated DrawIO XML
    """

    def __init__(
        self,
        app_knowledge: AppKnowledge,
        layout_info: LayoutInfo,
        drawio_xml: str,
        app_name: str,
    ):
        self.knowledge = app_knowledge
        self.layout = layout_info
        self.drawio_xml = drawio_xml
        self.app_name = app_name
        self._panel_id_counter = 1

    def _next_id(self) -> int:
        pid = self._panel_id_counter
        self._panel_id_counter += 1
        return pid

    # ------------------------------------------------------------------ build

    def build(self) -> Dict[str, Any]:
        panels: List[Dict[str, Any]] = []

        # 1. Title panel (Flow) – clone from example, update app name
        if self.layout.title_panel:
            title_panel = self._build_title_panel()
            panels.append(title_panel)

        # 2. Alert panel – copy verbatim
        if self.layout.alert_panel:
            alert = copy.deepcopy(self.layout.alert_panel.raw_json)
            panels.append(alert)

        # 3. Business metric panels
        panels.extend(self._build_business_metric_panels())

        # 4. System metric panels – copy verbatim (they already have TestData targets)
        for p in self.layout.system_metric_panels:
            panels.append(copy.deepcopy(p.raw_json))

        # 5. Main Flow diagram panel
        flow_panel = self._build_main_flow_panel()
        panels.append(flow_panel)

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
            "tags": ["generated", "sre", self.app_name.lower().replace(" ", "-")],
            "templating": {"list": []},
            "time": {"from": "now-3h", "to": "now"},
            "timepicker": {},
            "timezone": "browser",
            "title": f"{self.app_name} Observability Dashboard",
            "uid": _new_uid(),
            "version": 1,
        }

    # ------------------------------------------------------------------ title

    def _build_title_panel(self) -> Dict[str, Any]:
        panel = copy.deepcopy(self.layout.title_panel.raw_json)
        # Replace app name in title / content fields
        old_name = self._guess_old_app_name(panel)
        if old_name:
            panel_str = json.dumps(panel)
            panel_str = panel_str.replace(old_name, self.app_name)
            panel = json.loads(panel_str)
        return panel

    def _guess_old_app_name(self, panel: Dict[str, Any]) -> Optional[str]:
        """Heuristic: find the app name embedded in the title panel JSON."""
        title: str = panel.get("title", "")
        if title:
            # Remove common suffixes
            for suffix in [" Observability Dashboard", " Dashboard", " Overview"]:
                if title.endswith(suffix):
                    return title.replace(suffix, "").strip()
        return None

    # ------------------------------------------------------------------ flow diagram

    def _build_main_flow_panel(self) -> Dict[str, Any]:
        ref_raw = self.layout.flow_main_panel.raw_json if self.layout.flow_main_panel else None
        grid = (
            self.layout.flow_main_panel.grid_pos
            if self.layout.flow_main_panel
            else {"x": 0, "y": 20, "w": 16, "h": 14}
        )
        return build_flow_panel(
            drawio_xml=self.drawio_xml,
            grid_pos=grid,
            panel_id=self._next_id(),
            reference_panel=ref_raw,
        )

    # ------------------------------------------------------------------ business metrics

    def _build_business_metric_panels(self) -> List[Dict[str, Any]]:
        panels: List[Dict[str, Any]] = []

        # Group metrics by their banner group
        groups: Dict[str, List[BusinessMetric]] = {}
        for m in self.knowledge.business_metrics:
            groups.setdefault(m.group, []).append(m)

        # Get reference positions from the example layout
        ref_groups = self.layout.business_metric_groups
        ref_idx = 0

        current_y = self._get_metric_start_y()

        for group_name, metrics in groups.items():
            # Try to use reference grid positions for this group
            ref_banner_pos = None
            ref_stat_pos = None
            ref_ts_pos = None

            if ref_idx < len(ref_groups) and ref_groups[ref_idx]:
                ref_group = ref_groups[ref_idx]
                for rp in ref_group:
                    if rp.panel_type == "text":
                        ref_banner_pos = rp.grid_pos
                    elif rp.panel_type == "stat":
                        ref_stat_pos = rp.grid_pos
                    elif rp.panel_type == "timeseries":
                        ref_ts_pos = rp.grid_pos
                ref_idx += 1

            # Banner
            banner_pos = ref_banner_pos or {"x": 0, "y": current_y, "w": 6, "h": 2}
            panels.append(build_text_banner(group_name, banner_pos, self._next_id()))

            # Stat panels for instant metrics
            instant_metrics = [m for m in metrics if m.is_instant]
            for i, m in enumerate(instant_metrics):
                sp = ref_stat_pos or {"x": 0, "y": current_y + 2, "w": 3, "h": 4}
                # Offset by column position
                stat_pos = {**sp, "x": sp["x"] + i * sp["w"]}
                panels.append(build_stat_panel(m.name, stat_pos, self._next_id()))

            # Time series panel
            ts_pos = ref_ts_pos or {"x": 0, "y": current_y + 6, "w": 6, "h": 6}
            panels.append(
                build_timeseries_panel(
                    f"{group_name} Req & Resp Vol",
                    ts_pos,
                    self._next_id(),
                    legend_labels=[m.name for m in metrics],
                )
            )

            # Advance y if no reference
            if not ref_banner_pos:
                current_y += 14

        return panels

    def _get_metric_start_y(self) -> int:
        """Determine the Y position where business metrics start."""
        if self.layout.title_panel:
            tp = self.layout.title_panel.grid_pos
            return tp["y"] + tp["h"] + 1
        return 4


# ---------------------------------------------------------------------------
# Public convenience function
# ---------------------------------------------------------------------------

def build_dashboard(
    app_knowledge: AppKnowledge,
    layout_info: LayoutInfo,
    drawio_xml: str,
) -> Dict[str, Any]:
    """Build and return the complete Grafana dashboard dict."""
    builder = GrafanaDashboardBuilder(
        app_knowledge=app_knowledge,
        layout_info=layout_info,
        drawio_xml=drawio_xml,
        app_name=app_knowledge.app_name or "Application",
    )
    return builder.build()


# ---------------------------------------------------------------------------
# Layout extractor (parses an example dashboard JSON → LayoutInfo)
# ---------------------------------------------------------------------------

def extract_layout(dashboard_json: Dict[str, Any]) -> LayoutInfo:
    """
    Parse a Grafana dashboard JSON and return a LayoutInfo with all relevant
    panel positions extracted.
    """
    layout = LayoutInfo()
    all_panels: List[PanelLayout] = []

    raw_panels: List[Dict[str, Any]] = dashboard_json.get("panels", [])
    # Flatten nested rows
    flat = _flatten_panels(raw_panels)

    for p in flat:
        panel_type = p.get("type", "")
        title = p.get("title", "")
        grid_pos = p.get("gridPos", {"x": 0, "y": 0, "w": 6, "h": 4})
        pid = p.get("id", 0)

        pl = PanelLayout(
            panel_id=pid,
            panel_type=panel_type,
            title=title,
            grid_pos=grid_pos,
            raw_json=p,
        )
        all_panels.append(pl)

        # Classify panel
        if panel_type in FLOW_PANEL_TYPES:
            if _is_title_panel(p):
                layout.title_panel = pl
            else:
                layout.flow_main_panel = pl
        elif "alert" in title.lower() or panel_type in ("alertlist", "alertGroups"):
            layout.alert_panel = pl
        elif "system" in title.lower() or "cpu" in title.lower() or "mem" in title.lower():
            layout.system_metric_panels.append(pl)
        elif panel_type == "text" and _looks_like_banner(p):
            # Start of a new business metric group
            layout.business_metric_groups.append([pl])
        elif layout.business_metric_groups:
            # Add subsequent panels to the latest group
            layout.business_metric_groups[-1].append(pl)

    layout.all_panels = all_panels
    return layout


def _flatten_panels(panels: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for p in panels:
        if p.get("type") == "row":
            result.extend(_flatten_panels(p.get("panels", [])))
        else:
            result.append(p)
    return result


def _is_title_panel(panel: Dict[str, Any]) -> bool:
    """Heuristic: the title panel is a Flow panel at y=0 or with a very small y."""
    gp = panel.get("gridPos", {})
    return gp.get("y", 99) < 4 and gp.get("w", 0) >= 12


def _looks_like_banner(panel: Dict[str, Any]) -> bool:
    h = panel.get("gridPos", {}).get("h", 99)
    return h <= 3
