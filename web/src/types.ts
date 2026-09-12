// Mirrors novel_harness's dataclasses (models.py) and the webapi's JSON shapes exactly --
// keep these in sync by hand; there's no shared schema generation in this v1.

export interface Character {
  name: string
  description: string
  status: string
  arc_notes: string
  voice_notes: string
  voice_id: string | null
  introduced_in: string | null
}

export interface Location {
  name: string
  description: string
  introduced_in: string | null
}

export interface PlotThread {
  id: string
  description: string
  opened_in: string | null
  resolved_in: string | null
  status: string
}

export interface Promise {
  id: string
  description: string
  planted_in: string | null
  due_by: string | null
  status: string
  origin: string
  paid_in: string | null
}

export interface Motif {
  id: string
  phrase: string
  notes: string
  first_used_in: string | null
}

// The Telltale-games mechanic ("X will remember that") -- see models.py's Memory docstring.
export interface Memory {
  id: string
  subject: string // whose future behavior is affected
  about: string // who/what it concerns, usually another character
  event: string
  effect: string // how subject should act differently toward about going forward
  chapter_id: string | null
  status: string // active | resolved
  origin: string // manual | auto
}

// A standing relational fact between two entities ("X knows Y", "Z and Y hate each other from a
// fight") -- see models.py's Relationship docstring. Pinned into context deterministically
// whenever both sides are relevant, instead of relying on the model to remember it.
export interface Relationship {
  id: string
  a: string
  b: string
  kind: string
  polarity: string // positive | negative | neutral | complicated
  reason: string
  chapter_id: string | null
  status: string // active | resolved
  origin: string // manual | auto
}

// A motif the model flagged during extraction as recurrence-worthy but not yet promoted into a
// real Motif -- see models.py's ProjectState.motif_candidates docstring.
export interface MotifCandidate {
  id: string
  phrase: string
  notes: string
  chapter_id: string | null
}

// A beat is either a plain string, or an object declaring authored dependencies -- see
// models.py's Chapter.beats docstring.
export type StructuredBeat = { text: string; requires?: string[]; establishes?: string[] }
export type Beat = string | StructuredBeat

export function beatText(b: Beat): string {
  return typeof b === 'string' ? b : b.text
}
export function beatRequires(b: Beat): string[] {
  return typeof b === 'string' ? [] : b.requires ?? []
}
export function beatEstablishes(b: Beat): string[] {
  return typeof b === 'string' ? [] : b.establishes ?? []
}

export interface Chapter {
  id: string
  title: string
  pov: string
  beats: Beat[]
  word_target: number
  status: string // planned | drafted | revised | final
  file: string | null
  structural_beat: string | null
  frame_of: string | null // another chapter id this one is narrated from within, e.g. a flashback framed by a present-day chapter
  ending_style: string | null // free-text override of ProjectState.chapter_hook_rule for this chapter only
  mode: string // "outline" | "discovery" -- see `direction`
  direction: string // used only when mode === "discovery": a loose direction instead of a beat checklist
}

export interface ContinuityFlag {
  chapter_id: string
  issue: string
  severity: string
  resolved: boolean
  kind: string
}

export interface ProjectState {
  title: string
  premise: string
  style_guide: string
  running_summary: string
  characters: Record<string, Character>
  locations: Record<string, Location>
  plot_threads: Record<string, PlotThread>
  promises: Record<string, Promise>
  motifs: Record<string, Motif>
  memories: Record<string, Memory>
  relationships: Record<string, Relationship>
  motif_candidates: MotifCandidate[]
  genre_id: string | null
  genre_beats: unknown[]
  tropes_embrace: string[]
  tropes_avoid: string[]
  chapter_hook_rule: string
  target_chapters: number
  tags: string[]
  narrator_voice_id: string | null
}

export interface Snapshot {
  state: ProjectState
  outline: Chapter[]
  continuity: ContinuityFlag[]
  proposals: { outline: unknown[]; book_plan: unknown[] }
}

export interface ProjectEntry {
  id: string
  name: string
  path: string
}

export interface GraphNode {
  id: string
  kind: string // "chapter" | "character" | "location" | "plot_thread" | "promise"
  label: string
  established_at: string | null
}

export interface GraphEdge {
  source: string
  target: string
  kind: string // "establishes" | "references"
}

export interface DependencyGraph {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export interface AffectedChapter {
  id: string
  title: string
  status: string
}

// One candidate continuation from a beam search over the outline -- see
// pipeline.search_outline_continuations / OutlineBranch.
export interface OutlineBranch {
  chapters: Record<string, unknown>[]
  score: number
  // Each entry mixes numeric structural-check components (dependency_penalty, ...) with, when the
  // LLM judgment layer ran, llm_interest_score (number) and llm_interest_why (string) -- see
  // pipeline.search_outline_continuations/judge_branch_options.
  reward_breakdown: Record<string, number | string>[]
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'

export interface JobState {
  id: string
  kind: string
  project_id: string
  status: JobStatus
  progress: string[]
  result: unknown
  error: string | null
  created_at: number
  started_at: number | null
  finished_at: number | null
}
