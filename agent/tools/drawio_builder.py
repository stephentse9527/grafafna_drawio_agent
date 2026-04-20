"""
DrawIO XML builder.

Generates a valid DrawIO mxGraphModel XML string that can be embedded directly
in a Grafana FlowCharting / Flow panel as the diagram source.

Layout algorithm
----------------
  [UPSTREAM GROUPS]  →  [MIDDLEWARE-IN]  →  [APP FRAME]  →  [MIDDLEWARE-OUT]  →  [DOWNSTREAM GROUPS]

All positions are calculated dynamically based on the number of elements so
the diagram scales cleanly.
"""
from __future__ import annotations

import html
import textwrap
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET

from agent.state import AppKnowledge, ColorScheme


# ---------------------------------------------------------------------------
# Internal geometry helpers
# ---------------------------------------------------------------------------

BLOCK_W = 120       # solid block width
BLOCK_H = 36        # solid block height
BLOCK_GAP = 10      # vertical gap between blocks inside a group
FRAME_PAD = 16      # padding around blocks inside a frame
GROUP_GAP = 24      # vertical gap between groups in the same column
MW_W = 100          # middleware frame width
MW_H = 60           # middleware frame height
APP_FRAME_W = 260   # app frame width
COL_GAP = 60        # horizontal gap between columns

UPSTREAM_COL_X = 20
MW_LEFT_X = UPSTREAM_COL_X + BLOCK_W + COL_GAP          # ~200
APP_COL_X = MW_LEFT_X + MW_W + COL_GAP                   # ~360
MW_RIGHT_X = APP_COL_X + APP_FRAME_W + COL_GAP           # ~680
DOWNSTREAM_COL_X = MW_RIGHT_X + MW_W + COL_GAP           # ~840


@dataclass
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h


# ---------------------------------------------------------------------------
# Cell builder
# ---------------------------------------------------------------------------

