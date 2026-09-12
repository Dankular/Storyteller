"""Generation pipeline:
draft -> revise -> beat-coverage check -> (patch missing beats) -> POV check ->
continuity check -> fact extraction (incl. promises) -> promise-staleness audit -> compress.

generate_chapter() is the main entry point; the individual steps are exposed
separately so a caller can slot in human review between them (e.g. approve the
draft before it's revised, or edit extracted facts before they're merged into
the bible) instead of running the whole thing unattended.
"""
from __future__ import annotations
import copy
import json
import os
import time
from typing import Callable, List, Optional

from .llm import LLMClient
from .storage import Project
from .models import ProjectState, Chapter, ContinuityFlag, Character, Location, PlotThread, Promise, beat_text
from .context import build_chapter_context, _resolve_structural_beat
from .depgraph import check_dependencies
from .genres import GenrePreset, match_preset, clone_preset_beats

Progress = Callable[[str], None]


def _token_reporter(progress: Optional[Progress], label: str, interval: float = 25.0, expected_chars: Optional[int] = None):
    """Wraps LLMClient.call()'s on_token callback to throttle live progress updates (character
    count, elapsed time, and -- when expected_chars is given, e.g. from a chapter's word_target --
    a computed % and ETA extrapolated from the rate observed so far) instead of firing once per
    token, and instead of leaving the reader to manually diff two raw timestamps to guess a rate."""
    if progress is None:
        return None
    from .progress_utils import format_duration, eta_from_rate

    state = {"chars": 0, "last": 0.0}
    start = time.monotonic()

    def on_token(piece: str) -> None:
        state["chars"] += len(piece)
        now = time.monotonic()
        if now - state["last"] >= interval:
            state["last"] = now
            elapsed = now - start
            eta = eta_from_rate(state["chars"], expected_chars, elapsed)
            progress(f"{label}: {state['chars']} chars streamed so far ({format_duration(elapsed)} elapsed{eta})")

    return on_token

DRAFT_SYSTEM_BASE = """You are a skilled novelist ghost-writing a chapter of a novel for the author. \
Write full, immersive prose -- not an outline or summary. Follow the beats given, in order, \
but render them as scenes with sensory detail, dialogue, and interiority consistent with the \
established style guide and characters. Give a character a moment or reaction outside their single \
defining trait wherever the scene allows it -- a character who only ever displays the one trait \
their description leads with reads as flat, even when every line is technically consistent with it. \
Do not break the fourth wall or add author's notes. Output only the chapter text."""

REVISE_SYSTEM_BASE = """You are a demanding developmental and line editor. You will be given a chapter \
draft. Rewrite it to fix: pacing issues, flat dialogue, inconsistent voice, telling instead of \
showing, and any beat that was skipped or rushed. Preserve everything that already works. \
Output only the revised chapter text, no commentary."""

GENRE_AWARENESS_NOTE = """

The brief includes a "Where this chapter sits in the story" section when a genre profile is active -- \
honor its tension target and the chapter-ending hook rule exactly. Do not flatten a spike beat into a \
plateau, or let a plateau beat sprawl past its welcome."""

# LLM fiction measurably resolves tension earlier and more completely than skilled human writing does
# (see: "Spoiler Alert: Narrative Forecasting as a Metric for Tension in LLM Storytelling", 2026) --
# this note pushes back on that tendency at the prompt level, in both drafting and revision.
TENSION_DISCIPLINE_NOTE = """

Do not resolve tension, mystery, or emotional ambiguity earlier or more completely than the beats \
require. If a question is raised, let it stay open longer than feels comfortable -- a reader who gets \
the answer immediately after the question stops caring about the question. Prefer implication over \
explanation: when a reveal or emotional beat lands, trust it to land without following it with an \
explanatory paragraph spelling out what it means. A chapter that fully answers its own tension in the \
same scene it was introduced has failed at pacing, regardless of how polished the prose is."""

BEAT_COVERAGE_SYSTEM = """You audit whether a drafted chapter actually dramatizes every beat from its \
outline, rather than skipping or merely summarizing one. For each beat given, decide: "covered" (rendered \
as a full scene with detail), "rushed" (mentioned or summarized in passing, not dramatized), or "missing" \
(not present at all). Return JSON:
{"beats": [{"beat": "<beat text, verbatim>", "status": "covered|rushed|missing", "evidence": "<a SHORT quote, 10 words or fewer, or empty>"}]}
"evidence" must never exceed 10 words -- a pointer to where in the chapter, not a full excerpt. Judge \
strictly: a beat that's only referenced in a single throwaway line is "rushed", not "covered"."""

PATCH_BEATS_SYSTEM = """You are a novelist revising a chapter draft that skipped some of its required \
beats. You will be given the chapter and a list of beats that were missing or only rushed through. \
Rewrite the chapter to add full scenes for each missing/rushed beat, in a plausible place given the \
existing flow -- without disturbing scenes that already work. Preserve voice and continuity. Output only \
the revised chapter text, no commentary."""

CRITIQUE_SYSTEM = """You are a structural story editor. Do NOT rewrite anything. Read the chapter draft \
and identify structural problems only -- pacing across the chapter as a whole, whether it delivers the \
tension target and content of its structural beat (if given), whether it honors the genre's \
chapter-ending hook rule (if given), whether earlier beats are given room to breathe before later ones \
arrive, and whether the chapter resolves its own tension or mystery too early rather than sustaining it. \
Return JSON:
{"findings": ["<specific, actionable structural finding>", "..."]}
Each finding must point at a specific structural problem, not a line-level prose nitpick (that's a \
separate pass). If the chapter's structure is genuinely sound, return {"findings": []} -- do not invent \
problems to fill the list."""

PREMATURE_RESOLUTION_SYSTEM = """You check whether a chapter resolves tension, mystery, or an emotional \
question earlier or more completely than it should. Good tension is sustained: a reader's question stays \
open longer than feels comfortable. Given the chapter, its structural beat (if any), and promises to the \
reader that are NOT yet due, flag any place where the chapter:
- fully answers a question or reveals a secret that a not-yet-due promise implies should stay open,
- over-explains a reveal or twist immediately instead of letting the implication land on its own,
- resolves tension in the same scene/beat where it was introduced, with no room for the reader to sit \
  with it.
Return JSON: {"issues": [{"issue": "<description>", "severity": "note|warning"}]}
If the chapter's tension is well-sustained, return {"issues": []}. Do not invent issues that aren't \
really there -- most chapters that hit their beats cleanly will have zero issues here."""

