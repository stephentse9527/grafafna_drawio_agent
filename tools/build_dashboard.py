#!/usr/bin/env python3
"""
CLI tool for assembling the final Grafana dashboard JSON.

Usage
-----
  python tools/build_dashboard.py \\
      --knowledge  output/knowledge.json \\
      --example    .github/agents/grafana_json_standar/standar.json \\
      --flow-svg   output/app_flow.svg \\
      --output     output/ \\
      [--title-panel  .github/agents/panel_templates/title_panel.json] \\
      [--alert-panel  .github/agents/panel_templates/alert_panel.json] \\
      [--rca-analysis output/rca_analysis.json]

  Note: --flow-svg accepts the .svg wrapper file (not the .drawio XML).
  Both files are produced by build_drawio.py; use the .svg for this tool.

  --knowledge     Path to knowledge.json produced by the agent session
  --example       Path to the reference Grafana dashboard JSON (layout template)
  --flow-svg      Path to the DrawIO SVG wrapper file produced by build_drawio.py (output/APPNAME_flow.svg)
  --output        Output directory (default: ./output)
  --title-panel   (optional) Path to user-provided Z1-A panel JSON
  --alert-panel   (optional) Path to user-provided Z1-B panel JSON
  --rca-analysis  (optional) Path to rca_analysis.json (top BMs + system metrics)

The output file is named <app_name>_dashboard.json and written to --output.
Import it into Grafana via Dashboards -> Import.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.state import AppKnowledge
from agent.tools.grafana_builder import build_dashboard


def _load_json_optional(path_str: Optional[str], label: str) -> Optional[dict]:
    if not path_str:
        return None
    p = Path(path_str)
    if not p.exists():
        print(f"WARNING: {label} file not found: {p} (proceeding without it)", file=sys.stderr)
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="build_dashboard",
        description="Assemble a Grafana dashboard JSON from knowledge + reference layout.",
    )
    parser.add_argument("--knowledge", required=True,
                        help="Path to knowledge.json")
    parser.add_argument("--example", required=True,
                        help="Path to the reference Grafana dashboard JSON")
    parser.add_argument("--flow-svg", required=True,
                        help="Path to the DrawIO SVG wrapper file (output/APPNAME_flow.svg from build_drawio.py)")
    parser.add_argument("--output", default="./output",
                        help="Output directory (default: ./output)")
    parser.add_argument("--title-panel",
                        default=".github/agents/panel_templates/title_panel.json",
                        help="Path to user-provided Z1-A title panel JSON (required)")
    parser.add_argument("--alert-panel",
                        default=".github/agents/panel_templates/alert_panel.json",
                        help="Path to user-provided Z1-B alert panel JSON (required)")
    parser.add_argument("--rca-analysis", default=None,
                        help="Path to rca_analysis.json (top business metrics + system metrics)")
    args = parser.parse_args()

    knowledge_path = Path(args.knowledge)
    example_path = Path(args.example)
    flow_svg_path = Path(args.flow_svg)
    output_dir = Path(args.output)

    for p, label in [(knowledge_path, "knowledge"), (example_path, "example"),
                     (flow_svg_path, "flow-svg")]:
        if not p.exists():
            print(f"ERROR: {label} file not found: {p}", file=sys.stderr)
            sys.exit(1)

    knowledge = AppKnowledge(**json.loads(knowledge_path.read_text(encoding="utf-8")))
    _raw = example_path.read_text(encoding="utf-8")
    template_json, _ = json.JSONDecoder().raw_decode(_raw)
    drawio_svg = flow_svg_path.read_text(encoding="utf-8")

    # title_panel and alert_panel are REQUIRED — agent must verify before calling this script
    for path_str, label in [(args.title_panel, "--title-panel"), (args.alert_panel, "--alert-panel")]:
        if not Path(path_str).exists():
            print(
                f"ERROR: {label} file not found: {path_str}\n"
                f"Place the production panel JSON at that path and re-run.",
                file=sys.stderr,
            )
            sys.exit(1)
    title_panel_json = _load_json_optional(args.title_panel, "title-panel")
    alert_panel_json = _load_json_optional(args.alert_panel, "alert-panel")
    rca_analysis     = _load_json_optional(args.rca_analysis, "rca-analysis")

    dashboard = build_dashboard(
        knowledge, template_json, drawio_svg,
        title_panel_json=title_panel_json,
        alert_panel_json=alert_panel_json,
        rca_analysis=rca_analysis,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    app_name = (knowledge.app_name or "app").replace(" ", "_").lower()
    out_file = output_dir / f"{app_name}_dashboard.json"
    out_file.write_text(
        json.dumps(dashboard, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Dashboard JSON written to: {out_file}")
    print("Import into Grafana via: Dashboards -> Import -> Upload JSON file")


if __name__ == "__main__":
    main()
