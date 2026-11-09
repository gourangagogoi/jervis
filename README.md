# J.A.R.V.I.S. Nexus

**From 47 scattered alerts to one explained incident.**

Modern platforms emit thousands of alerts. A slow API, failed logins and failed payments
look like three problems — when one failing database caused all of them. J.A.R.V.I.S. Nexus
groups related events into a single incident, finds the root cause by reasoning over a
service dependency graph, shows who is affected, and writes the report with fix steps.
**A deterministic engine produces every finding. The AI only explains them.**

## Run it (Python only — no Docker, no Node)

Requires **Python 3.10+** (check: `python --version`) and nothing else —
the dashboard is prebuilt and served by the backend.

### 1. Open a terminal in this folder

In VSCode: open this folder, then press `` Ctrl + ` `` to open the terminal.
All commands below assume you are inside the **`backend/`** folder.

### 2. Create a virtual environment and install dependencies (one time)

**Windows PowerShell** (VSCode default):

```powershell
cd backend
python -m venv .venv

# If activation fails with "running scripts is disabled", run this ONCE, answer Y:
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

.\.venv\Scripts\activate
pip install -r requirements.txt
```

**Linux / macOS / WSL:**

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> No-activation alternative (works even if you skip the policy step above):
> `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`

### 3. Start the server

```powershell
# venv active:
uvicorn app.main:app --reload

# or without activation (Windows):
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

You should see `Uvicorn running on http://127.0.0.1:8000`.

### 4. Open the dashboard

**http://localhost:8000** — that's it.

### 5. (Optional) Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ -v
```

All 5 tests must pass (they verify all 3 fault scenarios diagnose correctly).

### Next time

Only two commands — the venv is already set up:

```powershell
cd backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

#### Optional: configuration via `.env`

Copy the template and edit it (`.env` is git-ignored, so keys stay private):

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

| Variable | What it does |
|---|---|
| `OPENAI_API_KEY` | Enables LLM-powered chat. Without it, the deterministic template assistant is used — the demo never breaks |
| `LLM_MODEL` | Override the model (default `gpt-4o-mini`) |
| `NEXUS_DB` | SQLite file location (default `backend/nexus.db`) |

Everything is optional — the app runs fully with no `.env` at all.

## Troubleshooting

| Problem | Fix |
|---|---|
| `uvicorn: command not found` / not recognized | venv not active → `.venv\Scripts\activate` (or use the `.\.venv\Scripts\python.exe -m uvicorn ...` form) |
| PowerShell blocks `Activate.ps1` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, answer Y |
| Port 8000 already in use | `uvicorn app.main:app --reload --port 8001` |
| `Form data requires "python-multipart"` | it's in requirements.txt — make sure you installed into the venv (step 2), not system Python |

### Optional: grounded LLM chat (works without it)

The chat assistant always works using deterministic template answers grounded in engine
findings. To route it through an LLM instead:

**PowerShell:** `$env:OPENAI_API_KEY="sk-..."` — **bash:** `export OPENAI_API_KEY=sk-...`

then start the server normally. The model receives only the structured findings JSON,
must cite event IDs, and the template takes over if the API fails — the demo never breaks.

## 3-minute demo

1. Dashboard loads calm — **ALL SYSTEMS NOMINAL**, alerts flowing into the feed.
2. Click **DB connection pool exhaustion**. Alerts flood in from 4 services; the feed
   shows the raw noise (anomalous ones marked with σ-score).
3. Watch the **metrics header**: dozens of alerts in → **1 incident out** (~98%+ noise reduction).
4. **Dependency graph**: Database turns red as root cause, Auth/Transaction orange as
   direct victims, Payment API yellow downstream — the cascade is visible at a glance.
   The Bank Simulator stays grey with an "UNRELATED NOISE" tag (the deliberate red
   herring — noisy, but the engine ranks by evidence, not volume).
