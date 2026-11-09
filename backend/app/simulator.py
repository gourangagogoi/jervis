"""Simulated payment platform: calm traffic + three injectable faults + a red herring."""
import random, time
from .engine import normalize, SERVICES

rnd = random.Random(42)
MSG = {
    "db_conn_error": ("connection pool exhausted", "CRITICAL"),
    "high_latency": ("response time above SLO", "ERROR"),
    "auth_timeout": ("authentication timeout", "ERROR"),
    "auth_failure": ("token validation failed", "ERROR"),
    "login_failed": ("login attempt failed", "ERROR"),
    "txn_failed": ("transaction failed", "ERROR"),
    "svc_timeout": ("upstream timeout", "ERROR"),
    "req_ok": ("request handled", "INFO"),
    "txn_ok": ("transaction settled", "INFO"),
    "cache_hit": ("cache hit", "INFO"),
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
    if latency is not None: md["latency_ms"] = int(latency)
    if code: md["error_code"] = code
    return normalize(svc, sev, et, f"{svc}: {msg}", ts, src, md)

def normal_tick(now, n_events=True):
    """Calm background traffic — builds the baseline."""
    out = []
    for svc in SERVICES:
        if rnd.random() < .35:
            et = rnd.choice(["req_ok", "req_ok", "txn_ok", "cache_hit", "high_latency"])
            lat = rnd.gauss(170, 40) if et == "high_latency" else rnd.gauss(120, 30)
            out.append(_ev(svc, et, now, latency=max(lat, 40)))
        if rnd.random() < .015:  # rare baseline warn
            out.append(normalize(svc, "WARN", "generic_warn", f"{svc}: transient hiccup", now, "stream"))
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
                                 "bank-simulator: cache eviction spike", now, "stream",
                                 {"latency_ms": int(rnd.gauss(950, 120))}))
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
