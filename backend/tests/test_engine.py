"""Unit tests: the engine must reach a different, correct root cause for each fault,
and must rank by evidence, not by alert volume (red herring)."""
import time, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import engine as E, simulator as S


def run_scenario(scenario):
    t0 = time.time() - 1200
    eng = E.Engine()
    eng.baseline.prefill(t0)
    evs = S.burst(scenario, t0 + 900, duration=45)
    for ev in evs:
        eng.ingest(ev)
    eng.close_active()
    assert eng.incidents, f"{scenario}: no incident built"
    return eng.incidents[0].to_dict()


def test_db_pool_exhaustion_blames_database_and_ignores_herring():
    inc = run_scenario("db_pool_exhaustion")
    assert inc["root_cause"] == "database", inc["root_cause"]
    # red herring: bank-simulator noise must NOT be the root cause
    assert inc["candidates"][0]["service"] != "bank-simulator"
    names = {c["service"] for c in inc["candidates"]}
    assert "bank-simulator" in names, "herring should appear as ranked-out candidate"
    assert inc["impact"]["directly_affected"], "auth/txn should be direct victims"
    assert inc["pattern"]["name"] == "Connection pool exhaustion on Database"
    # at least one service flagged as unrelated noise
    assert "bank-simulator" in inc["unrelated_noise"]


def test_bank_timeout_blames_bank_simulator_not_database():
    inc = run_scenario("bank_simulator_timeout")
    assert inc["root_cause"] == "bank-simulator", inc["root_cause"]
    cands = {c["service"]: c["confidence"] for c in inc["candidates"]}
    assert cands.get("bank-simulator", 0) > cands.get("database", 0)


def test_auth_outage_blames_auth_service():
    inc = run_scenario("auth_outage")
    assert inc["root_cause"] == "auth-service", inc["root_cause"]
    assert inc["pattern"]["name"] == "Auth Service outage"


def test_noise_reduction_and_explainable_formula():
    inc = run_scenario("db_pool_exhaustion")
    top = inc["candidates"][0]
    f = top["formula"]
    expected = round((0.35 * f["earliest_onset"] + 0.30 * f["downstream_coverage"]
                      + 0.20 * f["anomaly_magnitude"] + 0.15 * f["signature_match"]) * 100, 1)
    assert abs(expected - top["confidence"]) < 0.5  # confidence is a visible formula
    assert top["confidence"] > 50, "strong root cause should have majority confidence"
    assert len(inc["timeline"]) >= 3 and inc["timeline"][0]["trigger"]
    assert top["evidence_event_ids"], "every claim must cite event IDs"


def test_correlation_collapses_alerts_into_one_incident():
    eng = E.Engine()
    t0 = time.time() - 600
    eng.baseline.prefill(t0)
    evs = S.burst("db_pool_exhaustion", t0 + 300, duration=40)
    for ev in evs:
        eng.ingest(ev)
    eng.close_active()
    total_anomalous = sum(1 for e in eng.incidents[0].events if e["anomalous"])
    assert len(eng.incidents) == 1, "cascade must be ONE incident, not many"
    assert total_anomalous >= 10, "many alerts -> one incident (noise reduction)"
