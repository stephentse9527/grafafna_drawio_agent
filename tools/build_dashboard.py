#!/usr/bin/env python3
"""
CLI tool for assembling the final Grafana dashboard JSON.

Usage
-----
  python tools/build_dashboard.py \\
      --knowledge output/knowledge.json \\
      --example   examples/reference_dashboard.json \\
      --flow-xml  output/app_flow.xml \\
      --output    output/

  --knowledge   Path to knowledge.json produced by the agent session
  --example     Path to the reference Grafana dashboard JSON (layout template)
  --flow-xml    Path to the DrawIO XML file produced by build_drawio.py
  --output      Output directory (default: ./output)

The output file is named <app_name>_dashboard.json and written to --output.
Import it into Grafana via Dashboards -> Import.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.state import AppKnowledge
from agent.tools.grafana_builder import build_dashboard, extract_layout


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="build_dashboard",
        description="Assemble a Grafana dashboard JSON from knowledge + reference layout.",
    )
    parser.add_argument("--knowledge", required=True,
                        help="Path to knowledge.json")
    parser.add_argument("--example", required=True,
                        help="Path to the reference Grafana dashboard JSON")
    parser.add_argument("--flow-xml", required=True,
                        help="Path to the DrawIO XML file")
    parser.add_argument("--output", default="./output",
                        help="Output directory (default: ./output)")
    args = parser.parse_args()

    knowledge_path = Path(args.knowledge)
    example_path = Path(args.example)
    flow_xml_path = Path(args.flow_xml)
    output_dir = Path(args.output)

    for p, label in [(knowledge_path, "knowledge"), (example_path, "example"),
                     (flow_xml_path, "flow-xml")]:
        if not p.exists():
            print(f"ERROR: {label} file not found: {p}", file=sys.stderr)
            sys.exit(1)

    knowledge = AppKnowledge(**json.loads(knowledge_path.read_text(encoding="utf-8")))
    example_json = json.loads(example_path.read_text(encoding="utf-8"))
    flow_xml = flow_xml_path.read_text(encoding="utf-8")

    layout = extract_layout(example_json)
    dashboard = build_dashboard(knowledge, layout, flow_xml)

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
