import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getSnapshot, removeEntity, resolveMemory, updateBible } from '../api'
import type { Memory, Motif, Snapshot } from '../types'

export function Dashboard() {
  const { projectId } = useParams<{ projectId: string }>()
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [styleGuide, setStyleGuide] = useState('')
  const [tags, setTags] = useState('')
  const [genre, setGenre] = useState('')
  const [saving, setSaving] = useState(false)
  const [newMotifPhrase, setNewMotifPhrase] = useState('')
  const [newMotifNotes, setNewMotifNotes] = useState('')
  const [newMemSubject, setNewMemSubject] = useState('')
  const [newMemAbout, setNewMemAbout] = useState('')
  const [newMemEvent, setNewMemEvent] = useState('')
  const [newMemEffect, setNewMemEffect] = useState('')

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

  const addMotif = async () => {
    if (!projectId || !newMotifPhrase.trim()) return
    const id = newMotifPhrase
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      .slice(0, 40) || `motif-${Date.now()}`
    await updateBible(projectId, { motifs: [{ id, phrase: newMotifPhrase, notes: newMotifNotes }] })
    setNewMotifPhrase('')
    setNewMotifNotes('')
    load()
  }

  const removeMotif = async (id: string) => {
    if (!projectId) return
    await removeEntity(projectId, 'motif', id)
    load()
  }

  const addMemory = async () => {
    if (!projectId || !newMemSubject.trim() || !newMemEvent.trim() || !newMemEffect.trim()) return
    const id = `mem-${Date.now().toString(36)}`
    await updateBible(projectId, {
      memories: [{ id, subject: newMemSubject, about: newMemAbout, event: newMemEvent, effect: newMemEffect }],
    })
    setNewMemSubject('')
    setNewMemAbout('')
    setNewMemEvent('')
    setNewMemEffect('')
    load()
  }

  const removeMemory = async (id: string) => {
    if (!projectId) return
    await removeEntity(projectId, 'memory', id)
    load()
  }

  const markMemoryResolved = async (id: string) => {
    if (!projectId) return
    await resolveMemory(projectId, id)
    load()
  }

  if (!snapshot) return <p>Loading…</p>
  const { state, outline, continuity } = snapshot
  const openFlags = continuity.filter((f) => !f.resolved).length
  const motifs = Object.values(state.motifs) as Motif[]
  const memories = Object.values(state.memories) as Memory[]
  const characterNames = Object.keys(state.characters)

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

      <h2>Motifs</h2>
      <p className="hint">
        A recurring phrase or image meant to gain weight each time it resurfaces -- pinned into
        every chapter's context from here on, unlike a promise (which is a setup awaiting one
        specific payoff).
      </p>
      <ul className="motif-list">
        {motifs.map((m) => (
          <li key={m.id}>
            <strong>&ldquo;{m.phrase}&rdquo;</strong>
            {m.notes && <span className="motif-notes"> — {m.notes}</span>}
            {m.first_used_in && <span className="chip">since {m.first_used_in}</span>}
            <button className="danger" onClick={() => removeMotif(m.id)}>
              Remove
            </button>
          </li>
        ))}
        {motifs.length === 0 && <p>No motifs yet.</p>}
      </ul>
      <div className="new-project">
        <input
          placeholder="The recurring phrase/image, verbatim"
          value={newMotifPhrase}
          onChange={(e) => setNewMotifPhrase(e.target.value)}
        />
        <input
          placeholder="Notes: what it means / why it should recur (optional)"
          value={newMotifNotes}
          onChange={(e) => setNewMotifNotes(e.target.value)}
        />
        <button onClick={addMotif} disabled={!newMotifPhrase.trim()}>
          Add motif
        </button>
      </div>

      <h2>Memories</h2>
      <p className="hint">
        The Telltale-games mechanic ("X will remember that") -- a specific past incident that keeps
        shaping how one character treats another, distinct from a promise (resolves once, to the
        reader) or a character's general status. Pinned into context whenever the subject is
        relevant, until resolved.
      </p>
      <ul className="motif-list">
        {memories.map((m) => (
          <li key={m.id} className={m.status === 'resolved' ? 'resolved' : ''}>
            <strong>
              {m.subject}
              {m.about && ` re: ${m.about}`}
            </strong>
            <span className="motif-notes"> — {m.event}. Effect: {m.effect}</span>
            <span className="chip">{m.status}</span>
            {m.status === 'active' && (
              <button onClick={() => markMemoryResolved(m.id)}>Mark resolved</button>
            )}
            <button className="danger" onClick={() => removeMemory(m.id)}>
              Remove
            </button>
          </li>
        ))}
        {memories.length === 0 && <p>No memories yet.</p>}
      </ul>
      <div className="new-project">
        <input
          placeholder="Subject (whose future behavior is affected)"
          list="character-names"
          value={newMemSubject}
          onChange={(e) => setNewMemSubject(e.target.value)}
        />
        <input
          placeholder="About (who/what it concerns, optional)"
          list="character-names"
          value={newMemAbout}
          onChange={(e) => setNewMemAbout(e.target.value)}
        />
        <datalist id="character-names">
          {characterNames.map((n) => (
            <option key={n} value={n} />
          ))}
        </datalist>
        <input
          placeholder="What happened, briefly"
          value={newMemEvent}
          onChange={(e) => setNewMemEvent(e.target.value)}
        />
        <input
          placeholder="Effect: how subject should act differently toward about going forward"
          value={newMemEffect}
          onChange={(e) => setNewMemEffect(e.target.value)}
        />
        <button onClick={addMemory} disabled={!newMemSubject.trim() || !newMemEvent.trim() || !newMemEffect.trim()}>
          Add memory
        </button>
      </div>

      <h2>Running summary</h2>
      <p className="summary">{state.running_summary || '(empty so far)'}</p>
    </div>
  )
}
