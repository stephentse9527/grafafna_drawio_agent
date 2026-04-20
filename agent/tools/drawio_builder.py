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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from agent.state import AppKnowledge, ColorScheme


# ---------------------------------------------------------------------------
# Layout constants — derived from the provided .drawio component files
# ---------------------------------------------------------------------------

BLOCK_W   = 120   # solid block width
BLOCK_H   = 36    # solid block height
BLOCK_GAP = 10    # vertical gap between blocks inside a frame
FRAME_PAD = 16    # padding around blocks inside a frame
GROUP_GAP = 24    # vertical gap between groups in the same column
APP_FRAME_W = 260 # app frame width

# Connection unit dimensions (from solace.drawio / fileit.drawio etc.)
CONN_UNIT_W = 291  # total group width (left tail + icon box + right tail)
CONN_UNIT_H = 38   # group height
CONN_BOX_X  = 99   # icon box left offset within the group (local coords)
CONN_BOX_W  = 89   # icon box width
CONN_ICON_X = 109  # icon left offset (local)
CONN_ICON_Y = 6    # icon top offset (local)
CONN_ICON_W = 66   # icon width
CONN_ICON_H = 26   # icon height
CONN_LABEL_X = 122 # text label left offset (local, for built-in shapes)
CONN_LABEL_W = 72  # text label width

# Column X positions
UPSTREAM_COL_X   = 20
UP_FRAME_W       = BLOCK_W + FRAME_PAD * 2     # 152
CONN_LEFT_X      = UPSTREAM_COL_X + UP_FRAME_W # 172  ← connection units, left side
APP_COL_X        = CONN_LEFT_X + CONN_UNIT_W   # 463  ← app frame left edge
CONN_RIGHT_X     = APP_COL_X + APP_FRAME_W     # 723  ← connection units, right side
DOWNSTREAM_COL_X = CONN_RIGHT_X + CONN_UNIT_W  # 1014 ← downstream frames

