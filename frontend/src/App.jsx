import React, { useEffect, useRef, useState } from 'react'
import Graph from './Graph.jsx'

const $ = (u, o) => fetch(u, o).then(r => r.json())
const fmt = t => new Date(t * 1000).toLocaleTimeString('en-GB', { hour12: false })
const MD = s => s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, i) =>
  p.startsWith('**') ? <strong key={i}>{p.slice(2, -2)}</strong>
    : p.startsWith('`') ? <code key={i}>{p.slice(1, -1)}</code> : p)

const SEV_COLOR = { INFO: '#3a3d44', WARN: '#f2d55b', ERROR: '#ffa94d', CRITICAL: '#ff5d5d' }
const SERVICE_LABEL = {
  database: 'Database', 'bank-simulator': 'Bank Simulator', 'auth-service': 'Auth Service',
  'transaction-service': 'Transactions', 'payment-api': 'Payment API',
}
const EVENT_LABEL = {
  db_conn_error: 'Connection pool', high_latency: 'High latency', auth_timeout: 'Auth timeout',
  auth_failure: 'Auth failure', login_failed: 'Login failed', txn_failed: 'Payment failed',
  svc_timeout: 'Partner timeout', req_ok: 'Request completed', txn_ok: 'Payment settled',
  cache_hit: 'Cache hit', generic_warn: 'Recovered retry',
}
const serviceName = service => SERVICE_LABEL[service] || service.replaceAll('-', ' ')
const FAULTS = [
  ['db_pool_exhaustion', 'Database connection pool', 'Connection saturation affects checkout and payments'],
  ['bank_simulator_timeout', 'Bank Simulator timeout', 'Partner authorization requests begin timing out'],
  ['auth_outage', 'Auth Service outage', 'Token checks fail and customer sign-ins are affected'],
]