POV_CONSISTENCY_SYSTEM = """You check a chapter for point-of-view discipline against a single POV \
character and their voice profile. Flag only two things:
1. True head-hopping: the narration directly renders another character's private sensory experience \
   or interior thought AS THEIR OWN interiority (e.g. "Marcus felt a flicker of guilt he didn't show"),
   not as the POV character's observation, guess, memory, or belief about them.
2. Voice drift: prose that abandons the established voice profile's diction/rhythm/interiority style.

Do NOT flag as head-hopping:
- The POV character's own opinions, judgments, guesses, or assumptions about other people ("she knew \
  he'd sell her out" is the POV character's belief, stated from their head, not the other person's).
- Facts, backstory, or traits the POV character already knows or plausibly could know from past \
  interaction, reputation, or research.
- Information the POV character learns in-scene through dialogue, a letter, a document, a screen, or \
  any other diegetic source they are reading/hearing -- that is squarely inside their POV, not a \
  violation, no matter whose information it reveals.
Only flag content that could not be explained by any of the above.

Return JSON: {"issues": [{"issue": "<description>", "severity": "note|warning|violation"}]}
If the chapter is clean, return {"issues": []}. Do not invent issues that aren't really there -- when \
in doubt, do not flag it."""

CONTINUITY_SYSTEM = """You are a continuity checker for a novel. Given the story bible and a new \
chapter, list any contradictions between them (e.g. a character doing something inconsistent with \
their established status, a location described differently, a timeline impossibility). Return JSON:
{"issues": [{"issue": "<description>", "severity": "note|warning|contradiction"}]}
If there are no issues, return {"issues": []}. Do not invent issues that aren't really there."""

EXTRACT_SYSTEM = """You extract structured facts asserted by a chapter of a novel, for a story \
bible. Given the chapter text and the existing story bible, return JSON with this shape:
{
  "character_updates": {"<name>": "<new status/one-line update, or empty if unchanged>"},
  "new_characters": {"<name>": "<one-line description>"},
  "new_locations": {"<name>": "<one-line description>"},
  "threads_opened": [{"id": "<short-slug>", "description": "<...>"}],
  "threads_resolved": ["<existing thread id from the bible that this chapter resolves>"],
  "new_promises": [{"id": "<short-slug>", "description": "<a specific setup/promise made to the reader -- a planted detail, a foreshadowed threat, an unanswered question -- that will need to pay off later>", "due_by": "<optional: a chapter id or story-beat name if implied, else empty>"}],
  "promises_paid": ["<existing promise id from the bible that this chapter pays off>"],
  "chapter_summary": "<2-4 sentence summary of what happened, for the running summary>"
}
Only include entries that are actually supported by the chapter text. Only flag a new_promise if it's a \
genuine planted setup a reader would expect to matter later -- not every detail is a promise."""

SUMMARY_COMPRESS_SYSTEM = """Condense this running story summary into a tighter version, \
preserving all plot-critical facts, character developments, and open threads, but cutting \
redundant or resolved detail. Keep it under roughly 600 words. Output only the condensed summary."""

BEAT_DEPENDENCY_SCHEMA_NOTE = """Each beat is an object, not a bare string: {"text": "<the beat, as \
before>", "requires": [<entity refs this beat depends on already existing>], "establishes": [<entity \
refs this beat introduces for the first time>]}. Leave both arrays empty for most beats -- only use \
them when a beat genuinely turns on a specific named character/location/thread/promise not yet in \
play, or introduces one. An entity ref is exactly "kind:name-or-id" with kind one of character/ \
location/plot_thread/promise -- e.g. "character:Torvin", "promise:master-key-fits". Use the exact \
name/id already given for something that already exists; invent a new name/id only in "establishes" \
for something genuinely new. Worked example -- a beat that needs a not-yet-introduced mentor and \
plants a promise: {"text": "Torvin, an old locksmith, takes Mira in and teaches her the trade", \
"requires": [], "establishes": ["character:Torvin", "promise:torvin-secret-past"]}; an ordinary beat \
with nothing to declare: {"text": "Mira practices late into the night", "requires": [], "establishes": []}."""

OUTLINE_PLAN_SYSTEM = """You are a novel outliner. Given a premise, a genre's structural beat-sheet, the \
story so far, existing characters, open plot threads, and promises made to the reader that are due, \
propose the next chapters as beats -- not prose. Each proposed chapter should advance toward its nearest \
unclaimed structural beat and give due promises a real chance to pay off. Return JSON:
{"chapters": [{"id": "<short-slug, unique>", "title": "<...>", "pov": "<character name>", "beats": [<beat objects, see below>], "structural_beat": "<id from the beat-sheet given, or empty>", "word_target": <int>}]}
""" + BEAT_DEPENDENCY_SCHEMA_NOTE + """
Rules: pov MUST be one of the existing characters listed, unless none exist yet, in which case invent one \
consistent with the premise. word_target should be a normal single-chapter length (roughly 1500-3000 \
words) unless the genre's pacing notes clearly call for something shorter or longer -- never propose an \
oversized multi-chapter word count. Propose exactly the requested number of chapters. Do not write prose, \
only beats."""

BOOK_PLAN_SYSTEM = """You are a novel outliner planning the REST of a book's chapter structure in one \
pass -- not just the next few chapters, everything from here to the ending. This is the single most \
important lever for setups actually paying off: because you can see the whole remaining arc at once, \
you can place an exact payoff chapter for every plant you make, instead of a reactive chapter-by-chapter \
outliner improvising blind and discovering only afterward whether something ever got paid off.

Given the premise, genre, its full structural beat-sheet, existing characters, chapters already \
written/planned, and open promises, produce EXACTLY the requested number of NEW chapters -- not more, \
not fewer -- that:
- claim the genre's remaining structural beats at roughly their given percentage positions across the \
  chapters you're adding (the beat-sheet's position_pct is out of the WHOLE book, not just your chapters),
- give every open promise, including ones you introduce here, an explicit payoff chapter that either \
  already exists (already-written chapters) or is one of the new chapters in this same response,
- vary pacing sensibly -- not every chapter needs to be a spike,
- build on what's already written rather than contradicting it.

Rules:
- word_target must be a normal single-chapter length, roughly 1500-3000 words, regardless of how much \
  plot the chapter covers -- never propose an oversized multi-chapter word count like 6000+.
- A plant's payoff_chapter MUST be a chapter that comes LATER than the one the plant appears in -- never \
  the same chapter (that isn't a setup/payoff, it's just a beat) and never an earlier one.
- Not every chapter needs a plant. A plant without a genuine gap before its payoff isn't worth tracking.

Return JSON:
{"chapters": [{"id": "<new, unique short-slug id, not colliding with existing ids>", "title": "...", "pov": "<character name>", "structural_beat": "<id from the beat-sheet, or empty>", "beats": [<beat objects, see below>], "plants": [{"description": "<a setup/promise this chapter plants>", "payoff_chapter": "<a LATER chapter id -- existing or newly added here -- that pays this off>"}], "word_target": <int>}]}
""" + BEAT_DEPENDENCY_SCHEMA_NOTE + """
Since you can see every chapter you're adding at once, use establishes/requires deliberately across \
them: if chapter 5 introduces a character, mark it there with "establishes", and if an earlier \
chapter's beat actually needs that same character to already exist, that's a bug in your own plan -- \
fix the ordering before returning it, don't just declare a "requires" that comes before its "establishes".
pov must be one of the existing characters, unless none exist yet, in which case invent a cast \
consistent with the premise. Do not write prose, only structure."""