class DrawIOBuilder:
    """
    Builds a DrawIO mxGraphModel XML document programmatically.

    Usage::

        builder = DrawIOBuilder(color_scheme)
        builder.add_solid_block("AuthService", rect, cell_id="c1")
        builder.add_frame("Channel A", rect, cell_id="f1")
        builder.add_arrow("c1", "mw1")
        xml_string = builder.build()
    """

    def __init__(self, color_scheme: ColorScheme):
        self.cs = color_scheme
        self._cells: List[Dict[str, Any]] = []
        self._next_id = 10   # 0 and 1 are reserved for root cells

    # ------------------------------------------------------------------ ids

    def _new_id(self) -> str:
        self._next_id += 1
        return f"cell_{self._next_id}"

    # ------------------------------------------------------------------ add helpers

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
    ) -> str:
        """Add a filled rectangle (upstream/downstream/business-function block)."""
        cell_id = cell_id or self._new_id()
        f = fill or self.cs.healthy_fill
        s = stroke or self.cs.healthy_stroke
        fc = font_color or self.cs.text_color_on_fill
        style = (
            f"rounded=0;whiteSpace=wrap;html=1;"
            f"fillColor={f};strokeColor={s};"
            f"fontColor={fc};fontSize={font_size};"
            f"fontStyle={'1' if bold else '0'};"
        )
        self._cells.append(
            dict(
                id=cell_id,
                value=html.escape(label),
                style=style,
                vertex="1",
                x=rect.x, y=rect.y, w=rect.w, h=rect.h,
            )
        )
        return cell_id

    def add_frame(
        self,
        label: str,
        rect: Rect,
        cell_id: Optional[str] = None,
        stroke: Optional[str] = None,
        font_color: Optional[str] = None,
        label_position: str = "top",   # "top" | "center"
        font_size: int = 12,
        bold: bool = True,
        dashed: bool = False,
    ) -> str:
        """Add an outline rectangle (group frame or middleware container)."""
        cell_id = cell_id or self._new_id()
        s = stroke or self.cs.frame_stroke
        fc = font_color or self.cs.text_color_on_frame
        dash = "dashed=1;" if dashed else "dashed=0;"
        valign = "verticalAlign=top;" if label_position == "top" else "verticalAlign=middle;"
        style = (
            f"rounded=0;whiteSpace=wrap;html=1;"
            f"fillColor=none;strokeColor={s};"
            f"fontColor={fc};fontSize={font_size};"
            f"fontStyle={'1' if bold else '0'};"
            f"{valign}{dash}"
        )
        self._cells.append(
            dict(
                id=cell_id,
                value=html.escape(label),
                style=style,
                vertex="1",
                x=rect.x, y=rect.y, w=rect.w, h=rect.h,
            )
        )
        return cell_id

    def add_middleware_frame(
        self,
        name: str,
        rect: Rect,
        embedded_svg: Optional[str] = None,
        cell_id: Optional[str] = None,
    ) -> str:
        """
        Add a middleware component frame.
        If an embedded SVG is provided, embed it as an image label so the icon
        is visible inside the frame.
        """
        cell_id = cell_id or self._new_id()
        s = self.cs.frame_stroke

        if embedded_svg:
            # Escape SVG content for embedding as a label image
            escaped = html.escape(embedded_svg)
            label = (
                f'<img src="data:image/svg+xml,{escaped}" '
                f'width="40" height="40" /><br/><b>{html.escape(name)}</b>'
            )
        else:
            label = f"<b>{html.escape(name)}</b>"

        style = (
            f"rounded=0;whiteSpace=wrap;html=1;"
            f"fillColor=none;strokeColor={s};"
            f"fontColor={self.cs.text_color_on_frame};fontSize=10;"
            f"verticalAlign=middle;align=center;"
        )
        self._cells.append(
            dict(
                id=cell_id,
                value=label,
                style=style,
                vertex="1",
                x=rect.x, y=rect.y, w=rect.w, h=rect.h,
            )
        )
        return cell_id

    def add_arrow(
        self,
        source_id: str,
        target_id: str,
        cell_id: Optional[str] = None,
        label: str = "",
        color: Optional[str] = None,
        bidirectional: bool = False,
    ) -> str:
        """Add a directed arrow between two cells."""
        cell_id = cell_id or self._new_id()
        c = color or self.cs.connection_color
        start_arrow = "block" if bidirectional else "none"
        style = (
            f"endArrow=block;endFill=1;startArrow={start_arrow};startFill=1;"
            f"strokeColor={c};strokeWidth=2;"
            f"exitX=1;exitY=0.5;exitDx=0;exitDy=0;"
            f"entryX=0;entryY=0.5;entryDx=0;entryDy=0;"
        )
        self._cells.append(
            dict(
                id=cell_id,
                value=html.escape(label),
                style=style,
                edge="1",
                source=source_id,
                target=target_id,
            )
        )
        return cell_id

    # ------------------------------------------------------------------ XML output

    def build(self, canvas_w: int = 1400, canvas_h: int = 900) -> str:
        """Return the complete DrawIO XML string."""
        root = ET.Element(
            "mxGraphModel",
            dx="1422", dy="762",
            grid="1", gridSize="10",
            guides="1", tooltips="1",
            connect="1", arrows="1",
            fold="1", page="1",
            pageScale="1",
            pageWidth=str(canvas_w),
            pageHeight=str(canvas_h),
            math="0", shadow="0",
        )
        root_el = ET.SubElement(root, "root")
        ET.SubElement(root_el, "mxCell", id="0")
        ET.SubElement(root_el, "mxCell", id="1", parent="0")

        for cell in self._cells:
            attribs = {
                "id": str(cell["id"]),
                "value": cell.get("value", ""),
                "style": cell.get("style", ""),
                "parent": "1",
            }
            if "vertex" in cell:
                attribs["vertex"] = cell["vertex"]
            if "edge" in cell:
                attribs["edge"] = cell["edge"]
            if "source" in cell:
                attribs["source"] = cell["source"]
            if "target" in cell:
                attribs["target"] = cell["target"]

            mx_cell = ET.SubElement(root_el, "mxCell", **attribs)

            if "edge" not in cell:
                ET.SubElement(
                    mx_cell, "mxGeometry",
                    x=str(cell.get("x", 0)),
                    y=str(cell.get("y", 0)),
                    width=str(cell.get("w", 120)),
                    height=str(cell.get("h", 40)),
                    **{"as": "geometry"},
                )
            else:
                ET.SubElement(mx_cell, "mxGeometry", relative="1", **{"as": "geometry"})

        return ET.tostring(root, encoding="unicode", xml_declaration=False)


