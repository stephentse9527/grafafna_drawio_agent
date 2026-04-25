"""
Quick smoke-test for compose_flow_diagram().
Runs with the sample CCMS data provided during agent review.
Writes output/ccms_flow.drawio and output/ccms_flow.svg

Usage:
    python tools/test_flow.py
"""
import os, sys, pathlib

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agent.state import (
    AppKnowledge, ColorScheme,
    UpstreamInfo, DownstreamInfo,
    BusinessFunction, MiddlewareComponent,
)
from agent.tools.drawio_builder import compose_flow_diagram

# ── test data ─────────────────────────────────────────────────────────────────

knowledge = AppKnowledge(
    app_name="CCMS",
    app_description="Channel Credit Management System — processes DDI/eDDA/D3/MYRPP transactions",

    upstreams=[
        UpstreamInfo(name="SCPAY",  connection_middleware="Solace"),
        UpstreamInfo(name="CCMS",   connection_middleware="MQ"),
        UpstreamInfo(name="HSBC",   connection_middleware="Solace"),
        UpstreamInfo(name="GCG",    connection_middleware="Solace"),
    ],
    # No explicit grouping → each upstream is its own group (singleton)
    upstream_groups={},

    downstreams=[
        DownstreamInfo(name="TSAAS", connection_middleware="Solace"),
        DownstreamInfo(name="EBBS",  connection_middleware="Solace"),
        DownstreamInfo(name="PAIMI", connection_middleware="MQ"),
    ],
    downstream_groups={},

    business_functions=[
        BusinessFunction(name="DDI"),
        BusinessFunction(name="eDDA"),
        BusinessFunction(name="D3"),
        BusinessFunction(name="MYRPP"),
    ],

    # Internal shared services (infra — shown inside the APP frame)
    middleware_components=[
        MiddlewareComponent(name="DB",         component_type="database"),
        MiddlewareComponent(name="HashiCorp",  component_type="secret"),
        MiddlewareComponent(name="Hazelcast",  component_type="cache"),
        MiddlewareComponent(name="NAS",        component_type="file_transfer"),
        # Connection middleware (needed so the builder can distinguish conn vs infra)
        MiddlewareComponent(name="Solace",     component_type="messaging"),
        MiddlewareComponent(name="MQ",         component_type="messaging"),
    ],
)

color_scheme = ColorScheme()   # default green-on-dark theme

# No custom SVG files — builder will fall back to built-in DrawIO shapes
component_svgs: dict = {}

# ── run ───────────────────────────────────────────────────────────────────────

print("Building flow diagram …")
result = compose_flow_diagram(knowledge, color_scheme, component_svgs)

out_dir = ROOT / "output"
out_dir.mkdir(exist_ok=True)

drawio_path = out_dir / "ccms_flow.drawio"
svg_path    = out_dir / "ccms_flow.svg"

drawio_path.write_text(result.xml,  encoding="utf-8")
svg_path.write_text(result.svg,     encoding="utf-8")

print(f"Canvas: {result.canvas_w} × {result.canvas_h} px")
print(f"DrawIO : {drawio_path}")
print(f"SVG    : {svg_path}")

# Quick sanity checks
import xml.etree.ElementTree as ET
root = ET.fromstring(result.xml)
cells = root.findall(".//{http://www.w3.org/1999/xhtml}mxCell") or root.findall(".//mxCell")
print(f"mxCell count: {len(cells)}")

labels = [c.get("value","") for c in cells if c.get("value","").strip()]
print("Named cells:", labels[:30])
