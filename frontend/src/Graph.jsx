import React, { useEffect, useMemo, useState } from 'react'
import { ReactFlow, Background, MarkerType, Handle, Position } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

const POS = {
  'payment-api': { x: 290, y: 10 },
  'auth-service': { x: 60, y: 165 },
  'transaction-service': { x: 520, y: 165 },
  'database': { x: 60, y: 320 },
  'bank-simulator': { x: 520, y: 320 },
}
const LABEL = {
  'payment-api': 'Payment API', 'auth-service': 'Auth Service',
  'transaction-service': 'Transaction Service', 'database': 'Database',
  'bank-simulator': 'Bank Simulator',
}

function StatusNode({ data }) {
  return (
    <div className={`node ${data.status}`} style={{ borderColor: data.color, boxShadow: data.pulse ? `0 0 0 3px ${data.color}22` : 'none' }}>
      <Handle type="target" position={Position.Top} />
      <div className="node-name">{data.label}</div>
      <div className="node-tag" style={{ color: data.color }}>{data.tag}</div>
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}
const nodeTypes = { status: StatusNode }

export default function Graph({ graph, incident, highlight }) {
  const causalPlan = useMemo(() => {
    const root = incident?.impact?.root
    if (!root) return []
    const depth = new Map([[root, 0]])
    let changed = true
    while (changed) {
      changed = false
      for (const [service, dependencies] of Object.entries(graph.depends_on)) {
        const parentDepth = dependencies.reduce((min, dependency) =>
          depth.has(dependency) ? Math.min(min, depth.get(dependency)) : min, Infinity)
        if (!depth.has(service) && Number.isFinite(parentDepth)) {
          depth.set(service, parentDepth + 1)
          changed = true
        }
      }
    }
    return Object.entries(graph.depends_on)
      .flatMap(([service, dependencies]) => dependencies
        .filter(dependency => depth.has(service) && depth.has(dependency))
        .map(dependency => ({ id: `${service}->${dependency}`, depth: depth.get(service) })))
      .sort((a, b) => a.depth - b.depth || a.id.localeCompare(b.id))
      .map((edge, index, edges) => ({
        ...edge,
        delay: edge.depth === 1 ? 300 + index * 350 : 1100 + (index - edges.findIndex(item => item.depth === edge.depth)) * 350,
      }))
  }, [graph, incident?.impact?.root])
  const causalPath = useMemo(() => new Set(causalPlan.map(edge => edge.id)), [causalPlan])
  const [revealedEdges, setRevealedEdges] = useState(() => new Set())

  useEffect(() => {
    setRevealedEdges(new Set())
    const timers = causalPlan.map(edge => window.setTimeout(() => {
      setRevealedEdges(previous => new Set(previous).add(edge.id))
    }, edge.delay))
    return () => timers.forEach(window.clearTimeout)
  }, [causalPlan])

  const statusMap = useMemo(() => {
    const m = {}
    if (incident) {
      const imp = incident.impact
      m[imp.root] = { status: 'root', color: '#ff5d5d', tag: 'ROOT CAUSE', pulse: true }
      imp.directly_affected.forEach(s => m[s] = { status: 'direct', color: '#ffa94d', tag: 'DIRECTLY AFFECTED' })
      imp.downstream.forEach(s => m[s] = { status: 'down', color: '#f2d55b', tag: 'DOWNSTREAM' })
      ;(incident.unrelated_noise || []).forEach(s => { if (!m[s]) m[s] = { status: 'noise', color: '#8a8d93', tag: 'UNRELATED NOISE' } })
      incident.services_affected.forEach(s => { if (!m[s]) m[s] = { status: 'direct', color: '#ffa94d', tag: 'AFFECTED' } })
    }
    return m
  }, [incident])

  const nodes = useMemo(() => graph.services.map(s => {
    const status = statusMap[s] || { status: 'healthy', color: '#3a3d44', tag: 'HEALTHY' }
    return {
      id: s, type: 'status', position: POS[s],
      data: { label: LABEL[s], ...status, pulse: Boolean(status.pulse || highlight === s) },
    }
  }), [graph, statusMap, highlight])

  const edges = useMemo(() => {
    const affectedSet = new Set(incident ? incident.services_affected : [])
    return Object.entries(graph.depends_on).flatMap(([svc, deps]) =>
      deps.map(d => {
        const observed = affectedSet.has(svc) && affectedSet.has(d)
        const predicted = causalPath.has(`${svc}->${d}`)
        const shown = predicted && revealedEdges.has(`${svc}->${d}`)
        const stroke = observed ? '#ff6b63' : shown ? '#d6aa72' : '#343837'
        return {
          id: `${svc}->${d}`, source: svc, target: d,
          animated: observed || shown,
          style: {
            stroke,
            strokeWidth: observed ? 2 : shown ? 1.7 : 1.2,
            opacity: predicted && !shown && !observed ? 0.3 : 1,
            transition: 'stroke 450ms ease, stroke-width 450ms ease, opacity 450ms ease',
            ...(shown && !observed ? { strokeDasharray: '5 5' } : {}),
          },
          markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
        }
      }))
  }, [graph, incident, causalPath, revealedEdges])

  return (
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes}
      onInit={flow => flow.fitView({ padding: 0.12, duration: 0 })}
      proOptions={{ hideAttribution: true }}
      nodesDraggable={false} nodesConnectable={false} elementsSelectable={false}
      zoomOnScroll={false} panOnScroll={false}>
      <Background color="#17181b" gap={22} />
    </ReactFlow>
  )
}
