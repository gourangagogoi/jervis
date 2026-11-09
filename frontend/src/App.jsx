import React, { useEffect, useRef, useState } from 'react'
import Graph from './Graph.jsx'

const $ = (u, o) => fetch(u, o).then(r => r.json())
const fmt = t => new Date(t * 1000).toLocaleTimeString('en-GB', { hour12: false })
const MD = s => s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, i) =>
  p.startsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong>
    : p.startsWith('`') ? <code key={i}>{p.slice(1, -1)}</code> : p)

const SEV_COLOR = { INFO: '#3a3d44', WARN: '#f2d55b', ERROR: '#ffa94d', CRITICAL: '#ff5d5d' }
const FAULTS = [
  ['db_pool_exhaustion', 'DB connection pool exhaustion', 'Main scenario · includes a red herring'],
  ['bank_simulator_timeout', 'Bank Simulator timeout', 'Engine must NOT blame the database'],
  ['auth_outage', 'Auth outage', 'Engine must blame Auth Service'],
]

function Ring({ pct }) {
  const r = 34, c = 2 * Math.PI * r
  return (
    <div className="ring">
      <svg width="86" height="86">
        <circle cx="43" cy="43" r={r} stroke="#232529" strokeWidth="7" fill="none" />
        <circle cx="43" cy="43" r={r} stroke="#ff5d5d" strokeWidth="7" fill="none"
          strokeDasharray={c} strokeDashoffset={c * (1 - pct / 100)} strokeLinecap="round"
          transform="rotate(-90 43 43)" />
      </svg>
      <div className="ring-num">{pct}%</div>
    </div>
  )
}

function RootCausePanel({ incident, onHighlight }) {
  if (!incident) return <div className="panel pad muted">No incident. Platform is calm — inject a fault to see the engine reason.</div>
  const top = incident.candidates[0]
  const p = incident.pattern || {}
  const imp = incident.impact
  const F = [['Earliest onset', 'earliest_onset', 35], ['Downstream coverage', 'downstream_coverage', 30],
             ['Anomaly magnitude', 'anomaly_magnitude', 20], ['Signature match', 'signature_match', 15]]
  return (
    <div className="rootcause">
      <div className="rc-head">
        <Ring pct={top.confidence} />
        <div>
          <div className="rc-label">SUSPECTED ROOT CAUSE</div>
          <div className="rc-name">{top.service}</div>
          <div className="rc-conf">{top.max_severity} · {top.alerts} anomalous alerts · evidence {top.evidence_event_ids.slice(0, 2).join(', ')}</div>
        </div>
      </div>
      {p.name && <div className="pattern"><b>{p.name}</b><span>{p.why}</span></div>}
      <div className="formula">
        <div className="sec-title">CONFIDENCE FORMULA — EXPLAINABLE</div>
        {F.map(([label, k, w]) => (
          <div key={k} className="frow">
            <span className="flabel">{label} <em>×{w}%</em></span>
            <div className="fbar"><div style={{ width: `${top.formula[k] * 100}%` }} /></div>
            <span className="fval">{top.formula[k]}</span>
          </div>
        ))}
      </div>
      <div className="sec-title">ALTERNATIVE HYPOTHESES</div>
      {incident.candidates.slice(1, 3).map(c => (
        <div key={c.service} className="alt" onMouseEnter={() => onHighlight(c.service)}>
          <span className="alt-name">{c.service}</span><span className="alt-conf">{c.confidence}%</span>
          <div className="alt-ev">{c.for.length ? 'for: ' + c.for.join(', ') : ''}{c.against.length ? ' · against: ' + c.against.join(', ') : ''}</div>
        </div>
      ))}
      {incident.unrelated_noise.length > 0 && (
        <div className="noise">Ranked out as unrelated noise: <b>{incident.unrelated_noise.join(', ')}</b> — noisy, but no affected dependents.</div>
      )}
      <div className="sec-title">IMPACT</div>
      <div className="impact">
        <div><b>{imp.directly_affected.length + imp.downstream.length}</b><span>services affected</span></div>
        <div><b>~{imp.est_users_impacted}</b><span>users impacted</span></div>
        <div><b>{imp.failed_transactions}</b><span>failed txns</span></div>
        <div><b>{imp.failed_logins}</b><span>failed logins</span></div>
      </div>
      {p.actions && <>
        <div className="sec-title">RECOMMENDED ACTIONS</div>
        <ol className="actions">{p.actions.map((a, i) => <li key={i}>{a}</li>)}</ol>
      </>}
    </div>
  )
}

