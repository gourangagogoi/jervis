"""J.A.R.V.I.S. Nexus API — ingest, correlate, analyze, explain. Serves the dashboard."""
import asyncio, json, os, time
from pathlib import Path

try:  # load .env if present (project root or backend/) — optional dependency
    from dotenv import load_dotenv
    for p in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
        load_dotenv(p / ".env")
except ImportError:
    pass

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import engine as E, simulator as S, explain
from .db import DB

app = FastAPI(title="J.A.R.V.I.S. Nexus")
db = DB(os.environ.get("NEXUS_DB", "nexus.db"))
EN = E.Engine()

class Hub:
    def __init__(self): self.clients = set()
    async def broadcast(self, msg):
        dead = []
        data = json.dumps(msg)
        for ws in list(self.clients):
            try: await ws.send_text(data)
            except Exception: dead.append(ws)
        for ws in dead: self.clients.discard(ws)

HUB = Hub()

@app.on_event("startup")
async def startup():
    EN.baseline.prefill(time.time())
    # a little calm history for the feed
    now = time.time() - 60
    for ev in S.normal_tick(now)[:6]:
        EN.ingest(ev)
    asyncio.create_task(loop())

async def loop():
    """Simulated live stream: 1s ticks of calm traffic + active fault cascade."""
    while True:
        try:
            fault, elapsed = S.fault_state()
            evs = S.normal_tick(time.time()) + S.fault_tick(time.time(), elapsed)
            for ev in evs:
                anomalous, changed = EN.ingest(ev)
                db.save_event(ev, anomalous)
                await HUB.broadcast({"type": "event", "event": ev, "metrics": EN.metrics()})
                if changed and EN.active:
                    EN._analyze(EN.active)
                    await HUB.broadcast({"type": "incident", "incident": EN.active.to_dict()})
            if not fault and EN.active:
                EN.close_active()
                await HUB.broadcast({"type": "incident_closed",
                                     "incident": EN.incidents[-1].to_dict(),
                                     "metrics": EN.metrics()})
            await asyncio.sleep(1)
        except Exception as e:
            print("loop error:", e)
            await asyncio.sleep(1)

# ------------------------------------------------------------------- REST API
@app.get("/api/state")
def state():
    inc = EN.active.to_dict() if EN.active else (EN.incidents[-1].to_dict() if EN.incidents else None)
    return {"graph": {"services": E.SERVICES, "depends_on": E.DEPENDS_ON},
            "incident": inc, "metrics": EN.metrics(),
            "fault": S.fault_state()[0],
            "events": (EN.active.events[-40:] if EN.active else [])[-40:]}

@app.get("/api/incidents")
def incidents():
    return [i.to_dict() for i in EN.incidents]

@app.get("/api/incidents/{inc_id}")
def get_incident(inc_id: str):
    for i in [EN.active] + EN.incidents:
        if i and i.id == inc_id: return i.to_dict()
    return JSONResponse({"error": "not found"}, 404)

@app.get("/api/incidents/{inc_id}/report.md")
def report(inc_id: str):
    for i in [EN.active] + EN.incidents:
        if i and i.id == inc_id:
            md = E.report_markdown(i.to_dict())
            return PlainTextResponse(md, media_type="text/markdown",
                headers={"Content-Disposition": f'attachment; filename="{inc_id}-report.md"'})
    return JSONResponse({"error": "not found"}, 404)

@app.post("/api/inject/{scenario}")
async def inject(scenario: str):
    if scenario == "clear":
        S.clear()
        EN.close_active()
        return {"ok": True, "fault": None}
    name = S.inject(scenario)
    await HUB.broadcast({"type": "injected", "fault": name})
    return {"ok": True, "fault": name}

# ingestion from other sources (multi-data-source feature)
@app.post("/api/ingest")
async def ingest(body: dict):
    made = 0
    for raw in body.get("events", []):
        ev = E.normalize(raw.get("service", "unknown"), raw.get("severity", "INFO"),
                         raw.get("event_type", "generic"), raw.get("message", ""),
                         raw.get("timestamp") or time.time(), "api", raw.get("metadata"))
        anomalous, changed = EN.ingest(ev)
        db.save_event(ev, anomalous); made += 1
        if changed and EN.active:
            EN._analyze(EN.active)
            await HUB.broadcast({"type": "incident", "incident": EN.active.to_dict()})
    return {"ok": True, "ingested": made}

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    text = (await file.read()).decode("utf-8", "replace")
    parsed = [p for p in (E.parse_log_line(l) for l in text.splitlines()) if p]
    for ev in parsed:
        EN.ingest(ev)
        db.save_event(ev, ev.get("anomalous", False))
    if EN.active: EN._analyze(EN.active)
    return {"ok": True, "parsed": len(parsed)}

@app.post("/api/chat")
async def chat(body: dict):
    return explain.answer(body.get("question", ""), EN)

# ------------------------------------------------------------------- WebSocket
@app.websocket("/ws")
async def ws(ws: WebSocket):
    await ws.accept()
    HUB.clients.add(ws)
    try:
        while True: await ws.receive_text()
    except WebSocketDisconnect:
        HUB.clients.discard(ws)

# ------------------------------------------------------------------- frontend
STATIC = Path(__file__).resolve().parent.parent / "static"
if STATIC.exists():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="ui")