# ---------------------------------------------------------------------------
# Built-in middleware component specs
# Extracted from the provided .drawio files in .github/agents/svgs/
# Solace is NOT listed here — its icon comes from solace.svg (loaded at runtime)
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
    """Case-insensitive lookup in the component_svgs dict."""
    key = name.lower().strip()
    for k, v in component_svgs.items():
        if k.lower().strip() == key:
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
        parent: str = "1",
    ) -> str:
        cell_id = cell_id or self._new_id()
        s  = stroke     or self.cs.frame_stroke
        fc = font_color or self.cs.text_color_on_frame
        dash   = "dashed=1;" if dashed else "dashed=0;"
        valign = "verticalAlign=top;" if label_position == "top" else "verticalAlign=middle;"
        style = (
            f"rounded=0;whiteSpace=wrap;html=1;"
            f"fillColor=none;strokeColor={s};"
            f"fontColor={fc};fontSize={font_size};"
            f"fontStyle={'1' if bold else '0'};"
            f"{valign}{dash}"
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
    ) -> str:
        """
        Add a CONNECTION UNIT group at absolute position (abs_x, abs_y).

        The group spans CONN_UNIT_W wide and CONN_UNIT_H tall and contains:
          1. A fixed-geometry arrow (mxPoint, NO cell refs) — the only safe way
             to draw a straight line that will never fold, bend, or detach.
          2. A rounded-rect icon box in the centre of the group.
          3. The icon (SVG image or built-in DrawIO shape).
          4. An optional text label for built-in shapes that need one.

        Positioning rule (caller's responsibility):
          abs_x = right edge of the upstream/downstream frame
          abs_x + CONN_UNIT_W = left edge of the APP frame (or downstream frame)
          abs_y = group_center_y - CONN_UNIT_H / 2
        """
        group_id = self._new_id()
        arrow_id = self._new_id()
        box_id   = self._new_id()
        mid_y    = CONN_UNIT_H / 2
        stroke   = self.cs.healthy_stroke

        # 1. Group container (connectable=0 so it can't be wired by accident)
        self._cells.append(dict(
            id=group_id, value="", style="group",
            vertex="1", connectable="0", parent="1",
            x=abs_x, y=abs_y, w=CONN_UNIT_W, h=CONN_UNIT_H,
        ))

        # 2. Fixed-geometry arrow — sourcePoint/targetPoint are LOCAL coords
        #    inside the group. No source= or target= cell references.
        #    edgeStyle=none prevents ANY routing. This eliminates every
        #    possible cause of arrows flying, folding, or connecting to wrong cells.
        self._cells.append(dict(
            id=arrow_id, value="",
            style=(
                f"edgeStyle=none;html=1;"
                f"exitX=1;exitY=0.5;exitDx=0;exitDy=0;"
                f"entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
                f"strokeColor={stroke};strokeWidth=2;"
                f"startArrow=block;startFill=1;"
            ),
            edge="1", parent=group_id,
            source_point=(0, mid_y),
            target_point=(CONN_UNIT_W, mid_y),
        ))

        # 3. Icon box (rounded rect in centre of group)
        self._cells.append(dict(
            id=box_id, value="",
            style=(
                f"rounded=1;whiteSpace=wrap;html=1;"
                f"strokeColor={stroke};strokeWidth=2;fillColor=#111217;"
            ),
            vertex="1", parent=group_id,
            x=CONN_BOX_X, y=0, w=CONN_BOX_W, h=CONN_UNIT_H,
        ))

        # 4. Icon — SVG file takes priority over built-in shape
        if svg_content:
            b64 = base64.b64encode(svg_content.encode("utf-8")).decode("ascii")
            self._cells.append(dict(
                id=self._new_id(), value="",
                style=(
                    f"shape=image;verticalLabelPosition=bottom;"
                    f"labelBackgroundColor=default;verticalAlign=top;"
                    f"aspect=fixed;imageAspect=0;"
                    f"image=data:image/svg+xml,{b64};"
                ),
                vertex="1", parent=group_id,
                x=CONN_ICON_X, y=CONN_ICON_Y, w=CONN_ICON_W, h=CONN_ICON_H,
            ))
        elif builtin_spec:
            self._cells.append(dict(
                id=self._new_id(), value="",
                style=builtin_spec["icon_style"],
                vertex="1", parent=group_id,
                x=builtin_spec["icon_x"], y=builtin_spec["icon_y"],
                w=builtin_spec["icon_w"], h=builtin_spec["icon_h"],
            ))
            if builtin_spec.get("label"):
                self._cells.append(dict(
                    id=self._new_id(),
                    value=html.escape(builtin_spec["label"]),
                    style=(
                        "text;html=1;align=center;verticalAlign=middle;"
                        "whiteSpace=wrap;rounded=0;fillColor=none;"
                        "fontColor=#FFFFFF;fontSize=14;fontFamily=Times New Roman;"
                    ),
                    vertex="1", parent=group_id,
                    x=CONN_LABEL_X, y=0, w=CONN_LABEL_W, h=CONN_UNIT_H,
                ))
        else:
            # Fallback text-only (agent should have blocked before reaching here)
            self._cells.append(dict(
                id=self._new_id(),
                value=html.escape(component_name),
                style=(
                    "text;html=1;align=center;verticalAlign=middle;"
                    "whiteSpace=wrap;rounded=0;fillColor=none;fontColor=#FFFFFF;fontSize=12;"
                ),
                vertex="1", parent=group_id,
                x=CONN_BOX_X, y=0, w=CONN_BOX_W, h=CONN_UNIT_H,
            ))

        return group_id

    # ------------------------------------------------------------------ infra icon (inside app frame, no arrow)

    def add_infra_icon(
        self,
        name: str,
        rect: Rect,
        svg_content: Optional[str] = None,
        builtin_spec: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Render an infrastructure component (Oracle, NAS, etc.) inside the APP frame."""
        # Box
        self._cells.append(dict(
            id=self._new_id(), value="",
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
                id=self._new_id(), value="",
                style=(
                    f"shape=image;verticalLabelPosition=bottom;verticalAlign=top;"
                    f"aspect=fixed;imageAspect=0;image=data:image/svg+xml,{b64};"
                ),
                vertex="1", parent="1",
                x=ix, y=iy, w=icon_w, h=icon_h,
            ))
        elif builtin_spec:
            self._cells.append(dict(
                id=self._new_id(), value="",
                style=builtin_spec["icon_style"],
                vertex="1", parent="1",
                x=ix, y=iy, w=builtin_spec["icon_w"], h=builtin_spec["icon_h"],
            ))

        self._cells.append(dict(
            id=self._new_id(),
            value=html.escape(name),
            style=(
                "text;html=1;align=left;verticalAlign=middle;"
                "whiteSpace=wrap;rounded=0;fillColor=none;"
                f"fontColor={self.cs.text_color_on_frame};fontSize=10;"
            ),
            vertex="1", parent="1",
            x=ix + icon_w + 4, y=rect.y, w=rect.w - icon_w - 8, h=rect.h,
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

        return ET.tostring(root, encoding="unicode", xml_declaration=False)


# ---------------------------------------------------------------------------
# High-level layout composer
# ---------------------------------------------------------------------------

def compose_flow_diagram(
    knowledge: AppKnowledge,
    color_scheme: ColorScheme,
    component_svgs: Dict[str, str],
) -> str:
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

    # ---- helper: group frame height ----
    def frame_h(n_members: int) -> float:
        return FRAME_PAD * 2 + 20 + n_members * BLOCK_H + max(0, n_members - 1) * BLOCK_GAP

    # ---- collect upstream groups (handle ungrouped upstreams as singletons) ----
    grouped_up = {m for members in knowledge.upstream_groups.values() for m in members}
    all_up: List[Tuple[str, List[str]]] = list(knowledge.upstream_groups.items())
    for u in knowledge.upstreams:
        if u.name not in grouped_up:
            all_up.append((u.name, [u.name]))

    # ---- collect downstream groups ----
    grouped_dn = {m for members in knowledge.downstream_groups.values() for m in members}
    all_dn: List[Tuple[str, List[str]]] = list(knowledge.downstream_groups.items())
    for d in knowledge.downstreams:
        if d.name not in grouped_dn:
            all_dn.append((d.name, [d.name]))

    # ---- middleware lookup helpers ----
    def up_mw(members: List[str]) -> str:
        for m in members:
            for u in knowledge.upstreams:
                if u.name == m:
                    return u.connection_middleware
        return "Solace"

    def dn_mw(members: List[str]) -> str:
        for m in members:
            for d in knowledge.downstreams:
                if d.name == m:
                    return d.connection_middleware
        return "Solace"

    # ---- compute column heights ----
    def col_h(groups: List[Tuple[str, List[str]]]) -> float:
        return sum(frame_h(len(m)) for _, m in groups) + max(0, len(groups) - 1) * GROUP_GAP

    n_fns = len(knowledge.business_functions)
    infra = [mc for mc in knowledge.middleware_components
             if mc.component_type in ("database", "cache", "file_transfer", "secret")]
    app_inner = (
        20 + n_fns * BLOCK_H + max(0, n_fns - 1) * BLOCK_GAP
        + (BLOCK_GAP + len(infra) * (BLOCK_H + BLOCK_GAP) if infra else 0)
    )
    app_h = max(app_inner + FRAME_PAD * 2, col_h(all_up), col_h(all_dn))

    canvas_h = int(app_h + TOP + 80)
    canvas_w = int(DOWNSTREAM_COL_X + UP_FRAME_W + 60)

    # ---- draw upstream column ----
    up_y = TOP
    for name, members in all_up:
        gh = frame_h(len(members))
        if len(members) == 1 and name == members[0]:
            # singleton: just a block, no outer frame
            builder.add_solid_block(members[0], Rect(UPSTREAM_COL_X, up_y, BLOCK_W, BLOCK_H))
        else:
            builder.add_frame(name, Rect(UPSTREAM_COL_X, up_y, UP_FRAME_W, gh))
            iy = up_y + FRAME_PAD + 20
            for m in members:
                builder.add_solid_block(m, Rect(UPSTREAM_COL_X + FRAME_PAD, iy, BLOCK_W, BLOCK_H))
                iy += BLOCK_H + BLOCK_GAP
        up_y += gh + GROUP_GAP

    # ---- draw left connection units ----
    up_y = TOP
    for name, members in all_up:
        gh = frame_h(len(members))
        center_y = up_y + gh / 2
        mw = up_mw(members)
        builder.add_connection_unit(
            abs_x=CONN_LEFT_X,
            abs_y=center_y - CONN_UNIT_H / 2,
            component_name=mw,
            svg_content=_find_svg(mw, component_svgs),
            builtin_spec=_resolve_spec(mw),
        )
        up_y += gh + GROUP_GAP

    # ---- draw app frame ----
    builder.add_frame(knowledge.app_name or "Application", Rect(APP_COL_X, TOP, APP_FRAME_W, app_h))
    fn_y = TOP + FRAME_PAD + 20
    for fn in knowledge.business_functions:
        builder.add_solid_block(fn.name, Rect(APP_COL_X + FRAME_PAD, fn_y, BLOCK_W, BLOCK_H))
        fn_y += BLOCK_H + BLOCK_GAP
    infra_y = fn_y + BLOCK_GAP
    for mc in infra:
        builder.add_infra_icon(
            mc.name,
            Rect(APP_COL_X + FRAME_PAD, infra_y, CONN_BOX_W, BLOCK_H),
            svg_content=_find_svg(mc.name, component_svgs),
            builtin_spec=_resolve_spec(mc.name),
        )
        infra_y += BLOCK_H + BLOCK_GAP

    # ---- draw right connection units ----
    dn_y = TOP
    for name, members in all_dn:
        gh = frame_h(len(members))
        center_y = dn_y + gh / 2
        mw = dn_mw(members)
        builder.add_connection_unit(
            abs_x=CONN_RIGHT_X,
            abs_y=center_y - CONN_UNIT_H / 2,
            component_name=mw,
            svg_content=_find_svg(mw, component_svgs),
            builtin_spec=_resolve_spec(mw),
        )
        dn_y += gh + GROUP_GAP

    # ---- draw downstream column ----
    dn_y = TOP
    for name, members in all_dn:
        gh = frame_h(len(members))
        if len(members) == 1 and name == members[0]:
            builder.add_solid_block(members[0], Rect(DOWNSTREAM_COL_X, dn_y, BLOCK_W, BLOCK_H))
        else:
            builder.add_frame(name, Rect(DOWNSTREAM_COL_X, dn_y, UP_FRAME_W, gh))
            iy = dn_y + FRAME_PAD + 20
            for m in members:
                builder.add_solid_block(m, Rect(DOWNSTREAM_COL_X + FRAME_PAD, iy, BLOCK_W, BLOCK_H))
                iy += BLOCK_H + BLOCK_GAP
        dn_y += gh + GROUP_GAP

    return builder.build(canvas_w=canvas_w, canvas_h=canvas_h)
