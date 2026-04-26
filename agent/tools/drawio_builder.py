"""
DrawIO XML builder.

Generates a valid DrawIO mxGraphModel XML string that can be embedded directly
in a Grafana FlowCharting / Flow panel as the diagram source.

Connection unit pattern (the team's defining design rule)
---------------------------------------------------------
Every upstream→app and app→downstream link is a self-contained CONNECTION UNIT:

    [upstream frame]  ──────────────────────────────────────  [APP frame]
                       ←──── connection unit group ────→
                       [left tail] [icon box] [right tail]

The connection unit is a DrawIO GROUP cell that contains:
  1. A fixed-geometry arrow using mxPoint sourcePoint/targetPoint
     (NO source/target cell references → arrow can NEVER auto-route, fold, or fly)
  2. A rounded-rect icon box in the centre
  3. The icon (SVG image or built-in DrawIO shape) inside the box
  4. An optional text label for built-in shapes

The group is positioned absolutely so that:
  • group left edge  == right edge of the upstream/downstream frame
  • group right edge == left edge of the APP frame (or downstream frame)
  • group center Y   == center Y of the group it connects

This guarantees pixel-perfect, straight, non-routing arrows in all cases.

Column layout (derived from connection unit width = 291px):
  UPSTREAM frames  |  291px conn unit  |  APP frame  |  291px conn unit  |  DOWNSTREAM frames
  x=20             |  x=172            |  x=463      |  x=723            |  x=1014
"""
from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from agent.state import AppKnowledge, ColorScheme


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug(name: str) -> str:
    """Convert a display name to a safe DrawIO cell-ID segment.
    e.g. 'REST API' → 'rest_api', 'HSBC Connection' → 'hsbc_connection'
    """
    s = re.sub(r'[^a-z0-9]+', '_', name.lower().strip())
    return s.strip('_') or 'x'


@dataclass
class DrawIOOutput:
    """Result from compose_flow_diagram.

    xml      — mxGraphModel XML string; write as .drawio source file.
    svg      — DrawIO-compatible SVG wrapper; embed in Grafana FlowCharting panel.
    canvas_w — diagram canvas width  (pixels)
    canvas_h — diagram canvas height (pixels)
    """
    xml: str
    svg: str
    canvas_w: int
    canvas_h: int


def _make_svg_wrapper(xml: str, canvas_w: int, canvas_h: int,
                      bg: str = "#181B1F") -> str:
    """Wrap a DrawIO mxGraphModel XML string in a DrawIO-compatible SVG container.

    The SVG uses the ``content`` attribute (HTML-escaped XML) so that:
    - DrawIO desktop / app.diagrams.net can re-open it for editing.
    - Grafana FlowCharting panel can read the diagram source from it.
    """
    content_attr = html.escape(xml, quote=True)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'version="1.1" '
        f'width="{canvas_w}px" height="{canvas_h}px" '
        f'viewBox="-0.5 -0.5 {canvas_w} {canvas_h}" '
        f'content="{content_attr}" '
        f'style="background-color: {bg};">'
        f'<defs/><g/>'
        f'</svg>'
    )


# ---------------------------------------------------------------------------
# Layout constants — derived from the provided .drawio component files
# ---------------------------------------------------------------------------

BLOCK_W   = 120   # solid block width
BLOCK_H   = 36    # solid block height
BLOCK_GAP = 10    # vertical gap between blocks inside a frame
FRAME_PAD = 14    # padding around blocks inside a frame
GROUP_GAP = 20    # vertical gap between groups in the same column
APP_FRAME_W = 260 # app frame width

# ---- SVG Style Standard (v2) -----------------------------------------------
# UPSTREAM / DOWNSTREAM group frames:  dashed gray border, pt=2, no fill
#   strokeColor=#888888, dashed=1, dashPattern=8 4, strokeWidth=2, fillColor=none
# APP frame:                           solid green border (cs.frame_stroke)
# INFRA group box:                     dashed gray border, pt=2, no fill (same as up/dn groups)
# Solid blocks (upstream/downstream member nodes): fillColor=cs.healthy_fill
# Connection arrow:                    strokeColor=cs.healthy_stroke, strokeWidth=2
# Infra item box:                      rounded, strokeColor=cs.frame_stroke, fillColor=#1a1d23
# ----------------------------------------------------------------------------
GROUP_FRAME_STROKE   = "#888888"   # dashed gray for logical grouping frames
GROUP_FRAME_DASHPAT  = "dashed=1;dashPattern=8 4;"  # pt-2 dashed line
GROUP_STROKE_WIDTH   = "2"

# Connection unit dimensions (from solace.drawio / fileit.drawio etc.)
CONN_UNIT_W = 291  # total group width (left tail + icon box + right tail)
CONN_UNIT_H = 38   # group height
CONN_BOX_X  = 99   # icon box left offset within the group (local coords)
CONN_BOX_W  = 89   # icon box width
CONN_ICON_X = 109  # icon left offset (local)
CONN_ICON_Y = 6    # icon top offset (local)
CONN_ICON_W = 66   # icon width
CONN_ICON_H = 26   # icon height
# Standard dimensions for user-provided middleware SVGs (91×40 or 96×40 → render at 91×40)
CONN_SVG_W  = 91
CONN_SVG_H  = 40

# Infra grid layout constants
INFRA_COLS       = 2      # 2 items per row — optimal for 4 infra items
INFRA_ITEM_W     = 108    # width of each infra item box
INFRA_ITEM_H     = 32     # height of each infra item box
INFRA_H_GAP      = 10     # horizontal gap between items in a row
INFRA_V_GAP      = 8      # vertical gap between rows
INFRA_GROUP_PAD  = 10     # padding inside the infra group box
INFRA_GROUP_LABEL_H = 18  # height for group label at top of infra group box

# Column X positions
UPSTREAM_COL_X   = 20
UP_FRAME_W       = BLOCK_W + FRAME_PAD * 2     # 148
CONN_LEFT_X      = UPSTREAM_COL_X + UP_FRAME_W # left connection units
APP_COL_X        = CONN_LEFT_X + CONN_UNIT_W   # app frame left edge
CONN_RIGHT_X     = APP_COL_X + APP_FRAME_W     # right connection units
DOWNSTREAM_COL_X = CONN_RIGHT_X + CONN_UNIT_W  # downstream frames

# ---------------------------------------------------------------------------
# Z5-MAIN target aspect + auto-layout thresholds
# Z5-MAIN panel: 18w × 18h grid units  →  ~900px wide × 540px tall (5:3)
# (Grafana default: ~50px/col, 30px/row at a 1200px-wide dashboard)
# ---------------------------------------------------------------------------
Z5_PANEL_ASPECT = 5.0 / 3.0  # target canvas width:height to fill Z5-MAIN panel

# Left-to-right layout limits: exceed either threshold → auto-switch to TB
LR_ASPECT_LIMIT = 2.5  # LR canvas w:h beyond this is too wide for Z5-MAIN
LR_MAX_GROUPS   = 4    # either side having more groups than this → use TB

# Top-to-bottom layout constants
TB_MARGIN  = 24   # outer margin on all four sides of the TB canvas
TB_H_GAP   = 16   # minimum horizontal gap between groups in the same TB row

# TB connection unit dimensions — same icon box (89×38) as LR, oriented vertically
# Derived from how_connection_with_midleware_TB.drawio:
#   total vertical span = 148px  (source y=230 → target y=378)
#   icon box top offset = 50px   (box y=280, source y=230 → offset = 50)
#   same box: 89×38, fillColor=#111217, strokeColor=green
TB_CONN_UNIT_H  = 148  # total vertical span of one TB connection unit
TB_CONN_BOX_Y   = 50   # offset from top of TB_CONN_UNIT to top of icon box
TB_CU_H_SPACING = 10   # horizontal gap between side-by-side TB connection units (same group)

