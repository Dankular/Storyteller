import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getChapterText, getSnapshot, removeEntity, startContinue, startGenerate, updateChapter } from '../api'
import { useJobsContext } from '../JobsContext'
import { useJob } from '../useJob'
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
  const [sendJobId, setSendJobId] = useState<string | null>(null)
  const [characterNote, setCharacterNote] = useState<string | null>(null)
  const sendJob = useJob(sendJobId)

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

  useEffect(() => {
    if (sendJob.status !== 'succeeded' || !sendJob.result) return
    const result = sendJob.result as {
      text: string
      characters_created: string[]
      characters_updated: string[]
    }
    setText(result.text)
    const notes = [
      ...result.characters_created.map((n) => `${n} introduced`),
      ...result.characters_updated.map((n) => `${n} updated`),
    ]
    setCharacterNote(notes.length > 0 ? notes.join(', ') : null)
    setChapter((c) => (c ? { ...c, status: c.status === 'planned' ? 'drafted' : c.status } : c))
  }, [sendJob.status, sendJob.result])

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

  const send = async () => {
    if (!projectId || !chapterId) return
    setCharacterNote(null)
    const { job_id } = await startContinue(projectId, chapterId, text)
    setSendJobId(job_id)
    track(job_id, `Continue ${chapterId}`)
  }

  const regenerate = async () => {
    if (!projectId || !chapterId) return
    if (!window.confirm('Full rewrite runs the whole editorial pipeline (critique, revise, all checks) and REPLACES the current text -- any manual edits since the last generate will be lost. Continue?')) return
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
        <button onClick={regenerate}>Full rewrite (revise + all checks)</button>
        <button className="danger" onClick={remove}>
          Delete chapter
        </button>
      </div>

      <h2>Manuscript</h2>
      <p className="hint">
        Select, delete, or reword anything below, then Send -- the model continues from exactly
        what's here (your edits included), appends the next segment, and updates the character
        sheets from the result. For a full editorial pass over everything instead, use "Full
        rewrite" above.
      </p>
      <textarea
        className="manuscript-editor"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Not drafted yet -- press Send to write the opening."
        rows={18}
      />
      <div className="send-row">
        <button
          className="primary send-button"
          onClick={send}
          disabled={sendJob.status === 'running' || sendJob.status === 'queued'}
        >
          {sendJob.status === 'running' || sendJob.status === 'queued' ? 'Writing…' : 'Send'}
          {sendJob.status !== 'running' && sendJob.status !== 'queued' && <span>▶</span>}
        </button>
        {characterNote && <span className="character-note">Bible updated: {characterNote}</span>}
      </div>
      {(sendJob.status === 'running' || sendJob.status === 'queued') && (
        <p className="hint">{sendJob.progress[sendJob.progress.length - 1] ?? 'Starting…'}</p>
      )}
      {sendJob.status === 'failed' && <p className="error">{sendJob.error}</p>}
    </div>
  )
}
