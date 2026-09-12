import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { addProject, createProject, listProjects, removeProject } from '../api'
import type { ProjectEntry } from '../types'

export function Library() {
  const [projects, setProjects] = useState<ProjectEntry[]>([])
  const [path, setPath] = useState('')
  const [newTitle, setNewTitle] = useState('')
  const [newPremise, setNewPremise] = useState('')
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  const refresh = () => listProjects().then((r) => setProjects(r.projects))
  useEffect(() => {
    refresh()
  }, [])

  const add = async () => {
    setError(null)
    try {
      await addProject(path)
      setPath('')
      refresh()
    } catch (err) {
      setError(String(err))
    }
  }

  const create = async () => {
    setError(null)
    try {
      const entry = await createProject(newTitle, newPremise)
      setNewTitle('')
      setNewPremise('')
      refresh()
      navigate(`/projects/${entry.id}`)
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <div className="page">
      <h1>Novels</h1>

      <div className="new-project">
        <h3>Start a new novel</h3>
        <input
          placeholder="Title"
          value={newTitle}
          onChange={(e) => setNewTitle(e.target.value)}
        />
        <textarea
          placeholder="Premise -- a sentence or two"
          value={newPremise}
          onChange={(e) => setNewPremise(e.target.value)}
          rows={2}
        />
        <button className="primary" onClick={create} disabled={!newTitle.trim() || !newPremise.trim()}>
          Create
        </button>
      </div>

      <div className="add-project">
        <h3>Or add an existing project by path</h3>
        <input
          placeholder="Path to an existing novel_harness project (has state.json)…"
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button onClick={add} disabled={!path.trim()}>
          Add
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="project-grid">
        {projects.map((p) => (
          <div key={p.id} className="project-card" onClick={() => navigate(`/projects/${p.id}`)}>
            <h3>{p.name}</h3>
            <p className="path">{p.path}</p>
            <button
              className="remove"
              onClick={(e) => {
                e.stopPropagation()
                removeProject(p.id).then(refresh)
              }}
            >
              Remove from library
            </button>
          </div>
        ))}
        {projects.length === 0 && <p>No novels yet -- add one by path above.</p>}
      </div>
    </div>
  )
}