# ---------------------------------------------------------------------------
# Built-in middleware component specs
# Extracted from the provided .drawio files in .github/agents/svgs/
# ---------------------------------------------------------------------------

BUILTIN_COMPONENT_SPECS: Dict[str, Dict[str, Any]] = {
    "fileit": {
        "icon_style": (
            "sketch=0;"
            "points=[[0,0,0],[0.25,0,0],[0.5,0,0],[0.75,0,0],[1,0,0],"
            "[0,1,0],[0.25,1,0],[0.5,1,0],[0.75,1,0],[1,1,0],"
            "[0,0.25,0],[0,0.5,0],[0,0.75,0],[1,0.25,0],[1,0.5,0],[1,0.75,0]];"
            "outlineConnect=0;fontColor=#232F3E;fillColor=#01A88D;"
            "strokeColor=#ffffff;dashed=0;verticalLabelPosition=bottom;"
            "verticalAlign=top;align=center;html=1;fontSize=12;fontStyle=0;"
            "aspect=fixed;shape=mxgraph.aws4.resourceIcon;"
            "resIcon=mxgraph.aws4.transfer_family;"
        ),
        "icon_x": 109, "icon_y": 6, "icon_w": 25, "icon_h": 25,
        "label": "FileIT",
    },
    "mq": {
        "icon_style": (
            "outlineConnect=0;dashed=0;verticalLabelPosition=bottom;"
            "verticalAlign=top;align=center;html=1;shape=mxgraph.aws3.queue;"
            "fillColor=light-dark(#0066CC,#835801);gradientColor=#00CCCC;"
        ),
        "icon_x": 110, "icon_y": 9, "icon_w": 35, "icon_h": 19,
        "label": "MQ",
    },
    "ibm mq":  {"alias_of": "mq"},
    "ibmmq":   {"alias_of": "mq"},
    "rest api": {
        "icon_style": (
            "aspect=fixed;sketch=0;html=1;dashed=0;whitespace=wrap;"
            "verticalLabelPosition=bottom;verticalAlign=top;"
            "fillColor=#2875E2;strokeColor=#ffffff;"
            "points=[[0.005,0.63,0],[0.1,0.2,0],[0.9,0.2,0],[0.5,0,0],"
            "[0.995,0.63,0],[0.72,0.99,0],[0.5,1,0],[0.28,0.99,0]];"
            "shape=mxgraph.kubernetes.icon2;kubernetesLabel=1;prIcon=api;"
        ),
        "icon_x": 106, "icon_y": 5, "icon_w": 29, "icon_h": 28,
        "label": "REST API",
    },
    "rest_api": {"alias_of": "rest api"},
    "restapi":  {"alias_of": "rest api"},
}


def _resolve_spec(name: str) -> Optional[Dict[str, Any]]:
    """Return the built-in component spec for a middleware name (case-insensitive)."""
    key = name.lower().strip()
    spec = BUILTIN_COMPONENT_SPECS.get(key)
    if spec and "alias_of" in spec:
        spec = BUILTIN_COMPONENT_SPECS.get(spec["alias_of"])
    return spec


def _find_svg(name: str, component_svgs: Dict[str, str]) -> Optional[str]:
    """Case-insensitive lookup in the component_svgs dict.
    Normalises underscores/hyphens to spaces so 'rest_api' matches 'REST API'."""
    def _norm(s: str) -> str:
        return s.lower().strip().replace("_", " ").replace("-", " ")
    needle = _norm(name)
    for k, v in component_svgs.items():
        if _norm(k) == needle:
            return v
    return None


# ---------------------------------------------------------------------------
# Geometry helper
# ---------------------------------------------------------------------------

@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float: return self.x + self.w / 2
    @property
    def cy(self) -> float: return self.y + self.h / 2
    @property
    def right(self) -> float: return self.x + self.w
    @property
    def bottom(self) -> float: return self.y + self.h


# ---------------------------------------------------------------------------
# DrawIO XML builder
# ---------------------------------------------------------------------------

