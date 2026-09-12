# Design philosophy

This document names the design principles this codebase already follows — extracted from how
`novel_harness` is actually built, not aspirational — so a new feature (web UX, storytelling
mechanic, or agent capability) can be checked against the same reasoning the existing ones were
built with, instead of each addition inventing its own shape. See `README.md` for what the harness
does and `AGENTS.md` for the agent contract; this is about *why* it's built the way it is.

## The principles

### 1. Compute what's computable; hand the model only what isn't

Every fact that can be derived deterministically from the bible/outline is computed in pure Python
and handed to the model (or the author) as concrete material — never left to model inference or
long-range memory. `depgraph.check_dependencies` (forward references, promise/thread ordering),
`pipeline.audit_promise_staleness` (overdue setups), `context._build_relationships_block` (pinning
"X and Y hate each other" into context every time both are relevant instead of trusting the model to
remember it from chapters back), and `pipeline._swerve_structural_material` (handing `swerve_propose`
an unresolved feud or a never-connected character pair to build from, instead of asking for a
complication out of nowhere) are all the same move. A new feature should ask first: *is any part of
this a fact the harness already knows, or could compute, rather than something to ask the model to
notice or remember?*

### 2. Two-layer checks: exact first, judgment layered on top, never instead

Every automated safeguard splits into a cheap, exact, pure-Python layer (necessarily narrow — it can
only validate structure, never quality) and, only where that layer is genuinely blind, an explicit
LLM judgment call stacked *on top* of it, weighted so neither layer silently dominates. `outline_search`
is the clearest instance: `_score_branch_step` (exact, structural) plus `judge_branch_options` (an
actual "which option is more interesting" judgment), rescaled and combined, not substituted.
`check_dependencies`/`check_relationship_tensions` vs. the continuity/POV/beat-coverage checks follow
the same split at the whole-pipeline level — see README's "Design notes" for which checks are exact
and which "can miss things or hallucinate an issue that isn't real." A new check should be honest
about which side of that line it's on, and never presented as more certain than it is.

### 3. Proposal, then commit — nothing model-authored writes itself into canon

`plan`/`commit`, `outline_search`/`outline_search_select`, `swerve_propose` (never writes to the
bible at all), and motif candidates (`motif_candidate_promote`/`_dismiss`, an editorial choice, not
an auto-committed fact) all share one shape: the model proposes, a human or agent reviews, and only
an explicit second step commits. A new generative feature should default to this shape unless there's
a specific reason a fact is safe to auto-commit (the way `extract_and_update_state`'s character
status updates and new promises/memories/relationships are, because they're read directly off
already-written prose, not invented from a blank page).

### 4. The bible is editable, not append-only

Every entity kind has `remove`/`rename`/`resolve` alongside `add` — a wrong or outgrown character,
location, thread, promise, motif, memory, or relationship is a first-class correction, not something
you work around by adding a countermanding entry. A feature that only ever grows the bible without a
way to correct it is missing half of what every existing entity kind already has.

### 5. Context is a recomputed view, never a second source of truth

`context.build_chapter_context` and `depgraph.build_dependency_graph` are both explicitly documented
as views recomputed fresh from `state.json`/`outline.json` on every call — nothing about "what's
relevant to this chapter" or "what depends on what" is cached or persisted separately from the bible
itself. This is why the bible being editable (principle 4) doesn't produce stale derived state
anywhere. A new feature that needs a "what does the story currently look like" answer should compute
it from the bible/outline at call time, not maintain a separate index that can drift.

### 6. Scope failure to its actual blast radius

`generate_chapter`'s `_safe_stage` wraps every post-draft check/extraction call so a $0.02 JSON call
failing never destroys the minutes of work a draft/revise call already produced — the chapter text
is saved before anything else can touch it, and a failed check becomes a `pipeline_error` continuity
flag, not a lost chapter. Draft and revise are deliberately *not* wrapped this way, because there's
nothing to protect yet if those fail. A new pipeline stage should ask: if this fails, what's the
cheapest correct thing to lose — and make sure that's actually all that's lost.

### 7. State the cost, and give a cheaper path

Every expensive feature documents its model-call cost up front — `generate`'s "~13 model calls
baseline," `outline_search`'s "at most 14 (7 without judgment)" — and pairs it with an explicit
cheaper alternative: `--no-check`/`--no-beat-check`/etc. flags, `continue_chapter` (2 calls) as the
lightweight sibling to `generate` (up to ~13), discovery mode skipping beat-coverage entirely. A
costly new feature without a stated cost and a cheaper opt-out is inconsistent with everything else
here.

### 8. More than one right way to draft

Outline mode (a beat checklist, checked for coverage) and discovery mode (a loose direction, no
checklist, beats derived retroactively) are both first-class drafting philosophies, not one "correct"
pipeline with an escape hatch bolted on. A feature that only serves the planned, beat-checked mode
should say so explicitly rather than silently assuming it's the only mode in use.

