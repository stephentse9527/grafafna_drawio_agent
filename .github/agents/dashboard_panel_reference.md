# Dashboard Panel Function Reference

> **Critical:** Read this document in full before building any Grafana dashboard panels.  
> The 21-panel layout is a hard contract. Any deviation will fail the validation gate.

---

## Panel Inventory (x, y positions)

| Slot | x | y | h | w | Function | Source | Modifiable? |
|------|---|---|---|---|----------|--------|-------------|
| Z1-A | 0 | 0 | 3 | 20 | Main title panel | User-provided `.github/agents/panel_templates/title_panel.json` | Only: replace `"XXXX"` in title string |
| Z1-B | 21 | 0 | 3 | 3 | Alert management panel | User-provided `.github/agents/panel_templates/alert_panel.json` | **NO changes whatsoever** |
| Z2-1 | 0 | 3 | 1 | 6 | BM1 section header | RCA top-3 analysis | `title=""`, set `alias` only |
| Z2-2 | 6 | 3 | 1 | 6 | BM2 section header | RCA top-3 analysis | `title=""`, set `alias` only |
| Z2-3 | 12 | 3 | 1 | 6 | BM3 section header | RCA top-3 analysis | `title=""`, set `alias` only |
| Z2-4 | 18 | 3 | 1 | 6 | System Metrics header | Fixed literal | `title=""`, `alias="System Metrics"` always |
| Z3-1 | 0 | 4 | 4 | 3 | Stat: BM1 instant metric 1 | BM1.metrics[0] | Title = metric name |
| Z3-2 | 3 | 4 | 4 | 3 | Stat: BM1 instant metric 2 | BM1.metrics[1] | Title = metric name |
| Z3-3 | 6 | 4 | 4 | 3 | Stat: BM2 instant metric 1 | BM2.metrics[0] | Title = metric name |
| Z3-4 | 9 | 4 | 4 | 3 | Stat: BM2 instant metric 2 | BM2.metrics[1] | Title = metric name |
| Z3-5 | 12 | 4 | 4 | 3 | Stat: BM3 instant metric 1 | BM3.metrics[0] | Title = metric name |
| Z3-6 | 15 | 4 | 4 | 3 | Stat: BM3 instant metric 2 | BM3.metrics[1] | Title = metric name |
| Z3-7 | 18 | 4 | 4 | 6 | System metric timeseries (wide) | system_metrics[0] | Title = metric name |
| Z4-1 | 0 | 8 | 6 | 6 | BM1 timeseries (Req & Resp) | BM1.metrics all | Title = BM1 title |
| Z4-2 | 6 | 8 | 6 | 6 | BM2 timeseries (Req & Resp) | BM2.metrics all | Title = BM2 title |
| Z4-3 | 12 | 8 | 6 | 6 | BM3 timeseries (Req & Resp) | BM3.metrics all | Title = BM3 title |
| Z4-4 | 18 | 8 | 6 | 6 | System metric panel | system_metrics[1] | Title = metric name |
| Z5-MAIN | 0 | 14 | 18 | 18 | DrawIO architecture flow SVG | compose_flow_diagram() | App name in title only |
| Z5-R1 | 18 | 14 | 6 | 6 | System metric stat | system_metrics[2] | Title = metric name |
| Z5-R2 | 18 | 20 | 6 | 6 | System metric stat | system_metrics[3] | Title = metric name |
| Z5-R3 | 18 | 26 | 6 | 6 | System metric stat | system_metrics[4] | Title = metric name |

---

## Zone Descriptions

### Zone 1 — Title Row
Two user-provided panels locked to top of dashboard.

- **Z1-A** (wide, 20 wide): Main title panel. The agent receives this as `title_panel_json`.  
  - The ONLY permitted modification: find the string `"XXXX"` anywhere in the panel's `title` field and replace it with `app_knowledge.app_name`.  
  - If `title_panel_json` is `None`, raise `ValueError("title_panel.json is required")`.
- **Z1-B** (narrow, 3 wide): Alert panel. The agent receives this as `alert_panel_json`.  
  - Deep-copy only. Zero modifications to any field.  
  - If `alert_panel_json` is `None`, raise `ValueError("alert_panel.json is required")`.

---

### Zone 2 — Section Headers
Four thin (h=1) **stat** panels acting as column headers.

**Rendering mechanism** (derived from `standar.json`):
- Panel `title` = `""` (always empty — title field is NOT the visible text)
- `type: "stat"`, `options.textMode: "name"` — the stat renders the **target alias** as the display label
- `options.graphMode: "none"`, `options.colorMode: "background"`
- Color is **fixed** `#6d786b66` — hardcoded in `fieldConfig.defaults.thresholds.steps[0].color` from the template. **Must NOT be overridden.**
- `datasource: grafana-testdata-datasource`, `scenarioId: "random_walk"`

