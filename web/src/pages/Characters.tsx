import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getSnapshot, removeEntity, renameEntity, updateBible } from '../api'
import { AffectedChaptersModal } from '../components/AffectedChaptersModal'
import type { Character } from '../types'

const BLANK: Character = {
  name: '',
  description: '',
  status: '',
  arc_notes: '',
  voice_notes: '',
  voice_id: null,
  introduced_in: null,
}

export function Characters() {
  const { projectId } = useParams<{ projectId: string }>()
  const [characters, setCharacters] = useState<Record<string, Character>>({})
  const [selectedName, setSelectedName] = useState<string | null>(null)
  const [form, setForm] = useState<Character>(BLANK)
  const [creating, setCreating] = useState(false)
  const [affected, setAffected] = useState<{ name: string } | null>(null)

  const load = () => {
    if (!projectId) return
    getSnapshot(projectId).then((s) => setCharacters(s.state.characters))
  }
  useEffect(load, [projectId])

  const select = (name: string | null) => {
    setSelectedName(name)
    setCreating(false)
    setForm(name ? characters[name] : BLANK)
  }

  const save = async () => {
    if (!projectId || !form.name.trim()) return
    const originalName = selectedName
    await updateBible(projectId, {
      characters: [
        {
          name: form.name,
          description: form.description,
          status: form.status,
          arc_notes: form.arc_notes,
          voice_notes: form.voice_notes,
        },
      ],
    })
    load()
    setCreating(false)
    setSelectedName(form.name)
    // Only offer to regenerate affected chapters for an EDIT to an existing character, not a
    // brand-new one (nothing references a character that didn't exist a moment ago).
    if (originalName) setAffected({ name: form.name })
  }

  const rename = async () => {
    if (!projectId || !selectedName) return
    const newName = window.prompt('New name for ' + selectedName, selectedName)
    if (!newName || newName === selectedName) return
    await renameEntity(projectId, 'character', selectedName, newName)
    load()
    select(newName)
  }

  const remove = async () => {
    if (!projectId || !selectedName) return
    if (!window.confirm(`Remove ${selectedName} from the bible? This doesn't touch beat text or manuscript prose.`)) return
    await removeEntity(projectId, 'character', selectedName)
    load()
    select(null)
  }

  return (
    <div className="page two-col">
      <div className="col-list">
        <h1>Characters</h1>
        <button onClick={() => { setCreating(true); setSelectedName(null); setForm(BLANK) }}>
          + New character
        </button>
        <ul className="entity-list">
          {Object.values(characters).map((c) => (
            <li
              key={c.name}
              className={c.name === selectedName ? 'selected' : ''}
              onClick={() => select(c.name)}
            >
              <strong>{c.name}</strong>
              <p>{c.description}</p>
            </li>
          ))}
        </ul>
      </div>

      {(selectedName || creating) && (
        <div className="col-detail">
          <h2>{creating ? 'New character' : selectedName}</h2>
          <label>
            Name
            <input
              value={form.name}
              disabled={!creating}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            Description
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={2}
            />
          </label>
          <label>
            Status (current situation, updated as the story progresses)
            <textarea
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
              rows={2}
            />
          </label>
          <label>
            Arc notes
            <textarea
              value={form.arc_notes}
              onChange={(e) => setForm({ ...form, arc_notes: e.target.value })}
              rows={2}
            />
          </label>
          <label>
            Voice notes (diction/rhythm/tics -- pinned into context whenever this character is POV)
            <textarea
              value={form.voice_notes}
              onChange={(e) => setForm({ ...form, voice_notes: e.target.value })}
              rows={3}
            />
          </label>
          {!creating && form.introduced_in && (
            <p className="hint">Established in chapter {form.introduced_in}.</p>
          )}
          <div className="modal-actions">
            <button className="primary" onClick={save}>
              Save
            </button>
            {!creating && (
              <>
                <button onClick={rename}>Rename…</button>
                <button className="danger" onClick={remove}>
                  Remove
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {affected && projectId && (
        <AffectedChaptersModal
          projectId={projectId}
          kind="character"
          entityId={affected.name}
          entityLabel={affected.name}
          onClose={() => setAffected(null)}
        />
      )}
    </div>
  )
}
