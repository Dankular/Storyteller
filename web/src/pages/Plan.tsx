import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  commitPlan, getSnapshot, selectOutlineSearchBranch, startOutlineSearch, startPlan, startSwerve,
  updatePlanProposal,
} from '../api'
import { useJob } from '../useJob'
import { useJobsContext } from '../JobsContext'
import type { OutlineBranch } from '../types'

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

  const [swerveJobId, setSwerveJobId] = useState<string | null>(null)
  const swerveJob = useJob(swerveJobId)

  const [searchDepth, setSearchDepth] = useState(3)
  const [searchBranching, setSearchBranching] = useState(3)
  const [searchBeamWidth, setSearchBeamWidth] = useState(3)
  const [searchUseLlmJudgment, setSearchUseLlmJudgment] = useState(true)
  const [searchJobId, setSearchJobId] = useState<string | null>(null)
  const searchJob = useJob(searchJobId)

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

  const proposeSwerve = async () => {
    if (!projectId) return
    const { job_id } = await startSwerve(projectId)
    setSwerveJobId(job_id)
    track(job_id, 'Propose a swerve')
  }

  const runOutlineSearch = async () => {
    if (!projectId) return
    setError(null)
    const { job_id } = await startOutlineSearch(projectId, searchDepth, searchBranching, searchBeamWidth, searchUseLlmJudgment)
    setSearchJobId(job_id)
    track(job_id, `Outline search (depth ${searchDepth})`)
  }

  const useBranch = async (branch: OutlineBranch) => {
    if (!projectId) return
    setError(null)
    try {
      await selectOutlineSearchBranch(projectId, branch.chapters)
      setWholeBook(false)
      setProposalText(JSON.stringify(branch.chapters, null, 2))
    } catch (err) {
      setError(String(err))
    }
  }

  const swerveResult = swerveJob.status === 'succeeded' ? (swerveJob.result as Record<string, unknown> | null) : null
  const searchBranches = searchJob.status === 'succeeded' ? ((searchJob.result as { branches: OutlineBranch[] } | null)?.branches ?? []) : []

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

      <h2>Propose a swerve</h2>
      <p className="hint">
        A story editor brought in specifically to break a plan that's gotten too predictable --
        built from harness-computed structural material (an unresolved negative relationship to
        reignite, or a never-connected character pair to first collide) when there's any available.
        Does not write to the bible; review it and add it yourself (typically as a plot thread in
        the Characters/Story drawer) if it's worth pursuing.
      </p>
      <button onClick={proposeSwerve} disabled={swerveJob.status === 'running'}>
        {swerveJob.status === 'running' ? 'Thinking…' : 'Propose a swerve'}
      </button>
      {swerveJob.status === 'failed' && <p className="error">{swerveJob.error}</p>}
      {swerveResult && (
        <div className="summary">
          <p><strong>{String(swerveResult.description)}</strong></p>
          <p className="hint">{String(swerveResult.rationale)}</p>
          {Array.isArray(swerveResult.touches) && swerveResult.touches.length > 0 && (
            <p className="hint">Touches: {(swerveResult.touches as string[]).join(', ')}</p>
          )}
        </div>
      )}

      <h2>Search the outline</h2>
      <p className="hint">
        Beam search over candidate outline continuations. Scored two ways, layered together: a
        pure-Python structural check (dependency/relationship-tension/promise-payoff/beat
        coverage) plus, by default, an actual LLM judgment call rating each option's narrative
        interest -- the one dimension the structural check can't see. See AGENTS.md.
        Nothing is committed until you pick a branch below and then Commit as usual.
      </p>
      <div className="plan-form">
        <label>
          Depth (chapters ahead)
          <input type="number" value={searchDepth} onChange={(e) => setSearchDepth(Number(e.target.value))} />
        </label>
        <label>
          Branching (options explored per step)
          <input type="number" value={searchBranching} onChange={(e) => setSearchBranching(Number(e.target.value))} />
        </label>
        <label>
          Beam width (branches kept)
          <input type="number" value={searchBeamWidth} onChange={(e) => setSearchBeamWidth(Number(e.target.value))} />
        </label>
        <label>
          <input type="checkbox" checked={searchUseLlmJudgment} onChange={(e) => setSearchUseLlmJudgment(e.target.checked)} />
          Use LLM judgment for narrative interest (roughly doubles the call count; off falls back to structural score only)
        </label>
        <button onClick={runOutlineSearch} disabled={searchJob.status === 'running'}>
          {searchJob.status === 'running' ? 'Searching…' : 'Search'}
        </button>
      </div>
      {searchJob.status === 'failed' && <p className="error">{searchJob.error}</p>}
      {searchBranches.length > 0 && (
        <ul className="entity-list">
          {searchBranches.map((b, i) => (
            <li key={i}>
              <strong>Branch {i + 1}</strong> -- score {b.score.toFixed(1)}
              <p>{b.chapters.map((c) => `${c.id}: ${c.title}`).join(' -> ')}</p>
              {b.reward_breakdown.some((r) => r.llm_interest_why) && (
                <p className="hint">
                  {b.reward_breakdown
                    .map((r, j) => (r.llm_interest_why ? `${b.chapters[j].id}: ${r.llm_interest_why}` : null))
                    .filter(Boolean)
                    .join(' / ')}
                </p>
              )}
              <button onClick={() => useBranch(b)}>Use this branch</button>
            </li>
          ))}
        </ul>
      )}

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
