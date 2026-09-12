# novel-harness

A harness for writing long-form fiction with an LLM, built around one core problem:
**no model's context window holds a whole novel**, so consistency, genre structure, and
setups actually paying off have to be engineered, not hoped for.

## How it works

A project is a directory on disk with:

- `state.json` — the **story bible**: characters (with a *current status* and an optional
  *voice profile* for POV consistency), locations, plot threads (open/resolved), a
  **promises ledger** (setups/Chekhov's guns, tracked until they pay off), **motifs**
  (recurring phrases/images meant to resurface with escalating weight — distinct from a
  promise, which resolves once), a **genre profile** (a cloned, editable structural
  beat-sheet + tropes + a chapter-ending rule), a style guide, and a running summary kept
  under a word-count cap by an LLM compression pass.
- `outline.json` — chapters as an ordered list of **beats** (plot points to hit), not
  prose, each optionally tagged with which genre beat it serves and, optionally, which
  other chapter it's narrated from within (a **frame narrative**). You write (or
  outline-plan proposes, for review) the outline; the model writes the scenes.
- `outline_proposal.json` — a not-yet-committed outline proposal from `outline-plan`,
  meant to be reviewed/edited before `outline-commit-proposal` merges it in.
- `continuity_log.json` — flags raised automatically each time a chapter is generated,
  tagged by `kind`: `continuity` (factual contradictions), `pov` (head-hopping/voice
  drift), `promise` (a setup gone stale or overdue), `beat_coverage` (a beat that was
  skipped or rushed), `dependency` (a beat, promise, or thread used out of order — see
  "Structural dependency graph" below).
- `manuscript/<chapter_id>.md` — the actual prose.

### Getting started: `new` vs `init`

`new` is the guided entry point — it asks for a premise/description, a genre, and free-text
focus tags (e.g. "found family, revenge, morally gray protagonist"), optionally generating a
title from them, and applies the genre preset in the same step. `init` is the bare-bones
version (title + premise only, no prompts, no genre) for scripts/tests that want to compose
the setup themselves via `genre-set`/`bible-set-tags` afterward. Tags are independent of
genre — they're pinned into every chapter's context (see `context.py`) regardless of whether
a genre is set, and can be changed anytime with `bible-set-tags`.

### Genre profiles

Genre is always free text — type whatever describes your story, with no list to pick from.
`genre-set <id>` gives it a structural beat-sheet (approximate book-percentage positions, a
tension target per beat — rising/plateau/spike/resolution — genre tropes to embrace/avoid, and a
chapter-ending rule) one of two ways:

- If your exact wording happens to match one of a handful of built-in presets (`thriller`,
  `romance`, `mystery`, `horror`, `epic_fantasy`, `literary`, `three_act` — see `genres.py`),
  that preset's beat-sheet is **cloned** into the project's `state.json` for free, instantly, no
  model call. Later edits to the shipped presets never retroactively change a project that
  already applied one.
- Otherwise, the model synthesizes an equivalent bespoke beat-sheet grounded in the genre text you
  actually typed (plus the project's premise/tags), so "solarpunk heist" or "cozy mystery with a
  ghost" gets real structural guidance too, not just the handful of presets. This is the same
  shape as a cloned preset and is stored the same way.

Either way you're free to hand-edit the beats/tropes afterward, and `state.genre_id` is always
exactly what you typed. A chapter can be tagged with `--structural-beat <id>` (on `outline-add` or
via `outline-plan`) so the draft/revise prompts know exactly what that chapter needs to deliver and
how much tension it should carry.

### Setup/payoff (Chekhov's gun) tracking

Every chapter's fact-extraction pass also proposes `new_promises` (a planted detail, a
foreshadowed threat, an unanswered question) and `promises_paid` (existing promises this
chapter resolves), merged into `state.promises`. You can also register/resolve promises
by hand (`promise-add`, `promise-resolve`). After extraction, a pure-Python audit (no
model call) flags any promise that's gone stale (too many chapters since it was planted)
or blown past its `due_by` chapter or beat — logged as a `promise`-kind continuity flag
so an unfired gun doesn't just get forgotten.

### Structural dependency graph (`novel_harness/depgraph.py`)

