// Thin fetch wrapper over novel_harness's web API (novel_harness/webapi/routers/*.py) -- every
// function here is a direct call to one endpoint, no client-side logic beyond that.
import type {
  AffectedChapter,
  Chapter,
  DependencyGraph,
  JobState,
  ProjectEntry,
  Snapshot,
} from './types'

class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ? JSON.stringify(body.detail) : detail
    } catch {
      // response wasn't JSON -- keep statusText
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const json = (body: unknown) => JSON.stringify(body)

// ---- projects ----
export const listProjects = () => request<{ projects: ProjectEntry[] }>('/api/projects')
export const addProject = (path: string, name?: string) =>
  request<ProjectEntry>('/api/projects', { method: 'POST', body: json({ path, name }) })
export const createProject = (title: string, premise: string, styleGuide?: string) =>
  request<ProjectEntry>('/api/projects/new', {
    method: 'POST',
    body: json({ title, premise, style_guide: styleGuide }),
  })
export const removeProject = (id: string) =>
  request<{ removed: string }>(`/api/projects/${id}`, { method: 'DELETE' })
export const getSnapshot = (id: string) => request<Snapshot>(`/api/projects/${id}/snapshot`)

// ---- bible ----
export const updateBible = (id: string, payload: Record<string, unknown>) =>
  request<Snapshot>(`/api/projects/${id}/bible`, { method: 'PATCH', body: json(payload) })
export const removeEntity = (id: string, kind: string, entityId: string, force = false) =>
  request<Record<string, unknown>>(
    `/api/projects/${id}/entities/${kind}/${encodeURIComponent(entityId)}?force=${force}`,
    { method: 'DELETE' },
  )
export const renameEntity = (id: string, kind: string, oldId: string, newId: string) =>
  request<Record<string, unknown>>(`/api/projects/${id}/entities/${kind}/rename`, {
    method: 'POST',
    body: json({ old_id: oldId, new_id: newId }),
  })
export const resolvePromise = (id: string, promiseId: string, chapterId: string) =>
  request<Record<string, unknown>>(`/api/projects/${id}/promises/${promiseId}/resolve`, {
    method: 'POST',
    body: json({ chapter_id: chapterId }),
  })
export const resolveMemory = (id: string, memoryId: string) =>
  request<Record<string, unknown>>(`/api/projects/${id}/memories/${memoryId}/resolve`, {
    method: 'POST',
  })
export const resolveRelationship = (id: string, relationshipId: string) =>
  request<Record<string, unknown>>(`/api/projects/${id}/relationships/${relationshipId}/resolve`, {
    method: 'POST',
  })
export const promoteMotifCandidate = (id: string, candidateId: string, motifId?: string, notes?: string) =>
  request<Record<string, unknown>>(`/api/projects/${id}/motif-candidates/${encodeURIComponent(candidateId)}/promote`, {
    method: 'POST',
    body: json({ motif_id: motifId, notes }),
  })
export const dismissMotifCandidate = (id: string, candidateId: string) =>
  request<Record<string, unknown>>(`/api/projects/${id}/motif-candidates/${encodeURIComponent(candidateId)}`, {
    method: 'DELETE',
  })

// ---- outline ----
export const addChapters = (id: string, chapters: Partial<Chapter>[]) =>
  request<Record<string, unknown>>(`/api/projects/${id}/chapters`, {
    method: 'POST',
    body: json({ chapters }),
  })
export const updateChapter = (id: string, chapterId: string, payload: Record<string, unknown>) =>
  request<{ chapter: Chapter; outline: Chapter[] }>(
    `/api/projects/${id}/chapters/${chapterId}`,
    { method: 'PATCH', body: json(payload) },
  )
export const getChapterText = (id: string, chapterId: string) =>
  request<{ text: string }>(`/api/projects/${id}/chapters/${chapterId}/text`)

// ---- dependency ----
export const getDependencyGraph = (id: string) =>
  request<DependencyGraph>(`/api/projects/${id}/dependency-graph`)
export const runDependencyCheck = (id: string) =>
  request<{ flags: unknown[] }>(`/api/projects/${id}/dependency-check`, { method: 'POST' })
export const getAffectedChapters = (id: string, kind: string, entityId: string) =>
  request<{ entity: string; affected_chapters: AffectedChapter[] }>(
    `/api/projects/${id}/entities/${kind}/${encodeURIComponent(entityId)}/affected-chapters`,
  )

// ---- continuity ----
export const listContinuity = (id: string) =>
  request<{ continuity: Snapshot['continuity'] }>(`/api/projects/${id}/continuity`)
export const resolveContinuity = (id: string, index: number) =>
  request<Record<string, unknown>>(`/api/projects/${id}/continuity/${index}/resolve`, {
    method: 'POST',
  })

// ---- generation (job-backed) ----
export const startGenerate = (id: string, chapterId: string, options?: Record<string, unknown>) =>
  request<{ job_id: string }>(`/api/projects/${id}/chapters/${chapterId}/generate`, {
    method: 'POST',
    body: json({ options }),
  })
export const startContinue = (id: string, chapterId: string, editedText: string) =>
  request<{ job_id: string }>(`/api/projects/${id}/chapters/${chapterId}/continue`, {
    method: 'POST',
    body: json({ edited_text: editedText }),
  })
export const startPlan = (id: string, count: number, wholeBook: boolean) =>
  request<{ job_id: string }>(`/api/projects/${id}/plan`, {
    method: 'POST',
    body: json({ count, whole_book: wholeBook }),
  })
export const updatePlanProposal = (id: string, wholeBook: boolean, proposal: unknown[]) =>
  request<Record<string, unknown>>(`/api/projects/${id}/plan-proposal`, {
    method: 'PATCH',
    body: json({ whole_book: wholeBook, proposal }),
  })
export const commitPlan = (id: string, wholeBook: boolean) =>
  request<Record<string, unknown>>(`/api/projects/${id}/commit`, {
    method: 'POST',
    body: json({ whole_book: wholeBook }),
  })
export const startAudit = (id: string) =>
  request<{ job_id: string }>(`/api/projects/${id}/audit`, { method: 'POST' })
export const startSwerve = (id: string) =>
  request<{ job_id: string }>(`/api/projects/${id}/swerve`, { method: 'POST' })
export const startOutlineSearch = (
  id: string, depth: number, branching: number, beamWidth: number, useLlmJudgment = true,
) =>
  request<{ job_id: string }>(`/api/projects/${id}/outline-search`, {
    method: 'POST',
    body: json({ depth, branching, beam_width: beamWidth, use_llm_judgment: useLlmJudgment }),
  })
export const selectOutlineSearchBranch = (id: string, chapters: Record<string, unknown>[]) =>
  request<Record<string, unknown>>(`/api/projects/${id}/outline-search/select`, {
    method: 'POST',
    body: json({ chapters }),
  })

// ---- jobs ----
export const getJob = (jobId: string) => request<JobState>(`/api/jobs/${jobId}`)

export function jobWebSocketUrl(jobId: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/jobs/${jobId}`
}

export { ApiError }