MANUSCRIPT_AUDIT_SYSTEM = """You are a meticulous continuity editor reviewing an ENTIRE manuscript so \
far in one pass -- the real text of every chapter written, not one new chapter checked against a \
compressed bible summary. Find contradictions, dropped threads, timeline errors, and \
characters/objects/details that were established once and then quietly changed or forgotten -- the \
kind of thing a running-summary-based check could plausibly miss because the summary lost the specific \
detail. Return JSON:
{"issues": [{"chapters": "<e.g. 'ch03 vs ch11'>", "issue": "<description>", "severity": "note|warning|contradiction"}]}
Only flag things you can point to concretely in the given text -- quote or paraphrase both sides of a \
contradiction in the issue description. If the manuscript is consistent, return {"issues": []}."""

SPEAKER_ATTRIBUTION_SYSTEM = """You split a chapter's prose into an ordered list of speaker-tagged \
segments, for text-to-speech narration. For each segment, decide who is "speaking" it:
- "narrator" for descriptive/narrative prose, action, and interiority (even in first person).
- a character's exact name (from the list given) for their spoken dialogue lines only -- the words \
  inside quotation marks, not the surrounding "she said" attribution (that stays with the narrator).
Preserve the ORIGINAL TEXT EXACTLY, split only at natural boundaries (paragraph breaks, and at the \
start/end of each quoted line of dialogue) -- do not summarize, paraphrase, correct, or drop any text. \
Concatenating all segments' text back together in order must reproduce the chapter exactly, verbatim.
Return JSON: {"segments": [{"speaker": "narrator"|"<character name>", "text": "..."}]}"""

TITLE_SYSTEM = """You write a short, evocative title for a novel given its premise, genre, and focus \
tags/themes. Output only the title itself -- no quotes, no subtitle unless it truly earns one, no \
commentary, no alternatives."""

CHARACTER_SHEET_SYSTEM = """You generate a character sheet for a novel, grounded in its premise, genre, \
and focus tags/themes. Return JSON:
{
  "name": "<the character's name>",
  "description": "<one or two sentence physical/personality description>",
  "status": "<their situation/position at the very start of the story>",
  "voice_notes": "<diction, sentence rhythm, verbal tics, how their interiority reads -- 3-5 sentences, for keeping their prose voice consistent across chapters written far apart>"
}
If a name was supplied, use it exactly, unchanged. If no name was supplied, invent one that fits the \
story's genre and setting. If an age was supplied, make the description/status consistent with it. Be \
specific and grounded in the given premise/genre/tags -- avoid generic, interchangeable stock-character \
output that could belong to any story. Give them a concrete detail or interest unrelated to their main \
narrative role, not just the one trait the plot needs from them -- a real person is more than their \
function in someone else's story."""

GENRE_PROFILE_SYSTEM = """You design a structural beat-sheet for a novel's freely-chosen genre -- the \
author typed genre text that does not match any of a small set of built-in presets, so you invent an \
equivalent one from scratch, grounded in the genre text, premise, and focus tags given (never generic \
boilerplate -- a "solarpunk heist" and a "gothic romance" should produce visibly different beat-sheets). \
Return JSON:
{
  "beats": [{"id": "<short-slug, unique>", "name": "<...>", "position_pct": <0-100>, "guidance": "<what this beat needs to deliver>", "tension": "rising|plateau|spike|resolution"}, ...],
  "tropes_embrace": ["<genre convention worth leaning into>", "..."],
  "tropes_avoid": ["<genre failure mode/cliche to avoid>", "..."],
  "chapter_hook_rule": "<one sentence: what every chapter should end on for this genre>"
}
Rules: propose 6-10 beats, position_pct strictly ascending starting at 0, the final beat between 95 and \
100 with tension "resolution". Do not write prose -- structure only."""

VOICE_PROFILE_SYSTEM = """You write a short voice profile for a POV character in a novel, to keep their \
prose voice consistent across chapters written far apart. Cover: diction/register, sentence rhythm (short \
and clipped, or long and winding?), verbal tics, and how their interiority reads (analytical? sensory? \
guarded?). Base it on the character description and any excerpts given. Output only the profile, 4-8 \
sentences, no headers or commentary."""


CONTINUE_SYSTEM = """You are a skilled novelist continuing a chapter of a novel already in \
progress, with the author actively steering it. You'll be given the chapter's beats, story bible \
context, and everything written in this chapter SO FAR -- including the author's own edits, which \
are settled canon, not a draft to second-guess. Write the NEXT segment of prose, picking up \
immediately from where the existing text leaves off. Do not repeat, summarize, or restate anything \
already written, and do not wrap up or conclude the chapter unless the beats and what's already \
written make that the obvious next beat. Match the established voice exactly. Output only the new \
continuation text, nothing else."""


def build_draft_system(state: ProjectState) -> str:
    system = DRAFT_SYSTEM_BASE + TENSION_DISCIPLINE_NOTE
    if state.genre_beats:
        system += GENRE_AWARENESS_NOTE
    return system


def build_revise_system(state: ProjectState) -> str:
    system = REVISE_SYSTEM_BASE + TENSION_DISCIPLINE_NOTE
    if state.genre_beats:
        system += GENRE_AWARENESS_NOTE
    return system


def draft_chapter(
    client: LLMClient, project: Project, state: ProjectState, chapters: List[Chapter], chapter: Chapter,
    progress: Optional[Progress] = None,
) -> str:
    if progress:
        progress(f"Drafting '{chapter.title}' (target ~{chapter.word_target} words)...")
    context = build_chapter_context(project, state, chapters, chapter)
    expected_chars = chapter.word_target * 5.5  # rough English chars/word average, for the ETA only
    text = client.call(build_draft_system(state), context, max_tokens=8192, on_token=_token_reporter(progress, "Draft", expected_chars=expected_chars))
    if progress:
        progress(f"Draft done: {len(text.split())} words.")
    return text


def continue_chapter(
    client: LLMClient, project: Project, state: ProjectState, chapters: List[Chapter], chapter: Chapter,
    existing_text: str, progress: Optional[Progress] = None,
) -> str:
    """One model call, returning only the NEW segment to append -- not a rewrite. Reuses
    build_chapter_context() entirely (bible/promises/structural-beat/voice guidance) rather than
    forking it; the appended "written so far" section plus CONTINUE_SYSTEM's own instructions are
    what make this a continuation instead of a fresh draft, so the context-assembly logic itself
    never has to know which mode it's serving."""
    if progress:
        progress(f"Continuing '{chapter.title}'...")
    context = build_chapter_context(project, state, chapters, chapter)
    written_block = existing_text.strip() or "(nothing yet -- this is the opening of the chapter)"
    brief = context + f"\n\n## Written so far in this chapter (continue from here -- do not repeat it)\n{written_block}"
    remaining_chars = max(0, chapter.word_target * 5.5 - len(existing_text))
    text = client.call(
        CONTINUE_SYSTEM, brief, max_tokens=4096,
        on_token=_token_reporter(progress, "Continue", expected_chars=remaining_chars or None),
    )
    if progress:
        progress(f"Continuation done: {len(text.split())} words.")
    return text