# ---------------------------------------------------------------------------
# High-level layout composer
# ---------------------------------------------------------------------------

@dataclass
class GroupLayout:
    group_name: str
    members: List[str]
    middleware: str
    direction: str   # "in" (upstream) | "out" (downstream)


def compose_flow_diagram(
    knowledge: AppKnowledge,
    color_scheme: ColorScheme,
    component_svgs: Dict[str, str],
) -> str:
    """
    Build the complete DrawIO XML for the main flow diagram.

    Returns the XML string.
    """
    builder = DrawIOBuilder(color_scheme)
    cs = color_scheme

    # ------------------------------------------------------------------ build upstream groups
    upstream_groups: List[GroupLayout] = []
    for group_name, members in knowledge.upstream_groups.items():
        # Find middleware for this group (from first matching upstream)
        mw = "Solace"
        for up in knowledge.upstreams:
            if up.name in members:
                mw = up.connection_middleware
                break
        upstream_groups.append(GroupLayout(group_name, members, mw, "in"))

    # ------------------------------------------------------------------ build downstream groups
    downstream_groups: List[GroupLayout] = []
    for group_name, members in knowledge.downstream_groups.items():
        mw = "Solace"
        for dn in knowledge.downstreams:
            if dn.name in members:
                mw = dn.connection_middleware
                break
        downstream_groups.append(GroupLayout(group_name, members, mw, "out"))

    # ------------------------------------------------------------------ calculate heights

    def group_height(g: GroupLayout) -> float:
        return FRAME_PAD * 2 + len(g.members) * BLOCK_H + max(0, len(g.members) - 1) * BLOCK_GAP + 20  # 20 for label

    def column_height(groups: List[GroupLayout]) -> float:
        total = sum(group_height(g) for g in groups)
        total += max(0, len(groups) - 1) * GROUP_GAP
        return total

    up_col_h = column_height(upstream_groups)
    dn_col_h = column_height(downstream_groups)

    app_fn_h = (
        FRAME_PAD * 2 + 20  # label
        + len(knowledge.business_functions) * BLOCK_H
        + max(0, len(knowledge.business_functions) - 1) * BLOCK_GAP
    )
    # Add infra components (Oracle, NAS, etc.)
    infra_components = [
        mc for mc in knowledge.middleware_components
        if mc.component_type in ("database", "cache", "file_transfer", "secret")
    ]
    if infra_components:
        app_fn_h += BLOCK_GAP + len(infra_components) * MW_H + max(0, len(infra_components) - 1) * BLOCK_GAP

    app_frame_h = max(app_fn_h, up_col_h, dn_col_h)
    canvas_h = int(app_frame_h + 120)
    canvas_w = int(DOWNSTREAM_COL_X + BLOCK_W + 60)

    center_y = 40  # top margin

    # ------------------------------------------------------------------ draw upstream column

    # cell_id maps: upstream_name → cell_id, middleware_name → cell_id
    upstream_cell_ids: Dict[str, str] = {}
    mw_left_cell_ids: Dict[str, str] = {}   # middleware name → cell_id

    up_y = center_y
    for grp in upstream_groups:
        g_h = group_height(grp)
        frame_rect = Rect(UPSTREAM_COL_X, up_y, BLOCK_W + FRAME_PAD * 2, g_h)
        builder.add_frame(grp.group_name, frame_rect)

        inner_y = up_y + FRAME_PAD + 20  # skip label
        for member in grp.members:
            r = Rect(UPSTREAM_COL_X + FRAME_PAD, inner_y, BLOCK_W, BLOCK_H)
            cid = builder.add_solid_block(member, r)
            upstream_cell_ids[member] = cid
            inner_y += BLOCK_H + BLOCK_GAP

        up_y += g_h + GROUP_GAP

    # ------------------------------------------------------------------ draw middleware-left column (one per unique middleware)

    used_mw_left: Dict[str, str] = {}   # middleware_name → cell_id
    mw_left_y = center_y + (max(up_col_h, app_frame_h) - len(set(g.middleware for g in upstream_groups)) * (MW_H + GROUP_GAP)) / 2

    for grp in upstream_groups:
        mw = grp.middleware
        if mw not in used_mw_left:
            svg = component_svgs.get(mw)
            r = Rect(MW_LEFT_X, mw_left_y, MW_W, MW_H)
            cid = builder.add_middleware_frame(mw, r, svg)
            used_mw_left[mw] = cid
            mw_left_y += MW_H + GROUP_GAP

    # ------------------------------------------------------------------ draw app frame

    app_frame_rect = Rect(APP_COL_X, center_y, APP_FRAME_W, app_frame_h)
    builder.add_frame(knowledge.app_name or "Application", app_frame_rect)

    fn_y = center_y + FRAME_PAD + 20
    app_fn_cell_ids: Dict[str, str] = {}
    for fn in knowledge.business_functions:
        r = Rect(APP_COL_X + FRAME_PAD, fn_y, BLOCK_W, BLOCK_H)
        cid = builder.add_solid_block(fn.name, r)
        app_fn_cell_ids[fn.name] = cid
        fn_y += BLOCK_H + BLOCK_GAP

    # Infra components inside app frame (smaller, different shade)
    infra_y = fn_y + BLOCK_GAP
    infra_cell_ids: Dict[str, str] = {}
    for mc in infra_components:
        svg = component_svgs.get(mc.name)
        r = Rect(APP_COL_X + FRAME_PAD, infra_y, MW_W, MW_H)
        cid = builder.add_middleware_frame(mc.name, r, svg)
        infra_cell_ids[mc.name] = cid
        infra_y += MW_H + BLOCK_GAP

    # Representative cell_id for the app (use first business function or frame)
    app_representative_id = (
        list(app_fn_cell_ids.values())[0]
        if app_fn_cell_ids
        else None
    )

    # ------------------------------------------------------------------ draw middleware-right column

    used_mw_right: Dict[str, str] = {}
    mw_right_y = center_y + (max(dn_col_h, app_frame_h) - len(set(g.middleware for g in downstream_groups)) * (MW_H + GROUP_GAP)) / 2

    for grp in downstream_groups:
        mw = grp.middleware
        if mw not in used_mw_right:
            svg = component_svgs.get(mw)
            r = Rect(MW_RIGHT_X, mw_right_y, MW_W, MW_H)
            cid = builder.add_middleware_frame(mw, r, svg)
            used_mw_right[mw] = cid
            mw_right_y += MW_H + GROUP_GAP

    # ------------------------------------------------------------------ draw downstream column

    downstream_cell_ids: Dict[str, str] = {}
    dn_y = center_y
    for grp in downstream_groups:
        g_h = group_height(grp)
        frame_rect = Rect(DOWNSTREAM_COL_X, dn_y, BLOCK_W + FRAME_PAD * 2, g_h)
        builder.add_frame(grp.group_name, frame_rect)

        inner_y = dn_y + FRAME_PAD + 20
        for member in grp.members:
            r = Rect(DOWNSTREAM_COL_X + FRAME_PAD, inner_y, BLOCK_W, BLOCK_H)
            cid = builder.add_solid_block(member, r)
            downstream_cell_ids[member] = cid
            inner_y += BLOCK_H + BLOCK_GAP

        dn_y += g_h + GROUP_GAP

    # ------------------------------------------------------------------ draw connections

    # upstream members → middleware-left
    for grp in upstream_groups:
        mw_cid = used_mw_left.get(grp.middleware)
        if not mw_cid:
            continue
        for member in grp.members:
            src = upstream_cell_ids.get(member)
            if src:
                builder.add_arrow(src, mw_cid)

    # middleware-left → app (connect to first business function or app frame)
    if app_representative_id:
        for mw_cid in used_mw_left.values():
            builder.add_arrow(mw_cid, app_representative_id)

    # app → middleware-right
    if app_representative_id:
        for mw_cid in used_mw_right.values():
            builder.add_arrow(app_representative_id, mw_cid)

    # middleware-right → downstream members
    for grp in downstream_groups:
        mw_cid = used_mw_right.get(grp.middleware)
        if not mw_cid:
            continue
        for member in grp.members:
            tgt = downstream_cell_ids.get(member)
            if tgt:
                builder.add_arrow(mw_cid, tgt)

    return builder.build(canvas_w=canvas_w, canvas_h=canvas_h)
