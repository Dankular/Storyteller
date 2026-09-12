import { useEffect, useState } from 'react'
import { getAffectedChapters, startGenerate } from '../api'
import { useJobsContext } from '../JobsContext'
import type { AffectedChapter } from '../types'

/** Shown after a bible edit that might affect already-planned chapters (e.g. renaming a
 * character, changing when they're introduced). Lists every chapter the dependency graph says
 * references the edited entity (see webapi/routers/dependency.py's affected-chapters endpoint)
 * with NONE pre-checked -- regeneration overwrites drafted prose, so nothing regenerates unless
 * explicitly picked. This is deliberately not automatic/cascading. */
export function AffectedChaptersModal({
  projectId,
  kind,
  entityId,
  entityLabel,
  onClose,
}: {
  projectId: string
  kind: string
  entityId: string
  entityLabel: string
  onClose: () => void
}) {
  const { track } = useJobsContext()
  const [chapters, setChapters] = useState<AffectedChapter[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    getAffectedChapters(projectId, kind, entityId)
      .then((res) => setChapters(res.affected_chapters))
      .catch((err) => setError(String(err)))
  }, [projectId, kind, entityId])

  const toggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const regenerate = async () => {
    for (const chapterId of selected) {
      const { job_id } = await startGenerate(projectId, chapterId)
      track(job_id, `Regenerate ${chapterId}`)
    }
    onClose()
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Chapters affected by “{entityLabel}”</h3>
        {error && <p className="error">{error}</p>}
        {chapters === null && !error && <p>Checking the dependency graph…</p>}
        {chapters?.length === 0 && <p>No chapters reference this yet -- nothing to regenerate.</p>}
        {chapters && chapters.length > 0 && (
          <>
            <p>
              Pick which of these to regenerate now. Nothing is regenerated automatically --
              unchecked chapters keep their current text.
            </p>
            <ul className="affected-list">
              {chapters.map((c) => (
                <li key={c.id}>
                  <label>
                    <input
                      type="checkbox"
                      checked={selected.has(c.id)}
                      onChange={() => toggle(c.id)}
                    />
                    {c.id}: {c.title} <span className="chip">{c.status}</span>
                  </label>
                </li>
              ))}
            </ul>
          </>
        )}
        <div className="modal-actions">
          <button onClick={onClose}>Close</button>
          {chapters && chapters.length > 0 && (
            <button
              className="primary"
              disabled={selected.size === 0}
              onClick={regenerate}
            >
              Regenerate selected ({selected.size})
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