def _beat_desc_for(state: ProjectState, chapter: Chapter) -> str:
    if state.genre_beats and chapter.structural_beat:
        beat = next((b for b in state.genre_beats if b.get("id") == chapter.structural_beat), None)
        if beat:
            return f"{beat['name']}: {beat['guidance']} (tension target: {beat.get('tension', 'rising')})"
    return "(no structural beat set for this chapter)"


def critique_chapter(
    client: LLMClient, state: ProjectState, chapter: Chapter, chapter_text: str, progress: Optional[Progress] = None
) -> List[str]:
    """Structural-only critique pass, no rewriting -- run before revise_chapter so revision can
    target specific identified problems instead of blending 'find issues' and 'fix issues' into
    one pass (a single-pass revise, per the Dramaturge research, biases toward superficial edits)."""
    if progress:
        progress("Running structural critique (no rewriting yet)...")
    beats_list = "\n".join(f"- {beat_text(b)}" for b in chapter.beats)
    brief = (
        f"Chapter: {chapter.title}\nTarget length: ~{chapter.word_target} words.\n"
        f"Structural beat: {_beat_desc_for(state, chapter)}\n"
        f"Chapter-ending hook rule: {state.chapter_hook_rule or '(none)'}\n\n"
        f"Beats this chapter was supposed to hit:\n{beats_list}\n\n"
        f"Draft:\n{chapter_text}"
    )
    result = client.call_json(CRITIQUE_SYSTEM, brief, max_tokens=1500)
    findings = result.get("findings", [])
    if progress:
        progress(f"Critique: {len(findings)} structural finding(s).")
    return findings


def revise_chapter(
    client: LLMClient, draft_text: str, state: ProjectState, chapter: Chapter,
    critique_findings: Optional[List[str]] = None, progress: Optional[Progress] = None,
) -> str:
    if progress:
        progress("Revising draft (pacing, dialogue, showing-not-telling)...")
    findings_block = ""
    if critique_findings:
        findings_block = (
            "\n\nA structural critique pass flagged these specific issues -- address them:\n"
            + "\n".join(f"- {f}" for f in critique_findings)
        )
    user = f"Chapter: {chapter.title}\nTarget length: ~{chapter.word_target} words.{findings_block}\n\n{draft_text}"
    expected_chars = chapter.word_target * 5.5
    text = client.call(build_revise_system(state), user, max_tokens=8192, on_token=_token_reporter(progress, "Revise", expected_chars=expected_chars))
    if progress:
        progress(f"Revision done: {len(text.split())} words.")
    return text


def check_premature_resolution(
    client: LLMClient, state: ProjectState, chapter: Chapter, chapter_text: str, progress: Optional[Progress] = None
) -> List[ContinuityFlag]:
    """The counterpart to check_beat_coverage: that catches beats getting SKIPPED, this catches
    tension getting PAID OFF too early -- the failure mode documented in "Spoiler Alert:
    Narrative Forecasting as a Metric for Tension in LLM Storytelling" (2026), where LLM fiction
    systematically resolves uncertainty sooner than skilled human writing does."""
    if progress:
        progress("Checking for premature tension resolution...")
    not_due_promises = [
        f"[{p.id}] {p.description}" + (f" (due by: {p.due_by})" if p.due_by else "")
        for p in state.promises.values()
        if p.status in ("planted", "reinforced") and p.due_by not in (chapter.id, chapter.structural_beat)
    ]
    brief = (
        f"Structural beat for this chapter: {_beat_desc_for(state, chapter)}\n"
        f"Promises NOT yet due (should stay open in this chapter): {not_due_promises or '(none tracked)'}\n\n"
        f"Chapter:\n{chapter_text}"
    )
    result = client.call_json(PREMATURE_RESOLUTION_SYSTEM, brief)
    issues = result.get("issues", [])
    if progress:
        progress(f"Tension check: {len(issues)} issue(s) found.")
    return [
        ContinuityFlag(chapter_id="", issue=i["issue"], severity=i.get("severity", "note"), kind="tension")
        for i in issues
    ]


def check_beat_coverage(
    client: LLMClient, chapter: Chapter, chapter_text: str,
    only_beats: Optional[List[str]] = None, progress: Optional[Progress] = None,
) -> List[dict]:
    """Judges each of `chapter.beats` (or, if `only_beats` is given, just that subset -- used to
    cheaply re-verify a specific patch instead of re-checking the whole chapter) as covered/
    rushed/missing."""
    beats_to_check = only_beats if only_beats is not None else chapter.beats
    if progress:
        progress(f"Checking beat coverage ({len(beats_to_check)} beats)...")
    beats_list = "\n".join(f"- {beat_text(b)}" for b in beats_to_check)
    result = client.call_json(BEAT_COVERAGE_SYSTEM, f"Beats:\n{beats_list}\n\nChapter:\n{chapter_text}")
    beats = result.get("beats", [])
    if progress:
        covered = sum(1 for b in beats if b.get("status") == "covered")
        progress(f"Beat coverage: {covered}/{len(beats)} covered.")
    return beats


def patch_missing_beats(
    client: LLMClient, chapter_text: str, chapter: Chapter, missing_beats: List[str], progress: Optional[Progress] = None
) -> str:
    if progress:
        progress(f"Patching {len(missing_beats)} missing/rushed beat(s) back into the draft...")
    missing_list = "\n".join(f"- {b}" for b in missing_beats)
    user = f"Chapter: {chapter.title}\n\nMissing/rushed beats to add:\n{missing_list}\n\nCurrent draft:\n{chapter_text}"
    text = client.call(PATCH_BEATS_SYSTEM, user, max_tokens=8192, on_token=_token_reporter(progress, "Patch"))
    if progress:
        progress(f"Patch done: {len(text.split())} words.")
    return text


def check_pov_consistency(
    client: LLMClient, state: ProjectState, chapter: Chapter, chapter_text: str, progress: Optional[Progress] = None
) -> List[ContinuityFlag]:
    if progress:
        progress(f"Checking POV consistency for {chapter.pov or 'unspecified POV'}...")
    char = state.characters.get(chapter.pov)
    voice = char.voice_notes if char else ""
    brief = (
        f"POV character: {chapter.pov or 'unspecified'}\nVoice profile: {voice or '(none specified)'}\n\n"
        f"Chapter:\n{chapter_text}"
    )
    result = client.call_json(POV_CONSISTENCY_SYSTEM, brief)
    issues = result.get("issues", [])
    if progress:
        progress(f"POV check: {len(issues)} issue(s) found.")
    return [
        ContinuityFlag(chapter_id="", issue=i["issue"], severity=i.get("severity", "note"), kind="pov")
        for i in issues
    ]