A second pure-Python check, complementing the promise-staleness audit above: where that catches a
setup going stale (planted too long ago, never paid off), this catches the opposite failure mode
— a beat or a schedule using something *before* it exists. It's built on a real dependency graph
computed fresh from the bible + outline (nothing is persisted — like `context.build_chapter_context`,
it's a view, not a new source of truth):

- **Nodes**: one per chapter, plus one per character/location/plot_thread/promise, each tagged
  with the chapter id it was *established* at — `Character.introduced_in`/`Location.introduced_in`
  (set by extraction when the entity is organically discovered mid-story; `None` means it was
  added to the bible up front and is safe to reference in any chapter), `PlotThread.opened_in`,
  `Promise.planted_in`.
- **Edges**: an `establishes` edge from the establishing chapter to the entity, and a `references`
  edge from every chapter whose beats mention that entity (same substring match `context.py`
  already uses for bible relevance, so it inherits the same blind spot — a pronoun/epithet-only
  reference in a beat won't be caught).
- **The check** (`dependency-check`, and it also runs silently as part of every `generate`) flags,
  as a `dependency`-kind continuity flag: a chapter referencing an entity established in a *later*
  chapter (a forward reference), a promise due at or before its own planted chapter, a plot
  thread resolved before it's opened, or (see below) a beat requiring something that isn't
  established anywhere in the current plan at all.
- `graph-show` prints the graph itself — chapters, every entity with when it was established, and
  which chapters reference which entities — useful for sanity-checking an outline's shape before
  spending model calls drafting it, since (unlike the fact-extraction-driven fields above) chapter
  order and any manually-set `introduced_in`/`opened_in`/`planted_in` are known before drafting.

**Authored, not just inferred.** The above is all *reactive* — the graph is reconstructed from
what extraction happened to notice after a chapter was drafted. A beat can also declare its
dependencies directly instead of waiting to be inferred: `{"text": "Torvin takes Mira in", "requires":
[], "establishes": ["character:Torvin"]}` alongside (or instead of) a plain string. A ref is exactly
`kind:name-or-id` (`character:`/`location:`/`plot_thread:`/`promise:`, matching
`depgraph._entity_node_id`). This registers `Torvin` as an entity in the graph — with an
`established_at` of that chapter — even before he's ever added to `state.characters`, so a much
earlier or later chapter's `requires: ["character:Torvin"]` gets checked against the *plan*, not
just against chapters that have already been drafted and extracted. `plan_outline`/`plan_book` emit
beats this way now, asked to use `requires`/`establishes` only when a beat genuinely turns on (or
introduces) something specific, not on every beat. `outline-add`'s `--beats "a|b|c"` and manually
typed beats stay plain strings — nothing about hand-authoring a chapter changes.

Treat this precision as a bonus, not a guarantee: this project's default model is a 9B local
fine-tune, and ["ReFlect" (arXiv:2605.05737)](https://arxiv.org/abs/2605.05737) found that asking a
model to populate structured state like this only reliably helps at a larger scale than that — its
mid-size models (70B-class) already couldn't fill it in consistently. A beat that declares nothing,
or declares it wrong, just falls back to substring inference exactly as before (`_parse_entity_ref`
silently drops anything that doesn't parse as `kind:id`) — the deterministic check itself is what
you can actually rely on, same as the promise-staleness audit above.

### Recurring motifs

A `motif` (`bible-add-motif <id> <phrase> [--notes ...]`) is a recurring phrase or image meant to
gain weight each time it resurfaces — the "Still us?" / "Always" refrain craft technique, where a
short repeated line carries escalating stakes on each return. Distinct from a `promise`, which is a
setup awaiting one specific payoff and eventually resolves: a motif isn't resolved, it's
*reinforced*, so unlike promises (filtered by beat relevance/due-date) every motif stays pinned into
every chapter's context (`context.py`'s `_build_motifs_block`) from the moment it exists, with an
explicit instruction that each recurrence should land with more weight than the last, not
identically. `first_used_in` is optional and purely informational (set it by hand, or leave it
blank for a motif planted before any chapter is drafted).

Extraction can also flag a **motif candidate** after any chapter — something that felt striking and
repeatable but isn't a real motif until a human/agent says so (unlike a promise or memory, this is
an editorial choice, not a fact, so it's never auto-committed). Pending candidates show in
`bible-show`/`motif-candidates-show`; `motif-candidate-promote <id>` turns one into a real motif,
`motif-candidate-dismiss <id>` discards it.

### Frame narratives

A chapter can be tagged `--frame-of <chapter_id>` (`outline-add`, or `update_chapter`'s `frame_of`
field) to mark it as narrated from within another chapter — an old man remembering, a journalist
transcribing an interview, the Barnaby/Desperado structural pattern. When set, `context.py` pulls
in the frame chapter's POV, voice profile, and a short excerpt, and instructs the drafting model to
render the embedded scene consistent with being recalled/recounted from that vantage rather than as
an independent present-tense scene. `graph-show`/the dependency graph show this as a `frames` edge
(chapter → the chapter it's framed by) for visibility; it's purely a context-assembly and display
hint, not a rendering system — nothing else about how chapters/beats/generation work changes.

### Relationship memories ("X will remember that")

A `memory` (`bible-add-memory <id> <subject> <about> <event> <effect> [--chapter ...]`) is the
Telltale-games mechanic — a specific past incident that keeps shaping how one character treats
another, distinct from both a `promise` (a setup that resolves once, to the reader) and a
character's `status` (their overall current situation, not a per-relationship consequence).
`subject` is whose future behavior is affected, `about` is who/what it concerns (usually another
character), `event` is what happened, and `effect` is the behavioral instruction — not just a fact
for the record, an active steer on how `subject` should act toward `about` going forward (a
betrayal earns suspicion, a rescue earns loyalty, a lie caught earns distance). Like promises (and
unlike motifs, which are always pinned), a memory is only pulled into a chapter's context when
`subject` is that chapter's POV or is mentioned in the beats (`context.py`'s
`_build_memories_block`) — a large cast can accumulate many memories, and most won't matter to any
given chapter. Extraction (`extract_and_update_state`) can auto-propose one after any chapter where
something happens that should genuinely change how a character treats another; `memory-resolve`
(or the web UI's "Mark resolved") marks it settled (a grudge forgiven, trust repaired) without
deleting the record, at which point it stops being pinned into context.

### Standing relationships ("X and Y hate each other from a fight")

A `relationship` (`bible-add-relationship <id> <a> <b> <kind> [--polarity ...] [--reason ...]`) is
a standing relational fact — "X knows Y", "Z and Y hate each other from a fight", "this won't work
because of X" — deliberately kept off the model's plate: rather than trusting the model to remember
a relationship from chapters back, `context.py`'s `_build_relationships_block` pins it into context
deterministically every time BOTH sides are relevant, and `depgraph.check_relationship_tensions`
(pure Python, no model call) flags a beat that brings together two parties with a known, unresolved
**negative**-polarity relationship — not a contradiction detector (Python can't read prose for
subtext), just a reminder the friction exists so it's never silently missed. Distinct from a
`memory` (one-directional, event-derived: "X remembers Y did Z") and from a `plot_thread` (an
ongoing story line, not a relational fact). Extraction can auto-propose one (`new_relationships`)
or mark one changed/resolved (`relationships_changed`); `relationship-resolve` marks a feud ended or
a bond mended by hand.

### Per-chapter ending variety

`ProjectState.chapter_hook_rule` (set by a genre profile, e.g. "end every chapter on a hook") used
to apply uniformly to every chapter in the book — which means a book where every single chapter
ends on a cliffhanger, by construction. `outline-add --ending-style "..."` (or `update_chapter`'s
`ending_style` field) overrides it for just that one chapter — "end quietly, let this one breathe,"
"cut away mid-sentence," "end on dark irony" — so formal variety across chapter endings is something
the outline can express, not something the harness prevents. `plan`/`book-plan` are prompted to vary
it deliberately across the chapters they propose rather than leaving every one on the default hook.

### Discovery mode: drafting without a beat checklist

A chapter's `mode` (`outline-add --mode discovery --direction "..."`) defaults to `"outline"` — the
normal beat-by-beat pipeline, where deviating from the plan is something the critique/revise loop
corrects back into line. Setting `mode: "discovery"` inverts that: the chapter is drafted from a
loose one-or-two-sentence `direction` instead of a beat checklist, explicitly told it is *not*
required to hit specific beats in a specific order and should follow the direction where the scene
and characters actually lead. No coverage check runs against a plan that was never written; instead
`beats` is populated **retroactively** from the finished chapter (`extract_beats_retroactively`),
purely for downstream display and the dependency graph — a record of what the chapter turned out to
be, never fed back as a constraint on the text that produced it. Everything else (motifs, memories,
relationships, promises, continuity/POV/tension checks, extraction) works identically in either mode.

### The sharpen pass

`generate`'s critique/revise loop only ever pulls a draft *toward* spec compliance (beat coverage,
the hook rule, pacing) — it has no mechanism that rewards a genuinely surprising or specific choice,
so a model's single riskiest sentence is exactly as likely to get smoothed into blandness as its
worst one. The sharpen pass (`pipeline.sharpen_chapter`, `options.sharpen`, on by default) runs
last, after structural revision is done, with one job only: find the safest, most cliché phrasing
left — a stock phrase, a generic "heart pounding" reaction, dialogue with no subtext — and replace
it with something more specific to *this* character and *this* scene. It touches no structure, no
plot, no length; a chapter that's already sharp throughout gets no changes at all.

### Story-editor tools: swerve & outline search

Two agent-facing tools built specifically to counter a plan that's gotten too predictable, and to
"keep the logic off the model and on the harness" wherever the harness can compute something
concrete instead of asking the model to invent from a blank page:

- **`swerve-propose`** (`pipeline.propose_swerve`) asks for ONE genuine narrative complication that
  is *not* a logical continuation of what's already set up. When available, it's handed
  harness-computed structural material to build from: an unresolved negative `relationship` to
  reignite, or a pair of characters with **no** relationship to each other at all yet — a
  never-connected-nodes collision, computed by plain Python over the existing bible
  (`_swerve_structural_material`), not invented by the model. Nothing is written to the bible;
  review the result and add it yourself (typically as a `plot_thread`) if it's worth pursuing.
- **`outline-search`** (`pipeline.search_outline_continuations`) is a beam search over candidate
  outline continuations. This is a **native reimplementation** of the World Model / Search Config /
  Search Algorithm pattern from
  [`maitrix-org/llm-reasoners`](https://github.com/maitrix-org/llm-reasoners) — not a dependency on
  that library, which hard-requires `torch`/`transformers`/`bitsandbytes`/`peft`/`fairscale` (a
  local-model-serving research stack, per its `setup.py`) this project deliberately doesn't carry.
  The pattern is scoped to the one place it actually fits: a "state" here is a cheap beat-sheet call
  (`propose_outline_branches`, not a drafted chapter), and the reward
  (`_score_branch_step`) is `check_dependencies`/`check_relationship_tensions`/promise-payoff/
  beat-coverage — pure Python, exact, and cheap, unlike judging prose that doesn't exist yet at plan
  time. That's also why beam search was picked over MCTS: with a small branching factor and an
  exact, cheap reward, there's no need for MCTS's rollout/backpropagation machinery. Cost is at most
  `depth * beam_width` JSON calls (defaults: at most 7, no prose generated). Nothing is committed —
  pick a branch and it becomes the pending outline proposal, from which the normal
  `outline-commit-proposal` review/commit flow applies unchanged.

### POV / voice persistence

Characters can carry a `voice_notes` field — diction, sentence rhythm, verbal tics, how
their interiority reads — either hand-written (`voice-set`) or drafted by the model from
their description and any chapters already written in their POV (`voice-generate`).
Whenever a chapter's POV character has a voice profile, it's pinned into that chapter's
context, and a dedicated POV-consistency check flags head-hopping or voice drift
separately from general continuity issues.

### The generation pipeline (`novel_harness/pipeline.py`)

For each chapter, `generate_chapter()` runs:

1. **Context assembly** (`context.py`) — style guide, running summary, relevant bible
   entries (matched by name against the beat text, falling back to the full bible), the
   previous chapter's full text (for voice continuity), the current beats, the
   chapter's structural-beat/genre guidance, the POV character's voice profile, and any
   promises relevant to or coming due in this chapter.
2. **Draft** — one model call, full prose, genre-conditioned when a genre is set. Prompted to vary
   pacing deliberately (dilate emotionally important beats into full scenes, compress transitional
   ones into brief summary on purpose rather than dramatizing everything alike), build anticipation
   through reveal ordering (withhold a name, delay a direct answer) rather than just not resolving
   tension early, and externalize emotion through a specific object/action instead of naming the
   feeling outright — see `pipeline.PACING_AND_RESTRAINT_NOTE`.
3. **Critique ↔ revise, iteratively** — a structural-only critique pass (pacing, whether
   the chapter's tension target/hook rule is honored, premature resolution) hands its
   findings to a harsher editorial revise pass (pacing, flat dialogue, telling-not-showing).
   These two talk to each other more than once by default: round 1 always revises (the
   revise pass also does general polish, not just findings-fixing); from round 2 on, a
   fresh critique of the *just-revised* text decides whether another round is warranted —
   if it finds nothing left to fix, the loop stops instead of re-touching an already-clean
   draft. `--critique-rounds N` controls the cap (default 2, capped at 4); `--critique-rounds 1`
   reproduces the old one-shot behavior; `--no-critique` revises once with no critique
   guidance at all; `--no-revise` skips both entirely. Read "finds nothing left to fix" as
   exactly that — the critique pass returned no findings — not as a verified-clean guarantee:
   [ReFlect (arXiv:2605.05737)](https://arxiv.org/abs/2605.05737) found prompted self-critique
   frequently rubber-stamps (90/100 audited reflection blocks flagged nothing in their study),
   and this project's default model is on the smaller end of what that paper tested. An
   occasional wasted critique call when it stops early is a cheap price either way.
4. **Beat-coverage check** — a structured call that judges each beat as covered/rushed/missing.
   A brief, purposeful summary of a transitional beat counts as "covered," same as a fully
   dramatized scene — deliberate compression is good pacing craft, not a defect; "rushed" means
   *thin in a way that reads as an oversight*, not merely short. Missing/genuinely-rushed beats are
   patched back in with a targeted rewrite by default (or just flagged with `--no-patch-missing`),
   and the patch itself is then re-checked against just the beats it was supposed to fix, rather
   than trusted blindly — anything still missing/rushed after the patch attempt is flagged.
   Skippable entirely with `--no-beat-check`.
5. **POV consistency check** — flags head-hopping or voice drift against the POV
   character's voice profile. Skippable with `--no-pov-check`.
6. **Continuity check** — compares the chapter against the bible for contradictions.
   Skippable with `--no-check`.
7. **Tension/premature-resolution check** — flags tension, mystery, or an emotional
   question resolved earlier or more completely than it should be. Skippable with
   `--no-tension-check`.
8. **Fact extraction** — reads what the chapter actually asserted (character status
   changes, new characters/locations, threads and promises opened/resolved, a short
   summary) and merges it into the bible.
9. **Promise-staleness audit** — pure Python, no model call: flags any open promise
   that's overdue given the chapters that have passed since.
10. **Dependency check** — pure Python, no model call: flags a beat referencing
    something established later, a promise due at/before its own setup, or a thread
    resolved before it's opened (see "Structural dependency graph" above).
11. **Summary compression** — condensed once the running summary passes ~900 words.

Every step is also exposed individually in `pipeline.py`, so you can run this
semi-manually instead of unattended — e.g. approve a draft before revision, or eyeball
extracted facts/promises before they're merged into the bible.

## Install

```bash
pip install -r requirements.txt   # core CLI + the web UI's backend (fastapi/uvicorn/websockets)
# pip install -r requirements-narration.txt   # only if you want voice-*/narrate/--narrate (pulls in torch)
export INFERDEX_API_KEY=sk-...
# Optional overrides:
# export INFERDEX_BASE_URL=https://llm.khosa.co/v1
# export INFERDEX_MODEL=DavidAU/Qwen3.5-9B-The-Defiant-Fable-Uncensored-Heretic-NEO-IMATRIX-MAX-MTP-GGUF
```

## Quickstart

```bash
# Guided setup: asks for a premise, a genre (free text -- see "Genre profiles" above), and focus
# tags/themes to keep front-of-mind in every chapter. Any of these can be passed as flags to skip
# that prompt -- pass all of them (--description/--genre/--tags/--title) to run non-interactively.
python -m novel_harness.cli --root ./my-novel new
#   Describe your story -- premise, a sentence or two: A disgraced clockmaker's apprentice
#     discovers the city council is powered by a hidden mechanism only she can repair -- or sabotage.
#   Genre (type anything, or leave blank): thriller
#   Focus tags/themes to keep front-of-mind, comma-separated (optional): betrayal, found family, a ticking clock
#   No title given -- generate one from the premise/genre/tags? [Y/n]: y

python -m novel_harness.cli --root ./my-novel bible-set-target-length 24
python -m novel_harness.cli --root ./my-novel bible-set-style \
  "Third person past tense, close POV, restrained prose, Dickensian industrial setting."

# (or skip the prompts entirely with `init` for a bare title+premise, then `genre-set` separately)

# Build the bible
python -m novel_harness.cli --root ./my-novel bible-add-character \
  "Mira Voss" "A 19-year-old clockmaker's apprentice, sharp-eyed and stubborn." \
  --status "Newly dismissed, hiding a stolen master key."
python -m novel_harness.cli --root ./my-novel voice-generate "Mira Voss"
python -m novel_harness.cli --root ./my-novel bible-add-location \
  "The Gearworks" "A sprawling underground clock mechanism beneath the city council hall."
python -m novel_harness.cli --root ./my-novel bible-add-thread \
  "stolen-key" "Mira has a master key stolen from her former employer."
python -m novel_harness.cli --root ./my-novel promise-add \
  "master-key-fits" "The stolen key looks like it belongs to something bigger." --due-by climax

# Outline a chapter as beats, tagged to a genre beat
python -m novel_harness.cli --root ./my-novel outline-add ch01 "The Dismissal" \
  --pov "Mira Voss" --words 2200 --structural-beat inciting_danger \
  --beats "Mira is caught examining forbidden gear schematics|She is dismissed and stripped of her apprenticeship|She pockets a master key on her way out"

# ...or let the outline agent propose the next few chapters (review before committing)
python -m novel_harness.cli --root ./my-novel outline-plan --count 3
# edit outline_proposal.json if you want, then:
python -m novel_harness.cli --root ./my-novel outline-commit-proposal

# Generate a chapter (draft -> revise -> beat check -> pov check -> continuity check -> bible update)
python -m novel_harness.cli --root ./my-novel generate ch01

# Inspect state
python -m novel_harness.cli --root ./my-novel bible-show
python -m novel_harness.cli --root ./my-novel genre-show
python -m novel_harness.cli --root ./my-novel outline-show
python -m novel_harness.cli --root ./my-novel promise-show
python -m novel_harness.cli --root ./my-novel continuity-show --kind all
python -m novel_harness.cli --root ./my-novel status
```

Repeat outline-add (or outline-plan) + generate per chapter. The bible, promises ledger,
and running summary accumulate automatically.

The bible isn't append-only: `bible-remove-character`/`bible-rename-character` (renaming also
updates any chapter's `--pov` that used the old name), `bible-remove-location`/
`bible-rename-location`, `thread-remove`/`thread-rename`, `promise-remove`/`promise-rename`,
`motif-remove`/`motif-rename`, `memory-remove`/`memory-rename`/`memory-resolve`,
`relationship-remove`/`relationship-rename`/`relationship-resolve`, and
`outline-remove <id> [--force]` (refuses to delete an already-drafted chapter's manuscript text
unless forced) all exist for fixing a bible entry that turns out to be wrong or redundant.
`continuity-resolve <index>` marks one flag from `continuity-show`'s output resolved (`<index>` is
the `#N` it prints, which stays correct even when `--kind` filters the list). `character-generate`
and `title-generate` expose the same LLM-assisted generation `new` uses for a first character/title
as standalone commands, for use any time after setup.

## Using it as a library instead of the CLI

```python
from novel_harness import Project, LLMClient, generate_chapter

project = Project("./my-novel")
client = LLMClient()
chapter = generate_chapter(client, project, "ch01")
print(chapter.status, chapter.file)
```

## Web UI

`novel_harness/webapi/` is a FastAPI backend, and `web/` a React (Vite + TypeScript) frontend, for
browsing/editing a novel and driving generation from a browser instead of the CLI. It's a fourth
frontend over the same library `cli.py`/`agent_wrapper.py` already use -- `webapi/routers/*.py` are
thin wrappers around `agent_wrapper.AgentSession`, not a reimplementation of bible/outline logic.

```bash
# Backend (installs with the core requirements.txt above -- fastapi/uvicorn/websockets)
python -m novel_harness.webapi --port 8000

# Frontend, in a second terminal
cd web
npm install
npm run dev   # proxies /api and /ws to the backend on :8000 -- see web/vite.config.ts
```

Open the printed Vite URL. The library lets you create a brand-new novel (title + premise --
`POST /api/projects/new`, the browser's equivalent of the CLI's `init`, since a deployed instance
has no shell to run the CLI in first) or add an existing project by path.

**The manuscript is the primary surface**, NovelAI-style -- opening a project lands you directly in
a chapter's editor (the last one this browser had open, or the first in outline order, remembered
per-project in `localStorage`), not a dashboard. A slim always-visible chapter rail on the left
switches between chapters (and quick-adds a new one); everything else -- Characters, Story (premise/
genre/tags/style/motifs/memories/relationships/summary), Plan (which also hosts "Propose a swerve"
and outline search), and Continuity -- opens as a drawer on the right via the header toggles,
auxiliary to the manuscript rather than destinations you navigate away to. Only one drawer is open
at a time, closed by default so the manuscript gets full width.

Long-running operations (`generate`, `continue`, `plan`, `audit`) run as background jobs
(`novel_harness/webapi/jobs.py`) so the browser doesn't block on a multi-minute chapter draft --
every pipeline function that already accepted a `progress` callback streams its lines live over a
WebSocket (`/ws/jobs/{id}`) the moment they're produced, the same lines the CLI would print to
stderr. Editing a character (rename, change fields) doesn't cascade automatically: the UI queries
the dependency graph for which chapters reference the edited entity and lets you pick which ones,
if any, to actually regenerate -- nothing is regenerated without an explicit choice, since that
overwrites drafted prose.

A chapter's manuscript is an editable textarea, not read-only: select, delete, or reword anything
in it, then **Send** -- this calls `continue_and_extract()` (`pipeline.py`), which saves your edits
as the chapter's current text first, then appends one continuation segment picking up from exactly
that (not a rewrite) and runs fact extraction on the result, so the bible updates as you go. It's
two model calls, fast enough to click repeatedly, unlike **Full rewrite** (the old "Regenerate this
chapter," `generate_chapter()`'s complete critique/revise/all-checks pipeline, up to ~13 calls,
which replaces the whole text). Same split is available to agents: `generate` vs. the lighter
`continue_chapter` action in `agent_wrapper.py`.

Send's text streams into the editor live, character by character, as the model writes it --
`pipeline.continue_chapter`'s new `on_chunk` callback (a second, unthrottled sibling to the
existing `progress` callback) is wired all the way through the job engine as its own WebSocket
message type (`{"type":"chunk",...}`, `webapi/jobs.py`/`ws.py`) so the browser doesn't just see a
"still working" indicator for several seconds and then the whole result at once. `generate`/"Full
rewrite" deliberately doesn't stream its draft this way -- that intermediate text gets rewritten by
the revise pass, so showing it live would show the user text they won't end up with.

For production, `npm run build` in `web/` produces `web/dist/`, which `novel_harness.webapi.app`
serves automatically (as static files) if present -- one process, no separate frontend server
needed. This is a human-facing surface, not part of the agent contract in `AGENTS.md`; agents keep
talking to `agent_wrapper.py` directly.

Known limitations: job history is in-memory only (a server restart loses it -- the actual project
files are unaffected); no auth (single local user, matching every other entry point in this repo);
the dependency graph is shown as a list, not an interactive diagram, in this first version.

## Design notes / known limitations

- **Automated checks are themselves LLM calls** (continuity, beat-coverage, POV), so
  they can miss things or hallucinate an issue that isn't real — treat
  `continuity_log.json` as a prioritized review list, not ground truth. Verify flagged
  issues against the actual chapter text before trusting them. The **promise-staleness
  audit and the dependency-graph check are the exceptions** — both are pure Python
  arithmetic over chapter indices, so they're exact given accurate `planted_in`/`due_by`/
  `opened_in`/`introduced_in` data, but that data is only as good as what
  extraction/manual entry put in.
- **Relevance filtering for bible entries and promises, and the dependency graph's
  reference edges,** are all the same plain substring match against beat text. It's cheap
  and works well for named entities but won't catch a character, promise, or thread
  referred to only by pronoun or epithet in the beats — pad beats with names when it
  matters, and treat a clean `dependency-check` as "no *detectable* violation," not proof
  none exists.
- **Structural-beat matching by book position** (`target_chapters`) is a rough guess when
  a chapter isn't explicitly tagged with `--structural-beat`; an explicit tag always
  wins, and unbounded books (`target_chapters = 0`) skip position-based guessing
  entirely.
- **`outline-search`'s reward function only ever scores structural health, never narrative
  interest or prose quality** — at plan time there's no prose yet, only a beat-sheet, so
  `_score_branch_step` can check "does this introduce a dependency violation / claim an
  unclaimed beat / advance an overdue promise" and nothing about whether the resulting chapter
  would actually be any good to read. A high-scoring branch is a structurally sound one, not
  necessarily the most interesting one — review the branches yourself before picking one, the
  same way you'd review any other proposal.
- **`swerve-propose`'s structural material is a lower bound on what's interesting, not an
  exhaustive one** — it only surfaces relationships you've actually recorded and characters
  that exist in the bible; a swerve worth having that depends on something never entered as a
  `relationship` won't get the same harness-computed nudge (the model can still invent one from
  the premise/threads alone, same as before this feature existed).
- **`outline-plan` is assisted, not automatic.** It writes a proposal file for you to
  review/edit; nothing is committed to `outline.json` until you run
  `outline-commit-proposal`.
- **Committing a proposal stub-registers any character/location/thread/promise it names that
  doesn't exist in the bible yet** -- a POV character invented because none existed, or a beat's
  authored `establishes: ["character:X"]` -- so it's immediately visible and editable (Characters
  page, `bible-show`) before any chapter is ever drafted, not just once a chapter that happens to
  mention it gets drafted and extraction reactively notices it. Generalizes the same idea
  `book-plan`'s `plants` already used for pre-registering promises (`storage._register_planned_entities`).
- **Costs**: a full `generate` call's baseline is draft, critique, revise, beat-coverage
  check, POV check, continuity check, tension check, and extraction — 8 model calls before
  anything conditional. The critique↔revise loop can add another critique+revise pair per
  extra round (worst case 4 rounds = 8 calls just for that step), a missing beat adds a
  patch-plus-reverify pair, and summary compression fires occasionally — worst case is
  comfortably into the teens of model calls for one chapter. For a ~2,500-word chapter
  that's meaningful token spend across a whole novel — `--critique-rounds 1
  --no-check --no-beat-check --no-pov-check --no-tension-check` is available if you want
  to draft cheaply and run checks in a separate pass later (`--no-critique`/`--no-revise`
  cut deeper still, skipping the editorial pass(es) entirely).
- **JSON parsing from the model is best-effort, not guaranteed.** `LLMClient.call_json` retries
  once at a larger token budget on a parse failure, and separately attempts a cheap structural
  repair (stripping a trailing comma, appending a bracket the model dropped) before giving up --
  this recovers a real failure mode of small local models (a well-formed response missing its
  final `}`), but a genuinely malformed or truncated response still surfaces as an error.
- **Removing or renaming a bible entry (`bible-remove-*`, `*-rename`, `outline-remove`) doesn't
  cascade.** It doesn't rewrite beat text, already-written manuscript prose, or other records
  (promises, continuity flags) that still reference the old name/id -- those are left as dangling
  references, the same way the harness already tolerates a promise's `due_by` pointing at a
  chapter id that no longer exists.
