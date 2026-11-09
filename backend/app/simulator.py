"""Simulated payment platform: calm traffic + three injectable faults + a red herring."""
import random, time
from .engine import normalize, SERVICES

rnd = random.Random(42)
MSG = {
    "db_conn_error": ("Database connection pool exhausted", "CRITICAL"),
    "high_latency": ("Request latency exceeded the payment API SLO", "ERROR"),
    "auth_timeout": ("Token validation timed out waiting for Auth Service", "ERROR"),
    "auth_failure": ("Auth Service rejected token validation", "ERROR"),
    "login_failed": ("Login request failed while contacting Auth Service", "ERROR"),
    "txn_failed": ("Payment authorization failed after a dependency timeout", "ERROR"),
    "svc_timeout": ("Partner request exceeded the upstream timeout", "ERROR"),
    "req_ok": ("Payment API request completed", "INFO"),
    "txn_ok": ("Payment transaction settled", "INFO"),
    "cache_hit": ("Response served from cache", "INFO"),
}
ACTIVE = {"fault": None, "start": 0, "duration": 90}

def inject(scenario, now=None):
    assert scenario in ("db_pool_exhaustion", "bank_simulator_timeout", "auth_outage", None)
    ACTIVE["fault"], ACTIVE["start"], ACTIVE["duration"] = scenario, time.time(), 90
    return ACTIVE["fault"]

def clear():
    ACTIVE["fault"] = None

def fault_state():
    if not ACTIVE["fault"]:
        return None, 0
    el = time.time() - ACTIVE["start"]
    return (None, 0) if el > ACTIVE["duration"] else (ACTIVE["fault"], el)

def _ev(svc, et, ts, latency=None, code=None, src="stream"):
    sev, msg = MSG.get(et, ("event", "INFO"))[1], MSG.get(et, (et, "event"))[0]
    md = {}
    if latency is not None:
        latency = max(1, int(latency))
        md["latency_ms"] = latency
        if et == "high_latency":
            md["slo_ms"] = 500
            msg = f"p95 latency reached {latency} ms (500 ms SLO)"
        elif et in ("auth_timeout", "svc_timeout"):
            md["timeout_ms"] = 2000 if et == "auth_timeout" else 5000
            msg = f"{msg} after {latency} ms"
    if et == "db_conn_error":
        md.update({"pool_size": 120, "active_connections": 120, "waiting_requests": 14})
        msg = "Connection pool exhausted: 120/120 active; 14 requests waiting"
    if code:
        md["error_code"] = code
        msg = f"{msg} ({code})"
    return normalize(svc, sev, et, msg, ts, src, md)

def normal_tick(now, n_events=True):
    """Calm background traffic — builds the baseline."""
    out = []
    for svc in SERVICES:
        if rnd.random() < .35:
            et = rnd.choice(["req_ok", "req_ok", "txn_ok", "cache_hit", "high_latency"])
            lat = rnd.gauss(170, 40) if et == "high_latency" else rnd.gauss(120, 30)
            if et == "high_latency":
                # Routine latency samples stay informational; only abnormal latency is an error.
                out.append(normalize(svc, "INFO", "req_ok",
                                     f"Request completed in {int(max(lat, 40))} ms",
                                     now, "stream", {"latency_ms": int(max(lat, 40))}))
            else:
                out.append(_ev(svc, et, now, latency=max(lat, 40)))
        if rnd.random() < .015:  # rare baseline warn
            out.append(normalize(svc, "WARN", "generic_warn",
                                 "Intermittent upstream retry recovered on second attempt",
                                 now, "stream"))
    return out

def fault_tick(now, elapsed):
    """Events for the active fault, cascading over time. Returns [] when calm."""
    f = ACTIVE["fault"]
    if not f: return []
    out = []
    if f == "db_pool_exhaustion":
        for _ in range(rnd.randint(2, 4)):
            out.append(_ev("database", "db_conn_error", now, latency=rnd.gauss(4500, 900), code="POOL-EXH"))
        if elapsed > 3:
            for _ in range(rnd.randint(1, 2)):
                out.append(_ev("auth-service", "auth_timeout", now, latency=rnd.gauss(2200, 400), code="AUTH-504"))
            out.append(_ev("transaction-service", "txn_failed", now, code="TXN-500"))
        if elapsed > 6:
            for _ in range(rnd.randint(1, 3)):
                out.append(_ev("payment-api", "high_latency", now, latency=rnd.gauss(5200, 1100), code="API-504"))
            if rnd.random() < .5:
                out.append(_ev("transaction-service", "db_conn_error", now, code="DB-PROP"))
        # red herring: noisy but unrelated Bank Simulator warnings
        if elapsed > 5 and rnd.random() < .6:
            out.append(normalize("bank-simulator", "WARN", "high_latency",
                                 "Partner cache eviction rate elevated; authorization latency remains below timeout",
                                 now, "stream", {"latency_ms": int(rnd.gauss(950, 120)),
                                                  "cache_evictions_per_min": rnd.randint(160, 240)}))
    elif f == "bank_simulator_timeout":
        for _ in range(rnd.randint(2, 3)):
            out.append(_ev("bank-simulator", "svc_timeout", now, latency=rnd.gauss(6100, 900), code="BANK-504"))
        if elapsed > 3:
            out.append(_ev("transaction-service", "txn_failed", now, code="TXN-504"))
            if rnd.random() < .7:
                out.append(_ev("transaction-service", "svc_timeout", now, latency=rnd.gauss(3000, 500)))
        if elapsed > 6 and rnd.random() < .8:
            out.append(_ev("payment-api", "high_latency", now, latency=rnd.gauss(4600, 800), code="API-504"))
    elif f == "auth_outage":
        for _ in range(rnd.randint(2, 3)):
            out.append(_ev("auth-service", "auth_timeout", now, latency=rnd.gauss(3800, 700), code="AUTH-000"))
        if elapsed > 3 and rnd.random() < .9:
            out.append(_ev("payment-api", "login_failed", now, code="API-401"))
        if elapsed > 6 and rnd.random() < .7:
            out.append(_ev("payment-api", "high_latency", now, latency=rnd.gauss(3400, 600), code="API-504"))
    return out

def burst(scenario, t0, duration=45, step=1.0):
    """Synchronous event burst for unit tests: normal baseline noise + full cascade."""
    evs = []
    for i in range(int(duration / step)):
        t = t0 + i * step
        ACTIVE["fault"], ACTIVE["start"], ACTIVE["duration"] = scenario, t0, duration
        evs.extend(normal_tick(t, n_events=False))
        evs.extend(fault_tick(t, t - t0))
    ACTIVE["fault"] = None
    return evs