def check_continuity(client: LLMClient, state: ProjectState, chapter_text: str, progress: Optional[Progress] = None) -> List[ContinuityFlag]:
    if progress:
        progress("Checking continuity against the bible...")
    bible_summary = (
        f"Characters: {[(n, c.status) for n, c in state.characters.items()]}\n"
        f"Locations: {list(state.locations.keys())}\n"
        f"Open threads: {[t.description for t in state.plot_threads.values() if t.status == 'open']}"
    )
    result = client.call_json(CONTINUITY_SYSTEM, f"Bible:\n{bible_summary}\n\nChapter:\n{chapter_text}")
    issues = result.get("issues", [])
    if progress:
        progress(f"Continuity check: {len(issues)} issue(s) found.")
    return [
        ContinuityFlag(chapter_id="", issue=i["issue"], severity=i.get("severity", "note"), kind="continuity")
        for i in issues
    ]


def extract_and_update_state(
    client: LLMClient, state: ProjectState, chapter_text: str, chapter_id: str = "", progress: Optional[Progress] = None
) -> ProjectState:
    if progress:
        progress("Extracting facts/promises into the story bible...")
    bible_json = {
        "characters": {n: c.status for n, c in state.characters.items()},
        "locations": list(state.locations.keys()),
        "open_threads": {t.id: t.description for t in state.plot_threads.values() if t.status == "open"},
        "open_promises": {p.id: p.description for p in state.promises.values() if p.status in ("planted", "reinforced")},
    }
    result = client.call_json(EXTRACT_SYSTEM, f"Bible:\n{bible_json}\n\nChapter:\n{chapter_text}")

    for name, update in result.get("character_updates", {}).items():
        if name in state.characters and update:
            state.characters[name].status = update

    for name, desc in result.get("new_characters", {}).items():
        state.characters.setdefault(name, Character(name=name, description=desc, introduced_in=chapter_id))

    for name, desc in result.get("new_locations", {}).items():
        state.locations.setdefault(name, Location(name=name, description=desc, introduced_in=chapter_id))

    for t in result.get("threads_opened", []):
        state.plot_threads.setdefault(
            t["id"], PlotThread(id=t["id"], description=t["description"], opened_in=chapter_id)
        )

    for tid in result.get("threads_resolved", []):
        if tid in state.plot_threads:
            state.plot_threads[tid].status = "resolved"
            state.plot_threads[tid].resolved_in = chapter_id

    for p in result.get("new_promises", []):
        pid = p.get("id")
        if pid and pid not in state.promises:
            state.promises[pid] = Promise(
                id=pid,
                description=p.get("description", ""),
                planted_in=chapter_id,
                due_by=p.get("due_by") or None,
                origin="auto",
            )

    for pid in result.get("promises_paid", []):
        if pid in state.promises:
            state.promises[pid].status = "paid"
            state.promises[pid].paid_in = chapter_id

    summary_addition = result.get("chapter_summary", "")
    if summary_addition:
        state.running_summary = (state.running_summary + "\n" + summary_addition).strip()

    if progress:
        progress(
            f"Extraction done: {len(result.get('new_promises', []))} new promise(s), "
            f"{len(result.get('promises_paid', []))} paid off."
        )
    return state


def audit_promise_staleness(
    state: ProjectState, chapters: List[Chapter], chapter_id: str, max_gap: int = 12
) -> List[ContinuityFlag]:
    """Pure-Python check (no LLM call): flags open promises that have gone stale or blown past
    their due-by chapter/beat. Run after fact extraction so newly-paid promises are already updated."""
    idx_by_id = {c.id: i for i, c in enumerate(chapters)}
    current_idx = idx_by_id.get(chapter_id)
    if current_idx is None:
        return []
    current_chapter = chapters[current_idx]
    current_beat = _resolve_structural_beat(state, chapters, current_chapter)
    current_pct = current_beat.get("position_pct") if current_beat else None
    beat_pct_by_id = {b["id"]: b.get("position_pct") for b in state.genre_beats}

    flags: List[ContinuityFlag] = []
    for p in state.promises.values():
        if p.status not in ("planted", "reinforced"):
            continue
        reason = ""
        if p.planted_in in idx_by_id and current_idx - idx_by_id[p.planted_in] > max_gap:
            gap = current_idx - idx_by_id[p.planted_in]
            reason = f"planted in {p.planted_in}, {gap} chapters ago, still unresolved"
        if p.due_by in idx_by_id and current_idx >= idx_by_id[p.due_by]:
            reason = f"due by chapter {p.due_by}, which has already passed"
        elif p.due_by in beat_pct_by_id and current_pct is not None and current_pct >= (beat_pct_by_id[p.due_by] or 0):
            reason = f"due by the '{p.due_by}' beat, which has already passed"
        if reason:
            flags.append(
                ContinuityFlag(
                    chapter_id=chapter_id,
                    issue=f"Unfired promise [{p.id}]: {p.description} ({reason})",
                    severity="warning",
                    kind="promise",
                )
            )
    return flags


def maybe_compress_summary(
    client: LLMClient, state: ProjectState, threshold_words: int = 900, progress: Optional[Progress] = None
) -> ProjectState:
    if len(state.running_summary.split()) > threshold_words:
        if progress:
            progress("Running summary over threshold -- compressing...")
        state.running_summary = client.call(
            SUMMARY_COMPRESS_SYSTEM, state.running_summary, max_tokens=2048, temperature=0.3
        )
        if progress:
            progress("Summary compressed.")
    return state


def plan_outline(client: LLMClient, state: ProjectState, chapters: List[Chapter], count: int = 3) -> List[dict]:
    """Propose the next `count` chapters as beats (not prose). Returned as plain dicts for the
    caller to review/edit before committing -- never merged into the outline automatically."""
    used_beat_ids = {c.structural_beat for c in chapters if c.structural_beat}
    remaining_beats = [b for b in state.genre_beats if b.get("id") not in used_beat_ids]
    existing_characters = [
        f"{c.name}: {c.description}" + (f" (status: {c.status})" if c.status else "")
        for c in state.characters.values()
    ]
    open_threads = [t.description for t in state.plot_threads.values() if t.status == "open"]
    open_promises = [
        f"[{p.id}] {p.description}" + (f" (due by: {p.due_by})" if p.due_by else "")
        for p in state.promises.values()
        if p.status in ("planted", "reinforced")
    ]
    last_block = "\n".join(f"- {c.title}: {'; '.join(beat_text(b) for b in c.beats)}" for c in chapters[-2:]) or "(none yet -- this is the opening)"

    brief = f"""Premise: {state.premise}
Genre: {state.genre_id or 'unspecified'} (pacing notes: {state.chapter_hook_rule or 'none given'})
Remaining structural beats: {json.dumps(remaining_beats, indent=2)}

Existing characters (pick pov from these if any are listed): {existing_characters or '(none yet)'}

Story so far: {state.running_summary or '(none yet)'}

Open plot threads: {open_threads or '(none)'}
Open promises: {open_promises or '(none)'}

Most recent chapters:
{last_block}

Propose the next {count} chapter(s)."""
    result = client.call_json(OUTLINE_PLAN_SYSTEM, brief, max_tokens=3000)
    return result.get("chapters", [])


