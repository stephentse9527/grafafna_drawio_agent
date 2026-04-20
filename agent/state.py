"""
Pydantic state models for the Grafana Dashboard Agent.
These models track everything the agent knows and has produced.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Knowledge models (what the agent learns from Confluence)
# ---------------------------------------------------------------------------

class UpstreamInfo(BaseModel):
    name: str
    channel_group: Optional[str] = None       # e.g. "Retail Channel"
    connection_middleware: str = "Unknown"     # e.g. "Solace", "MQ", "FileIT"
    notes: Optional[str] = None


class DownstreamInfo(BaseModel):
    name: str
    category: Optional[str] = None            # e.g. "Clearing", "Core Banking"
    connection_middleware: str = "Unknown"     # e.g. "Solace", "REST API"
    notes: Optional[str] = None


class BusinessFunction(BaseModel):
    name: str
    description: Optional[str] = None


class BusinessMetric(BaseModel):
    name: str
    group: str                                # Banner label, e.g. "Transactions", "Accounts"
    description: Optional[str] = None
    is_instant: bool = True                   # True → stat panel, False → time series only
    common_issues: List[str] = Field(default_factory=list)   # Derived from RCA analysis


class MiddlewareComponent(BaseModel):
    name: str                                 # e.g. "Solace", "Oracle", "IBM MQ"
    component_type: str = "messaging"         # messaging | database | file_transfer | cache | secret
    svg_provided: bool = False
    svg_content: Optional[str] = None


class AppKnowledge(BaseModel):
    app_name: str = ""
    app_description: str = ""
    upstreams: List[UpstreamInfo] = Field(default_factory=list)
    downstreams: List[DownstreamInfo] = Field(default_factory=list)
    business_functions: List[BusinessFunction] = Field(default_factory=list)
    business_metrics: List[BusinessMetric] = Field(default_factory=list)
    middleware_components: List[MiddlewareComponent] = Field(default_factory=list)
    # Grouped upstreams: {"Channel A": ["AuthService", "StorageService", "APIGateway"], ...}
    upstream_groups: Dict[str, List[str]] = Field(default_factory=dict)
    # Grouped downstreams: {"Clearing": ["SCPay"], "Core Banking": ["EBUS"]}
    downstream_groups: Dict[str, List[str]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layout model (what the agent learns from the example dashboard JSON)
# ---------------------------------------------------------------------------

class PanelLayout(BaseModel):
    """Minimal position/size info for a panel."""
    panel_id: int
    panel_type: str
    title: str
    grid_pos: Dict[str, int]          # {"x": 0, "y": 0, "w": 24, "h": 4}
    raw_json: Dict[str, Any]          # Full original panel JSON (for cloning)


class LayoutInfo(BaseModel):
    grid_width: int = 24              # Grafana standard
    title_panel: Optional[PanelLayout] = None
    alert_panel: Optional[PanelLayout] = None
    flow_main_panel: Optional[PanelLayout] = None
    business_metric_groups: List[List[PanelLayout]] = Field(default_factory=list)
    system_metric_panels: List[PanelLayout] = Field(default_factory=list)
    all_panels: List[PanelLayout] = Field(default_factory=list)


class ColorScheme(BaseModel):
    """Color rules extracted from the example SVG / JSON."""
    healthy_fill: str = "#00b050"
    healthy_stroke: str = "#00b050"
    warning_fill: str = "#FFA500"
    unhealthy_fill: str = "#C0392B"
    frame_fill: str = "none"
    frame_stroke: str = "#00b050"
    text_color_on_fill: str = "#ffffff"
    text_color_on_frame: str = "#00b050"
    connection_color: str = "#00b050"
    background_color: str = "#161719"


# ---------------------------------------------------------------------------
# Top-level agent state
# ---------------------------------------------------------------------------

class AgentState(BaseModel):

    # ---- Inputs (collected during initialisation) ----
    confluence_space_key: Optional[str] = None
    rca_space_key: Optional[str] = None
    example_dashboard_json: Optional[Dict[str, Any]] = None
    # component_name → raw SVG string provided by the user
    component_svgs: Dict[str, str] = Field(default_factory=dict)

    # ---- Extracted / learned ----
    app_knowledge: Optional[AppKnowledge] = None
    layout_info: Optional[LayoutInfo] = None
    color_scheme: Optional[ColorScheme] = None

    # ---- Outputs ----
    flow_svg: Optional[str] = None
    grafana_dashboard_json: Optional[Dict[str, Any]] = None

    # ---- Progress tracking ----
    current_phase: str = "init"        # init | knowledge | diagram | dashboard | complete
    confluence_pages_read: List[str] = Field(default_factory=list)
    skipped_pages: List[str] = Field(default_factory=list)
    error_log: List[str] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