### 9. Ground prompts in named failure modes, not "write better"

`TENSION_DISCIPLINE_NOTE` and `PACING_AND_RESTRAINT_NOTE` (`pipeline.py`) exist because of specific
cited research on how LLM fiction actually fails (resolving tension too early, flattening pacing to
uniform full-narration) — not generic craft boilerplate. The sharpen pass exists because critique/
revise is *provably* one-directional (only ever pulls toward spec compliance, never rewards a risky
choice). A new prompt-level intervention should be able to name the specific failure mode it corrects
for; if it can't, it's probably not adding anything a generic "write well" instruction wasn't already
asking for.

### 10. Agent and human surfaces are separate, on top of one library

`agent_wrapper.py` is a stable, JSON-only, non-interactive contract; `cli.py` and `webapi/`+`web/`
are richer, friendlier surfaces built on the *same* `pipeline.py`/`storage.py`, never the reverse
(README: "a fourth frontend over the same library `cli.py`/`agent_wrapper.py` already use — thin
wrappers ... not a reimplementation of bible/outline logic"). A new capability belongs in the shared
library first; whether it's *exposed* to agents, the CLI, the web UI, or some subset is a separate
decision made after, not a reason to duplicate logic in one surface only.

## A checklist for evaluating a new addition

Before building a proposed feature (web UX, a new storytelling mechanic, or a new agent action), ask:

1. **Is any part of it a fact the harness can compute instead of asking the model to infer or
   remember?** (principle 1) If so, compute it in pure Python and hand it over as material/context.
2. **Does it need a judgment call a deterministic check can't make?** (principle 2) If so, layer an
   explicit LLM call on top of the exact check, weighted so it doesn't silently dominate — and be
   honest in the docs about which parts are exact and which are judgment.
3. **Does it invent or propose something?** (principle 3) Default to proposal-then-commit unless
   it's reading a fact directly off already-written prose.
4. **Does it add a new entity/record kind?** (principle 4) Give it `remove`/`rename`/`resolve` from
   day one, not as a later patch.
5. **Does it need a "what does the story look like right now" answer?** (principle 5) Compute it
   from `state.json`/`outline.json` at call time; don't cache a second copy.
6. **Can it fail midway?** (principle 6) Scope the failure boundary before writing the happy path.
7. **Does it cost real model calls?** (principle 7) State the cost, and provide a cheaper/skippable
   path.
8. **Does it assume outline mode?** (principle 8) Say so explicitly if discovery mode isn't served.
9. **Is it a prompt-level craft intervention?** (principle 9) Name the specific failure mode it's
   correcting for.
10. **Where does it live?** (principle 10) Library first (`pipeline.py`/`storage.py`/`context.py`/
    `depgraph.py`), then decide which surface(s) expose it.

## Research directions worth exploring against this list

The following are **brainstormed candidates, not a roadmap** — none of these are decided or planned;
they're starting points for research, sized against the principles above, and some are already named
as explicit gaps in `README.md`'s "Design notes."

**Web UX**

- An actual interactive dependency-graph view. `README.md` already names this as a known limitation
  ("shown as a list, not an interactive diagram, in this first version") — `dependency_graph`'s
  nodes/edges are already computed (principle 5); this would be a pure rendering feature on data
  that already exists, no new backend logic.
- Manuscript-wide search/find (character name, motif phrase, promise description) across chapters —
  currently there's no cross-chapter text search surfaced anywhere; `manuscript/<id>.md` files exist
  on disk already, so this is a read-only view over existing data (principle 5), not new state.
- A continuity/promise "board" view grouping open continuity_log flags by kind and chapter, since
  today `continuity-show`/the snapshot's `continuity` array is a flat list a human has to scan.

**Storytelling / craft**

- A cross-chapter "motif never returned" check: pure Python (principle 1/2) — a motif with
  `first_used_in` set but no later chapter's beats/text mentioning its phrase again could be flagged,
  the same "note, not proof" spirit as `check_relationship_tensions`.
- Multi-POV consistency across alternating chapters — today `check_pov_consistency` is scoped to one
  chapter at a time; a cross-chapter version (closer to `audit_manuscript`'s whole-manuscript scope)
  could catch a voice profile drifting gradually across several of a character's POV chapters rather
  than only within one.
- A pacing dashboard derived from what's already computed: structural-beat tension targets
  (`state.genre_beats`) plotted against actual critique/tension-check findings history per chapter —
  a read-only view over existing data (principle 5), not a new check.
- Extending `check_authoring_coverage`-style nudges (this change) to promises/threads once a more
  precise match than free-text substring is available for them (today deliberately excluded — see
  the function's own docstring — because a description-substring match on those two kinds would
  false-positive constantly).

Each of these should go through the checklist above before being scoped into real work — several
touch principle 7 (cost) immediately (a cross-chapter POV check is closer to `audit_manuscript`'s
cost profile than a per-chapter one) and should state that cost up front rather than being pitched
as "free."