class DrawIOBuilder:
    """
    Builds a DrawIO mxGraphModel XML document.

    Core invariant: every upstream/downstream ↔ app connection uses
    add_connection_unit(), which produces a GROUP with a fixed-geometry
    arrow (mxPoint sourcePoint/targetPoint, NO cell references).
    This guarantees perfectly straight arrows that never auto-route.
    """

    def __init__(self, color_scheme: ColorScheme):
        self.cs = color_scheme
        self._cells: List[Dict[str, Any]] = []
        self._next_id = 10

    def _new_id(self) -> str:
        self._next_id += 1
        return f"c{self._next_id}"

    # ------------------------------------------------------------------ primitives

    def add_solid_block(
        self,
        label: str,
        rect: Rect,
        cell_id: Optional[str] = None,
        fill: Optional[str] = None,
        stroke: Optional[str] = None,
        font_color: Optional[str] = None,
        font_size: int = 11,
        bold: bool = True,
        parent: str = "1",
    ) -> str:
        cell_id = cell_id or self._new_id()
        f  = fill       or self.cs.healthy_fill
        s  = stroke     or self.cs.healthy_stroke
        fc = font_color or self.cs.text_color_on_fill
        style = (
            f"rounded=0;whiteSpace=wrap;html=1;"
            f"fillColor={f};strokeColor={s};"
            f"fontColor={fc};fontSize={font_size};"
            f"fontStyle={'1' if bold else '0'};"
        )
        self._cells.append(dict(
            id=cell_id, value=html.escape(label), style=style,
            vertex="1", parent=parent,
            x=rect.x, y=rect.y, w=rect.w, h=rect.h,
        ))
        return cell_id

    def add_frame(
        self,
        label: str,
        rect: Rect,
        cell_id: Optional[str] = None,
        stroke: Optional[str] = None,
        font_color: Optional[str] = None,
        label_position: str = "top",
        font_size: int = 12,
        bold: bool = True,
        dashed: bool = False,
        stroke_width: int = 1,
        parent: str = "1",
    ) -> str:
        cell_id = cell_id or self._new_id()
        s  = stroke     or self.cs.frame_stroke
        fc = font_color or self.cs.text_color_on_frame
        if dashed:
            dash = GROUP_FRAME_DASHPAT
            s = GROUP_FRAME_STROKE
            fc = GROUP_FRAME_STROKE
        else:
            dash = "dashed=0;"
        sw = f"strokeWidth={stroke_width};"
        valign = "verticalAlign=top;" if label_position == "top" else "verticalAlign=middle;"
        style = (
            f"rounded=0;whiteSpace=wrap;html=1;"
            f"fillColor=none;strokeColor={s};"
            f"fontColor={fc};fontSize={font_size};"
            f"fontStyle={'1' if bold else '0'};"
            f"{valign}{dash}{sw}"
        )
        self._cells.append(dict(
            id=cell_id, value=html.escape(label), style=style,
            vertex="1", parent=parent,
            x=rect.x, y=rect.y, w=rect.w, h=rect.h,
        ))
        return cell_id

    # ------------------------------------------------------------------ connection unit

    def add_connection_unit(
        self,
        abs_x: float,
        abs_y: float,
        component_name: str,
        svg_content: Optional[str] = None,
        builtin_spec: Optional[Dict[str, Any]] = None,
        arrow_direction: str = "right",
        base_id: Optional[str] = None,
    ) -> None:
        """
        Add a CONNECTION UNIT at absolute position (abs_x, abs_y).

        base_id: meaningful prefix for all internal cell IDs, e.g.
                 "cu_up_ccms_solace" → produces cells
                 "cu_up_ccms_solace_arrow", "cu_up_ccms_solace_box", etc.
                 Falls back to auto-incrementing IDs when omitted.

        arrow_direction controls arrowhead placement:
          "right" = arrow points right (→)  — upstream→app or app→downstream
          "left"  = arrow points left  (←)  — reverse flow
          "both"  = bidirectional (↔)

        Structure: ONE full-width arrow (placed first, z-order behind) +
        icon box + icon/label (placed after, z-order in front of arrow).
        """
        def _cid(suffix: str) -> str:
            return f"{base_id}_{suffix}" if base_id else self._new_id()

        mid_y  = abs_y + CONN_UNIT_H / 2
        stroke = self.cs.healthy_stroke
        box_left  = abs_x + CONN_BOX_X
        box_right = box_left + CONN_BOX_W

        # Arrow style based on direction
        if arrow_direction == "right":
            start_arrow = "none"
            end_arrow   = "block"
        elif arrow_direction == "left":
            start_arrow = "block"
            end_arrow   = "none"
        else:  # both
            start_arrow = "block"
            end_arrow   = "block"

        # 1. ONE full-width arrow — placed FIRST so it is behind the box/icon (z-order)
        # exitX/exitY and entryX/entryY pin the endpoints to the connection-point
        # edge of the adjacent frames so DrawIO shows a logically connected graph.
        self._cells.append(dict(
            id=_cid("arrow"), value="",
            style=(
                f"edgeStyle=orthogonalEdgeStyle;html=1;"
                f"exitX=1;exitY=0.5;exitDx=0;exitDy=0;"
                f"entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
                f"strokeColor={stroke};strokeWidth=2;"
                f"startArrow={start_arrow};startFill=1;"
                f"endArrow={end_arrow};endFill=1;"
            ),
            edge="1", parent="1",
            source_point=(abs_x, mid_y),
            target_point=(abs_x + CONN_UNIT_W, mid_y),
        ))

        # 2. Middleware component visual.
        #    User-provided SVGs already contain the complete box+icon+label — embed as-is.
        #    Built-in specs fall back to drawing a separate box + DrawIO shape.
        if svg_content:
            # The SVG (91×40) includes the rounded box, icon and label — one cell only.
            b64 = base64.b64encode(svg_content.encode("utf-8")).decode("ascii")
            self._cells.append(dict(
                id=_cid("svg"), value="",
                style=(
                    f"shape=image;html=1;aspect=fixed;imageAspect=0;"
                    f"image=data:image/svg+xml,{b64};"
                ),
                vertex="1", connectable="0", parent="1",
                x=abs_x + CONN_BOX_X - 1, y=abs_y - 1,
                w=CONN_SVG_W, h=CONN_SVG_H,
            ))
        else:
            # No SVG — draw box + built-in shape or text label.
            self._cells.append(dict(
                id=_cid("box"), value="",
                style=(
                    f"rounded=1;whiteSpace=wrap;html=1;"
                    f"strokeColor={stroke};strokeWidth=2;fillColor=#111217;"
                ),
                vertex="1", connectable="0", parent="1",
                x=box_left, y=abs_y, w=CONN_BOX_W, h=CONN_UNIT_H,
            ))
            if builtin_spec and not builtin_spec.get("label_only"):
                self._cells.append(dict(
                    id=_cid("icon"), value="",
                    style=builtin_spec["icon_style"],
                    vertex="1", connectable="0", parent="1",
                    x=abs_x + builtin_spec["icon_x"], y=abs_y + builtin_spec["icon_y"],
                    w=builtin_spec["icon_w"], h=builtin_spec["icon_h"],
                ))
                if builtin_spec.get("label"):
                    lbl_x = abs_x + builtin_spec["icon_x"] + builtin_spec["icon_w"] + 2
                    lbl_w = (abs_x + CONN_BOX_X + CONN_BOX_W) - lbl_x
                    self._cells.append(dict(
                        id=_cid("label"),
                        value=html.escape(builtin_spec["label"]),
                        style=(
                            "text;html=1;align=left;verticalAlign=middle;"
                            "whiteSpace=wrap;rounded=0;fillColor=none;"
                            "fontColor=#FFFFFF;fontSize=12;fontFamily=Times New Roman;"
                        ),
                        vertex="1", connectable="0", parent="1",
                        x=lbl_x, y=abs_y, w=lbl_w, h=CONN_UNIT_H,
                    ))
            else:
                # Ultimate fallback: centred text label
                label = (builtin_spec or {}).get("label") or component_name
                self._cells.append(dict(
                    id=_cid("label"),
                    value=html.escape(label),
                    style=(
                        "text;html=1;align=center;verticalAlign=middle;"
                        "whiteSpace=wrap;rounded=0;fillColor=none;"
                        "fontColor=#FFFFFF;fontSize=12;fontFamily=Times New Roman;"
                    ),
                    vertex="1", connectable="0", parent="1",
                    x=abs_x + CONN_BOX_X, y=abs_y, w=CONN_BOX_W, h=CONN_UNIT_H,
                ))

    # ------------------------------------------------------------------ infra icon (inside app frame, no arrow)

    def add_infra_icon(
        self,
        name: str,
        rect: Rect,
        svg_content: Optional[str] = None,
        builtin_spec: Optional[Dict[str, Any]] = None,
        base_id: Optional[str] = None,
    ) -> None:
        """Render an infrastructure component (Oracle, NAS, etc.) inside the APP frame.

        base_id: meaningful prefix, e.g. "infra_oracle" → cells
                 "infra_oracle_box", "infra_oracle_icon", "infra_oracle_label".
        """
        def _cid(suffix: str) -> str:
            return f"{base_id}_{suffix}" if base_id else self._new_id()

        # Box
        self._cells.append(dict(
            id=_cid("box"), value="",
            style=(
                f"rounded=1;whiteSpace=wrap;html=1;"
                f"strokeColor={self.cs.frame_stroke};strokeWidth=1;fillColor=#1a1d23;"
            ),
            vertex="1", parent="1",
            x=rect.x, y=rect.y, w=rect.w, h=rect.h,
        ))
        icon_h = min(rect.h - 4, 24)
        icon_w = icon_h
        iy = rect.y + (rect.h - icon_h) / 2
        ix = rect.x + 4

        if svg_content:
            b64 = base64.b64encode(svg_content.encode("utf-8")).decode("ascii")
            self._cells.append(dict(
                id=_cid("icon"), value="",
                style=(
                    f"shape=image;verticalLabelPosition=bottom;verticalAlign=top;"
                    f"aspect=fixed;imageAspect=0;image=data:image/svg+xml,{b64};"
                ),
                vertex="1", parent="1",
                x=ix, y=iy, w=icon_w, h=icon_h,
            ))
        elif builtin_spec:
            self._cells.append(dict(
                id=_cid("icon"), value="",
                style=builtin_spec["icon_style"],
                vertex="1", parent="1",
                x=ix, y=iy, w=builtin_spec["icon_w"], h=builtin_spec["icon_h"],
            ))

        self._cells.append(dict(
            id=_cid("label"),
            value=html.escape(name),
            style=(
                "text;html=1;align=left;verticalAlign=middle;"
                "whiteSpace=wrap;rounded=0;fillColor=none;"
                f"fontColor={self.cs.text_color_on_frame};fontSize=10;"
            ),
            vertex="1", parent="1",
            x=ix + icon_w + 4, y=rect.y, w=rect.w - icon_w - 8, h=rect.h,
        ))

    # ------------------------------------------------------------------ TB connection arrow (deprecated helper, kept for compatibility)

    def add_tb_connection_arrow(
        self,
        x_start: float,
        x_end: float,
        y_start: float,
        y_end: float,
        label: str,
        base_id: str,
    ) -> None:
        """Simple fixed-geometry vertical arrow with inline label.
        Kept for compatibility. Use add_tb_connection_unit for production diagrams.
        """
        stroke = self.cs.healthy_stroke
        self._cells.append(dict(
            id=f"{base_id}_arrow",
            value=html.escape(label),
            style=(
                f"html=1;rounded=0;edgeStyle=none;"
                f"strokeColor={stroke};strokeWidth=2;"
                f"startArrow=none;startFill=0;"
                f"endArrow=block;endFill=1;"
                f"fontColor=#cccccc;fontSize=9;"
                f"labelBackgroundColor=#1a1d23;"
                f"labelBorderColor={stroke};"
            ),
            edge="1", parent="1",
            source_point=(x_start, y_start),
            target_point=(x_end, y_end),
        ))

    # ------------------------------------------------------------------ TB connection unit (icon-box, same pattern as LR but vertical)

    def add_tb_connection_unit(
        self,
        abs_x: float,
        y_top: float,
        y_bot: float,
        component_name: str,
        svg_content: Optional[str] = None,
        builtin_spec: Optional[Dict[str, Any]] = None,
        base_id: Optional[str] = None,
    ) -> None:
        """
        Add a TB CONNECTION UNIT — the vertical equivalent of add_connection_unit.

        Pattern (from how_connection_with_midleware_TB.drawio):
          - One fixed-geometry vertical arrow spanning (abs_x, y_top) → (abs_x, y_bot)
            exitX=0.5;exitY=1  (exits bottom-center of source)
            entryX=0.5;entryY=0 (enters top-center of target)
            Total span = TB_CONN_UNIT_H = 148px
          - Same 89×38 rounded icon box as LR, centered horizontally on abs_x,
            placed at y = y_top + TB_CONN_BOX_Y (= y_top + 50)
          - Same icon/label content as LR connection unit

        The arrow passes THROUGH the icon box (z-order: arrow first, then box on top).
        abs_x  : horizontal center of this connection unit
        y_top  : arrow source Y  (= bottom of upstream group)
        y_bot  : arrow target Y  (= top of APP frame or downstream group)
                 Must satisfy  y_bot - y_top == TB_CONN_UNIT_H  for correct proportions.
        """
        def _cid(suffix: str) -> str:
            return f"{base_id}_{suffix}" if base_id else self._new_id()

        stroke  = self.cs.healthy_stroke
        box_x   = abs_x - CONN_BOX_W / 2
        box_y   = (y_top + y_bot - CONN_UNIT_H) / 2.0  # centre icon box in the connection gap

        # 1. Vertical fixed-geometry arrow — placed FIRST (behind icon box)
        self._cells.append(dict(
            id=_cid("arrow"), value="",
            style=(
                f"edgeStyle=none;html=1;rounded=0;"
                f"strokeColor={stroke};strokeWidth=2;"
                f"startArrow=block;startFill=1;"
                f"endArrow=block;endFill=1;"
            ),
            edge="1", parent="1",
            source_point=(abs_x, y_top),
            target_point=(abs_x, y_bot),
        ))

        # 2. Icon box + content — same logic as LR add_connection_unit
        if svg_content:
            b64 = base64.b64encode(svg_content.encode("utf-8")).decode("ascii")
            self._cells.append(dict(
                id=_cid("svg"), value="",
                style=(
                    f"shape=image;html=1;aspect=fixed;imageAspect=0;"
                    f"image=data:image/svg+xml,{b64};"
                ),
                vertex="1", connectable="0", parent="1",
                x=box_x - 1, y=box_y - 1,
                w=CONN_SVG_W, h=CONN_SVG_H,
            ))
        else:
            self._cells.append(dict(
                id=_cid("box"), value="",
                style=(
                    f"rounded=1;whiteSpace=wrap;html=1;"
                    f"strokeColor={stroke};strokeWidth=2;fillColor=#111217;"
                ),
                vertex="1", connectable="0", parent="1",
                x=box_x, y=box_y, w=CONN_BOX_W, h=CONN_UNIT_H,
            ))
            if builtin_spec and not builtin_spec.get("label_only"):
                # Icon x/y relative to box_x/box_y (same offsets as LR)
                icon_x = box_x + (builtin_spec["icon_x"] - CONN_BOX_X)
                icon_y = box_y + builtin_spec["icon_y"]
                self._cells.append(dict(
                    id=_cid("icon"), value="",
                    style=builtin_spec["icon_style"],
                    vertex="1", connectable="0", parent="1",
                    x=icon_x, y=icon_y,
                    w=builtin_spec["icon_w"], h=builtin_spec["icon_h"],
                ))
                if builtin_spec.get("label"):
                    lbl_x = icon_x + builtin_spec["icon_w"] + 2
                    lbl_w = box_x + CONN_BOX_W - lbl_x
                    self._cells.append(dict(
                        id=_cid("label"),
                        value=html.escape(builtin_spec["label"]),
                        style=(
                            "text;html=1;align=left;verticalAlign=middle;"
                            "whiteSpace=wrap;rounded=0;fillColor=none;"
                            "fontColor=#FFFFFF;fontSize=12;fontFamily=Times New Roman;"
                        ),
                        vertex="1", connectable="0", parent="1",
                        x=lbl_x, y=box_y, w=lbl_w, h=CONN_UNIT_H,
                    ))
            else:
                label = (builtin_spec or {}).get("label") or component_name
                self._cells.append(dict(
                    id=_cid("label"),
                    value=html.escape(label),
                    style=(
                        "text;html=1;align=center;verticalAlign=middle;"
                        "whiteSpace=wrap;rounded=0;fillColor=none;"
                        "fontColor=#FFFFFF;fontSize=12;fontFamily=Times New Roman;"
                    ),
                    vertex="1", connectable="0", parent="1",
                    x=box_x, y=box_y, w=CONN_BOX_W, h=CONN_UNIT_H,
                ))

    # ------------------------------------------------------------------ XML output

    def build(self, canvas_w: int = 1300, canvas_h: int = 900) -> str:
        """Return the complete DrawIO mxGraphModel XML string."""
        root = ET.Element(
            "mxGraphModel",
            dx="1422", dy="762",
            grid="0", gridSize="10",
            guides="1", tooltips="1",
            connect="1", arrows="1",
            fold="1", page="1",
            pageScale="1",
            pageWidth=str(canvas_w),
            pageHeight=str(canvas_h),
            background="#181B1F",
            math="0", shadow="0",
        )
        root_el = ET.SubElement(root, "root")
        ET.SubElement(root_el, "mxCell", id="0")
        ET.SubElement(root_el, "mxCell", id="1", parent="0")

        for cell in self._cells:
            parent = str(cell.get("parent", "1"))
            attribs: Dict[str, str] = {
                "id":     str(cell["id"]),
                "value":  cell.get("value", ""),
                "style":  cell.get("style", ""),
                "parent": parent,
            }
            if cell.get("connectable") == "0":
                attribs["connectable"] = "0"
            if "vertex" in cell:
                attribs["vertex"] = str(cell["vertex"])
            if "edge" in cell:
                attribs["edge"] = str(cell["edge"])
                if "source" in cell:
                    attribs["source"] = str(cell["source"])
                if "target" in cell:
                    attribs["target"] = str(cell["target"])

            mx = ET.SubElement(root_el, "mxCell", **attribs)

            if "edge" not in cell:
                ET.SubElement(
                    mx, "mxGeometry",
                    x=str(cell.get("x", 0)), y=str(cell.get("y", 0)),
                    width=str(cell.get("w", 0)), height=str(cell.get("h", 0)),
                    **{"as": "geometry"},
                )
            else:
                geom = ET.SubElement(mx, "mxGeometry", relative="1", **{"as": "geometry"})
                # Fixed-point edges use mxPoint instead of cell references
                if "source_point" in cell:
                    sx, sy = cell["source_point"]
                    ET.SubElement(geom, "mxPoint", x=str(sx), y=str(sy), **{"as": "sourcePoint"})
                if "target_point" in cell:
                    tx, ty = cell["target_point"]
                    ET.SubElement(geom, "mxPoint", x=str(tx), y=str(ty), **{"as": "targetPoint"})
                if "waypoints" in cell:
                    pts_el = ET.SubElement(geom, "Array", **{"as": "points"})
                    for wx, wy in cell["waypoints"]:
                        ET.SubElement(pts_el, "mxPoint", x=str(round(wx)), y=str(round(wy)))

        return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------------
