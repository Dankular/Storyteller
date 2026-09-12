import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { commitPlan, getSnapshot, startPlan, updatePlanProposal } from '../api'
import { useJob } from '../useJob'
import { useJobsContext } from '../JobsContext'

/** Triggers outline-plan/book-plan (a job -- the Architect role, see pipeline.py), shows the raw
 * proposal once it lands, lets you edit it as JSON before committing, matching the CLI's
 * review-then-commit separation (nothing is merged into outline.json until Commit is pressed). */
export function Plan() {
  const { projectId } = useParams<{ projectId: string }>()
  const { track } = useJobsContext()
  const navigate = useNavigate()
  const [count, setCount] = useState(3)
  const [wholeBook, setWholeBook] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [proposalText, setProposalText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const job = useJob(jobId)

  const loadExistingProposal = async () => {
    if (!projectId) return
    const snapshot = await getSnapshot(projectId)
    const existing = wholeBook ? snapshot.proposals.book_plan : snapshot.proposals.outline
    if (existing.length > 0) setProposalText(JSON.stringify(existing, null, 2))
  }
  useEffect(() => {
    loadExistingProposal()
  }, [projectId, wholeBook])

  useEffect(() => {
    if (job.status === 'succeeded' && job.result) {
      const result = job.result as { proposal: unknown[] }
      setProposalText(JSON.stringify(result.proposal, null, 2))
    }
    if (job.status === 'failed') setError(job.error)
  }, [job.status])

  const generate = async () => {
    if (!projectId) return
    setError(null)
    const { job_id } = await startPlan(projectId, count, wholeBook)
    setJobId(job_id)
    track(job_id, wholeBook ? 'Plan the rest of the book' : `Plan next ${count} chapter(s)`)
  }

  const saveEdits = async () => {
    if (!projectId) return
    setError(null)
    try {
      const proposal = JSON.parse(proposalText)
      await updatePlanProposal(projectId, wholeBook, proposal)
    } catch (err) {
      setError(String(err))
    }
  }

  const commit = async () => {
    if (!projectId) return
    setError(null)
    try {
      await saveEdits()
      await commitPlan(projectId, wholeBook)
      setProposalText('')
      // Back to the main workspace -- the committed chapters now show up in the chapter rail.
      navigate(`/projects/${projectId}`)
    } catch (err) {
      setError(String(err))
    }
  }

  return (
    <div className="page">
      <h1>Plan</h1>
      <p>
        Do not treat a model-generated plan as unquestionable truth -- review the arc and promise
        payoffs below before committing. Nothing is written to the outline until you press Commit.
      </p>

      <div className="plan-form">
        <label>
          <input type="checkbox" checked={wholeBook} onChange={(e) => setWholeBook(e.target.checked)} />
          Plan the REST of the book (not just the next few chapters)
        </label>
        {!wholeBook && (
          <label>
            How many chapters
            <input type="number" value={count} onChange={(e) => setCount(Number(e.target.value))} />
          </label>
        )}
        <button className="primary" onClick={generate} disabled={job.status === 'running'}>
          {job.status === 'running' ? 'Generating…' : 'Generate proposal'}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      {proposalText && (
        <>
          <h2>Proposal (edit freely, then Commit)</h2>
          <textarea
            className="proposal-editor"
            value={proposalText}
            onChange={(e) => setProposalText(e.target.value)}
            rows={20}
          />
          <div className="modal-actions">
            <button onClick={saveEdits}>Save edits</button>
            <button className="primary" onClick={commit}>
              Commit to outline
            </button>
          </div>
        </>
      )}
    </div>
  )
}
