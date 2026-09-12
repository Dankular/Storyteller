import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getChapterText, getSnapshot, removeEntity, startGenerate, updateChapter } from '../api'
import { useJobsContext } from '../JobsContext'
import { beatEstablishes, beatRequires, beatText, type Chapter } from '../types'

// Editing beats as a flat row of (text, requires-csv, establishes-csv) is a deliberately simple
// v1 -- see models.py's Chapter.beats docstring for the full "kind:raw_id" ref format
// (character:/location:/plot_thread:/promise:) that requires/establishes entries must use.
interface BeatRow {
  text: string
  requires: string
  establishes: string
}

function toRows(chapter: Chapter): BeatRow[] {
  return chapter.beats.map((b) => ({
    text: beatText(b),
    requires: beatRequires(b).join(', '),
    establishes: beatEstablishes(b).join(', '),
  }))
}

function fromRows(rows: BeatRow[]) {
  return rows
    .filter((r) => r.text.trim())
    .map((r) => {
      const requires = r.requires.split(',').map((s) => s.trim()).filter(Boolean)
      const establishes = r.establishes.split(',').map((s) => s.trim()).filter(Boolean)
      if (requires.length === 0 && establishes.length === 0) return r.text
      return { text: r.text, requires, establishes }
    })
}

export function ChapterDetail() {
  const { projectId, chapterId } = useParams<{ projectId: string; chapterId: string }>()
  const { track } = useJobsContext()
  const navigate = useNavigate()
  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [pov, setPov] = useState('')
  const [wordTarget, setWordTarget] = useState(2500)
  const [rows, setRows] = useState<BeatRow[]>([])
  const [saving, setSaving] = useState(false)

  const load = async () => {
    if (!projectId || !chapterId) return
    const [snapshot, manuscript] = await Promise.all([
      getSnapshot(projectId),
      getChapterText(projectId, chapterId),
    ])
    const c = snapshot.outline.find((x) => x.id === chapterId)
    if (!c) return
    setChapter(c)
    setTitle(c.title)
    setPov(c.pov)
    setWordTarget(c.word_target)
    setRows(toRows(c))
    setText(manuscript.text)
  }
  useEffect(() => {
    load()
  }, [projectId, chapterId])

  const save = async () => {
    if (!projectId || !chapterId) return
    setSaving(true)
    try {
      await updateChapter(projectId, chapterId, {
        title,
        pov,
        word_target: wordTarget,
        beats: fromRows(rows),
      })
      await load()
    } finally {
      setSaving(false)
    }
  }

  const regenerate = async () => {
    if (!projectId || !chapterId) return
    const { job_id } = await startGenerate(projectId, chapterId)
    track(job_id, `Regenerate ${chapterId}`)
  }

  const remove = async () => {
    if (!projectId || !chapterId || !chapter) return
    const drafted = chapter.status !== 'planned'
    const force = drafted
      ? window.confirm(`"${chapterId}" is already ${chapter.status} -- delete it AND its manuscript text?`)
      : window.confirm(`Delete chapter "${chapterId}"?`)
    if (!force && drafted) return
    if (!drafted && !force) return
    await removeEntity(projectId, 'chapter', chapterId, force)
    navigate(`/projects/${projectId}/outline`)
  }

  if (!chapter) return <p>Loading…</p>

  return (
    <div className="page">
      <h1>
        {chapterId}: {chapter.title} <span className={`chip status-${chapter.status}`}>{chapter.status}</span>
      </h1>

      <div className="chapter-meta-form">
        <label>
          Title
          <input value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label>
          POV
          <input value={pov} onChange={(e) => setPov(e.target.value)} />
        </label>
        <label>
          Word target
          <input
            type="number"
            value={wordTarget}
            onChange={(e) => setWordTarget(Number(e.target.value))}
          />
        </label>
      </div>

      <h2>Beats</h2>
      <p className="hint">
        `requires`/`establishes` use entity refs like <code>character:Torvin</code> or{' '}
        <code>promise:some-id</code> -- leave blank for most beats.
      </p>
      {rows.map((row, i) => (
        <div key={i} className="beat-row">
          <textarea
            value={row.text}
            onChange={(e) => {
              const next = [...rows]
              next[i] = { ...next[i], text: e.target.value }
              setRows(next)
            }}
            rows={2}
          />
          <input
            placeholder="requires (comma-separated)"
            value={row.requires}
            onChange={(e) => {
              const next = [...rows]
              next[i] = { ...next[i], requires: e.target.value }
              setRows(next)
            }}
          />
          <input
            placeholder="establishes (comma-separated)"
            value={row.establishes}
            onChange={(e) => {
              const next = [...rows]
              next[i] = { ...next[i], establishes: e.target.value }
              setRows(next)
            }}
          />
          <button onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button onClick={() => setRows([...rows, { text: '', requires: '', establishes: '' }])}>
        + Add beat
      </button>

      <div className="modal-actions">
        <button className="primary" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save beats'}
        </button>
        <button onClick={regenerate}>Regenerate this chapter</button>
        <button className="danger" onClick={remove}>
          Delete chapter
        </button>
      </div>

      <h2>Manuscript</h2>
      {text ? <pre className="manuscript">{text}</pre> : <p>Not drafted yet.</p>}
    </div>
  )
}