def min_chapters_for_remaining_beats(state: ProjectState, chapters: List[Chapter]) -> int:
    """A floor on how many new chapters a book plan needs: at minimum, one per unclaimed
    structural beat, so every beat gets a home and plants have at least some room before their
    payoff chapter instead of being crammed together with no arc to speak of."""
    if not state.genre_beats:
        return 1
    used_beat_ids = {c.structural_beat for c in chapters if c.structural_beat}
    remaining = [b for b in state.genre_beats if b.get("id") not in used_beat_ids]
    return max(len(remaining), 1)


def plan_book(client: LLMClient, state: ProjectState, chapters: List[Chapter], new_chapter_count: int) -> List[dict]:
    """Plans the REST of the book in one call, not just the next few chapters -- the single
    biggest lever for setups actually paying off, since the model can place an exact payoff
    chapter for a plant instead of a reactive per-chapter outliner improvising blind. Each
    proposed chapter's `plants` get folded into its beats and pre-registered as Promise entries
    (origin="planned") when the proposal is committed -- see Project.commit_book_plan_proposal.

    new_chapter_count is floored at min_chapters_for_remaining_beats() regardless of what's
    requested -- asking for fewer chapters than there are unclaimed beats guarantees either a
    skipped beat or a plant with nowhere to breathe before its payoff."""
    new_chapter_count = max(new_chapter_count, min_chapters_for_remaining_beats(state, chapters))
    used_beat_ids = {c.structural_beat for c in chapters if c.structural_beat}
    remaining_beats = [b for b in state.genre_beats if b.get("id") not in used_beat_ids]
    existing_characters = [
        f"{c.name}: {c.description}" + (f" (status: {c.status})" if c.status else "")
        for c in state.characters.values()
    ]
    open_promises = [
        f"[{p.id}] {p.description}" + (f" (due by: {p.due_by})" if p.due_by else "")
        for p in state.promises.values()
        if p.status in ("planted", "reinforced")
    ]
    written_block = (
        "\n".join(f"- {c.id} ({c.title}): {'; '.join(beat_text(b) for b in c.beats)}" for c in chapters)
        or "(none yet -- this plan covers the whole book from the start)"
    )

    brief = f"""Premise: {state.premise}
Genre: {state.genre_id or 'unspecified'}
Full structural beat-sheet: {json.dumps(state.genre_beats, indent=2) if state.genre_beats else '(none -- use a sensible three-act shape)'}
Remaining unclaimed beats: {json.dumps(remaining_beats, indent=2)}

Existing characters: {existing_characters or '(none yet -- invent a cast consistent with the premise)'}
Focus tags: {', '.join(state.tags) or '(none)'}

Chapters already written/planned:
{written_block}

Open promises: {open_promises or '(none yet)'}

Plan exactly {new_chapter_count} new chapters to complete the book."""
    result = client.call_json(BOOK_PLAN_SYSTEM, brief, max_tokens=6000)
    return result.get("chapters", [])


def audit_manuscript(
    client: LLMClient, project: Project, chapters: List[Chapter], progress: Optional[Progress] = None
) -> List[ContinuityFlag]:
    """Reads every written chapter's real text in one call and cross-checks the whole manuscript,
    instead of the per-chapter continuity check's newest-chapter-vs-compressed-bible comparison.
    Meant to be run periodically (every several chapters), not on every `generate` call -- cost
    and prompt size scale with total manuscript length, and it needs at least 2 written chapters
    to have anything to cross-check."""
    written = [c for c in chapters if c.status != "planned"]
    if len(written) < 2:
        return []
    if progress:
        progress(f"Auditing full manuscript ({len(written)} chapters) for cross-chapter issues...")
    manuscript = "\n\n".join(
        f"=== {c.title} ({c.id}) ===\n{project.load_chapter_text(c.id)}" for c in written
    )
    result = client.call_json(MANUSCRIPT_AUDIT_SYSTEM, manuscript, max_tokens=3000)
    issues = result.get("issues", [])
    if progress:
        progress(f"Manuscript audit: {len(issues)} cross-chapter issue(s) found.")
    return [
        ContinuityFlag(
            chapter_id=i.get("chapters", ""), issue=i["issue"], severity=i.get("severity", "note"),
            kind="manuscript_audit",
        )
        for i in issues
    ]


def attribute_speakers(
    client: LLMClient, state: ProjectState, chapter_text: str, progress: Optional[Progress] = None
) -> List[dict]:
    """Splits chapter text into [{"speaker": "narrator"|"<character name>", "text": "..."}]
    segments for narration -- a text-attribution problem (who is this line's speaker), not an
    audio one (diarization tools identify who's speaking in EXISTING audio, which doesn't exist
    yet at this stage)."""
    if progress:
        progress("Attributing narration vs. dialogue by speaker...")
    character_names = list(state.characters.keys())
    brief = f"Characters in this story: {character_names or '(none defined)'}\n\nChapter:\n{chapter_text}"
    result = client.call_json(SPEAKER_ATTRIBUTION_SYSTEM, brief, max_tokens=8192, max_retry_tokens=16384)
    segments = result.get("segments", [])
    if progress:
        speakers = {s.get("speaker") for s in segments}
        progress(f"Attribution: {len(segments)} segment(s), {len(speakers)} distinct speaker(s): {', '.join(sorted(speakers))}.")
    return segments


def narrate_chapter(
    client: LLMClient, project: Project, state: ProjectState, chapter: Chapter, chapter_text: str,
    progress: Optional[Progress] = None,
) -> str:
    """Full text-to-audio pipeline for one chapter: attribute speakers, resolve each speaker to a
    voice-catalog id (character.voice_id, falling back to state.narrator_voice_id for anyone
    unassigned), synthesize each segment with Audio8 (local, voice-cloned per speaker), stitch
    into one file. Requires narration.py's dependencies (torch/transformers/soundfile/librosa/
    faster-whisper) and at least a narrator voice assigned (`voice-assign narrator <id>`)."""
    from . import narration  # deferred: keeps narration's heavier deps optional for text-only use

    segments = attribute_speakers(client, state, chapter_text, progress=progress)
    voice_map = {"narrator": state.narrator_voice_id}
    for name, char in state.characters.items():
        if char.voice_id:
            voice_map[name] = char.voice_id
    if not voice_map.get("narrator"):
        raise ValueError("No narrator voice assigned -- run `voice-assign narrator <voice_id>` first.")

    output_path = os.path.join(project.manuscript_dir, f"{chapter.id}.wav")
    return narration.stitch_narration(segments, voice_map, output_path, progress=progress)


