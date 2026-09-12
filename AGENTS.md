# AGENTS.md

## Purpose

This repository contains `novel_harness`, a persistent long-form fiction harness. Agents should use `novel_harness.agent_wrapper` for story-building work because it is non-interactive and returns one JSON response per invocation.

## Agent interface

Run from the repository root:

```powershell
python -m novel_harness.agent_wrapper --root .\my-novel snapshot '{}'
python -m novel_harness.agent_wrapper --root .\my-novel init '{"title":"The Gearworks","premise":"A dismissed apprentice discovers a city-sized machine beneath the council hall.","style_guide":"Close third person, past tense, restrained prose."}'
```

The payload may be `-` to read JSON from stdin. stdout is JSON only; model progress is written to stderr. Every response has either `{"ok":true,"result":...}` or `{"ok":false,"error":...}`.

Supported actions:

- `init`: create a project. Requires `title` and `premise`.
- `snapshot`: return the bible, outline, continuity log, and pending proposals. Each continuity
  flag's position in the `continuity` array returned here is its `index` for `continuity_resolve`.
- `update_bible`: **partial update** — merges `genre`, `tags`, `style_guide`, `target_chapters`,
  and arrays of `characters`, `locations`, `plot_threads`, `promises`, `motifs`, `memories`, or
  `relationships` into the existing bible. Each character/location entry is keyed by `name`, each
  plot_thread/promise/motif/memory/relationship by `id`; only the fields you actually pass on an
  existing entry change (e.g. `{"characters":[{"name":"Mira Voss","voice_notes":"..."}]}` updates
  only `voice_notes`, leaving `description`/`status`/etc. untouched) — a `name`/`id` that doesn't
  exist yet creates a new entry instead. A memory is `{"id":"...", "subject":"<character whose
  future behavior is affected>", "about":"<who/what it concerns, usually another character>",
  "event":"<what happened>", "effect":"<how subject should act differently toward about going
  forward>", "chapter_id":"...", "status":"active|resolved"}` — the Telltale-games "X will remember
  that" mechanic: a specific incident that keeps shaping how one character treats another, pinned
  into that character's context whenever relevant until resolved. A relationship is `{"id":"...",
  "a":"...", "b":"...", "kind":"rivals|married|acquainted|...", "polarity":"positive|negative|
  neutral|complicated", "reason":"...", "status":"active|resolved"}` — a STANDING relational fact
  ("X knows Y", "Z and Y hate each other from a fight"), distinct from a memory (one-directional,
  event-derived): pinned into context deterministically whenever BOTH sides are relevant, so the
  model is told the fact every time rather than trusted to remember it from earlier chapters, and
  checked by `dependency_check` (a negative-polarity, unresolved relationship mentioned in the same
  beats gets flagged as a reminder). A motif is `{"id":"...", "phrase":"...", "notes":"...",
  "first_used_in":"<chapter id or null>"}` — a recurring line/image meant to resurface with more
  weight each time, always pinned into every chapter's context from `first_used_in` onward (unlike
  promises/memories/relationships, which are relevance-filtered). `genre` is always free text,
  never a fixed list: a handful of built-in beat-sheets (thriller/romance/mystery/horror/
  epic_fantasy/literary/three_act) get cloned in for free if your wording happens to match one
  exactly; anything else, the model generates an equivalent bespoke beat-sheet for whatever you
  typed, so every genre gets real structural guidance, not just those few (this makes one model
  call when nothing matches).