# High-level layout composer
# ---------------------------------------------------------------------------

def compose_flow_diagram(
    knowledge: AppKnowledge,
    color_scheme: ColorScheme,
    component_svgs: Dict[str, str],
) -> DrawIOOutput:
    """
    Build the complete flow diagram DrawIO XML.

    Design philosophy (inspired by the e-Collections reference diagram):
    ─────────────────────────────────────────────────────────────────────
    • ALL elements share a proportional height — no element is arbitrarily
      taller or shorter than its neighbours.
    • Connection arrows span the ACTUAL gap between columns — not a fixed
      constant.  The icon box is centred inside that gap.
    • Block/frame widths adapt to the available column width so the diagram
      fills the canvas without whitespace or overflow.
    • LR layout: upstream col | gap | APP frame | gap | downstream col
    • TB layout: upstream row ↕ gap ↕ APP frame ↕ gap ↕ downstream row

    Adaptive sizing rules
    ─────────────────────
    LR:
      - canvas_h is chosen to be "tall enough" (min 600px, or enough for all
        groups to display without cramping).
      - Each upstream/downstream GROUP occupies an equal slice of canvas_h,
        so ALL groups have the same height regardless of member count.
      - Inside each group, blocks stretch to fill the group height evenly.
      - The connection gap (between upstream right and app left, and between
        app right and downstream left) is computed from the actual column positions.
      - The icon box is centred in the connection gap both horizontally and
        vertically relative to the group it connects.
    TB:
      - Each group occupies an equal slice of dynamic_app_w (same proportional
        logic as LR but horizontal).
      - Groups stretch vertically to fill the slice height.
      - Connection arrows span the actual vertical gap (variable).
    """
    builder = DrawIOBuilder(color_scheme)

    # ── collect groups ───────────────────────────────────────────────────────
    grouped_up = {m for members in knowledge.upstream_groups.values() for m in members}
    all_up_raw: List[Tuple[str, List[str]]] = list(knowledge.upstream_groups.items())
    for u in knowledge.upstreams:
        if u.name not in grouped_up:
            all_up_raw.append((u.name, [u.name]))

    grouped_dn = {m for members in knowledge.downstream_groups.values() for m in members}
    all_dn_raw: List[Tuple[str, List[str]]] = list(knowledge.downstream_groups.items())
    for d in knowledge.downstreams:
        if d.name not in grouped_dn:
            all_dn_raw.append((d.name, [d.name]))

    # ── unique middlewares per group ─────────────────────────────────────────
    def up_mws(members: List[str]) -> List[str]:
        seen: List[str] = []
        for m in members:
            for u in knowledge.upstreams:
                if u.name == m and u.connection_middleware not in seen:
                    seen.append(u.connection_middleware)
        return seen or ["Solace"]

    def dn_mws(members: List[str]) -> List[str]:
        seen: List[str] = []
        for m in members:
            for d in knowledge.downstreams:
                if d.name == m and d.connection_middleware not in seen:
                    seen.append(d.connection_middleware)
        return seen or ["Solace"]

    # (group_name, members, [mw1, mw2, ...])
    all_up: List[Tuple[str, List[str], List[str]]] = [(n, m, up_mws(m)) for n, m in all_up_raw]
    all_dn: List[Tuple[str, List[str], List[str]]] = [(n, m, dn_mws(m)) for n, m in all_dn_raw]

    # ── infra items (not connection middleware) ──────────────────────────────
    conn_mw_names = {u.connection_middleware.lower() for u in knowledge.upstreams}
    conn_mw_names |= {d.connection_middleware.lower() for d in knowledge.downstreams}
    infra = [
        mc for mc in knowledge.middleware_components
        if mc.component_type in ("database", "cache", "file_transfer", "secret")
        and mc.name.lower() not in conn_mw_names
    ]

    n_fns   = len(knowledge.business_functions)
    n_up    = max(len(all_up), 1)
    n_dn    = max(len(all_dn), 1)
    app_slug = _slug(knowledge.app_name or "application")

    # ── LR/TB decision ───────────────────────────────────────────────────────
    # Use TB when either side has more than LR_MAX_GROUPS groups.
    # For small group counts check whether the LR canvas would be too wide.
    _ltr_w_est = DOWNSTREAM_COL_X + UP_FRAME_W + 60
    _ltr_h_est = 600.0  # minimum canvas height we always enforce
    _ltr_aspect = _ltr_w_est / _ltr_h_est
    use_tb = (_ltr_aspect > LR_ASPECT_LIMIT) or (max(n_up, n_dn) > LR_MAX_GROUPS)

    # ════════════════════════════════════════════════════════════════════════
    # LR LAYOUT
    # ════════════════════════════════════════════════════════════════════════
    if not use_tb:
        # ── fixed horizontal column dimensions ──────────────────────────────
        MARGIN_TOP    = 40.0
        MARGIN_BOTTOM = 40.0
        MARGIN_LEFT   = 20.0
        MARGIN_RIGHT  = 20.0
        LR_UP_COL_W  = 148.0   # upstream/downstream frame column width
        LR_CONN_GAP  = 160.0   # gap between col and APP frame (holds icon box)
        LR_APP_W     = 280.0   # app frame width
        LR_DN_COL_W  = 148.0
        LR_GROUP_GAP = 16.0    # minimum gap between consecutive groups

        canvas_w = int(MARGIN_LEFT + LR_UP_COL_W + LR_CONN_GAP
                       + LR_APP_W + LR_CONN_GAP + LR_DN_COL_W + MARGIN_RIGHT)

        # ── natural (compact) height of each group ───────────────────────────
        # Groups are NEVER stretched — blocks stay at BLOCK_H = 36px.
        # A "bare block" is a singleton whose name equals its single member.
        def _lr_group_h(name: str, members: List[str]) -> float:
            if len(members) == 1 and name == members[0]:
                return float(BLOCK_H + 16)   # block + 8px top/bottom breathing room
            n = len(members)
            return float(FRAME_PAD * 2 + 20 + n * BLOCK_H + max(0, n - 1) * BLOCK_GAP)

        up_ghs = [_lr_group_h(n, m) for n, m, _ in all_up]
        dn_ghs = [_lr_group_h(n, m) for n, m, _ in all_dn]

        # ── infra dimensions ─────────────────────────────────────────────────
        infra_group_h = 0.0
        if infra:
            i_rows        = (len(infra) + INFRA_COLS - 1) // INFRA_COLS
            igrid_h       = i_rows * INFRA_ITEM_H + max(0, i_rows - 1) * INFRA_V_GAP
            infra_group_h = igrid_h + INFRA_GROUP_PAD * 2 + INFRA_GROUP_LABEL_H

        # ── app natural height (sized exactly to content) ────────────────────
        fn_total_h    = n_fns * BLOCK_H + max(0, n_fns - 1) * BLOCK_GAP
        infra_sect_h  = (BLOCK_GAP * 2 + infra_group_h) if infra else 0
        app_natural_h = float(FRAME_PAD * 2 + 20 + fn_total_h + infra_sect_h)

        # ── usable height = tallest element column (min 240px) ───────────────
        # Groups are distributed with even gaps; this is the vertical range.
        total_up_gh = sum(up_ghs) + max(0, n_up - 1) * LR_GROUP_GAP
        total_dn_gh = sum(dn_ghs) + max(0, n_dn - 1) * LR_GROUP_GAP
        usable_h    = max(total_up_gh, total_dn_gh, app_natural_h, 240.0)
        canvas_h    = int(usable_h + MARGIN_TOP + MARGIN_BOTTOM)

        # ── column X positions ───────────────────────────────────────────────
        lx_up     = MARGIN_LEFT
        lx_conn_l = lx_up + LR_UP_COL_W
        lx_app    = lx_conn_l + LR_CONN_GAP
        lx_conn_r = lx_app + LR_APP_W
        lx_dn     = lx_conn_r + LR_CONN_GAP

        # ── app frame: compact height, vertically centred ────────────────────
        app_h = app_natural_h
        app_y = MARGIN_TOP + (usable_h - app_h) / 2.0

        # ── distribute groups with even gaps, centred if only one ────────────
        MAX_GAP = 80.0   # cap inter-group gap so columns don't look sparse

        def _col_positions(ghs: List[float], n: int) -> Tuple[float, List[float]]:
            """Return (start_y, [gap_after_group_0, gap_after_group_1, ...]).

            start_y is MARGIN_TOP (or centred for single group).
            Gaps are capped at MAX_GAP; the whole column is centred when capped.
            """
            if n == 0:
                return MARGIN_TOP, []
            if n == 1:
                return MARGIN_TOP + (usable_h - ghs[0]) / 2.0, [0.0]
            total_gh = sum(ghs)
            raw_gap  = (usable_h - total_gh) / (n - 1)
            gap      = min(MAX_GAP, max(LR_GROUP_GAP, raw_gap))
            col_h    = total_gh + (n - 1) * gap
            start_y  = MARGIN_TOP + (usable_h - col_h) / 2.0
            return start_y, [gap] * (n - 1) + [0.0]

        up_start_y, up_gaps = _col_positions(up_ghs, n_up)
        dn_start_y, dn_gaps = _col_positions(dn_ghs, n_dn)

        # ── draw app frame contents (top-aligned, no centering whitespace) ───
        def _draw_app_contents_lr(ax: float, ay: float, aw: float) -> None:
            inner_w = aw - FRAME_PAD * 2
            fn_y    = ay + FRAME_PAD + 20   # label clearance
            for fn in knowledge.business_functions:
                builder.add_solid_block(
                    fn.name,
                    Rect(ax + FRAME_PAD, fn_y, inner_w, BLOCK_H),
                    cell_id=f"app_fn_{_slug(fn.name)}",
                )
                fn_y += BLOCK_H + BLOCK_GAP
            if infra:
                igrid_w  = INFRA_COLS * INFRA_ITEM_W + (INFRA_COLS - 1) * INFRA_H_GAP
                igroup_w = igrid_w + INFRA_GROUP_PAD * 2
                igx      = ax + (aw - igroup_w) / 2.0
                igy      = fn_y + BLOCK_GAP   # one-gap separator
                builder.add_frame(
                    "Infrastructure",
                    Rect(igx, igy, igroup_w, infra_group_h),
                    cell_id=f"infra_group_{app_slug}",
                    dashed=True, stroke_width=2, font_size=10, bold=False,
                )
                for idx, mc in enumerate(infra):
                    col_i = idx % INFRA_COLS
                    row_i = idx // INFRA_COLS
                    ix = igx + INFRA_GROUP_PAD + col_i * (INFRA_ITEM_W + INFRA_H_GAP)
                    iy = (igy + INFRA_GROUP_LABEL_H + INFRA_GROUP_PAD
                          + row_i * (INFRA_ITEM_H + INFRA_V_GAP))
                    builder.add_infra_icon(
                        mc.name, Rect(ix, iy, INFRA_ITEM_W, INFRA_ITEM_H),
                        svg_content=_find_svg(mc.name, component_svgs),
                        builtin_spec=_resolve_spec(mc.name),
                        base_id=f"infra_{_slug(mc.name)}",
                    )

        # ── draw one group at its natural compact size ───────────────────────
        def _draw_lr_group(
            name: str, members: List[str], col_x: float, col_w: float,
            group_y: float, group_h: float, cell_prefix: str,
        ) -> None:
            g_slug  = _slug(name)
            is_bare = (len(members) == 1 and name == members[0])
            if is_bare:
                # Bare singleton block — fixed height BLOCK_H, centred in natural slot
                bh = float(BLOCK_H)
                by = group_y + (group_h - bh) / 2.0
                builder.add_solid_block(
                    members[0],
                    Rect(col_x, by, col_w, bh),
                    cell_id=f"{cell_prefix}_{g_slug}",
                )
            else:
                # Dashed frame wrapping member blocks at fixed BLOCK_H
                builder.add_frame(
                    name,
                    Rect(col_x, group_y, col_w, group_h),
                    cell_id=f"{cell_prefix}_frame_{g_slug}",
                    dashed=True, stroke_width=2,
                )
                iy = group_y + FRAME_PAD + 20
                bw = col_w - FRAME_PAD * 2
                for m in members:
                    builder.add_solid_block(
                        m,
                        Rect(col_x + FRAME_PAD, iy, bw, BLOCK_H),
                        cell_id=f"{cell_prefix}_{g_slug}_{_slug(m)}",
                    )
                    iy += BLOCK_H + BLOCK_GAP

        # ── draw one LR connection icon + elbow-routed arrow in the gap ─────
        def _draw_lr_connection(
            mw: str,
            x_left: float, x_right: float,   # gap boundaries
            group_cy: float,                   # group center y (on the group side)
            port_y: float,                     # app wall attachment y (may differ)
            base_id: str,
            is_upstream: bool = True,
        ) -> None:
            """
            Route the connection with an elbow bend near the APP side so that:
            - Upstream  (is_upstream=True) : long segment near group, bend 20px before app.
            - Downstream (is_upstream=False): short segment near app, bend 20px after app,
                                              long segment near group.
            The middleware icon-box sits on the long segment at group_cy.
            When group_cy == port_y (no offset needed), the path is a straight line.
            Non-crossing guarantee: groups and ports are both ordered top-to-bottom,
            so vertical segments at the same bend_x never intersect.
            """
            gap_w  = x_right - x_left
            box_w  = min(CONN_BOX_W, max(40.0, gap_w - 30.0))
            box_h  = float(CONN_UNIT_H)
            stroke = builder.cs.healthy_stroke

            svg_content  = _find_svg(mw, component_svgs)
            builtin_spec = _resolve_spec(mw)

            def _cid(s: str) -> str:
                return f"{base_id}_{s}"

            needs_elbow = abs(group_cy - port_y) > 2.0

            if is_upstream:
                # Flow: group (x_left, group_cy) → [long segment+box] → [bend] → app (x_right, port_y)
                bend_x       = x_right - 20.0
                src_pt       = (x_left, group_cy)
                tgt_pt       = (x_right, port_y)
                waypoints    = [(bend_x, group_cy), (bend_x, port_y)] if needs_elbow else []
                seg_end      = bend_x if needs_elbow else x_right
                box_center_x = (x_left + seg_end) / 2.0
                box_y        = group_cy - box_h / 2.0
            else:
                # Flow: app (x_left, port_y) → [bend] → [long segment+box] → group (x_right, group_cy)
                bend_x       = x_left + 20.0
                src_pt       = (x_left, port_y)
                tgt_pt       = (x_right, group_cy)
                waypoints    = [(bend_x, port_y), (bend_x, group_cy)] if needs_elbow else []
                seg_start    = bend_x if needs_elbow else x_left
                box_center_x = (seg_start + x_right) / 2.0
                box_y        = group_cy - box_h / 2.0

            box_x = box_center_x - box_w / 2.0

            # Arrow with optional elbow waypoints
            arrow_dict = dict(
                id=_cid("arrow"), value="",
                style=(
                    f"edgeStyle=none;html=1;rounded=0;strokeColor={stroke};strokeWidth=2;"
                    f"startArrow=none;startFill=1;endArrow=block;endFill=1;"
                ),
                edge="1", parent="1",
                source_point=src_pt,
                target_point=tgt_pt,
            )
            if waypoints:
                arrow_dict["waypoints"] = waypoints
            builder._cells.append(arrow_dict)

            if svg_content:
                b64 = base64.b64encode(svg_content.encode("utf-8")).decode("ascii")
                builder._cells.append(dict(
                    id=_cid("svg"), value="",
                    style=(
                        f"shape=image;html=1;aspect=fixed;imageAspect=0;"
                        f"image=data:image/svg+xml,{b64};"
                    ),
                    vertex="1", connectable="0", parent="1",
                    x=box_x, y=group_cy - CONN_SVG_H / 2.0,
                    w=CONN_SVG_W, h=CONN_SVG_H,
                ))
            else:
                builder._cells.append(dict(
                    id=_cid("box"), value="",
                    style=(
                        f"rounded=1;whiteSpace=wrap;html=1;"
                        f"strokeColor={stroke};strokeWidth=2;fillColor=#111217;"
                    ),
                    vertex="1", connectable="0", parent="1",
                    x=box_x, y=box_y, w=box_w, h=box_h,
                ))
                if builtin_spec and not builtin_spec.get("label_only"):
                    ico_w = min(builtin_spec["icon_w"], box_w - 30)
                    ico_h = builtin_spec["icon_h"]
                    ico_x = box_x + 4
                    ico_y = box_y + (box_h - ico_h) / 2.0
                    builder._cells.append(dict(
                        id=_cid("icon"), value="",
                        style=builtin_spec["icon_style"],
                        vertex="1", connectable="0", parent="1",
                        x=ico_x, y=ico_y, w=ico_w, h=ico_h,
                    ))
                    if builtin_spec.get("label"):
                        lbl_x = ico_x + ico_w + 2
                        lbl_w = box_x + box_w - lbl_x
                        builder._cells.append(dict(
                            id=_cid("label"),
                            value=html.escape(builtin_spec["label"]),
                            style=(
                                "text;html=1;align=left;verticalAlign=middle;"
                                "whiteSpace=wrap;rounded=0;fillColor=none;"
                                "fontColor=#FFFFFF;fontSize=11;"
                            ),
                            vertex="1", connectable="0", parent="1",
                            x=lbl_x, y=box_y, w=lbl_w, h=box_h,
                        ))
                else:
                    label = (builtin_spec or {}).get("label") or mw
                    builder._cells.append(dict(
                        id=_cid("label"),
                        value=html.escape(label),
                        style=(
                            "text;html=1;align=center;verticalAlign=middle;"
                            "whiteSpace=wrap;rounded=0;fillColor=none;"
                            "fontColor=#FFFFFF;fontSize=11;"
                        ),
                        vertex="1", connectable="0", parent="1",
                        x=box_x, y=box_y, w=box_w, h=box_h,
                    ))

        # ── app connection ports: one per group, evenly distributed along the app wall ──
        # This ensures every connection arrow reaches the app frame even when the
        # group column is taller than the app.
        up_ports = [app_y + app_h * (i + 0.5) / n_up for i in range(n_up)]
        dn_ports = [app_y + app_h * (i + 0.5) / n_dn for i in range(n_dn)]

        # ── draw upstream column ─────────────────────────────────────────────
        up_y = up_start_y
        for i, (name, members, mws) in enumerate(all_up):
            gh = up_ghs[i]
            _draw_lr_group(name, members, lx_up, LR_UP_COL_W, up_y, gh,
                           cell_prefix="up_block")
            # Connection arrows centred on the group; multiple MWs stacked
            group_cy = up_y + gh / 2.0
            n_mws    = len(mws)
            mw_step  = CONN_UNIT_H + 6.0
            for j, mw in enumerate(mws):
                offset   = (j - (n_mws - 1) / 2.0) * mw_step
                conn_cy  = group_cy + offset
                _draw_lr_connection(mw, lx_conn_l, lx_app, conn_cy, up_ports[i],
                                    f"cu_up_{_slug(name)}_{_slug(mw)}",
                                    is_upstream=True)
            up_y += gh + up_gaps[i]

        # ── draw app frame ───────────────────────────────────────────────────
        builder.add_frame(
            knowledge.app_name or "Application",
            Rect(lx_app, app_y, LR_APP_W, app_h),
            cell_id=f"app_frame_{app_slug}",
        )
        _draw_app_contents_lr(lx_app, app_y, LR_APP_W)

        # ── draw downstream column ───────────────────────────────────────────
        dn_y = dn_start_y
        for i, (name, members, mws) in enumerate(all_dn):
            gh = dn_ghs[i]
            _draw_lr_group(name, members, lx_dn, LR_DN_COL_W, dn_y, gh,
                           cell_prefix="dn_block")
            group_cy = dn_y + gh / 2.0
            n_mws    = len(mws)
            mw_step  = CONN_UNIT_H + 6.0
            for j, mw in enumerate(mws):
                offset  = (j - (n_mws - 1) / 2.0) * mw_step
                conn_cy = group_cy + offset
                _draw_lr_connection(mw, lx_conn_r, lx_dn, conn_cy, dn_ports[i],
                                    f"cu_dn_{_slug(name)}_{_slug(mw)}",
                                    is_upstream=False)
            dn_y += gh + dn_gaps[i]

    # ════════════════════════════════════════════════════════════════════════
    # TB LAYOUT
    # ════════════════════════════════════════════════════════════════════════
    else:
        # ── group size helpers ───────────────────────────────────────────────
        def _is_bare_block(name: str, members: List[str]) -> bool:
            return len(members) == 1 and name == members[0]

        def _tb_min_gw(name: str, members: List[str]) -> float:
            return float(BLOCK_W if _is_bare_block(name, members) else UP_FRAME_W)

        def _tb_natural_gh(name: str, members: List[str]) -> float:
            """Compact natural height — blocks always BLOCK_H, no stretching."""
            if _is_bare_block(name, members):
                return float(BLOCK_H + 16)
            n = len(members)
            return float(FRAME_PAD * 2 + 20 + n * BLOCK_H + max(0, n - 1) * BLOCK_GAP)

        up_natural_ghs = [_tb_natural_gh(n, m) for n, m, _ in all_up]
        dn_natural_ghs = [_tb_natural_gh(n, m) for n, m, _ in all_dn]
        up_row_h = max(up_natural_ghs) if up_natural_ghs else float(BLOCK_H + 16)
        dn_row_h = max(dn_natural_ghs) if dn_natural_ghs else float(BLOCK_H + 16)

        # Minimum APP frame width so proportionally-spaced groups never overlap
        up_min_gws = [_tb_min_gw(n, m) for n, m, _ in all_up]
        dn_min_gws = [_tb_min_gw(n, m) for n, m, _ in all_dn]

        def _min_app_w_for_row(gws: List[float], n: int) -> float:
            if n <= 0 or not gws:
                return float(APP_FRAME_W)
            if n == 1:
                return max(float(APP_FRAME_W), gws[0] + 2 * TB_MARGIN)
            adj = max(
                (gws[i] + gws[i + 1]) / 2.0 + TB_H_GAP
                for i in range(len(gws) - 1)
            )
            return float(n) * max(adj, max(gws[0], gws[-1]))

        dynamic_app_w = max(
            float(APP_FRAME_W),
            _min_app_w_for_row(up_min_gws, n_up),
            _min_app_w_for_row(dn_min_gws, n_dn),
        )

        # App natural height (compact, sized to content)
        if infra:
            i_rows        = (len(infra) + INFRA_COLS - 1) // INFRA_COLS
            i_grid_h      = i_rows * INFRA_ITEM_H + max(0, i_rows - 1) * INFRA_V_GAP
            infra_group_h_tb = i_grid_h + INFRA_GROUP_PAD * 2 + INFRA_GROUP_LABEL_H
        else:
            infra_group_h_tb = 0.0

        # 2-column function grid in TB mode: reduces app height significantly
        fn_cols_tb = 2 if n_fns > 1 else 1
        fn_rows_tb = (n_fns + fn_cols_tb - 1) // fn_cols_tb
        fn_total_h_tb = fn_rows_tb * BLOCK_H + max(0, fn_rows_tb - 1) * BLOCK_GAP
        infra_sect_tb = (BLOCK_GAP * 2 + infra_group_h_tb) if infra else 0
        app_h = max(float(FRAME_PAD * 2 + 20 + fn_total_h_tb + infra_sect_tb), 80.0)

        tb_conn_gap = 120.0   # visible arrow = (120 - CONN_UNIT_H) / 2 ≈ 41px each side

        canvas_w = int(dynamic_app_w + 2 * TB_MARGIN)
        canvas_h = int(TB_MARGIN + up_row_h + tb_conn_gap + app_h
                       + tb_conn_gap + dn_row_h + TB_MARGIN)

        app_x_tb    = float(TB_MARGIN)
        up_row_top  = float(TB_MARGIN)
        up_conn_top = up_row_top + up_row_h
        app_y_tb    = up_conn_top + tb_conn_gap
        dn_conn_top = app_y_tb + app_h
        dn_row_top  = dn_conn_top + tb_conn_gap

        up_slot_w = dynamic_app_w / n_up
        dn_slot_w = dynamic_app_w / n_dn

        # ── draw upstream groups (natural height, centred in row) ────────────
        for i, (name, members, mws) in enumerate(all_up):
            g_cx    = app_x_tb + (i + 0.5) * up_slot_w
            g_slug  = _slug(name)
            gh      = up_natural_ghs[i]
            is_bare = _is_bare_block(name, members)
            gy      = up_row_top + (up_row_h - gh) / 2.0

            if is_bare:
                bh = float(BLOCK_H)
                bw = min(_tb_min_gw(name, members) + 20.0, up_slot_w - 8)
                builder.add_solid_block(
                    members[0],
                    Rect(g_cx - bw / 2.0, gy + (gh - bh) / 2.0, bw, bh),
                    cell_id=f"up_block_{g_slug}",
                )
            else:
                gw = min(_tb_min_gw(name, members), up_slot_w - 8)
                gx = g_cx - gw / 2.0
                builder.add_frame(
                    name, Rect(gx, gy, gw, gh),
                    cell_id=f"up_frame_{g_slug}",
                    dashed=True, stroke_width=2,
                )
                iy = gy + FRAME_PAD + 20
                bw = gw - FRAME_PAD * 2
                for m in members:
                    builder.add_solid_block(
                        m, Rect(gx + FRAME_PAD, iy, bw, BLOCK_H),
                        cell_id=f"up_block_{g_slug}_{_slug(m)}",
                    )
                    iy += BLOCK_H + BLOCK_GAP

            n_mws = len(mws)
            cu_total_w = n_mws * CONN_BOX_W + max(0, n_mws - 1) * TB_CU_H_SPACING
            cu_start_x = g_cx - cu_total_w / 2.0 + CONN_BOX_W / 2.0
            for j, mw in enumerate(mws):
                cu_x = cu_start_x + j * (CONN_BOX_W + TB_CU_H_SPACING)
                builder.add_tb_connection_unit(
                    abs_x=cu_x, y_top=up_conn_top, y_bot=app_y_tb,
                    component_name=mw,
                    svg_content=_find_svg(mw, component_svgs),
                    builtin_spec=_resolve_spec(mw),
                    base_id=f"cu_up_{g_slug}_{_slug(mw)}",
                )

        # ── draw app frame (compact, top-aligned, 2-col fn grid) ────────────
        builder.add_frame(
            knowledge.app_name or "Application",
            Rect(app_x_tb, app_y_tb, dynamic_app_w, app_h),
            cell_id=f"app_frame_{app_slug}",
        )
        inner_w    = dynamic_app_w - FRAME_PAD * 2
        fn_col_w   = (inner_w - (fn_cols_tb - 1) * BLOCK_GAP) / fn_cols_tb
        fn_y_start = app_y_tb + FRAME_PAD + 20
        for fi, fn in enumerate(knowledge.business_functions):
            col_i = fi % fn_cols_tb
            row_i = fi // fn_cols_tb
            fx = app_x_tb + FRAME_PAD + col_i * (fn_col_w + BLOCK_GAP)
            fy = fn_y_start + row_i * (BLOCK_H + BLOCK_GAP)
            builder.add_solid_block(
                fn.name,
                Rect(fx, fy, fn_col_w, BLOCK_H),
                cell_id=f"app_fn_{_slug(fn.name)}",
            )
        fn_y = fn_y_start + fn_rows_tb * BLOCK_H + max(0, fn_rows_tb - 1) * BLOCK_GAP
        if infra:
            i_cols   = INFRA_COLS
            igrid_w  = i_cols * INFRA_ITEM_W + (i_cols - 1) * INFRA_H_GAP
            igroup_w = igrid_w + INFRA_GROUP_PAD * 2
            igx      = app_x_tb + (dynamic_app_w - igroup_w) / 2.0
            igy      = fn_y + BLOCK_GAP
            builder.add_frame(
                "Infrastructure",
                Rect(igx, igy, igroup_w, infra_group_h_tb),
                cell_id=f"infra_group_{app_slug}",
                dashed=True, stroke_width=2, font_size=10, bold=False,
            )
            for idx, mc in enumerate(infra):
                c_i = idx % i_cols
                r_i = idx // i_cols
                ix  = igx + INFRA_GROUP_PAD + c_i * (INFRA_ITEM_W + INFRA_H_GAP)
                iy  = igy + INFRA_GROUP_LABEL_H + INFRA_GROUP_PAD + r_i * (INFRA_ITEM_H + INFRA_V_GAP)
                builder.add_infra_icon(
                    mc.name, Rect(ix, iy, INFRA_ITEM_W, INFRA_ITEM_H),
                    svg_content=_find_svg(mc.name, component_svgs),
                    builtin_spec=_resolve_spec(mc.name),
                    base_id=f"infra_{_slug(mc.name)}",
                )

        # ── draw downstream groups (natural height, centred in row) ──────────
        for i, (name, members, mws) in enumerate(all_dn):
            g_cx    = app_x_tb + (i + 0.5) * dn_slot_w
            g_slug  = _slug(name)
            gh      = dn_natural_ghs[i]
            is_bare = _is_bare_block(name, members)
            gy      = dn_row_top + (dn_row_h - gh) / 2.0

            if is_bare:
                bh = float(BLOCK_H)
                bw = min(_tb_min_gw(name, members) + 20.0, dn_slot_w - 8)
                builder.add_solid_block(
                    members[0],
                    Rect(g_cx - bw / 2.0, gy + (gh - bh) / 2.0, bw, bh),
                    cell_id=f"dn_block_{g_slug}",
                )
            else:
                gw = min(_tb_min_gw(name, members), dn_slot_w - 8)
                gx = g_cx - gw / 2.0
                builder.add_frame(
                    name, Rect(gx, gy, gw, gh),
                    cell_id=f"dn_frame_{g_slug}",
                    dashed=True, stroke_width=2,
                )
                iy = gy + FRAME_PAD + 20
                bw = gw - FRAME_PAD * 2
                for m in members:
                    builder.add_solid_block(
                        m, Rect(gx + FRAME_PAD, iy, bw, BLOCK_H),
                        cell_id=f"dn_block_{g_slug}_{_slug(m)}",
                    )
                    iy += BLOCK_H + BLOCK_GAP

            n_mws = len(mws)
            cu_total_w = n_mws * CONN_BOX_W + max(0, n_mws - 1) * TB_CU_H_SPACING
            cu_start_x = g_cx - cu_total_w / 2.0 + CONN_BOX_W / 2.0
            for j, mw in enumerate(mws):
                cu_x = cu_start_x + j * (CONN_BOX_W + TB_CU_H_SPACING)
                builder.add_tb_connection_unit(
                    abs_x=cu_x, y_top=dn_conn_top, y_bot=dn_row_top,
                    component_name=mw,
                    svg_content=_find_svg(mw, component_svgs),
                    builtin_spec=_resolve_spec(mw),
                    base_id=f"cu_dn_{g_slug}_{_slug(mw)}",
                )

    xml = builder.build(canvas_w=canvas_w, canvas_h=canvas_h)
    svg = _make_svg_wrapper(xml, canvas_w, canvas_h)
    return DrawIOOutput(xml=xml, svg=svg, canvas_w=canvas_w, canvas_h=canvas_h)
