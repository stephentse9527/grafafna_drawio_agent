# Grafana Dashboard Agent

An LLM-powered agent that reads a Confluence knowledge base and automatically
generates a production-quality Grafana observability dashboard — including a
DrawIO flow diagram showing all upstreams, app components, and downstreams.

**No Anthropic API key required.** The agent uses whichever model you have
selected in VS Code Copilot (e.g. Claude Sonnet, GPT-4o, etc.).

The end-to-end workflow is documented in [`docs/workflow.drawio`](docs/workflow.drawio).

---

## How It Works

```
VS Code Copilot  (your selected model — no extra API key needed)
  │
  │  uses tools:
  ├─ python tools/confluence_tool.py list APP_SPACE
  │       → returns page list JSON
  ├─ python tools/confluence_tool.py read PAGE_ID
  │       → returns page plain text
  │
  │  [Copilot reads, triages, extracts knowledge, builds knowledge.json]
  │
  ├─ python tools/build_drawio.py --knowledge ... --example ...
  │       → writes output/<app>_flow.xml
  │
  └─ python tools/build_dashboard.py --knowledge ... --example ... --flow-xml ...
          → writes output/<app>_dashboard.json
```

All LLM reasoning (triage, knowledge extraction, layout planning) is done by
Copilot itself. The Python tools only handle REST API calls and file assembly —
they contain no LLM calls.

---

## Setup (do this once)

### 1. Clone and open in VS Code

```bash
git clone <repo-url>
cd grafana-dashboard-agent
code .
```

### 2. Install Python dependencies

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Configure Confluence credentials

```bash
cp .env.example .env
```

Edit `.env` and fill in:

```
CONFLUENCE_BASE_URL=https://yourcompany.atlassian.net/wiki
CONFLUENCE_USERNAME=your.email@company.com
CONFLUENCE_API_TOKEN=your-api-token-here
```

Generate your Confluence API token at:
https://id.atlassian.com/manage-profile/security/api-tokens

### 4. Prepare a reference dashboard JSON

Export any existing Grafana dashboard as JSON
(Dashboard → Share → Export → Save to file).
Place it somewhere accessible, e.g. `examples/reference_dashboard.json`.

---

## Using the Agent in VS Code

### Load the agent

1. Open Copilot Chat (`Ctrl+Alt+I`)
2. Click the **agent selector button** at the top of the chat input
3. Select **Grafana Dashboard Agent** from the list

### Example prompts

```
Generate a Grafana dashboard for the PaymentApp application.
Confluence space: PAYMENT_APP, RCA space: PAYMENT_RCA
Reference dashboard: ./examples/reference_dashboard.json
```

```
Run the dashboard agent for space PAYMENT_APP, RCA space PAYMENT_RCA,
reference dashboard at ./examples/ref.json
```

The agent will guide you through the entire workflow step by step,
asking for middleware icons when needed and reporting where the output files are.

### Import into Grafana

1. Grafana → **Dashboards → Import**
2. Upload `./output/<app>_dashboard.json`

---

## Project Structure

```
.github/agents/
  grafana-dashboard-agent.md   ← Agent definition (loaded by VS Code Copilot)

tools/
  confluence_tool.py           ← CLI: list / read Confluence pages
  build_drawio.py              ← CLI: build DrawIO XML from knowledge JSON
  build_dashboard.py           ← CLI: assemble Grafana dashboard JSON

agent/
  config.py                    ← Confluence credentials config
  state.py                     ← Pydantic data models
  tools/
    confluence.py              ← Confluence REST API client
    drawio_builder.py          ← DrawIO XML generation logic
    grafana_builder.py         ← Grafana panel assembly logic

.env.example                   ← Credential template
requirements.txt               ← Python dependencies (no anthropic)
```

---

## Inputs

| Input | How to provide |
|---|---|
| App Confluence space key | Tell the agent in chat |
| RCA Confluence space key | Tell the agent in chat |
| Reference dashboard JSON | Tell the agent the file path |
| Middleware SVG/PNG icons | The agent will ask; save them to `./svgs/` |

---

## Outputs

| File | Description |
|---|---|
| `output/<app>_dashboard.json` | Complete Grafana dashboard — import via Dashboards → Import |
| `output/<app>_flow.xml` | DrawIO mxGraphModel XML for the flow diagram |
| `output/knowledge.json` | Extracted app knowledge (reusable / editable) |

---

## Extending the Agent

| What to change | Where |
|---|---|
| Agent instructions / workflow | `.github/agents/grafana-dashboard-agent.md` |
| DrawIO layout algorithm | `agent/tools/drawio_builder.py` |
| Grafana panel templates | `agent/tools/grafana_builder.py` |


