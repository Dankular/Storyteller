import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { getSnapshot } from '../api'
import type { Chapter } from '../types'

const STATUS_LABEL: Record<string, string> = {
  planned: 'Planned',
  drafted: 'Drafted',
  revised: 'Revised',
  final: 'Final',
}

export function Outline() {
  const { projectId } = useParams<{ projectId: string }>()
  const [chapters, setChapters] = useState<Chapter[]>([])

  useEffect(() => {
    if (!projectId) return
    getSnapshot(projectId).then((s) => setChapters(s.outline))
  }, [projectId])

  return (
    <div className="page">
      <h1>Outline</h1>
      <ul className="chapter-list">
        {chapters.map((c) => (
          <li key={c.id}>
            <Link to={`/projects/${projectId}/outline/${c.id}`}>
              <span className={`chip status-${c.status}`}>{STATUS_LABEL[c.status] ?? c.status}</span>
              <strong>{c.id}</strong> — {c.title}
              <span className="meta">
                {c.pov ? `POV: ${c.pov} · ` : ''}
                ~{c.word_target}w
                {c.structural_beat ? ` · ${c.structural_beat}` : ''}
              </span>
            </Link>
          </li>
        ))}
        {chapters.length === 0 && <p>No chapters yet -- use Plan to propose some.</p>}
      </ul>
    </div>
  )
}
