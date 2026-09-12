import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getDependencyGraph, listContinuity, resolveContinuity, runDependencyCheck } from '../api'
import type { ContinuityFlag, DependencyGraph } from '../types'

const KINDS = ['all', 'continuity', 'pov', 'promise', 'beat_coverage', 'tension', 'dependency', 'pipeline_error', 'manuscript_audit']

export function Continuity() {
  const { projectId } = useParams<{ projectId: string }>()
  const [flags, setFlags] = useState<ContinuityFlag[]>([])
  const [kind, setKind] = useState('all')
  const [graph, setGraph] = useState<DependencyGraph | null>(null)
  const [checking, setChecking] = useState(false)

  const load = () => {
    if (!projectId) return
    listContinuity(projectId).then((r) => setFlags(r.continuity))
    getDependencyGraph(projectId).then(setGraph)
  }
  useEffect(load, [projectId])

  const resolve = async (index: number) => {
    if (!projectId) return
    await resolveContinuity(projectId, index)
    load()
  }

  const runCheck = async () => {
    if (!projectId) return
    setChecking(true)
    try {
      await runDependencyCheck(projectId)
      load()
    } finally {
      setChecking(false)
    }
  }

  const visible = flags
    .map((f, i) => ({ ...f, index: i }))
    .filter((f) => kind === 'all' || f.kind === kind)

  const entityNodes = graph?.nodes.filter((n) => n.kind !== 'chapter') ?? []

  return (
    <div className="page">
      <h1>Continuity &amp; dependencies</h1>

      <div className="filter-row">
        <label>
          Kind
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <button onClick={runCheck} disabled={checking}>
          {checking ? 'Checking…' : 'Run dependency check (no model call)'}
        </button>
      </div>

      <ul className="flag-list">
        {visible.map((f) => (
          <li key={f.index} className={f.resolved ? 'resolved' : ''}>
            <span className={`chip severity-${f.severity}`}>{f.severity}</span>
            <span className="chip">{f.kind}</span>
            <strong>{f.chapter_id}</strong>: {f.issue}
            {!f.resolved && <button onClick={() => resolve(f.index)}>Resolve</button>}
          </li>
        ))}
        {visible.length === 0 && <p>No flags of this kind.</p>}
      </ul>

      <h2>Dependency graph</h2>
      <p className="hint">
        Every character/location/plot thread/promise, and the chapter each was established in
        (blank = pre-established, available from the start).
      </p>
      <table className="graph-table">
        <thead>
          <tr>
            <th>Kind</th>
            <th>Entity</th>
            <th>Established in</th>
          </tr>
        </thead>
        <tbody>
          {entityNodes.map((n) => (
            <tr key={n.id}>
              <td>{n.kind}</td>
              <td>{n.label}</td>
              <td>{n.established_at ?? '(from the start)'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
