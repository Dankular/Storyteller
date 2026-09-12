import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  addChapters,
  getChapterText,
  getSnapshot,
  removeEntity,
  startContinue,
  startGenerate,
  updateChapter,
} from '../api'
import { useJobsContext } from '../JobsContext'
import { useJob } from '../useJob'
import { beatEstablishes, beatRequires, beatText, type Chapter } from '../types'
import { Dashboard } from './Dashboard'
import { Characters } from './Characters'
import { Plan } from './Plan'
import { Continuity } from './Continuity'

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

function lastChapterKey(projectId: string) {
  return `workspace:lastChapter:${projectId}`
}

function rememberChapter(projectId: string, chapterId: string) {
  try {
    localStorage.setItem(lastChapterKey(projectId), chapterId)
  } catch {
    // per-viewer convenience only -- private window / blocked storage just means no memory next time
  }
}

function recallChapter(projectId: string): string | null {
  try {
    return localStorage.getItem(lastChapterKey(projectId))
  } catch {
    return null
  }
}

async function quickAddChapter(projectId: string): Promise<string | null> {
  const title = window.prompt('Title for the new chapter:')
  if (!title || !title.trim()) return null
  const base = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'chapter'
  let id = base
  let n = 2
  const { outline } = await getSnapshot(projectId)
  const existing = new Set(outline.map((c) => c.id))
  while (existing.has(id)) {
    id = `${base}-${n}`
    n += 1
  }
  await addChapters(projectId, [{ id, title, beats: [] }])
  return id
}

type DrawerKind = 'characters' | 'story' | 'plan' | 'continuity' | null