function ReactorMark() {
  return (
    <svg className="reactor-mark" viewBox="0 0 64 64" aria-hidden="true">
      <circle cx="32" cy="32" r="29" />
      <path d="M19 11 26 4h12l7 7M8 24l10-10m38 10L46 14M8 40l10 10m38-10L46 50M19 53l7 7h12l7-7" />
      <path d="m17 21 15-8 15 8-15 27-15-27Zm5 1h20L32 40 22 22Zm10-8v9m0 17v8M11 32h9m24 0h9" />
      <path d="m24 25 8 13 8-13" />
    </svg>
  )
}

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
  if (!incident) return <div className="pad muted">No active incident. Inject a fault to see the engine analyze it.</div>
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
          <div className="rc-label">Likely cause</div>
          <div className="rc-name">{serviceName(top.service)}</div>
          <div className="rc-conf">{top.max_severity.toLowerCase()} · {top.alerts} unusual alerts · evidence {top.evidence_event_ids.slice(0, 2).join(', ')}</div>
        </div>
      </div>
      {p.name && <div className="pattern"><b>{p.name}</b><span>{p.why}</span></div>}
      <div className="formula">
        <div className="sec-title">What supports this diagnosis</div>
        {F.map(([label, k, w]) => (
          <div key={k} className="frow">
            <span className="flabel">{label} <em>×{w}%</em></span>
            <div className="fbar"><div style={{ width: `${top.formula[k] * 100}%` }} /></div>
            <span className="fval">{top.formula[k]}</span>
          </div>
        ))}
      </div>
      <div className="sec-title">Other possible causes</div>
      {incident.candidates.slice(1, 3).map(c => (
        <div key={c.service} className="alt" onMouseEnter={() => onHighlight(c.service)}>
          <span className="alt-name">{serviceName(c.service)}</span><span className="alt-conf">{c.confidence}%</span>
          <div className="alt-ev">{c.for.length ? 'for: ' + c.for.join(', ') : ''}{c.against.length ? ' · against: ' + c.against.join(', ') : ''}</div>
        </div>
      ))}
      {incident.unrelated_noise.length > 0 && (
        <div className="noise">Unrelated alerts: <b>{incident.unrelated_noise.map(serviceName).join(', ')}</b>. They do not explain the affected services.</div>
      )}
      <div className="sec-title">Estimated impact</div>
      <div className="impact">
        <div><b>{imp.directly_affected.length + imp.downstream.length}</b><span>services affected</span></div>
        <div><b>~{imp.est_users_impacted}</b><span>users impacted</span></div>
        <div><b>{imp.failed_transactions}</b><span>failed txns</span></div>
        <div><b>{imp.failed_logins}</b><span>failed logins</span></div>
      </div>
      {p.actions?.length > 0 && <>
        <div className="sec-title">Suggested next steps</div>
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
          <span className="tsvc">{serviceName(t.service)}</span>
          <span className="ttype">{EVENT_LABEL[t.event_type] || t.event_type.replaceAll('_', ' ')}</span>
          <span className="tlink">{t.trigger ? 'Started here' : t.caused_by ? `← after ${serviceName(t.caused_by)}` : ''}</span>
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
        <div className="brand">J.A.R.V.I.S.<span> Nexus</span></div>
        <div className="tagline">Correlate alerts. Find the cause.</div>
        <div className="chips">
          <div className="chip"><b>{metrics.alerts_in}</b><span>alerts received</span></div>
          <div className="chip accent"><b>{metrics.incidents_out}</b><span>incidents</span></div>
          <div className="chip"><b>{metrics.noise_reduction_pct}%</b><span>noise reduction</span></div>
          <div className="chip"><b>{metrics.time_to_root_cause_s != null ? metrics.time_to_root_cause_s + 's' : '—'}</b><span>to root cause</span></div>
        </div>
        <div className="header-actions">
          <div className={`status ${fault ? 'bad' : 'ok'}`}>{fault ? 'Incident active' : 'All systems nominal'}</div>
          <div className="assistant-dock">
            {chat.open && <section className="chat" aria-label="Ask J.A.R.V.I.S.">
              <div className="chat-head">
                <div>J.A.R.V.I.S. AI assistant<span className="muted chat-subtitle">{chat.msgs.length ? ` · ${chat.msgs.filter(m => !m.me).at(-1)?.mode === 'llm' ? 'grounded AI' : 'grounded assistant'}` : ' · Answers from incident findings'}</span></div>
                <button className="chat-close" type="button" aria-label="Close assistant" onClick={() => setChat(c => ({ ...c, open: false }))}>×</button>
              </div>
              <div className="chat-body" id="chat-body">
                {chat.msgs.map((m, i) => <div key={i} className={`msg ${m.me ? 'me' : ''}`}>{MD(m.text)}</div>)}
                {chat.busy && <div className="msg muted">analysing findings…</div>}
                <div className="sugs">
                  {['What should I check first?', 'What is the root cause?', 'Which services were affected?', 'Show the timeline', 'How is confidence computed?'].map(s =>
                    <button key={s} onClick={() => ask(s)} disabled={chat.busy}>{s}</button>)}
                </div>
                <form onSubmit={e => { e.preventDefault(); const v = e.target[0].value.trim(); if (v) { ask(v); e.target.reset() } }}>
                  <input placeholder="Ask about the incident…" disabled={chat.busy} />
                </form>
              </div>
            </section>}
            <button className={`assistant-launcher ${chat.open ? 'active' : ''}`} type="button"
              aria-label={chat.open ? 'Close J.A.R.V.I.S. AI assistant' : 'Open J.A.R.V.I.S. AI assistant'}
              title="J.A.R.V.I.S. assistant" aria-expanded={chat.open}
              onClick={() => setChat(c => ({ ...c, open: !c.open }))}>
              <ReactorMark /><span className="launcher-label">AI assistant</span>
            </button>
          </div>
        </div>
      </header>

      <div className="cols">
        <aside>
          <div className="panel pad">
            <div className="sec-title">Sample incidents</div>
            {FAULTS.map(([id, label, note]) => (
              <button key={id} className="inject" onClick={() => inject(id)} disabled={!!fault}>
                <b>{label}</b><span>{note}</span>
              </button>
            ))}
            <button className="clearbtn" onClick={() => inject('clear')}>Resolve and clear</button>
          </div>
          <div className="panel grow">
            <div className="sec-title pad-h">Alert feed <span className="muted">· incoming service events</span></div>
            <div className="feed" ref={feedRef}>
              {feed.map(ev => (
                <div key={ev.id} className={`frow2 ${ev.anomalous ? 'anom' : ''}`} onMouseEnter={() => setHighlight(ev.service)}>
                  <span className="fdot" style={{ background: SEV_COLOR[ev.severity] }} />
                  <div className="fdetails">
                    <div className="fmeta"><span className="ftime">{ev.timestamp}</span><span className="fsvc">{serviceName(ev.service)}</span>
                      <span className="ftype">{EVENT_LABEL[ev.event_type] || ev.event_type.replaceAll('_', ' ')}</span>
                      {ev.anomalous && <span className="fz">{ev.z}σ</span>}
                    </div>
                    <div className="fmessage">{ev.message}</div>
                  </div>
                </div>
              ))}
              {!feed.length && <div className="muted pad">Waiting for alerts…</div>}
            </div>
          </div>
        </aside>

        <main>
          <div className="panel graph-wrap">
            <div className="sec-title pad-h">Service dependencies {incident && <span className="muted">· {incident.id}</span>}</div>
            <div className="graph-legend" aria-label="Graph arrow legend">
              <span><i className="legend-line observed" />Observed alert path</span>
              <span><i className="legend-line predicted" />Likely dependency path</span>
            </div>
            <div className="graph"><Graph graph={state.graph} incident={incident} highlight={highlight} /></div>
          </div>
          <div className="panel">
            <div className="sec-title pad-h">Incident timeline</div>
            {incident ? <Timeline incident={incident} onHighlight={setHighlight} /> : <div className="muted pad">No active incident.</div>}
          </div>
        </main>

        <aside className="right">
          <div className="panel pad">
            <div className="sec-title">Incident analysis</div>
            <RootCausePanel incident={incident} onHighlight={setHighlight} />
            <a className={`report ${incident ? '' : 'disabled'}`}
              href={incident ? `/api/incidents/${incident.id}/report.md` : undefined}
              download={incident ? `${incident.id}-report.md` : undefined}
              aria-disabled={!incident} tabIndex={incident ? 0 : -1}>
              ↓ Export incident report
            </a>
          </div>
        </aside>
      </div>

    </div>
  )
}