- `add_chapters`: append explicit chapter objects. Each needs `id` and `title`; use `beats`, `pov`, `word_target`, `structural_beat`, `ending_style`, `mode`, and `direction` when relevant. Errors if an `id` already exists -- use `update_chapter` to edit one. Each entry in `beats` may be a plain string (checked only by substring inference) or `{"text":"...", "requires":["character:Name", ...], "establishes":["promise:some-id", ...]}` to author an exact dependency edge instead -- refs are `kind:name-or-id` with kind one of character/location/plot_thread/promise. `plan`/`commit` already emit this shape for you; hand-authoring it yourself here is more reliable than counting on the (9B, local) model to. `ending_style` is a free-text override of the book's `chapter_hook_rule` for just this chapter (e.g. "end quietly, let this one breathe") -- without it every chapter in the book ends on the same shape by construction. `mode: "discovery"` (default is `"outline"`) drafts the chapter from a loose `direction` string instead of a beat checklist -- no coverage check runs against it, and `beats` gets populated RETROACTIVELY after drafting purely for downstream display/dependency-graph consumers, never fed back as a constraint on the text that produced it.
- `update_chapter`: partial update of an existing chapter. `{"chapter_id":"ch01", ...fields}` merges `title`/`pov`/`beats`/`word_target`/`structural_beat`/`ending_style`/`mode`/`direction`/`status` (only the fields given); `id`/`file` can't be changed this way.
- `remove`: delete a bible entry or a chapter. `{"kind":"character|location|plot_thread|promise|motif|memory|relationship|chapter", "id":"...", "force":false}`. Deleting a chapter that's already drafted/revised/final is refused unless `force:true`, which also deletes its manuscript file. Removing an entry does not rewrite beat text, manuscript prose, or other entries that reference its id/name -- dangling references are left as-is.
- `rename`: `{"kind":"character|location|plot_thread|promise|motif|memory|relationship", "old_id":"...", "new_id":"..."}`. Renaming a character also updates every chapter's `pov` that referenced the old name; it does not rewrite beat text or manuscript prose. Chapters can't be renamed (their id is also their filename and is referenced by other records) -- remove and re-add instead.
- `promise_resolve`: `{"id":"...", "chapter_id":"..."}` marks a promise paid in the given chapter.
- `memory_resolve`: `{"id":"..."}` marks a memory resolved (a grudge forgiven, trust repaired) -- it stops being pinned into context but stays in the bible as a record.
- `relationship_resolve`: `{"id":"..."}` marks a relationship resolved (a feud ended, a bond mended) -- same effect on context-pinning as `memory_resolve`.
- `motif_candidate_promote`: `{"id":"...", "motif_id":"<optional>", "notes":"<optional>"}` turns a pending, extraction-flagged motif candidate (see `snapshot`'s `state.motif_candidates`) into a real motif -- an editorial choice a human/agent makes deliberately, unlike a promise or memory, which extraction commits automatically.
- `motif_candidate_dismiss`: `{"id":"..."}` discards a pending motif candidate without creating a motif.
- `continuity_resolve`: `{"index":<int>}` marks one continuity flag resolved, by its position in the array `snapshot`/the continuity log returns (stable -- flags are only ever appended).
- `voice_search`: `{"query":"", "gender":null, "age":null, "accent":null, "language":null, "limit":20}` searches the ~11K-entry TTS voice catalog. No extra dependencies needed.
- `voice_assign`: `{"target":"narrator"|"<character name>", "voice_id":"..."}` assigns a catalog voice, downloading and transcribing its reference clip now. **Requires the optional narration dependencies** (`soundfile`, `librosa`, `torch`, `torchaudio`, `transformers` -- see `requirements.txt`); without them this raises a normal structured error, not a crash. A narrator voice must be assigned before `generate`'s `options.narrate` (below) can succeed at all.
- `character_generate`: `{"name":null, "age":null}` — one model call grounded in the project's own premise/genre/tags, returning a `{name, description, status, voice_notes}` sheet. Does not write to the bible itself; review it, then add it with `update_bible`.
- `title_generate`: `{"tags":[...]}` (optional; defaults to the project's stored tags) — generates a title from the project's premise/genre.
- `dependency_graph`: `{}` — returns `{"nodes":[...], "edges":[...]}`, a structural view over the current bible+outline. Nodes are chapters plus every character/location/plot_thread/promise, each tagged with the chapter id it was established at (`introduced_in`/`opened_in`/`planted_in`; `null` = pre-established, safe anywhere). Edges are `establishes` (entity's own establishing chapter) and `references` (a chapter whose beats mention that entity, via the same substring match `update_bible`'s context assembly already uses). No model call; safe to inspect an outline's shape before drafting anything.
- `dependency_check`: `{}` — pure-Python "Dependency Manager" pass over the same graph: flags a chapter referencing something established in a *later* chapter, a promise due at or before its own `planted_in`, a thread `resolved_in` before its `opened_in`, or an authored `requires` naming something not established anywhere in the current plan at all. Also runs automatically at the end of every `generate`. Flags land in `continuity_log.json` with `kind:"dependency"`, resolvable via `continuity_resolve` like any other flag. Inherits the substring-match blind spot noted above (a pronoun/epithet-only reference won't be caught) -- a clean result means "no *detectable* violation," not proof none exists. The check itself is fully deterministic; only how *precise* it is depends on whether beats declared authored `requires`/`establishes`. Also includes `check_authoring_coverage`'s "note"-severity nudge: a beat mentioning 2+ named characters/locations with no `requires`/`establishes` declared at all gets flagged as a candidate for exact authoring -- a craft suggestion, not a detected violation, and not something a clean run of the rest of this check should be read as endorsing either way.
- `generate`/`continue_chapter` also run a **pre-draft check** before spending any model calls: the same `dependency_check`/relationship-tension logic, filtered to just the chapter about to be (re)written, surfaced as `progress` lines (stderr) only -- not persisted to `continuity_log.json`, since the full post-hoc check below re-runs unfiltered anyway. Run `dependency_check` yourself beforehand if you want these as durable, resolvable flags before spending the calls.
- `plan`: ask the model for a proposal. Use `{"count":3}` for the next chapters or `{"count":20,"whole_book":true}` for the remaining book.
- `commit`: commit the matching pending proposal with `{"whole_book":true}` for a book plan. Review or edit proposal JSON before committing.
- `generate`: write one chapter. Requires `chapter_id`; optional `options` can disable `revise`, `critique`, `check`, `beat_check`, `patch_missing`, `pov_check`, `tension_check`, `sharpen`, or enable `narrate` (requires a narrator voice already assigned via `voice_assign`). `critique_rounds` (default 2, capped at 4) controls how many critique->revise cycles run when `critique` is on: round 1 always revises, round 2+ only if a fresh critique of the just-revised text still finds something (it stops early otherwise) -- `critique_rounds:1` reproduces the old one-shot behavior. A missing beat's patch is also re-verified against just that beat rather than trusted blindly; anything still missing/rushed lands in `continuity_log.json` as `beat_coverage`. `sharpen` (default on) runs a final pass whose only job is replacing the safest/most cliché phrasing left in the draft with something more specific -- a deliberate counterweight to critique/revise, which only ever pulls toward spec compliance and never pushes past what's safe. For a `mode:"discovery"` chapter, `beat_check`/`patch_missing` are skipped (there's no pre-authored beat list to check coverage against) and `beats` is populated retroactively from the finished text instead. This is the *full* pipeline (up to ~13 model calls) and **overwrites** the chapter's current text -- for incremental co-writing use `continue_chapter` instead.
- `continue_chapter`: `{"chapter_id":"...", "edited_text":"<optional>"}` -- appends ONE continuation segment to the chapter instead of rewriting it (2 model calls: continue, then fact extraction), and updates the bible from the result. If `edited_text` is given it's saved as the chapter's current text *first* -- this is how you hand over your own edits (or a human's, via the web UI's Send button) as settled canon before the model continues from them; omit it to just continue from whatever's already on disk. Returns `{"chapter", "text" (the full chapter text now), "appended" (just the new segment), "characters_created", "characters_updated"}`. Prefer this over repeated `generate` calls for anything resembling paragraph-by-paragraph co-writing -- `generate`'s critique/revise/checks are wasted work if you're about to keep extending the chapter anyway; run `generate`/`audit` once the chapter is actually done for the full editorial pass.
- `audit`: cross-check the written manuscript; best run after several chapters. `generate`/`continue_chapter` track how many chapters have been written since the last `audit` call (`state.last_manuscript_audit_count`) and, once `pipeline.AUDIT_NUDGE_INTERVAL` (6) chapters have passed since, emit a `progress` suggestion to run it -- advisory only, not a continuity flag, since per-chapter checks only ever compare against the compressed running summary and the immediately preceding chapter, never the full prior manuscript the way `audit` does.
- `swerve_propose`: `{}` -- one model call proposing ONE genuine narrative complication that is NOT a logical continuation of what's already set up (see `pipeline.propose_swerve`). Prefers harness-computed structural material when available: an unresolved negative `relationship` to reignite, or a pair of characters with NO relationship to each other at all yet (a "never-connected nodes" collision) -- both computed by pure Python over the existing bible, handed to the model as preferred raw material instead of asking it to invent a complication from nothing. Returns `{"id","description","rationale","touches","built_from","structural_material"}`. Does **not** write to the bible -- review it, then add it yourself with `update_bible` (typically as a new `plot_threads` entry) if it's worth pursuing.
- `outline_search`: `{"depth":3, "branching":3, "beam_width":3, "use_llm_judgment":true, "judge_weight":1.0}` -- beam search over candidate outline continuations (see `pipeline.search_outline_continuations`). A **native reimplementation** of the World Model / Search Config / Search Algorithm pattern from `maitrix-org/llm-reasoners` (that library itself hard-depends on `torch`/`transformers`/`bitsandbytes`/etc, a local-model-serving stack this project doesn't use -- see its `setup.py`), scoped to the one place in this pipeline where the pattern's assumptions actually hold: a "state" here is a cheap beat-sheet call (not a drafted chapter). The reward is **two layers**, not one: `check_dependencies`/`check_relationship_tensions`/promise-payoff/beat-coverage (pure Python, exact, cheap) *plus*, when `use_llm_judgment` is true (the default), an actual LLM call (`judge_branch_options`) rating each candidate's narrative interest across the same cheap options set -- the one dimension the structural check genuinely cannot see. The judged score is rescaled (`(score-5)/2`) and multiplied by `judge_weight` before being added to the structural delta, so neither layer silently dominates by default; set `use_llm_judgment:false` to fall back to the structural-only score at roughly half the call count. Total cost is at most `depth * beam_width * (1 or 2)` cheap JSON calls (defaults with judgment on: at most 14; off: at most 7) -- still no prose generated. Returns `{"branches":[{"chapters":[...], "score":..., "reward_breakdown":[...]}]}`, sorted best-first, nothing committed. Each `reward_breakdown` entry includes `llm_interest_score`/`llm_interest_why` when judgment ran.
- `outline_search_select`: `{"chapters":[...]}` -- takes one branch's `chapters` from `outline_search`'s result and saves it as the pending outline proposal, the same shape `plan` produces. From there the normal `outline-commit-proposal`/`commit` review flow applies unchanged.

## Recommended agent loop

1. `init`, then `update_bible` with genre, style, characters, and constraints.
2. `plan` with `whole_book:true`; inspect `book_plan_proposal.json`; commit only when the arc and promise payoffs are sound. `dependency_graph`/`dependency_check` the committed outline before generating anything -- it's free (no model call) and catches an out-of-order plant/payoff or a beat referencing something not yet established while it's still cheap to fix.
3. Generate chapters in outline order. After each generation, inspect the returned chapter and `snapshot` for open promises and continuity flags (`dependency_check` also runs automatically here).
4. Resolve or revise flagged issues against the actual manuscript: `continuity_resolve` a flag once you've verified and handled it, `promise_resolve` a payoff once a chapter actually delivers it. Run `audit` periodically.
5. Treat the bible as editable, not append-only: `update_bible` to correct a field on an existing entry, `rename`/`remove` when a character, location, thread, or promise turns out to be wrong or redundant, `update_chapter` to fix an outline entry before it's drafted.

Do not treat model-generated plans or continuity flags as unquestionable truth. Keep proposal review separate from commit, preserve the on-disk project files, and include character names in beats when they matter for context selection.

## Code conventions

- Keep the core harness usable as a library and keep the human CLI in `cli.py`.
- Put agent-specific behavior in `agent_wrapper.py`; do not add prompts to the agent interface.
- Maintain JSON-serializable responses and actionable structured errors.
- Run a focused smoke test after wrapper changes: import the module, initialize a temporary project, add a chapter, and inspect a snapshot.
- `novel_harness/webapi/` + `web/` (a FastAPI backend and a React frontend, see README.md's "Web UI" section) are a human-facing browser UI, not part of this agent contract -- agents keep using `agent_wrapper.py` directly, not the HTTP API.