def generate_genre_profile(client: LLMClient, genre_text: str, premise: str, tags: List[str]) -> dict:
    """For a freely-typed genre that doesn't match one of the built-in presets (see genres.py):
    synthesizes an equivalent beat-sheet/tropes/hook-rule from scratch instead of leaving the
    project with no structural guidance at all. Returns {"beats": [...], "tropes_embrace": [...],
    "tropes_avoid": [...], "chapter_hook_rule": "..."} in the same shape clone_preset_beats()
    already produces, so it slots into ProjectState.genre_beats/tropes_*/chapter_hook_rule exactly
    like a cloned preset -- nothing downstream (context.py, the rest of pipeline.py) needs to know
    or care which one it was."""
    brief = (
        f"Genre (as typed by the author): {genre_text}\nPremise: {premise}\n"
        f"Focus tags: {', '.join(tags) or '(none)'}"
    )
    return client.call_json(GENRE_PROFILE_SYSTEM, brief, max_tokens=2500)


def apply_genre(state: ProjectState, raw_genre: str, client: Optional[LLMClient] = None) -> Optional[GenrePreset]:
    """Genre is free text, typed by the author or an agent -- never picked from a fixed list.
    `state.genre_id` is always stored exactly as typed. If it happens to match a built-in preset
    (see genres.GENRE_PRESETS), that preset's beat-sheet/tropes/hook-rule are cloned in for free --
    a fast, deterministic shortcut for the common cases, not a constraint on what can be typed.
    Otherwise, when `client` is given, the model synthesizes a bespoke beat-sheet grounded in the
    raw genre text and the project's premise/tags (see generate_genre_profile), so a genre that
    doesn't match any preset still gets real structural guidance instead of none. Returns the
    matched preset, or None (whether nothing matched, generation ran, or no client was given)."""
    state.genre_id = raw_genre
    preset = match_preset(raw_genre)
    if preset:
        state.genre_beats = clone_preset_beats(preset)
        state.tropes_embrace = list(preset.tropes_embrace)
        state.tropes_avoid = list(preset.tropes_avoid)
        state.chapter_hook_rule = preset.chapter_hook_rule
        return preset
    if client is not None:
        profile = generate_genre_profile(client, raw_genre, state.premise, state.tags)
        state.genre_beats = profile.get("beats", [])
        state.tropes_embrace = profile.get("tropes_embrace", [])
        state.tropes_avoid = profile.get("tropes_avoid", [])
        state.chapter_hook_rule = profile.get("chapter_hook_rule", "")
        return None
    state.genre_beats = []
    state.tropes_embrace = []
    state.tropes_avoid = []
    state.chapter_hook_rule = ""
    return None


def generate_title(client: LLMClient, premise: str, genre_id: Optional[str], tags: List[str]) -> str:
    brief = f"Premise: {premise}\nGenre: {genre_id or 'unspecified'}\nFocus tags: {', '.join(tags) or '(none)'}"
    title = client.call(TITLE_SYSTEM, brief, max_tokens=32, temperature=0.9)
    return title.strip().strip('"').strip("'")


def generate_character_sheet(
    client: LLMClient, state: ProjectState, name: Optional[str] = None, age: Optional[str] = None
) -> dict:
    """One call -> {name, description, status, voice_notes}, grounded in the project's premise/
    genre/tags. Name and age are the only inputs an author has to supply; everything else --
    including the voice profile, folded in here instead of a separate step -- is generated."""
    brief = (
        f"Premise: {state.premise}\nGenre: {state.genre_id or 'unspecified'}\n"
        f"Focus tags: {', '.join(state.tags) or '(none)'}\n"
        f"Name: {name or '(not given -- invent one)'}\nAge: {age or '(not given)'}"
    )
    return client.call_json(CHARACTER_SHEET_SYSTEM, brief, max_tokens=500)


def generate_voice_profile(client: LLMClient, project: Project, chapters: List[Chapter], character: Character) -> str:
    excerpts = []
    for c in chapters:
        if c.pov == character.name:
            text = project.load_chapter_text(c.id)
            if text:
                excerpts.append(text[:1500])
        if len(excerpts) >= 2:
            break
    excerpt_block = "\n\n---\n\n".join(excerpts) if excerpts else "(no chapter text yet)"
    brief = (
        f"Character: {character.name}\nDescription: {character.description}\nStatus: {character.status}\n\n"
        f"Excerpts written in/about them so far:\n{excerpt_block}"
    )
    return client.call(VOICE_PROFILE_SYSTEM, brief, max_tokens=512, temperature=0.7)


