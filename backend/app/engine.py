"""J.A.R.V.I.S. Nexus — deterministic correlation, root-cause and impact engine."""
import itertools, math, time, uuid
from collections import defaultdict, deque

SEV_W = {"INFO": 0.0, "WARN": 0.4, "ERROR": 0.75, "CRITICAL": 1.0}
WINDOW_S = 300          # correlation sliding window
Z_THRESHOLD = 3.0       # anomaly threshold (3 sigma)

SERVICES = ["database", "bank-simulator", "auth-service", "transaction-service", "payment-api"]
DEPENDS_ON = {  # service -> what it depends on
    "payment-api": ["auth-service", "transaction-service"],
    "auth-service": ["database"],
    "transaction-service": ["database", "bank-simulator"],
    "database": [],
    "bank-simulator": [],
}
SIG_WEIGHTS = {  # signature match (15%) — known failure signatures
    "db_conn_error": 1.0, "pool_exhausted": 1.0, "svc_timeout": 1.0,
    "auth_timeout": 1.0, "auth_failure": 1.0, "txn_failed": 0.9,
    "login_failed": 0.9, "high_latency": 0.5, "generic_warn": 0.15,
}

# ---------------------------------------------------------------- graph utils
def _undirected():
    adj = defaultdict(set)
    for svc, deps in DEPENDS_ON.items():
        for d in deps:
            adj[svc].add(d); adj[d].add(svc)
    return adj

_ADJ = _undirected()

def connected(a, b):
    if a == b: return True
    seen, stack = {a}, [a]
    while stack:
        for n in _ADJ[stack.pop()]:
            if n == b: return True
            if n not in seen: seen.add(n); stack.append(n)
    return False

def dependents_of(svc):
    """All services that transitively depend on `svc` (i.e. would be hurt by it)."""
    out, queue = set(), [svc]
    while queue:
        cur = queue.pop(0)
        for s, deps in DEPENDS_ON.items():
            if cur in deps and s not in out:
                out.add(s); queue.append(s)
    return out

