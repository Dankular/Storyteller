import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getSnapshot, updateBible } from '../api'
import type { Snapshot } from '../types'

export function Dashboard() {
  const { projectId } = useParams<{ projectId: string }>()
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [styleGuide, setStyleGuide] = useState('')
  const [tags, setTags] = useState('')
  const [genre, setGenre] = useState('')
  const [saving, setSaving] = useState(false)

  const load = () => {
    if (!projectId) return
    getSnapshot(projectId).then((s) => {
      setSnapshot(s)
      setStyleGuide(s.state.style_guide)
      setTags(s.state.tags.join(', '))
      setGenre(s.state.genre_id ?? '')
    })
  }
  useEffect(load, [projectId])

  const save = async () => {
    if (!projectId) return
    setSaving(true)
    try {
      await updateBible(projectId, {
        style_guide: styleGuide,
        tags: tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        // Only send genre if it actually changed -- setting it to the same value re-clones a
        // preset or re-runs generation for free text unnecessarily otherwise.
        ...(genre !== (snapshot?.state.genre_id ?? '') ? { genre } : {}),
      })
      load()
    } finally {
      setSaving(false)
    }
  }

  if (!snapshot) return <p>Loading…</p>
  const { state, outline, continuity } = snapshot
  const openFlags = continuity.filter((f) => !f.resolved).length

  return (
    <div className="page">
      <h1>{state.title}</h1>
      <p className="premise">{state.premise}</p>

      <div className="stat-row">
        <div className="stat">
          <strong>{outline.length}</strong> chapters
        </div>
        <div className="stat">
          <strong>{Object.keys(state.characters).length}</strong> characters
        </div>
        <div className="stat">
          <strong>{Object.values(state.promises).filter((p) => p.status !== 'paid').length}</strong>{' '}
          open promises
        </div>
        <div className="stat">
          <strong>{openFlags}</strong> open continuity flags
        </div>
      </div>

      <label>
        Genre (free text -- an exact match to a built-in preset clones its beat-sheet; anything
        else, the model generates one for it)
        <input value={genre} onChange={(e) => setGenre(e.target.value)} />
      </label>
      <label>
        Style guide
        <textarea value={styleGuide} onChange={(e) => setStyleGuide(e.target.value)} rows={3} />
      </label>
      <label>
        Focus tags (comma-separated)
        <input value={tags} onChange={(e) => setTags(e.target.value)} />
      </label>
      <button className="primary" onClick={save} disabled={saving}>
        {saving ? 'Saving…' : 'Save'}
      </button>

      <h2>Running summary</h2>
      <p className="summary">{state.running_summary || '(empty so far)'}</p>
    </div>
  )
}