export function Workspace() {
  const { projectId, chapterId: routedChapterId } = useParams<{ projectId: string; chapterId?: string }>()
  const navigate = useNavigate()
  const { track } = useJobsContext()

  const [outline, setOutline] = useState<Chapter[] | null>(null)
  const [projectTitle, setProjectTitle] = useState('')
  const [drawer, setDrawer] = useState<DrawerKind>(null)

  const [chapter, setChapter] = useState<Chapter | null>(null)
  const [text, setText] = useState('')
  const [title, setTitle] = useState('')
  const [pov, setPov] = useState('')
  const [wordTarget, setWordTarget] = useState(2500)
  const [frameOf, setFrameOf] = useState('')
  const [endingStyle, setEndingStyle] = useState('')
  const [mode, setMode] = useState('outline')
  const [direction, setDirection] = useState('')
  const [rows, setRows] = useState<BeatRow[]>([])
  const [saving, setSaving] = useState(false)
  const [sendJobId, setSendJobId] = useState<string | null>(null)
  const [characterNote, setCharacterNote] = useState<string | null>(null)
  const sendJob = useJob(sendJobId)
  const isSending = sendJob.status === 'running' || sendJob.status === 'queued'
  const editorRef = useRef<HTMLTextAreaElement>(null)

  // Load the outline (for the rail) and, if the URL doesn't name a chapter, resolve one: the
  // last chapter this browser had open for this project (if it still exists), else the first in
  // outline order, else none (empty-state). Canonicalizes the URL once resolved.
  useEffect(() => {
    if (!projectId) return
    getSnapshot(projectId).then((s) => {
      setOutline(s.outline)
      setProjectTitle(s.state.title)
      if (routedChapterId) return
      const remembered = recallChapter(projectId)
      const resolved = (remembered && s.outline.some((c) => c.id === remembered) && remembered)
        || s.outline[0]?.id
      if (resolved) navigate(`/projects/${projectId}/chapters/${resolved}`, { replace: true })
    })
  }, [projectId, routedChapterId])

  const load = async () => {
    if (!projectId || !routedChapterId) return
    rememberChapter(projectId, routedChapterId)
    const [snapshot, manuscript] = await Promise.all([
      getSnapshot(projectId),
      getChapterText(projectId, routedChapterId),
    ])
    setOutline(snapshot.outline)
    const c = snapshot.outline.find((x) => x.id === routedChapterId)
    if (!c) return
    setChapter(c)
    setTitle(c.title)
    setPov(c.pov)
    setWordTarget(c.word_target)
    setFrameOf(c.frame_of ?? '')
    setEndingStyle(c.ending_style ?? '')
    setMode(c.mode || 'outline')
    setDirection(c.direction || '')
    setRows(toRows(c))
    setText(manuscript.text)
  }
  useEffect(() => {
    load()
  }, [projectId, routedChapterId])

  useEffect(() => {
    if (isSending && editorRef.current) {
      editorRef.current.scrollTop = editorRef.current.scrollHeight
    }
  }, [isSending, sendJob.streamedText])

  useEffect(() => {
    if (sendJob.status !== 'succeeded' || !sendJob.result) return
    const result = sendJob.result as { text: string; characters_created: string[]; characters_updated: string[] }
    setText(result.text)
    const notes = [
      ...result.characters_created.map((n) => `${n} introduced`),
      ...result.characters_updated.map((n) => `${n} updated`),
    ]
    setCharacterNote(notes.length > 0 ? notes.join(', ') : null)
    setChapter((c) => (c ? { ...c, status: c.status === 'planned' ? 'drafted' : c.status } : c))
  }, [sendJob.status, sendJob.result])

  const saveMeta = async () => {
    if (!projectId || !routedChapterId) return
    setSaving(true)
    try {
      await updateChapter(projectId, routedChapterId, {
        title, pov, word_target: wordTarget, frame_of: frameOf || null,
        ending_style: endingStyle || null, mode, direction,
        beats: fromRows(rows),
      })
      await load()
    } finally {
      setSaving(false)
    }
  }

  const send = async () => {
    if (!projectId || !routedChapterId) return
    setCharacterNote(null)
    const { job_id } = await startContinue(projectId, routedChapterId, text)
    setSendJobId(job_id)
    track(job_id, `Continue ${routedChapterId}`)
  }

  const regenerate = async () => {
    if (!projectId || !routedChapterId) return
    if (!window.confirm('Full rewrite runs the whole editorial pipeline (critique, revise, all checks) and REPLACES the current text -- any manual edits since the last generate will be lost. Continue?')) return
    const { job_id } = await startGenerate(projectId, routedChapterId)
    track(job_id, `Regenerate ${routedChapterId}`)
  }

  const removeChapter = async () => {
    if (!projectId || !routedChapterId || !chapter) return
    const drafted = chapter.status !== 'planned'
    if (!window.confirm(drafted
      ? `"${routedChapterId}" is already ${chapter.status} -- delete it AND its manuscript text?`
      : `Delete chapter "${routedChapterId}"?`)) return
    await removeEntity(projectId, 'chapter', routedChapterId, drafted)
    const remaining = (outline ?? []).filter((c) => c.id !== routedChapterId)
    setOutline(remaining)
    navigate(remaining[0] ? `/projects/${projectId}/chapters/${remaining[0].id}` : `/projects/${projectId}`)
  }

  const addChapter = async () => {
    if (!projectId) return
    const id = await quickAddChapter(projectId)
    if (id) navigate(`/projects/${projectId}/chapters/${id}`)
  }

  if (!projectId) return null

  return (
    <div className="workspace">
      <header className="workspace-header">
        <Link to="/" className="library-link">← Library</Link>
        <h1>{projectTitle}</h1>
        <div className="drawer-toggles">
          {(['characters', 'story', 'plan', 'continuity'] as const).map((d) => (
            <button
              key={d}
              className={drawer === d ? 'active' : ''}
              onClick={() => setDrawer(drawer === d ? null : d)}
            >
              {d === 'characters' ? 'Characters' : d === 'story' ? 'Story' : d === 'plan' ? 'Plan' : 'Continuity'}
            </button>
          ))}
        </div>
      </header>

      <div className="workspace-body">
        <nav className="chapter-rail">
          {outline?.map((c) => (
            <Link
              key={c.id}
              to={`/projects/${projectId}/chapters/${c.id}`}
              className={c.id === routedChapterId ? 'selected' : ''}
            >
              <span className={`chip status-${c.status}`}>{c.status}</span>
              <span className="rail-title">{c.title || c.id}</span>
            </Link>
          ))}
          <button className="rail-add" onClick={addChapter}>+ New chapter</button>
        </nav>

        <main className="workspace-main">
          {outline !== null && outline.length === 0 && (
            <div className="empty-state">
              <h2>No chapters yet</h2>
              <p>Write your first chapter's title to start, or open the Plan drawer to propose an outline.</p>
              <button className="primary" onClick={addChapter}>Create first chapter</button>
            </div>
          )}
          {chapter && (
            <>
              <h2>
                {routedChapterId}: {chapter.title}{' '}
                <span className={`chip status-${chapter.status}`}>{chapter.status}</span>
              </h2>

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
                  <input type="number" value={wordTarget} onChange={(e) => setWordTarget(Number(e.target.value))} />
                </label>
                <label>
                  Frame chapter (narrated from within)
                  <select value={frameOf} onChange={(e) => setFrameOf(e.target.value)}>
                    <option value="">(none -- ordinary chapter)</option>
                    {outline?.filter((c) => c.id !== routedChapterId).map((c) => (
                      <option key={c.id} value={c.id}>{c.id}: {c.title}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Ending style (overrides the book's default hook rule for this chapter)
                  <input
                    value={endingStyle}
                    onChange={(e) => setEndingStyle(e.target.value)}
                    placeholder="e.g. 'end quietly, let this one breathe' -- blank uses the book default"
                  />
                </label>
                <label>
                  Mode
                  <select value={mode} onChange={(e) => setMode(e.target.value)}>
                    <option value="outline">outline (beat checklist)</option>
                    <option value="discovery">discovery (loose direction, no checklist)</option>
                  </select>
                </label>
              </div>

              {mode === 'discovery' && (
                <label>
                  Direction (a loose starting point -- not a checklist; beats are filled in
                  retroactively after this chapter is drafted, for the record)
                  <textarea
                    value={direction}
                    onChange={(e) => setDirection(e.target.value)}
                    rows={2}
                    placeholder="e.g. 'Torvin decides whether to help Mira.'"
                  />
                </label>
              )}

              <details className="beats-details">
                <summary>Beats ({rows.length}){mode === 'discovery' && ' -- populated after drafting, not a plan'}</summary>
                <p className="hint">
                  `requires`/`establishes` use entity refs like <code>character:Torvin</code> or{' '}
                  <code>promise:some-id</code> -- leave blank for most beats.
                </p>
                {rows.map((row, i) => (
                  <div key={i} className="beat-row">
                    <textarea
                      value={row.text}
                      onChange={(e) => {
                        const next = [...rows]; next[i] = { ...next[i], text: e.target.value }; setRows(next)
                      }}
                      rows={2}
                    />
                    <input
                      placeholder="requires (comma-separated)"
                      value={row.requires}
                      onChange={(e) => {
                        const next = [...rows]; next[i] = { ...next[i], requires: e.target.value }; setRows(next)
                      }}
                    />
                    <input
                      placeholder="establishes (comma-separated)"
                      value={row.establishes}
                      onChange={(e) => {
                        const next = [...rows]; next[i] = { ...next[i], establishes: e.target.value }; setRows(next)
                      }}
                    />
                    <button onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
                  </div>
                ))}
                <button onClick={() => setRows([...rows, { text: '', requires: '', establishes: '' }])}>
                  + Add beat
                </button>
              </details>

              <div className="modal-actions">
                <button className="primary" onClick={saveMeta} disabled={saving}>
                  {saving ? 'Saving…' : 'Save chapter/beats'}
                </button>
                <button onClick={regenerate}>Full rewrite (revise + all checks)</button>
                <button className="danger" onClick={removeChapter}>Delete chapter</button>
              </div>

              <h2>Manuscript</h2>
              <p className="hint">
                Select, delete, or reword anything below, then Send -- the model continues from
                exactly what's here (your edits included), appends the next segment, and updates
                the character sheets from the result.
              </p>
              <textarea
                className={`manuscript-editor${isSending ? ' streaming' : ''}`}
                value={isSending ? text + (text.trim() ? '\n\n' : '') + sendJob.streamedText : text}
                onChange={(e) => setText(e.target.value)}
                readOnly={isSending}
                placeholder="Not drafted yet -- press Send to write the opening."
                rows={18}
                ref={editorRef}
              />
              <div className="send-row">
                <button className="primary send-button" onClick={send} disabled={isSending}>
                  {isSending ? 'Writing…' : 'Send'}
                  {!isSending && <span>▶</span>}
                </button>
                {characterNote && <span className="character-note">Bible updated: {characterNote}</span>}
              </div>
              {isSending && <p className="hint">{sendJob.progress[sendJob.progress.length - 1] ?? 'Starting…'}</p>}
              {sendJob.status === 'failed' && <p className="error">{sendJob.error}</p>}
            </>
          )}
        </main>

        {drawer && (
          <aside className="drawer">
            {drawer === 'characters' && <Characters />}
            {drawer === 'story' && <Dashboard />}
            {drawer === 'plan' && <Plan />}
            {drawer === 'continuity' && <Continuity />}
          </aside>
        )}
      </div>
    </div>
  )
}