# ------------------------------------------------------------------ baselines
class Baseline:
    """Rolling per (service,event_type) rate baseline + per service latency baseline."""
    def __init__(self):
        self.minutes = defaultdict(lambda: deque(maxlen=16))  # (svc,type)-> per-minute counts
        self.lat = defaultdict(lambda: deque(maxlen=300))     # svc -> latency samples
        self._cur_min = None

    def prefill(self, now):
        """Seed ~14 min of calm history so anomalies are detectable immediately."""
        import random
        rnd = random.Random(7)
        base_rate = {"database": .06, "auth-service": .07, "transaction-service": .06,
                     "payment-api": .08, "bank-simulator": .04}
        for m in range(14):
            t = now - (14 - m) * 60
            for svc in SERVICES:
                for et, p in (("high_latency", .35), ("req_ok", .5), ("txn_ok", .3)):
                    c = rnd.random() < p * base_rate[svc] * 8
                    self._bucket(svc, et, t, count=int(c) + (1 if rnd.random() < .15 else 0))
                self.lat[svc].extend(max(40, rnd.gauss(160, 35)) for _ in range(rnd.randint(2, 5)))

    def _bucket(self, svc, et, ts, count=1):
        m = int(ts // 60)
        dq = self.minutes[(svc, et)]
        if dq and dq[-1][0] == m: dq[-1][1] += count
        else: dq.append([m, count])

    def observe(self, ev):
        self._bucket(ev["service"], ev["event_type"], ev["ts_epoch"])
        if ev["metadata"].get("latency_ms") is not None:
            self.lat[ev["service"]].append(ev["metadata"]["latency_ms"])

    def rate_anomaly(self, svc, et, ts):
        dq = self.minutes[(svc, et)]
        m = int(ts // 60)
        cur = dq[-1][1] if dq and dq[-1][0] == m else 0
        hist = [c for mm, c in dq if mm < m][-10:]
        if len(hist) < 4: return 0.0, False
        mean = sum(hist) / len(hist)
        var = sum((x - mean) ** 2 for x in hist) / len(hist)
        sigma = max(math.sqrt(var), 0.6)
        z = (cur - mean - 1) / sigma
        return z, (cur >= 3 and z > Z_THRESHOLD)

    def latency_anomaly(self, svc, latency):
        hist = list(self.lat[svc])
        if len(hist) < 25 or latency is None: return 0.0, False
        mean = sum(hist) / len(hist)
        var = sum((x - mean) ** 2 for x in hist) / len(hist)
        sigma = max(math.sqrt(var), 10.0)
        z = (latency - mean) / sigma
        return z, z > Z_THRESHOLD

# --------------------------------------------------------------- normalization
_ID = itertools.count(1)

def normalize(raw_service, severity, event_type, message, ts_epoch, source, metadata=None):
    ts_epoch = float(ts_epoch)
    return {
        "id": f"ev-{next(_ID):05d}",
        "ts_epoch": ts_epoch,
        "timestamp": time.strftime("%H:%M:%S", time.gmtime(ts_epoch)),
        "service": raw_service.strip().lower().replace(" ", "-").replace("_", "-"),
        "severity": severity.upper(),
        "event_type": event_type.strip().lower(),
        "message": message,
        "source": source,
        "metadata": metadata or {},
    }

import json as _json, re as _re
LOG_PATTERNS = [
    # {"level":"ERROR","service":"payment-api","type":"high_latency","msg":"...","latency_ms":4200,"ts":1699}
    (_re.compile(r'\{.*\}'), lambda m: _try_json(m.group(0))),
    (_re.compile(r'(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\s+(?P<lvl>[A-Z]+)\s+(?P<svc>[\w.-]+)\s+(?P<msg>.*)'),
     lambda m: (m.group("ts"), m.group("lvl"), m.group("svc"), m.group("msg"))),
]
def _try_json(line):
    try:
        d = _json.loads(line)
        return (d.get("timestamp", d.get("ts", time.time())), d.get("level", "INFO"),
                d.get("service", "unknown"), d.get("message", ""), d)
    except Exception:
        return None

def parse_log_line(line):
    """Tiny regex adapters for heterogeneous log formats -> normalized schema."""
    line = line.strip()
    if not line: return None
    j = _try_json(line)
    if j:
        ts, lvl, svc, msg, d = j
        if isinstance(ts, str):
            try: ts = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
            except Exception: ts = time.time()
        return normalize(svc, lvl, d.get("event_type", "generic"), msg, ts, "file", d.get("metadata"))
    m = _re.match(r'(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})\s+\[(?P<lvl>[A-Z]+)\]\s+(?P<svc>[\w.-]+)\s+(?P<et>\w+)?\s*-?\s*(?P<msg>.*)', line)
    if m:
        ts = time.mktime(time.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S"))
        et = m.group("et") or ("high_latency" if "latency" in m.group("msg").lower() else "generic")
        return normalize(m.group("svc"), m.group("lvl"), et, m.group("msg"), ts, "file")
    return None

# ------------------------------------------------------------------- patterns
def match_pattern(root, ev_types, downstream_degraded):
    if root == "database" and "db_conn_error" in ev_types and downstream_degraded:
        return ("Connection pool exhaustion on Database",
                "Database is emitting connection errors while every dependent service degrades "
                "simultaneously — the classic signature of an exhausted connection pool.",
                ["Check active connections against the pool limit",
                 "Look for long-running queries holding connections",
                 "Review recent deploys touching the database layer",
                 "Consider a temporary pool increase with admin approval"])
    if root == "bank-simulator" and {"svc_timeout", "txn_failed"} & ev_types:
        return ("Bank Simulator timeout",
                "Timeouts originate at the Bank Simulator edge while the Database stays healthy, "
                "pointing to a slow/unresponsive bank endpoint rather than our own data layer.",
                ["Verify Bank Simulator endpoint health / circuit breaker state",
                 "Check network latency to the bank endpoint",
                 "Open the breaker to fail fast and queue retries",
                 "Engage the payments on-call if timeouts persist"])
    if root == "auth-service" and {"auth_timeout", "auth_failure"} & ev_types:
        return ("Auth Service outage",
                "Authentication failures start at Auth Service and cascade into the Payment API "
                "while Database and Bank Simulator remain healthy.",
                ["Check Auth Service instance health and restart unhealthy pods",
                 "Inspect recent auth-related deploys and config changes",
                 "Verify session store / token issuer availability",
                 "Enable login rate-limit shield while restoring service"])
    return (None, None, [])

# ---------------------------------------------------------------------- engine
class Incident:
    def __init__(self, inc_id, first_ts):
        self.id, self.events = inc_id, []
        self.first_ts, self.last_ts = first_ts, first_ts
        self.identified_at = None
        self.root_cause = None
        self.analysis = {}

    def to_dict(self):
        return {
            "id": self.id, "events": self.events[-200:], "first_ts": self.first_ts,
            "last_ts": self.last_ts, "root_cause": self.root_cause, **self.analysis,
        }

class Engine:
    def __init__(self):
        self.baseline = Baseline()
        self.active = None
        self.incidents = []          # finalized
        self.total_alerts = 0
        self.anomalous_alerts = 0

    # ---- ingest one normalized event; returns (is_new_anomaly, incident_changed)
    def ingest(self, ev, announce=True):
        self.total_alerts += 1
        self.baseline.observe(ev)
        anomalous, z_rate, z_lat = self._is_anomalous(ev)
        ev["anomalous"] = anomalous
        if anomalous:
            ev["z"] = round(max(z_rate, z_lat), 1)
            self.anomalous_alerts += 1
        changed = False
        if anomalous:
            self._correlate(ev)
            changed = True
        return anomalous, changed

    def _is_anomalous(self, ev):
        if ev["severity"] == "INFO":
            return False, 0.0, 0.0
        zr, ok_r = self.baseline.rate_anomaly(ev["service"], ev["event_type"], ev["ts_epoch"])
        zl, ok_l = self.baseline.latency_anomaly(ev["service"], ev["metadata"].get("latency_ms"))
        z = max(zr, zl)
        if ev["severity"] == "CRITICAL" and z > 0:
            return True, zr, zl
        return (ok_r or ok_l), zr, zl

    # ---- correlation: time window + graph connection
    def _correlate(self, ev):
        now = ev["ts_epoch"]
        if self.active and now - self.active.last_ts <= WINDOW_S:
            if any(connected(ev["service"], e["service"]) for e in self.active.events if e["anomalous"]):
                self.active.events.append(ev)
                self.active.last_ts = now
                if self.active.identified_at is None and len({e["service"] for e in self.active.events}) >= 2:
                    self.active.identified_at = now
                return
        if self.active:
            self.close_active()
        self.active = Incident(f"INC-{len(self.incidents)+1:03d}", now)
        self.active.events.append(ev)

    def close_active(self):
        if not self.active: return
        self._analyze(self.active)
        self.incidents.append(self.active)
        self.active = None

    # ---------------------------------------------------------- root cause etc.
    def _analyze(self, inc):
        anoms = [e for e in inc.events if e["anomalous"]]
        by_svc = defaultdict(list)
        for e in anoms: by_svc[e["service"]].append(e)
        onsets = {s: min(e["ts_epoch"] for e in evs) for s, evs in by_svc.items()}
        t_min, t_max = min(onsets.values()), max(onsets.values())
        span = max(t_max - t_min, 1e-6)
        affected = set(onsets)
        ev_types = {e["event_type"] for e in anoms}
        max_z = max((e.get("z", 1) for e in anoms), default=1) or 1

        cands = []
        for s in affected:
            onset_s = 1 - (onsets[s] - t_min) / span
            cover = len(dependents_of(s) & affected) / max(len(affected) - 1, 1)
            sig = max((SIG_WEIGHTS.get(e["event_type"], SIG_WEIGHTS["generic_warn"]) *
                       (1.0 if e["severity"] in ("ERROR", "CRITICAL") else 0.5)
                       for e in by_svc[s]), default=0.1)
            mag = min(max(e.get("z", 1) for e in by_svc[s]) / max_z, 1.0)
            score = 0.35 * onset_s + 0.30 * cover + 0.20 * mag + 0.15 * sig
            ev_ids = [e["id"] for e in by_svc[s]][:6]
            for_e = []
            if onset_s > .6: for_e.append("earliest anomaly onset")
            if cover > .5: for_e.append(f"upstream of {int(cover*100)}% of affected services")
            if mag > .6: for_e.append("extreme deviation from baseline")
            if sig > .6: for_e.append("matches known failure signature")
            against = []
            if cover < .3: against.append("few or no affected dependents")
            if onset_s < .3: against.append("failed after its dependents")
            if sig < .3: against.append("generic warning, no failure signature")
            cands.append({"service": s, "score": round(score, 3),
                          "formula": {"earliest_onset": round(onset_s, 2),
                                      "downstream_coverage": round(cover, 2),
                                      "anomaly_magnitude": round(mag, 2),
                                      "signature_match": round(sig, 2)},
                          "evidence_event_ids": ev_ids, "for": for_e, "against": against,
                          "first_anomaly": onsets[s], "alerts": len(by_svc[s]),
                          "max_severity": max((e["severity"] for e in by_svc[s]),
                                              key=lambda x: SEV_W[x])})
        total = sum(c["score"] for c in cands) or 1.0
        for c in cands:
            c["evidence_share"] = round(c["score"] / total * 100, 1)
            c["confidence"] = round(c["score"] * 100, 1)  # score itself is 0..1
        cands.sort(key=lambda c: -c["score"])
        root = cands[0]
        inc.root_cause = root["service"]
        inc.identified_at = inc.identified_at or anoms[-1]["ts_epoch"]

        # impact: walk graph down from root
        direct, downstream = set(), set()
        for d in DEPENDS_ON.get(root["service"], []):
            pass  # root's own deps are upstream, not affected
        dep_children = {s for s, deps in DEPENDS_ON.items() if root["service"] in deps}
        direct = dep_children & affected
        reach = dependents_of(root["service"])
        downstream = (reach & affected) - direct
        counts = defaultdict(int)
        for e in anoms:
            counts[e["event_type"]] += 1
        failed_logins = counts.get("auth_timeout", 0) + counts.get("login_failed", 0)
        failed_txns = counts.get("txn_failed", 0)
        slow_reqs = counts.get("high_latency", 0)
        users = failed_logins * 3 + failed_txns * 2 + int(slow_reqs * 1.5)

        downstream_degraded = bool(direct or downstream)
        name, why, actions = match_pattern(root["service"], ev_types, downstream_degraded)

        # timeline links: each service's first anomaly -> likely upstream trigger
        timeline = []
        trig = min(anoms, key=lambda e: e["ts_epoch"])
        for s in sorted(affected, key=lambda s: onsets[s]):
            first = min(by_svc[s], key=lambda e: e["ts_epoch"])
            link = None
            if s != trig["service"]:
                causal = {root["service"]} | dependents_of(root["service"])
                ups = [u for u in affected if u != s and u in causal
                       and u in _upstream_closure(s) and onsets[u] <= onsets[s]]
                if ups:
                    link = max(ups, key=lambda u: onsets[u])
            timeline.append({"service": s, "at": first["ts_epoch"], "event_id": first["id"],
                             "event_type": first["event_type"], "severity": first["severity"],
                             "trigger": s == trig["service"], "caused_by": link})
        timeline.sort(key=lambda t: t["at"])

        inc.analysis = {
            "candidates": cands,
            "services_affected": sorted(affected, key=lambda s: onsets[s]),
            "impact": {"root": root["service"], "directly_affected": sorted(direct),
                       "downstream": sorted(downstream),
                       "failed_logins": failed_logins, "failed_transactions": failed_txns,
                       "slow_requests": slow_reqs, "est_users_impacted": users,
                       "anomalous_alerts": len(anoms)},
            "pattern": {"name": name, "why": why, "actions": actions},
            "timeline": timeline,
            "unrelated_noise": sorted(s for s in affected
                                      if s != root["service"] and s not in dependents_of(root["service"])),
            "summary": (f"{len(anoms)} anomalous alerts across {len(affected)} services collapsed "
                        f"into 1 incident. Suspected root cause: {root['service']} "
                        f"({root['confidence']}% confidence)."),
        }

    def metrics(self):
        incs = len(self.incidents) + (1 if self.active else 0)
        nr = round((1 - incs / self.total_alerts) * 100, 1) if self.total_alerts else 0.0
        tt = None
        inc = self.active or (self.incidents[-1] if self.incidents else None)
        if inc and inc.identified_at:
            tt = round(inc.identified_at - inc.first_ts, 1)
        return {"alerts_in": self.total_alerts, "incidents_out": incs,
                "noise_reduction_pct": nr, "time_to_root_cause_s": tt}

def _upstream_closure(svc):
    """svc + everything it depends on, transitively."""
    out, stack = set(), [svc]
    while stack:
        for d in DEPENDS_ON.get(stack.pop(), []):
            if d not in out: out.add(d); stack.append(d)
    return out

# --------------------------------------------------------------------- report
def report_markdown(inc_dict):
    imp = inc_dict["impact"]; root = inc_dict["candidates"][0]
    sev = root["max_severity"]
    lines = [
        f"# Incident Report — {inc_dict['id']}",
        f"_Generated by J.A.R.V.I.S. Nexus — deterministic engine findings, AI-explained._",
        "",
        f"**Severity:** {sev}  |  **Window:** {inc_dict['first_ts']:.0f} → {inc_dict['last_ts']:.0f} (epoch)",
        "",
        "## Summary", inc_dict["summary"], "",
        f"## Root cause — {root['service']} ({root['confidence']}% confidence)",
    ]
    p = inc_dict.get("pattern") or {}
    if p.get("name"):
        lines += [f"**Pattern:** {p['name']}", "", p["why"], ""]
    lines += ["**Score formula** (weights: onset 35% · coverage 30% · magnitude 20% · signature 15%)",
              "", "```",
              f"earliest_onset       {root['formula']['earliest_onset']}",
              f"downstream_coverage  {root['formula']['downstream_coverage']}",
              f"anomaly_magnitude    {root['formula']['anomaly_magnitude']}",
              f"signature_match      {root['formula']['signature_match']}",
              "```", "",
              f"Evidence events: {', '.join(root['evidence_event_ids'])}", "",
              "## Alternative hypotheses"]
    for alt in inc_dict["candidates"][1:3]:
        lines.append(f"- **{alt['service']}** ({alt['confidence']}%) — for: {', '.join(alt['for']) or '—'}; "
                     f"against: {', '.join(alt['against']) or '—'}")
    lines += ["", "## Impact",
              f"- Directly affected: {', '.join(imp['directly_affected']) or '—'}",
              f"- Downstream: {', '.join(imp['downstream']) or '—'}",
              f"- Failed logins: {imp['failed_logins']} · Failed transactions: {imp['failed_transactions']} "
              f"· Slow requests: {imp['slow_requests']}",
              f"- Estimated users impacted: ~{imp['est_users_impacted']}", "",
              "## Timeline"]
    for t in inc_dict["timeline"]:
        mark = "⚡ TRIGGER" if t["trigger"] else f"← caused by {t['caused_by']}" if t["caused_by"] else ""
        lines.append(f"- `{t['event_id']}` {t['service']} · {t['event_type']} ({t['severity']}) {mark}")
    if inc_dict.get("unrelated_noise"):
        lines += ["", "## Unrelated noise (ranked out)",
                  f"- {', '.join(inc_dict['unrelated_noise'])} — noisy but no affected dependents / wrong onset."]
    if p.get("actions"):
        lines += ["", "## Recommended actions (priority order)"]
        lines += [f"{i}. {a}" for i, a in enumerate(p["actions"], 1)]
    lines += ["", "---", "All findings produced by a deterministic engine; every score is a visible formula."]
    return "\n".join(lines)
