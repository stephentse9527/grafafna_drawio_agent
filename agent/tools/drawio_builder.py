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
        box_y   = y_top + TB_CONN_BOX_Y

        # 1. Vertical fixed-geometry arrow — placed FIRST (behind icon box)
        self._cells.append(dict(
            id=_cid("arrow"), value="",
            style=(
                f"edgeStyle=none;html=1;"
                f"exitX=0.5;exitY=1;exitDx=0;exitDy=0;"
                f"entryX=0.5;entryY=0;entryDx=0;entryDy=0;"
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

    One connection unit per upstream/downstream GROUP, positioned so:
      left_edge  = upstream frame right edge
      right_edge = app frame left edge (or downstream frame left edge)
      center_y   = center of the group it connects

    Fixed-geometry arrows inside each connection unit prevent all routing issues.
    """
    builder = DrawIOBuilder(color_scheme)
    TOP = 40.0  # top margin
    CONN_UNIT_GAP = 8  # vertical gap between stacked connection units

    # ---- collect upstream groups (handle ungrouped upstreams as singletons) ----
    grouped_up = {m for members in knowledge.upstream_groups.values() for m in members}
    all_up_raw: List[Tuple[str, List[str]]] = list(knowledge.upstream_groups.items())
    for u in knowledge.upstreams:
        if u.name not in grouped_up:
            all_up_raw.append((u.name, [u.name]))

    # ---- collect downstream groups ----
    grouped_dn = {m for members in knowledge.downstream_groups.values() for m in members}
    all_dn_raw: List[Tuple[str, List[str]]] = list(knowledge.downstream_groups.items())
    for d in knowledge.downstreams:
        if d.name not in grouped_dn:
            all_dn_raw.append((d.name, [d.name]))

    # ---- gather ALL unique middlewares per group (ordered, deduplicated) ----
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

    # Augment groups with their middleware lists
    # (group_name, members, [mw1, mw2, ...])
    all_up = [(n, m, up_mws(m)) for n, m in all_up_raw]
    all_dn = [(n, m, dn_mws(m)) for n, m in all_dn_raw]

    # ---- helper: frame height accounts for both blocks AND stacked conn units ----
    def frame_h(n_members: int, n_mws: int) -> float:
        block_h = FRAME_PAD * 2 + 20 + n_members * BLOCK_H + max(0, n_members - 1) * BLOCK_GAP
        conn_h  = n_mws * CONN_UNIT_H + max(0, n_mws - 1) * CONN_UNIT_GAP + FRAME_PAD * 2
        return max(block_h, conn_h)

    def col_h(groups: List[Tuple[str, List[str], List[str]]]) -> float:
        return (sum(frame_h(len(m), len(mws)) for _, m, mws in groups)
                + max(0, len(groups) - 1) * GROUP_GAP)

    # ---- infra = internal infra components only (NOT connection middleware) ----
    conn_mw_names = {u.connection_middleware.lower() for u in knowledge.upstreams}
    conn_mw_names |= {d.connection_middleware.lower() for d in knowledge.downstreams}
    infra = [
        mc for mc in knowledge.middleware_components
        if mc.component_type in ("database", "cache", "file_transfer", "secret")
        and mc.name.lower() not in conn_mw_names
    ]

    n_fns = len(knowledge.business_functions)
    # Compute how much infra group box adds to the app frame height
    if infra:
        _icols = INFRA_COLS
        _irows = (len(infra) + _icols - 1) // _icols
        _igrid_h = _irows * INFRA_ITEM_H + max(0, _irows - 1) * INFRA_V_GAP
        _igroup_h = _igrid_h + INFRA_GROUP_PAD * 2 + INFRA_GROUP_LABEL_H
        infra_section_h = BLOCK_GAP + _igroup_h
    else:
        infra_section_h = 0
    app_inner = (
        20 + n_fns * BLOCK_H + max(0, n_fns - 1) * BLOCK_GAP
        + infra_section_h
    )
    app_h = max(app_inner + FRAME_PAD * 2, col_h(all_up), col_h(all_dn))

    app_slug = _slug(knowledge.app_name or "application")

    # ---- auto-select layout direction (LR vs TB) ----
    # LR (left-to-right): upstream | conn-units | APP | conn-units | downstream
    # TB (top-to-bottom): upstream row ↓ conn-arrows ↓ APP ↓ conn-arrows ↓ downstream row
    #
    # Switch to TB when:
    #   (a) either side has > LR_MAX_GROUPS groups — LR column becomes too tall, OR
    #   (b) LR canvas width:height would exceed LR_ASPECT_LIMIT — diagram too wide
    #       for the Z5-MAIN panel (≈ 5:3 aspect) without excessive letterboxing
    _ltr_w = DOWNSTREAM_COL_X + UP_FRAME_W + 60
    _ltr_h = TOP + app_h + 80
    _ltr_aspect = _ltr_w / max(_ltr_h, 1.0)
    use_tb = (_ltr_aspect > LR_ASPECT_LIMIT) or (max(len(all_up), len(all_dn)) > LR_MAX_GROUPS)

    # ---- shared helper: draw app-frame contents at a given (ax, ay) origin ----
    def _draw_app_contents(ax: float, ay: float) -> None:
        app_block_w = APP_FRAME_W - FRAME_PAD * 2
        total_fn_h = n_fns * BLOCK_H + max(0, n_fns - 1) * BLOCK_GAP
        fn_y = ay + max(FRAME_PAD + 20, (app_h - total_fn_h) / 2)
        for fn in knowledge.business_functions:
            builder.add_solid_block(
                fn.name, Rect(ax + FRAME_PAD, fn_y, app_block_w, BLOCK_H),
                cell_id=f"app_fn_{_slug(fn.name)}",
            )
            fn_y += BLOCK_H + BLOCK_GAP
        if infra:
            cols = INFRA_COLS
            rows = (len(infra) + cols - 1) // cols
            grid_w = cols * INFRA_ITEM_W + (cols - 1) * INFRA_H_GAP
            grid_h = rows * INFRA_ITEM_H + (rows - 1) * INFRA_V_GAP
            group_w = grid_w + INFRA_GROUP_PAD * 2
            group_h = grid_h + INFRA_GROUP_PAD * 2 + INFRA_GROUP_LABEL_H
            infra_group_x = ax + (APP_FRAME_W - group_w) / 2
            infra_group_y = fn_y + BLOCK_GAP
            builder.add_frame(
                "Infrastructure",
                Rect(infra_group_x, infra_group_y, group_w, group_h),
                cell_id=f"infra_group_{app_slug}",
                dashed=True, stroke_width=2, font_size=10, bold=False,
            )
            for idx, mc in enumerate(infra):
                col_i = idx % cols
                row_i = idx // cols
                ix = infra_group_x + INFRA_GROUP_PAD + col_i * (INFRA_ITEM_W + INFRA_H_GAP)
                iy = (infra_group_y + INFRA_GROUP_LABEL_H + INFRA_GROUP_PAD
                      + row_i * (INFRA_ITEM_H + INFRA_V_GAP))
                builder.add_infra_icon(
                    mc.name, Rect(ix, iy, INFRA_ITEM_W, INFRA_ITEM_H),
                    svg_content=_find_svg(mc.name, component_svgs),
                    builtin_spec=_resolve_spec(mc.name),
                    base_id=f"infra_{_slug(mc.name)}",
                )

    if use_tb:
        # ============================================================
        # TB layout: upstream row ↓ TB_CONN_UNIT ↓ APP ↓ TB_CONN_UNIT ↓ downstream row
        #
        # Connection zones use the SAME icon-box unit as LR, oriented vertically
        # (from how_connection_with_midleware_TB.drawio):
        #   - 148px tall vertical span
        #   - Same 89×38 rounded box, same icon/label as LR
        #   - Arrow passes through box (z-order: arrow first, box on top)
        #   - Multiple middlewares per group: side-by-side horizontally
        #
        # Adaptive horizontal fill:
        #   Both upstream and downstream rows are stretched to fill canvas_w
        #   by increasing inter-group gaps proportionally.
        # ============================================================

        def _tb_gw(members: List[str]) -> float:
            return float(UP_FRAME_W if len(members) > 1 else BLOCK_W)

        def _tb_gh(n: int) -> float:
            if n <= 1:
                return float(BLOCK_H)
            return float(FRAME_PAD * 2 + 20 + n * BLOCK_H + max(0, n - 1) * BLOCK_GAP)

        up_gws = [_tb_gw(m) for _, m, _ in all_up]
        dn_gws = [_tb_gw(m) for _, m, _ in all_dn]
        up_ghs = [_tb_gh(len(m)) for _, m, _ in all_up]
        dn_ghs = [_tb_gh(len(m)) for _, m, _ in all_dn]

        max_up_h = max(up_ghs) if up_ghs else float(BLOCK_H)
        max_dn_h = max(dn_ghs) if dn_ghs else float(BLOCK_H)

        up_row_w_natural = sum(up_gws) + max(0, len(all_up) - 1) * TB_H_GAP
        dn_row_w_natural = sum(dn_gws) + max(0, len(all_dn) - 1) * TB_H_GAP

        content_w = max(up_row_w_natural, float(APP_FRAME_W), dn_row_w_natural) + 2 * TB_MARGIN
        canvas_h = int(TB_MARGIN + max_up_h + TB_CONN_UNIT_H + app_h + TB_CONN_UNIT_H + max_dn_h + TB_MARGIN)
        canvas_w = max(int(content_w), int(canvas_h * Z5_PANEL_ASPECT))

        # ---- adaptive horizontal gap: stretch each row to fill canvas_w ----
        avail_w = float(canvas_w) - 2 * TB_MARGIN

        def _row_gap(gws: List[float]) -> float:
            n = len(gws)
            if n <= 1:
                return TB_H_GAP
            natural = sum(gws)
            gap = (avail_w - natural) / (n - 1)
            return max(TB_H_GAP, gap)

        up_gap = _row_gap(up_gws)
        dn_gap = _row_gap(dn_gws)

        # ---- starting x for each row (centred or left-aligned when gap>=TB_H_GAP) ----
        def _row_start_x(gws: List[float], gap: float) -> float:
            if not gws:
                return float(TB_MARGIN)
            total = sum(gws) + max(0, len(gws) - 1) * gap
            if len(gws) == 1:
                return (canvas_w - gws[0]) / 2.0
            return float(TB_MARGIN)

        app_x_tb = (canvas_w - APP_FRAME_W) / 2.0
        app_y_tb = TB_MARGIN + max_up_h + TB_CONN_UNIT_H
        n_up = max(len(all_up), 1)
        n_dn = max(len(all_dn), 1)

        # ---- draw upstream groups + TB connection units ----
        up_x = _row_start_x(up_gws, up_gap)
        for i, (name, members, mws) in enumerate(all_up):
            gw = up_gws[i]
            gh = up_ghs[i]
            gy = TB_MARGIN + (max_up_h - gh) / 2.0
            g_slug = _slug(name)
            if len(members) == 1 and name == members[0]:
                builder.add_solid_block(
                    members[0],
                    Rect(up_x + (gw - BLOCK_W) / 2, gy + (gh - BLOCK_H) / 2, BLOCK_W, BLOCK_H),
                    cell_id=f"up_block_{g_slug}",
                )
            else:
                builder.add_frame(
                    name, Rect(up_x, gy, gw, gh),
                    cell_id=f"up_frame_{g_slug}",
                    dashed=True, stroke_width=2,
                )
                iy = gy + FRAME_PAD + 20
                for m in members:
                    builder.add_solid_block(
                        m, Rect(up_x + FRAME_PAD, iy, BLOCK_W, BLOCK_H),
                        cell_id=f"up_block_{g_slug}_{_slug(m)}",
                    )
                    iy += BLOCK_H + BLOCK_GAP

            # TB connection units: one per middleware, side-by-side horizontally
            # Centred around the proportional entry point on the APP frame top edge
            app_entry_x = app_x_tb + (i + 0.5) / n_up * APP_FRAME_W
            n_mws = len(mws)
            total_cu_w = n_mws * CONN_BOX_W + max(0, n_mws - 1) * TB_CU_H_SPACING
            cu_start_x = app_entry_x - total_cu_w / 2.0 + CONN_BOX_W / 2.0
            for j, mw in enumerate(mws):
                cu_cx = cu_start_x + j * (CONN_BOX_W + TB_CU_H_SPACING)
                builder.add_tb_connection_unit(
                    abs_x=cu_cx,
                    y_top=TB_MARGIN + max_up_h,
                    y_bot=app_y_tb,
                    component_name=mw,
                    svg_content=_find_svg(mw, component_svgs),
                    builtin_spec=_resolve_spec(mw),
                    base_id=f"cu_up_{g_slug}_{_slug(mw)}",
                )
            up_x += gw + up_gap

        # ---- draw app frame ----
        builder.add_frame(
            knowledge.app_name or "Application",
            Rect(app_x_tb, app_y_tb, APP_FRAME_W, app_h),
            cell_id=f"app_frame_{app_slug}",
        )
        _draw_app_contents(app_x_tb, app_y_tb)

        # ---- draw downstream groups + TB connection units ----
        dn_conn_y_top = app_y_tb + app_h          # arrow source = app frame bottom
        dn_row_y      = dn_conn_y_top + TB_CONN_UNIT_H  # downstream group top
        dn_x = _row_start_x(dn_gws, dn_gap)
        for i, (name, members, mws) in enumerate(all_dn):
            gw = dn_gws[i]
            gh = dn_ghs[i]
            gy = dn_row_y + (max_dn_h - gh) / 2.0
            g_slug = _slug(name)
            if len(members) == 1 and name == members[0]:
                builder.add_solid_block(
                    members[0],
                    Rect(dn_x + (gw - BLOCK_W) / 2, gy + (gh - BLOCK_H) / 2, BLOCK_W, BLOCK_H),
                    cell_id=f"dn_block_{g_slug}",
                )
            else:
                builder.add_frame(
                    name, Rect(dn_x, gy, gw, gh),
                    cell_id=f"dn_frame_{g_slug}",
                    dashed=True, stroke_width=2,
                )
                iy = gy + FRAME_PAD + 20
                for m in members:
                    builder.add_solid_block(
                        m, Rect(dn_x + FRAME_PAD, iy, BLOCK_W, BLOCK_H),
                        cell_id=f"dn_block_{g_slug}_{_slug(m)}",
                    )
                    iy += BLOCK_H + BLOCK_GAP

            # TB connection units from APP bottom → downstream group top
            app_exit_x = app_x_tb + (i + 0.5) / n_dn * APP_FRAME_W
            n_mws = len(mws)
            total_cu_w = n_mws * CONN_BOX_W + max(0, n_mws - 1) * TB_CU_H_SPACING
            cu_start_x = app_exit_x - total_cu_w / 2.0 + CONN_BOX_W / 2.0
            for j, mw in enumerate(mws):
                cu_cx = cu_start_x + j * (CONN_BOX_W + TB_CU_H_SPACING)
                builder.add_tb_connection_unit(
                    abs_x=cu_cx,
                    y_top=dn_conn_y_top,
                    y_bot=dn_row_y,
                    component_name=mw,
                    svg_content=_find_svg(mw, component_svgs),
                    builtin_spec=_resolve_spec(mw),
                    base_id=f"cu_dn_{g_slug}_{_slug(mw)}",
                )
            dn_x += gw + dn_gap

    else:
        # ============================================================
        # LR layout (left-to-right): the original column arrangement.
        #
        # Adaptive vertical fill:
        #   Both upstream and downstream columns fill canvas height (= app_h)
        #   by increasing inter-group gaps proportionally. When one side has
        #   fewer groups, its gaps are larger so both columns are the same height.
        #   Single-group columns are vertically centred.
        # ============================================================
        canvas_w = int(_ltr_w)
        canvas_h = max(int(_ltr_h), int(canvas_w / Z5_PANEL_ASPECT))

        # ---- adaptive vertical gap helpers ----
        def _col_start_and_gap(groups: List[Tuple[str, List[str], List[str]]], target_h: float):
            """Returns (start_y, gap) so groups fill target_h from start_y."""
            n = len(groups)
            if n == 0:
                return TOP, GROUP_GAP
            natural_h = sum(frame_h(len(m), len(mws)) for _, m, mws in groups)
            if n == 1:
                return TOP + (target_h - natural_h) / 2.0, GROUP_GAP
            gap = (target_h - natural_h) / (n - 1)
            return TOP, max(GROUP_GAP, gap)

        up_start_y, up_gap = _col_start_and_gap(all_up, app_h)
        dn_start_y, dn_gap = _col_start_and_gap(all_dn, app_h)

        # ---- draw upstream column ----
        up_y = up_start_y
        for name, members, mws in all_up:
            gh = frame_h(len(members), len(mws))
            g_slug = _slug(name)
            if len(members) == 1 and name == members[0]:
                block_y = up_y + (gh - BLOCK_H) / 2  # centre within slot
                builder.add_solid_block(
                    members[0], Rect(UPSTREAM_COL_X, block_y, BLOCK_W, BLOCK_H),
                    cell_id=f"up_block_{g_slug}",
                )
            else:
                builder.add_frame(
                    name, Rect(UPSTREAM_COL_X, up_y, UP_FRAME_W, gh),
                    cell_id=f"up_frame_{g_slug}",
                    dashed=True, stroke_width=2,
                )
                iy = up_y + FRAME_PAD + 20
                for m in members:
                    builder.add_solid_block(
                        m, Rect(UPSTREAM_COL_X + FRAME_PAD, iy, BLOCK_W, BLOCK_H),
                        cell_id=f"up_block_{g_slug}_{_slug(m)}",
                    )
                    iy += BLOCK_H + BLOCK_GAP
            up_y += gh + up_gap

        # ---- draw left connection units (one per middleware, stacked) ----
        up_y = up_start_y
        for name, members, mws in all_up:
            gh = frame_h(len(members), len(mws))
            g_slug = _slug(name)
            total_conn_h = len(mws) * CONN_UNIT_H + max(0, len(mws) - 1) * CONN_UNIT_GAP
            conn_start_y = up_y + (gh - total_conn_h) / 2
            for i, mw in enumerate(mws):
                cu_y = conn_start_y + i * (CONN_UNIT_H + CONN_UNIT_GAP)
                builder.add_connection_unit(
                    abs_x=CONN_LEFT_X, abs_y=cu_y,
                    component_name=mw,
                    svg_content=_find_svg(mw, component_svgs),
                    builtin_spec=_resolve_spec(mw),
                    base_id=f"cu_up_{g_slug}_{_slug(mw)}",
                )
            up_y += gh + up_gap

        # ---- draw app frame ----
        builder.add_frame(
            knowledge.app_name or "Application",
            Rect(APP_COL_X, TOP, APP_FRAME_W, app_h),
            cell_id=f"app_frame_{app_slug}",
        )
        _draw_app_contents(APP_COL_X, TOP)

        # ---- draw right connection units (one per middleware, stacked) ----
        dn_y = dn_start_y
        for name, members, mws in all_dn:
            gh = frame_h(len(members), len(mws))
            g_slug = _slug(name)
            total_conn_h = len(mws) * CONN_UNIT_H + max(0, len(mws) - 1) * CONN_UNIT_GAP
            conn_start_y = dn_y + (gh - total_conn_h) / 2
            for i, mw in enumerate(mws):
                cu_y = conn_start_y + i * (CONN_UNIT_H + CONN_UNIT_GAP)
                builder.add_connection_unit(
                    abs_x=CONN_RIGHT_X, abs_y=cu_y,
                    component_name=mw,
                    svg_content=_find_svg(mw, component_svgs),
                    builtin_spec=_resolve_spec(mw),
                    base_id=f"cu_dn_{g_slug}_{_slug(mw)}",
                )
            dn_y += gh + dn_gap

        # ---- draw downstream column ----
        dn_y = dn_start_y
        for name, members, mws in all_dn:
            gh = frame_h(len(members), len(mws))
            g_slug = _slug(name)
            if len(members) == 1 and name == members[0]:
                block_y = dn_y + (gh - BLOCK_H) / 2  # centre within slot
                builder.add_solid_block(
                    members[0], Rect(DOWNSTREAM_COL_X, block_y, BLOCK_W, BLOCK_H),
                    cell_id=f"dn_block_{g_slug}",
                )
            else:
                builder.add_frame(
                    name, Rect(DOWNSTREAM_COL_X, dn_y, UP_FRAME_W, gh),
                    cell_id=f"dn_frame_{g_slug}",
                    dashed=True, stroke_width=2,
                )
                iy = dn_y + FRAME_PAD + 20
                for m in members:
                    builder.add_solid_block(
                        m, Rect(DOWNSTREAM_COL_X + FRAME_PAD, iy, BLOCK_W, BLOCK_H),
                        cell_id=f"dn_block_{g_slug}_{_slug(m)}",
                    )
                    iy += BLOCK_H + BLOCK_GAP
            dn_y += gh + dn_gap

    xml = builder.build(canvas_w=canvas_w, canvas_h=canvas_h)
    svg = _make_svg_wrapper(xml, canvas_w, canvas_h)
    return DrawIOOutput(xml=xml, svg=svg, canvas_w=canvas_w, canvas_h=canvas_h)
