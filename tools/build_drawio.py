#!/usr/bin/env python3
"""
CLI tool for building a DrawIO flow diagram XML from app knowledge JSON.

Usage
-----
  python tools/build_drawio.py \\
      --knowledge output/knowledge.json \\
      --example   examples/reference_dashboard.json \\
      --output    output/app_flow.xml \\
      [--svgs     svgs/]

  --knowledge   Path to knowledge.json produced by the agent session
  --example     Path to the reference Grafana dashboard JSON (used to
                extract the colour scheme)
  --output      Where to write the DrawIO XML
  --svgs        Optional directory containing SVG/PNG files named after
                middleware components (e.g. Solace.svg, Oracle.png)

knowledge.json schema (all fields optional except app_name):
{
  "app_name": "MyApp",
  "app_description": "...",
  "upstreams": [
    {"name": "CCMS", "channel_group": "Retail Channel",
     "connection_middleware": "Solace", "notes": null}
  ],
  "downstreams": [
    {"name": "SCPay", "category": "Clearing",
     "connection_middleware": "REST API", "notes": null}
  ],
  "business_functions": [
    {"name": "Payment Processing", "description": null}
  ],
  "business_metrics": [
    {"name": "Payment TPS", "group": "Transactions",
     "description": null, "is_instant": true, "common_issues": []}
  ],
  "middleware_components": [
    {"name": "Solace", "component_type": "messaging",
     "svg_provided": false, "svg_content": null}
  ],
  "upstream_groups": {"Channel A": ["AuthService", "StorageService"]},
  "downstream_groups": {"Clearing": ["SCPay"]}
}
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.state import AppKnowledge, ColorScheme
from agent.tools.drawio_builder import compose_flow_diagram
from agent.tools.grafana_builder import FLOW_PANEL_TYPES


# ---------------------------------------------------------------------------
# Colour extraction (from reference dashboard SVG/XML source)
# ---------------------------------------------------------------------------

def _find_svg_source(panel: Dict[str, Any]) -> Optional[str]:
    if "flowcharting" in panel:
        fc = panel["flowcharting"]
        return fc.get("svg") or (fc.get("source") or {}).get("content")
    for key in ("svg", "content", "source"):
        if key in panel.get("options", {}):
            return str(panel["options"][key])
    return None


def extract_color_scheme(dashboard_json: Dict[str, Any]) -> ColorScheme:
    """Extract dominant fill/stroke colours from the reference dashboard's Flow panel."""
    for panel in dashboard_json.get("panels", []):
        if panel.get("type", "") in FLOW_PANEL_TYPES:
            svg_source = _find_svg_source(panel)
            if svg_source:
                fills = re.findall(r"fillColor=(#[0-9A-Fa-f]{6})", svg_source)
                strokes = re.findall(r"strokeColor=(#[0-9A-Fa-f]{6})", svg_source)
                if fills:
                    dominant = max(set(fills), key=fills.count)
                    stroke = max(set(strokes), key=strokes.count) if strokes else dominant
                    return ColorScheme(
                        healthy_fill=dominant,
                        healthy_stroke=stroke,
                        frame_stroke=stroke,
                        connection_color=stroke,
                    )
    return ColorScheme()   # safe defaults


# ---------------------------------------------------------------------------
# SVG / PNG loader
# ---------------------------------------------------------------------------

def load_component_svgs(svgs_dir: Optional[Path]) -> Dict[str, str]:
    """Load SVG and PNG files from a directory; key = filename stem."""
    component_svgs: Dict[str, str] = {}
    if svgs_dir is None or not svgs_dir.is_dir():
        return component_svgs
    for f in svgs_dir.glob("*.svg"):
        component_svgs[f.stem] = f.read_text(encoding="utf-8", errors="replace")
    for f in svgs_dir.glob("*.png"):
        b64 = base64.b64encode(f.read_bytes()).decode()
        component_svgs[f.stem] = (
            f'<img src="data:image/png;base64,{b64}" '
            f'style="width:100%;height:100%"/>'
        )
    return component_svgs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="build_drawio",
        description="Build a DrawIO flow diagram XML from app knowledge JSON.",
    )
    parser.add_argument("--knowledge", required=True,
                        help="Path to knowledge.json")
    parser.add_argument("--example", required=True,
                        help="Path to the reference Grafana dashboard JSON")
    parser.add_argument("--output", required=True,
                        help="Output path for the DrawIO XML file")
    parser.add_argument("--svgs", default=None,
                        help="Directory containing SVG/PNG icon files (optional)")
    args = parser.parse_args()

    knowledge_path = Path(args.knowledge)
    example_path = Path(args.example)
    output_path = Path(args.output)

    if not knowledge_path.exists():
        print(f"ERROR: knowledge file not found: {knowledge_path}", file=sys.stderr)
        sys.exit(1)
    if not example_path.exists():
        print(f"ERROR: example dashboard not found: {example_path}", file=sys.stderr)
        sys.exit(1)

    knowledge = AppKnowledge(**json.loads(knowledge_path.read_text(encoding="utf-8")))
    example_json = json.loads(example_path.read_text(encoding="utf-8"))
    color_scheme = extract_color_scheme(example_json)
    component_svgs = load_component_svgs(Path(args.svgs) if args.svgs else None)

    xml = compose_flow_diagram(knowledge, color_scheme, component_svgs)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(xml, encoding="utf-8")
    print(f"DrawIO XML written to: {output_path}")


if __name__ == "__main__":
    main()