function Timeline({ incident, onHighlight }) {
  if (!incident) return null
  return (
    <div className="timeline">
      {incident.timeline.map((t, i) => (
        <div key={i} className={`trow ${t.trigger ? 'trigger' : ''}`} onMouseEnter={() => onHighlight(t.service)}>
          <span className="tdot" style={{ background: SEV_COLOR[t.severity] }} />
          <span className="ttime">{fmt(t.at)}</span>
          <span className="tsvc">{t.service}</span>
          <span className="ttype">{t.event_type}</span>
          <span className="tlink">{t.trigger ? '⚡ TRIGGER' : t.caused_by ? `← caused by ${t.caused_by}` : ''}</span>
          <code className="tid">{t.event_id}</code>
        </div>
      ))}
    </div>
  )
}

export default function App() {
  const [state, setState] = useState(null)
  const [incident, setIncident] = useState(null)
  const [metrics, setMetrics] = useState({ alerts_in: 0, incidents_out: 0, noise_reduction_pct: 0, time_to_root_cause_s: null })
  const [feed, setFeed] = useState([])
  const [fault, setFault] = useState(null)
  const [highlight, setHighlight] = useState(null)
  const [chat, setChat] = useState({ open: false, msgs: [], busy: false })
  const feedRef = useRef(null)

  const inject = async (s) => {
    const result = await $(`/api/inject/${s}`, { method: 'POST' })
    if (s === 'clear') {
      setIncident(null)
      setFault(result.fault)
      setFeed([])
      setHighlight(null)
    }
  }

  useEffect(() => {
    $('/api/state').then(s => { setState(s); setMetrics(s.metrics); setFault(s.fault); if (s.incident) setIncident(s.incident) })
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/ws`)
    ws.onmessage = e => {
      const m = JSON.parse(e.data)
      if (m.type === 'event') {
        setMetrics(m.metrics)
        if (m.event.severity !== 'INFO') setFeed(f => [m.event, ...f].slice(0, 80))
      } else if (m.type === 'incident') { setIncident(m.incident) }
      else if (m.type === 'incident_closed') { setIncident(m.incident); setMetrics(m.metrics) }
      else if (m.type === 'cleared') { setIncident(null); setFault(null); setFeed([]); setHighlight(null); setMetrics(m.metrics) }
      else if (m.type === 'injected') setFault(m.fault)
    }
    return () => ws.close()
  }, [])

  const ask = async (q) => {
    setChat(c => ({ ...c, busy: true, msgs: [...c.msgs, { me: true, text: q }] }))
    const r = await $(`/api/chat`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: q }) })
    setChat(c => ({ ...c, busy: false, msgs: [...c.msgs, { me: false, text: r.text, mode: r.mode }] }))
  }

  if (!state) return <div className="loading">J.A.R.V.I.S. Nexus — booting…</div>

  return (
    <div className="app">
      <header>
        <div className="brand">J.A.R.V.I.S.<span> NEXUS</span></div>
        <div className="tagline">47 scattered alerts → one explained incident</div>
        <div className="chips">
          <div className="chip"><b>{metrics.alerts_in}</b><span>alerts in</span></div>
          <div className="chip accent"><b>{metrics.incidents_out}</b><span>incidents out</span></div>
          <div className="chip"><b>{metrics.noise_reduction_pct}%</b><span>noise reduction</span></div>
          <div className="chip"><b>{metrics.time_to_root_cause_s != null ? metrics.time_to_root_cause_s + 's' : '—'}</b><span>to root cause</span></div>
        </div>
        <div className={`status ${fault ? 'bad' : 'ok'}`}>{fault ? 'FAULT ACTIVE' : 'ALL SYSTEMS NOMINAL'}</div>
      </header>

      <div className="cols">
        <aside>
          <div className="panel pad">
            <div className="sec-title">FAILURE INJECTION</div>
            {FAULTS.map(([id, label, note]) => (
              <button key={id} className="inject" onClick={() => inject(id)} disabled={!!fault}>
                <b>{label}</b><span>{note}</span>
              </button>
            ))}
            <button className="clearbtn" onClick={() => inject('clear')}>Resolve / clear</button>
          </div>
          <div className="panel grow">
            <div className="sec-title pad-h">LIVE ALERT FEED <span className="muted">(raw, uncorrelated)</span></div>
            <div className="feed" ref={feedRef}>
              {feed.map(ev => (
                <div key={ev.id} className={`frow2 ${ev.anomalous ? 'anom' : ''}`} onMouseEnter={() => setHighlight(ev.service)}>
                  <span className="fdot" style={{ background: SEV_COLOR[ev.severity] }} />
                  <span className="ftime">{ev.timestamp}</span>
                  <span className="fsvc">{ev.service}</span>
                  <span className="ftype">{ev.event_type}</span>
                  {ev.anomalous && <span className="fz">σ{ev.z}</span>}
                </div>
              ))}
              {!feed.length && <div className="muted pad">Waiting for alerts…</div>}
            </div>
          </div>
        </aside>

        <main>
          <div className="panel graph-wrap">
            <div className="sec-title pad-h">SERVICE DEPENDENCY GRAPH {incident && <span className="muted">· incident {incident.id}</span>}</div>
            <div className="graph"><Graph graph={state.graph} incident={incident} highlight={highlight} /></div>
          </div>
          <div className="panel">
            <div className="sec-title pad-h">INCIDENT TIMELINE <span className="muted">· cascade, trigger marked</span></div>
            {incident ? <Timeline incident={incident} onHighlight={setHighlight} /> : <div className="muted pad">No active incident.</div>}
          </div>
        </main>

        <aside className="right">
          <div className="panel pad">
            <div className="sec-title">ROOT CAUSE ANALYSIS</div>
            <RootCausePanel incident={incident} onHighlight={setHighlight} />
            {incident && <a className="report" href={`/api/incidents/${incident.id}/report.md`} target="_blank" rel="noreferrer">↓ Export incident report (Markdown)</a>}
          </div>
        </aside>
      </div>

      <div className={`chat ${chat.open ? 'open' : ''}`}>
        <div className="chat-head" onClick={() => setChat(c => ({ ...c, open: !c.open }))}>
          ASK J.A.R.V.I.S. <span className="muted">{chat.msgs.length ? `· ${chat.msgs.filter(m => !m.me).at(-1)?.mode === 'llm' ? 'LLM · grounded' : 'template · grounded'}` : '· answers only from engine findings, cites event IDs'}</span>
          <span className="chev">{chat.open ? '▾' : '▴'}</span>
        </div>
        {chat.open && <div className="chat-body">
          {chat.msgs.map((m, i) => <div key={i} className={`msg ${m.me ? 'me' : ''}`}>{MD(m.text)}</div>)}
          {chat.busy && <div className="msg muted">analysing findings…</div>}
          <div className="sugs">
            {['What should I check first?', 'What is the root cause?', 'Which services were affected?', 'Show the timeline', 'How is confidence computed?'].map(s =>
              <button key={s} onClick={() => ask(s)} disabled={chat.busy}>{s}</button>)}
          </div>
          <form onSubmit={e => { e.preventDefault(); const v = e.target[0].value.trim(); if (v) { ask(v); e.target.reset() } }}>
            <input placeholder="Ask about the incident…" disabled={chat.busy} />
          </form>
        </div>}
      </div>
    </div>
  )
}
