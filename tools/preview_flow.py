#!/usr/bin/env python3
"""
tools/preview_flow.py — Visual HTML preview of a DrawIO flow diagram.

Parses the .drawio XML and renders all cells as a standalone HTML page
with inline SVG so the agent can visually inspect the layout before
finalising.

Usage:
    python tools/preview_flow.py output/APPNAME_flow.drawio
    # Writes output/APPNAME_flow.preview.html
    # Prints a text layout report + any detected issues to stdout.

Exit codes:
    0 — preview written, no structural issues detected
    1 — input file not found, parse error, or layout issues detected
"""
from __future__ import annotations

import html
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


# ---------------------------------------------------------------------------
# Style-string helpers
# ---------------------------------------------------------------------------

def _style_val(s: str, key: str, default: str = "") -> str:
    m = re.search(rf'(?<![a-zA-Z]){re.escape(key)}=([^;]+)', s)
    return m.group(1).strip() if m else default


def _color_ok(v: str) -> bool:
    return bool(re.match(r'^#[0-9a-fA-F]{3,8}$', v))


def _fill(s: str) -> str:
    v = _style_val(s, "fillColor", "#1a1d23")
    if v.lower() in ("none", "transparent") or not _color_ok(v):
        return "transparent"
    return v


def _stroke(s: str, default: str = "#888888") -> str:
    v = _style_val(s, "strokeColor", default)
    return v if _color_ok(v) else default


def _font_color(s: str) -> str:
    v = _style_val(s, "fontColor", "#cccccc")
    return v if _color_ok(v) else "#cccccc"


def _font_size(s: str) -> int:
    try:
        return max(8, int(_style_val(s, "fontSize", "11")))
    except ValueError:
        return 11


def _stroke_width(s: str) -> str:
    return _style_val(s, "strokeWidth", "1")


# ---------------------------------------------------------------------------
# Core renderer
# ---------------------------------------------------------------------------