def generate_chapter(
    client: LLMClient,
    project: Project,
    chapter_id: str,
    revise: bool = True,
    critique: bool = True,
    critique_rounds: int = 2,
    check: bool = True,
    beat_check: bool = True,
    patch_missing: bool = True,
    pov_check: bool = True,
    tension_check: bool = True,
    progress: Optional[Progress] = None,
) -> Chapter:
    state = project.load_state()
    chapters = project.load_outline()
    chapter = project.get_chapter(chapter_id)

    def _safe_stage(stage_name: str, fn, *fn_args, **fn_kwargs):
        """Run an auxiliary pipeline stage (a check/extraction) without letting its failure
        destroy already-completed work. Draft and revise are NOT run through this -- if those
        fail there's no chapter to save yet anyway -- but every check/audit stage after the
        text is on disk goes through here, because a hiccup in a $0.02 JSON call should never
        cost the minutes of generation that already succeeded."""
        try:
            return fn(*fn_args, **fn_kwargs), None
        except Exception as error:  # noqa: BLE001 -- deliberately broad: this is a resilience boundary
            if progress:
                progress(f"{stage_name} failed, skipping it (chapter text is unaffected): {error}")
            project.add_continuity_flags([
                ContinuityFlag(
                    chapter_id=chapter.id,
                    issue=f"{stage_name} did not complete: {error}",
                    severity="note",
                    kind="pipeline_error",
                )
            ])
            return None, error

    # Draft and revise are the expensive, unrecoverable-if-lost work -- let a failure here
    # raise normally (there's nothing to save yet), but the instant we have text, it's written
    # to disk before anything else touches it.
    text = draft_chapter(client, project, state, chapters, chapter, progress=progress)
    if revise:
        # Critique and revise talk to each other, not just hand off once: round 1 always revises
        # (revise_chapter is a general pacing/dialogue/showing-not-telling pass, not purely a
        # findings-fixer, so it runs even when critique found nothing). From round 2 on, a fresh
        # critique of the just-revised text decides whether another pass is warranted -- if it
        # finds nothing left to fix, the loop stops instead of re-touching an already-clean draft.
        rounds = max(1, min(int(critique_rounds), 4)) if critique else 1
        findings: List[str] = []
        for round_num in range(1, rounds + 1):
            if critique:
                findings, _ = _safe_stage(
                    f"Structural critique (round {round_num}/{rounds})", critique_chapter,
                    client, state, chapter, text, progress=progress,
                )
                findings = findings or []
            if round_num > 1 and not findings:
                if progress:
                    progress("Critique found nothing further to fix -- stopping the revision loop early.")
                break
            text = revise_chapter(client, text, state, chapter, critique_findings=findings, progress=progress)

    project.save_chapter_text(chapter.id, text)
    chapter.status = "revised" if revise else "drafted"
    chapter.file = project.chapter_text_path(chapter.id)
    project.update_chapter(chapter)

    if beat_check:
        coverage, error = _safe_stage("Beat-coverage check", check_beat_coverage, client, chapter, text, progress=progress)
        if coverage is not None:
            missing = [c["beat"] for c in coverage if c.get("status") == "missing"]
            rushed = [c["beat"] for c in coverage if c.get("status") == "rushed"]
            if rushed:
                project.add_continuity_flags([
                    ContinuityFlag(chapter_id=chapter.id, issue=f"Beat rushed: {b}", severity="note", kind="beat_coverage")
                    for b in rushed
                ])
            if missing:
                if patch_missing:
                    patched, error = _safe_stage("Beat patch", patch_missing_beats, client, text, chapter, missing, progress=progress)
                    if patched is not None:
                        text = patched
                        project.save_chapter_text(chapter.id, text)
                        # A patch's own output is never re-checked otherwise -- verify it actually
                        # dramatized what it was supposed to instead of trusting it blindly.
                        recheck, _ = _safe_stage(
                            "Beat-patch verification", check_beat_coverage, client, chapter, text, missing, progress=progress
                        )
                        if recheck is not None:
                            still_missing = [c["beat"] for c in recheck if c.get("status") == "missing"]
                            still_rushed = [c["beat"] for c in recheck if c.get("status") == "rushed"]
                            if still_missing:
                                project.add_continuity_flags([
                                    ContinuityFlag(chapter_id=chapter.id, issue=f"Beat still missing after patch attempt: {b}", severity="warning", kind="beat_coverage")
                                    for b in still_missing
                                ])
                            if still_rushed:
                                project.add_continuity_flags([
                                    ContinuityFlag(chapter_id=chapter.id, issue=f"Beat still rushed after patch attempt: {b}", severity="note", kind="beat_coverage")
                                    for b in still_rushed
                                ])
                    else:
                        project.add_continuity_flags([
                            ContinuityFlag(chapter_id=chapter.id, issue=f"Beat missing (patch failed): {b}", severity="warning", kind="beat_coverage")
                            for b in missing
                        ])
                else:
                    project.add_continuity_flags([
                        ContinuityFlag(chapter_id=chapter.id, issue=f"Beat missing: {b}", severity="warning", kind="beat_coverage")
                        for b in missing
                    ])

    if tension_check:
        tension_flags, _ = _safe_stage("Tension/premature-resolution check", check_premature_resolution, client, state, chapter, text, progress=progress)
        if tension_flags:
            for f in tension_flags:
                f.chapter_id = chapter.id
            project.add_continuity_flags(tension_flags)

    if pov_check and chapter.pov:
        pov_flags, _ = _safe_stage("POV-consistency check", check_pov_consistency, client, state, chapter, text, progress=progress)
        if pov_flags:
            for f in pov_flags:
                f.chapter_id = chapter.id
            project.add_continuity_flags(pov_flags)

    if check:
        flags, _ = _safe_stage("Continuity check", check_continuity, client, state, text, progress=progress)
        if flags:
            for f in flags:
                f.chapter_id = chapter.id
            project.add_continuity_flags(flags)

    new_state, error = _safe_stage("Fact extraction", extract_and_update_state, client, state, text, chapter.id, progress=progress)
    if new_state is not None:
        state = new_state

        staleness_flags = audit_promise_staleness(state, chapters, chapter.id)
        if staleness_flags:
            project.add_continuity_flags(staleness_flags)
            if progress:
                progress(f"Promise-staleness audit: {len(staleness_flags)} overdue promise(s) flagged.")

        dependency_flags = check_dependencies(state, chapters)
        if dependency_flags:
            project.add_continuity_flags(dependency_flags)
            if progress:
                progress(f"Dependency check: {len(dependency_flags)} out-of-order reference(s) flagged.")

        state, _ = _safe_stage("Summary compression", maybe_compress_summary, client, state, progress=progress)
        if state is None:
            state = new_state  # compression failed -- keep the pre-compression (still valid) state
        project.save_state(state)
    # If extraction itself failed, the bible is left untouched rather than half-updated --
    # the chapter text and its status are already saved regardless.

    if progress:
        progress(f"Chapter '{chapter.id}' complete -> status={chapter.status}.")
    return chapter


def continue_and_extract(
    client: LLMClient, project: Project, chapter_id: str,
    edited_text: Optional[str] = None, progress: Optional[Progress] = None,
) -> dict:
    """The NovelAI-style "Send" button: appends one continuation segment to a chapter instead of
    drafting/rewriting the whole thing, and keeps the bible in sync as it goes -- a lighter,
    faster sibling to generate_chapter() (2 model calls, not up to ~13), for repeated clicking
    rather than a one-shot full editorial pass (that's still what generate_chapter/"Regenerate
    this chapter" is for).

    edited_text, if given, is saved as the chapter's current text FIRST, before anything else --
    this is what captures the user's own selection/delete/reword edits (made directly in the
    editor) as settled canon before the model continues from them. None means "continue from
    whatever's already on disk," e.g. for a non-interactive caller that isn't tracking the text
    itself.
    """
    state = project.load_state()
    chapters = project.load_outline()
    chapter = project.get_chapter(chapter_id)

    if edited_text is not None:
        project.save_chapter_text(chapter_id, edited_text)
        existing_text = edited_text
    else:
        existing_text = project.load_chapter_text(chapter_id)

    new_text = continue_chapter(client, project, state, chapters, chapter, existing_text, progress=progress)
    full_text = f"{existing_text.rstrip()}\n\n{new_text.strip()}" if existing_text.strip() else new_text.strip()
    project.save_chapter_text(chapter_id, full_text)

    if chapter.status == "planned":
        chapter.status = "drafted"
    chapter.file = project.chapter_text_path(chapter_id)
    project.update_chapter(chapter)

    characters_before = copy.deepcopy(state.characters)
    try:
        state = extract_and_update_state(client, state, full_text, chapter_id, progress=progress)
    except Exception as error:  # noqa: BLE001 -- same resilience boundary generate_chapter's _safe_stage gives extraction
        if progress:
            progress(f"Fact extraction failed, skipping it (chapter text is unaffected): {error}")
        project.add_continuity_flags([
            ContinuityFlag(chapter_id=chapter_id, issue=f"Fact extraction did not complete: {error}", severity="note", kind="pipeline_error")
        ])
        characters_created: List[str] = []
        characters_updated: List[str] = []
    else:
        characters_created = [name for name in state.characters if name not in characters_before]
        characters_updated = [
            name for name, char in state.characters.items()
            if name in characters_before and char != characters_before[name]
        ]

        staleness_flags = audit_promise_staleness(state, chapters, chapter_id)
        if staleness_flags:
            project.add_continuity_flags(staleness_flags)

        dependency_flags = check_dependencies(state, chapters)
        if dependency_flags:
            project.add_continuity_flags(dependency_flags)

        project.save_state(state)

    if progress:
        progress(f"Chapter '{chapter_id}' continued -> {len(full_text.split())} words total.")

    return {
        "chapter": chapter, "text": full_text, "appended": new_text,
        "characters_created": characters_created, "characters_updated": characters_updated,
    }