5. **Root cause panel**: confidence ring, the visible score formula (onset 35% ·
   downstream coverage 30% · magnitude 20% · signature 15%), alternative hypotheses
   with evidence for/against, estimated user impact, prioritized fix steps.
6. **Timeline**: the cascade order, trigger marked ⚡, each degradation linked to its
   upstream cause.
7. **ASK J.A.R.V.I.S.** (bottom right): "What should I check first?" — answer cites event IDs.
8. **↓ Export incident report** — full Markdown report downloads.
9. **Resolve / clear**, then inject **Bank Simulator timeout** → engine blames the Bank
   Simulator, *not* the database. Then **Auth outage** → blames Auth Service. Three
   different faults, three different correct diagnoses — nothing is hardcoded.

## Architecture (5-stage pipeline)

```
Ingest → Normalize → Correlate → Analyze → Explain
```

| Stage | What happens |
|---|---|
| **Ingest** | Simulated live stream (1 s ticks), `POST /api/ingest` (JSON), `POST /api/upload` (log files — JSON + plaintext adapters) |
| **Normalize** | Every event → one schema: `id, timestamp, service, event_type, severity, message, source, metadata` |
| **Correlate** | Group into one incident: 5-min sliding window + graph-connected services + shared error signature. 3σ anomaly detection vs rolling per-service baselines ignores normal INFO traffic |
| **Analyze** | Root-cause scoring (weighted formula), impact walk over the dependency graph, cascade timeline, known-pattern library |
| **Explain** | Template report/chat from structured findings; optional LLM with citation guardrails |

**Root-cause scoring** — per candidate service, 0–1 score:
- Earliest onset (35%): how early its first anomaly appeared
- Downstream coverage (30%): share of affected services that depend on it
- Anomaly magnitude (20%): deviation from baseline
- Signature match (15%): matches a known failure pattern (pool exhaustion, timeouts, auth failure)

A service that is noisy but has no affected dependents, or that fails *after* its
dependents, scores low — the engine never blames whichever alert was loudest.

## Simulated platform

```
Payment API ──▶ Auth Service ──▶ Database
      │                               ▲
      └──▶ Transaction Service ───────┘
                    │
                    ▼
             Bank Simulator
```

| Injectable fault | Correct diagnosis |
|---|---|
| DB connection pool exhaustion (+ red-herring Bank noise) | Database — herring ranked out |
| Bank Simulator timeout | Bank Simulator — not the database |
| Auth outage | Auth Service |

## Project layout

```
backend/
  app/engine.py      # correlation, baselines, root-cause scoring, impact, report
  app/simulator.py   # calm traffic + 3 fault cascades + red herring
  app/explain.py     # grounded chat (LLM optional, template fallback)
  app/main.py        # FastAPI: REST + WebSocket + static dashboard
  app/db.py          # SQLite (swappable for PostgreSQL)
  static/            # prebuilt React dashboard (served at /)
  tests/             # scenario tests: all 3 faults diagnose correctly
frontend/            # React + React Flow source (only needed to modify the UI)
```

## Tech stack

Python · FastAPI · WebSockets · SQLite | React · React Flow (dark minimalism UI)

## Rebuilding the dashboard (only if you change the UI)

```bash
cd frontend
npm install
npm run build
cp -r dist/* ../backend/static/    # Windows: xcopy /E dist ..\backend\static\
```

## Judge FAQ

- **How is confidence computed?** A transparent weighted formula over onset, downstream
  coverage, magnitude and signature match — shown in the UI next to every diagnosis.
- **Can the AI hallucinate?** It only sees structured findings, must cite event IDs, and
  a deterministic template takes over on any failure.
- **Is it hardcoded?** No — the injection panel produces three different correct
  diagnoses, and the red herring is ranked out by evidence.
- **Will it scale?** Correlation is windowed and incremental; production adds Kafka +
  PostgreSQL and real integrations (Prometheus, CloudWatch).
- **How is this different from Datadog/PagerDuty?** They group alerts; Nexus reasons
  over dependencies, separates cause from symptom, explains with evidence, and writes
  the report.