def render_preview(drawio_path: Path) -> tuple[str, str, list[str]]:
    """
    Returns (html_content, text_report, issues).

    issues is a list of human-readable problem strings.
    """
    tree = ET.parse(str(drawio_path))
    root = tree.getroot()

    canvas_w = int(root.get("pageWidth", 1200))
    canvas_h = int(root.get("pageHeight", 900))

    cells = root.findall(".//mxCell")

    edges = [c for c in cells if c.get("edge")]
    verts = [c for c in cells if not c.get("edge")]

    svg_parts: list[str] = []
    issues: list[str] = []
    cell_lines: list[str] = []

    # ---- defs: arrowhead marker ----
    svg_parts.append(
        '<defs>'
        '<marker id="endarrow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">'
        '<path d="M0,0 L8,3 L0,6 z" fill="#00b050"/>'
        '</marker>'
        '<marker id="startarrow" markerWidth="8" markerHeight="6" refX="0" refY="3" orient="auto">'
        '<path d="M8,0 L0,3 L8,6 z" fill="#00b050"/>'
        '</marker>'
        '</defs>'
    )

    # ---- background ----
    svg_parts.append(
        f'<rect width="{canvas_w}" height="{canvas_h}" fill="#181B1F"/>'
    )

    # ---- edges (drawn first, behind vertices) ----
    for cell in edges:
        style = cell.get("style", "")
        geo = cell.find("mxGeometry")
        if geo is None:
            continue
        sp = geo.find("mxPoint[@as='sourcePoint']")
        tp = geo.find("mxPoint[@as='targetPoint']")
        if sp is None or tp is None:
            continue

        x1 = float(sp.get("x", 0))
        y1 = float(sp.get("y", 0))
        x2 = float(tp.get("x", 0))
        y2 = float(tp.get("y", 0))

        sc = _stroke(style, "#00b050")
        sw = _stroke_width(style)
        dash = ' stroke-dasharray="6,3"' if "dashed=1" in style else ""

        start_arrow = _style_val(style, "startArrow", "none")
        end_arrow = _style_val(style, "endArrow", "block")

        ms = ' marker-start="url(#startarrow)"' if start_arrow not in ("none", "") else ""
        me = ' marker-end="url(#endarrow)"' if end_arrow not in ("none", "") else ""

        svg_parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{sc}" stroke-width="{sw}"{dash}{ms}{me}/>'
        )

    # ---- vertices ----
    for cell in verts:
        style = cell.get("style", "")
        value = cell.get("value") or ""
        geo = cell.find("mxGeometry")
        if geo is None:
            continue

        x = float(geo.get("x", 0))
        y = float(geo.get("y", 0))
        w = float(geo.get("width", 0))
        h = float(geo.get("height", 0))

        if w <= 0 or h <= 0:
            continue

        # ---- bounds check ----
        if x < -5 or y < -5 or x + w > canvas_w + 10 or y + h > canvas_h + 10:
            issues.append(
                f"OUT-OF-BOUNDS: '{value or cell.get('id','?')}' "
                f"pos=({x:.0f},{y:.0f}) size=({w:.0f}×{h:.0f}) "
                f"canvas=({canvas_w}×{canvas_h})"
            )

        cell_lines.append(
            f"  {'[IMG]' if 'shape=image' in style else '[BOX]'} "
            f"{(value or '(unlabeled)'):<24} "
            f"x={x:<7.1f} y={y:<7.1f} w={w:<7.1f} h={h:.1f}"
        )

        is_image = "shape=image" in style
        is_pure_text = (
            style.startswith("text;")
            or ("fillColor=none" in style and "strokeColor=none" in style)
        )

        if is_image:
            sc = _stroke(style, "#555555")
            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                f'fill="#2a2a2a" stroke="{sc}" stroke-width="1" rx="3"'
                f' opacity="0.7"/>'
            )
            label_esc = html.escape("[icon]")
            svg_parts.append(
                f'<text x="{x + w/2}" y="{y + h/2}" text-anchor="middle" '
                f'dominant-baseline="central" font-size="9" fill="#888" '
                f'font-family="sans-serif">{label_esc}</text>'
            )

        elif is_pure_text:
            fc = _font_color(style)
            fs = _font_size(style)
            if value:
                svg_parts.append(
                    f'<text x="{x + w/2}" y="{y + h/2}" text-anchor="middle" '
                    f'dominant-baseline="central" font-size="{fs}" fill="{fc}" '
                    f'font-family="sans-serif">{html.escape(value)}</text>'
                )

        else:
            fill_c = _fill(style)
            sc = _stroke(style)
            dashed = "dashed=1" in style
            dash_attr = ' stroke-dasharray="8,4"' if dashed else ""
            rx = "5" if "rounded=1" in style else "0"
            sw = _stroke_width(style)

            svg_parts.append(
                f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
                f'fill="{fill_c}" stroke="{sc}" stroke-width="{sw}"{dash_attr} rx="{rx}"/>'
            )

            if value:
                fc = _font_color(style)
                fs = _font_size(style)
                valign = _style_val(style, "verticalAlign", "middle")
                ty = y + h / 2
                if valign == "top":
                    ty = y + fs + 4
                svg_parts.append(
                    f'<text x="{x + w/2}" y="{ty}" text-anchor="middle" '
                    f'dominant-baseline="central" font-size="{fs}" fill="{fc}" '
                    f'font-family="sans-serif" clip-path="url(#none)">'
                    f'{html.escape(value)}</text>'
                )

    # ---- assemble HTML ----
    svg_body = "\n".join(svg_parts)
    issue_block = ""
    if issues:
        issue_lines = "\n".join(f"  ⚠ {i}" for i in issues)
        issue_block = (
            f"<h3 style='color:#ff4444'>Layout Issues ({len(issues)})</h3>"
            f"<pre style='color:#ff6666;background:#1a1a1a;padding:10px;"
            f"border-radius:4px;overflow-x:auto'>{html.escape(issue_lines)}</pre>"
        )
    else:
        issue_block = "<p style='color:#00b050'>✓ No layout issues detected.</p>"

    cell_report_html = html.escape("\n".join(cell_lines) or "(no named cells)")

    html_out = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Flow Preview — {html.escape(drawio_path.stem)}</title>
  <style>
    body  {{ background:#111; margin:16px; font-family:monospace; color:#ccc; }}
    h2    {{ color:#00b050; margin-bottom:4px; }}
    h3    {{ color:#aaa; margin-top:20px; }}
    pre   {{ background:#1a1a1a; padding:10px; border-radius:4px;
             overflow-x:auto; font-size:12px; line-height:1.5; }}
    svg   {{ display:block; border:1px solid #333; margin:12px 0; }}
    .meta {{ color:#888; font-size:13px; margin-bottom:8px; }}
  </style>
</head>
<body>
  <h2>Flow Diagram Preview — {html.escape(drawio_path.stem)}</h2>
  <p class="meta">Canvas: {canvas_w} × {canvas_h} px &nbsp;|&nbsp; Total cells: {len(cells)} &nbsp;|&nbsp; Edges: {len(edges)} &nbsp;|&nbsp; Vertices: {len(verts)}</p>

  <svg width="{canvas_w}" height="{canvas_h}"
       viewBox="0 0 {canvas_w} {canvas_h}"
       xmlns="http://www.w3.org/2000/svg"
       style="max-width:100%">
{svg_body}
  </svg>

  {issue_block}

  <h3>Cell Layout Report</h3>
  <pre>{cell_report_html}</pre>

  <h3>Visual Checklist</h3>
  <pre>Manually verify after opening this page in a browser:
  [ ] All upstream/downstream blocks are visible and correctly labelled
  [ ] APP frame is visible, labelled with the app name
  [ ] Each connection unit (icon box + arrow) is centred between its block and the APP frame
  [ ] Connection arrow is horizontally/vertically straight
  [ ] Icon box label is readable (not clipped or overflowing)
  [ ] Infra items inside APP frame are arranged in a neat grid (no overlap)
  [ ] No elements are cut off at canvas edges
  [ ] Overall layout looks balanced (no huge whitespace gaps on one side)</pre>
</body>
</html>
"""

    text_report = "\n".join(
        [
            f"Canvas: {canvas_w} × {canvas_h}  |  cells: {len(cells)}  edges: {len(edges)}  verts: {len(verts)}",
            "Named cells:",
        ]
        + cell_lines
        + (
            ["\nISSUES DETECTED:"] + [f"  {i}" for i in issues]
            if issues
            else ["\nNo structural issues detected."]
        )
    )

    return html_out, text_report, issues


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(
            "Usage: python tools/preview_flow.py <path/to/file.drawio>",
            file=sys.stderr,
        )
        sys.exit(1)

    drawio_path = Path(sys.argv[1])
    if not drawio_path.exists():
        print(f"ERROR: File not found: {drawio_path}", file=sys.stderr)
        sys.exit(1)

    try:
        html_content, text_report, issues = render_preview(drawio_path)
    except ET.ParseError as e:
        print(f"ERROR: Cannot parse DrawIO XML: {e}", file=sys.stderr)
        sys.exit(1)

    out_path = drawio_path.with_suffix(".preview.html")
    out_path.write_text(html_content, encoding="utf-8")

    print(text_report)
    print(f"\nPreview HTML  : {out_path.resolve()}")

    if issues:
        print(f"\nVALIDATION FAILED — {len(issues)} layout issue(s) detected (see above)")
        sys.exit(1)
    else:
        print("\nVALIDATION PASSED — layout looks structurally correct")
        print(
            "Open the preview HTML in a browser to perform the visual checklist."
        )


if __name__ == "__main__":
    main()
