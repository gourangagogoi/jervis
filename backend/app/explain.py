"""Grounded explanation layer: template answers from structured findings, optional LLM.

The model (if configured) receives ONLY the structured findings JSON and must cite
event IDs. If no API key or the call fails, the deterministic template takes over —
the demo never breaks.
"""
import os, json

SYSTEM = ("You are J.A.R.V.I.S. Nexus, an incident explanation assistant. Answer ONLY from the "
          "structured findings JSON provided. Cite event IDs like [ev-00012] for every factual "
          "claim. If the data does not support an answer, reply exactly: "
          "'Not enough evidence in the current findings.'")

def findings_context(engine):
    inc = engine.active or (engine.incidents[-1] if engine.incidents else None)
    if not inc:
        return None, {}
    d = inc.to_dict()
    ctx = {
        "incident": d["id"],
        "root_cause": d["root_cause"],
        "candidates": [{"service": c["service"], "confidence": c["confidence"],
                        "formula": c["formula"], "for": c["for"], "against": c["against"],
                        "evidence_event_ids": c["evidence_event_ids"]} for c in d["candidates"]],
        "impact": d["impact"],
        "pattern": d["pattern"],
        "timeline": d["timeline"],
        "unrelated_noise": d["unrelated_noise"],
    }
    return d["id"], ctx

def template_answer(question, ctx):
    q = question.lower()
    root = ctx["root_cause"]; imp = ctx["impact"]; p = ctx["pattern"]
    top = ctx["candidates"][0]
    evs = ", ".join(f"[{e}]" for e in top["evidence_event_ids"][:4])
    if any(k in q for k in ("check first", "first", "start", "do now", "action", "fix")):
        acts = p.get("actions") or ["Inspect the root-cause service directly."]
        body = "Start here, in priority order:\n" + "\n".join(f"{i}. {a}" for i, a in enumerate(acts, 1))
        return f"Root cause is **{root}** ({top['confidence']}% confidence, evidence {evs}).\n\n{body}"
    if any(k in q for k in ("affect", "who", "impact", "users", "blast")):
        return (f"**{root}** is the root. Directly affected: {', '.join(imp['directly_affected']) or '—'}. "
                f"Downstream: {', '.join(imp['downstream']) or '—'}.\nEstimated user impact: "
                f"{imp['failed_logins']} failed logins, {imp['failed_transactions']} failed transactions, "
                f"{imp['slow_requests']} slow requests (~{imp['est_users_impacted']} users).")
    if any(k in q for k in ("root cause", "why", "cause", "wrong")):
        alt = ", ".join(f"{c['service']} ({c['confidence']}%)" for c in ctx["candidates"][1:3])
        msg = f"**{root}** — {top['confidence']}% confidence. Evidence: {evs}."
        if p.get("name"): msg += f"\nPattern match: **{p['name']}**. {p['why']}"
        if alt: msg += f"\nAlternatives considered: {alt}."
        if ctx["unrelated_noise"]:
            msg += f"\nRanked out as unrelated noise: {', '.join(ctx['unrelated_noise'])}."
        return msg
    if any(k in q for k in ("timeline", "when", "happen", "cascade", "order")):
        steps = "\n".join(f"- `{t['event_id']}` **{t['service']}** {t['event_type']}"
                          + (f" ← caused by {t['caused_by']}" if t["caused_by"] else " ⚡ trigger")
                          for t in ctx["timeline"])
        return "Cascade order:\n" + steps
    if any(k in q for k in ("confidence", "score", "sure")):
        f = top["formula"]
        return (f"Confidence is a transparent weighted formula, normalized across candidates: "
                f"earliest onset {f['earliest_onset']} × 0.35 + downstream coverage "
                f"{f['downstream_coverage']} × 0.30 + anomaly magnitude {f['anomaly_magnitude']} × 0.20 "
                f"+ signature match {f['signature_match']} × 0.15 → **{top['confidence']}%**.")
    return ctx.get("summary") or (f"Current incident: root cause **{root}** ({top['confidence']}%), "
                                  f"{len(ctx['timeline'])} services affected. Ask about the root cause, "
                                  f"impact, timeline or what to check first.")

def answer(question, engine):
    inc_id, ctx = findings_context(engine)
    if not inc_id:
        return {"mode": "template", "incident": None,
                "text": "No incident detected yet — the platform is calm. Inject a fault and ask again."}
    ctx["summary"] = (engine.active or engine.incidents[-1]).analysis.get("summary")
    text = None
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        try:
            import httpx
            r = httpx.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={"model": os.environ.get("LLM_MODEL", "gpt-4o-mini"),
                      "messages": [{"role": "system", "content": SYSTEM},
                                   {"role": "user", "content": json.dumps(ctx) + "\n\nQuestion: " + question}],
                      "max_tokens": 300, "temperature": 0.1}, timeout=8)
            text = r.json()["choices"][0]["message"]["content"]
        except Exception:
            text = None
    return {"mode": "llm" if text else "template", "incident": inc_id,
            "text": text or template_answer(question, ctx)}