**What to set per panel:**
- Set `targets[0].alias` = the BM short title (1–2 words) or `"System Metrics"`
- Leave `title = ""`
- Clone ALL `fieldConfig`, `options`, `type` exactly from template — do not alter them

**Z2-1, Z2-2, Z2-3**: `alias` = `rca_analysis["top_business_metrics"][0|1|2]["title"]`  
**Z2-4**: `alias` = `"System Metrics"` (literal, never changed)

---

### Zone 3 — Instant Metric Stats
Six narrow stat panels (one per BM) + one wide system metric timeseries.

For BM `i` (0-indexed), **Z3-{2i+1}** = BM[i].metrics[0], **Z3-{2i+2}** = BM[i].metrics[1].

Fallback if a BM has only one metric: duplicate metric name, add suffix ` (2)`.  
Fallback if a BM has zero metrics: use BM title as placeholder title with no target.

- **Z3-7** (wide): First system metric from `system_metrics[0]["name"]`. Panel type = **timeseries**.

---

### Zone 4 — Business Metric Timeseries
Three medium timeseries panels (one per BM) + one system metric panel.

- **Z4-1/2/3**: Title = `BM["title"]`. Targets = ALL metrics listed in `BM["metrics"]`.  
  Each metric becomes one timeseries series (refId A, B, C…).
- **Z4-4**: System metric `system_metrics[1]["name"]`. Panel type = **timeseries**.

---

### Zone 5 — Architecture + Right Column
- **Z5-MAIN**: DrawIO SVG panel. Type = `agenty-flowcharting-panel`.  
  Title = `f"{app_name} Architecture"`. SVG injected from `drawio_svg` arg.  
  **Never touch** gridPos, type, or SVG content of any other panel.
- **Z5-R1/R2/R3**: System metric stat panels for `system_metrics[2/3/4]`.  
  Panel type = **stat**. Title = metric name. If fewer than 5 system metrics exist,  
  use fallback labels: `"CPU Usage"`, `"Memory Usage"`, `"Error Rate"`.

---

## RCA Analysis JSON Structure

The agent MUST produce this structure at Step 3 and write it to `output/rca_analysis.json`:

```json
{
  "top_business_metrics": [
    {
      "title": "DDI",
      "metrics": ["DDI Req Count", "DDI Resp Count"],
      "rca_source": "RCA-2024-031"
    },
    {
      "title": "eDDA",
      "metrics": ["eDDA Req Count", "eDDA Resp Count"],
      "rca_source": "RCA-2024-047"
    },
    {
      "title": "D3",
      "metrics": ["D3 Req Count", "D3 Resp Count"],
      "rca_source": null
    }
  ],
  "system_metrics": [
    {"name": "Solace Queue Depth", "description": "Solace queue utilization"},
    {"name": "DB Connections",     "description": "Oracle connection pool usage"},
    {"name": "CPU Usage",          "description": "Application CPU"},
    {"name": "Memory Usage",       "description": "JVM heap"},
    {"name": "Error Rate",         "description": "5xx/exception rate"}
  ]
}
```

**Rules:**
- `top_business_metrics` MUST have exactly 3 items (pad with placeholders if RCA found fewer).
- `system_metrics` MUST have exactly 5 items (pad with standard defaults listed above if needed).
- `metrics` array inside each BM entry contains the display names of the relevant timeseries.

---

## Panel Template Files

These files MUST exist before the dashboard build step:

| File | Purpose |
|------|---------|
| `.github/agents/panel_templates/title_panel.json` | Z1-A source JSON — **user-provided**, export from Grafana |
| `.github/agents/panel_templates/alert_panel.json` | Z1-B source JSON — **user-provided**, export from Grafana |

If either file is missing, the agent MUST stop and ask the user to provide it. Do NOT proceed with a synthesized panel.

---

## Validation Rules (automated gate)

1. `len(panels) == 21` — always.
2. `panels[0]["gridPos"] == {"h":3,"w":20,"x":0,"y":0}` (Z1-A).
3. `panels[1]["gridPos"] == {"h":3,"w":3,"x":21,"y":0}` (Z1-B).
4. Z1-B panel must be a deep copy of `alert_panel_json` with NO field changes except `id`.
5. Z2-1/2/3/4 panel `title` must be `""` (empty); display text lives in `targets[0].alias`.
6. Z2-4 `targets[0].alias` == `"System Metrics"` (literal).
7. Z5-MAIN type must be `"agenty-flowcharting-panel"`.
8. All 21 panels have `gridPos` matching REQUIRED_LAYOUT exactly.
9. No duplicate panel `id` values.
