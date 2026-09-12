"""Assembles the prompt context for drafting a given chapter.

The core problem this solves: you can't fit a whole novel-in-progress into a
context window, and you shouldn't try. Instead each chapter gets: the style
guide, a compressed running summary of everything so far, only the bible
entries plausibly relevant to *this* chapter's beats (matched by name against
the beat text, falling back to the full bible if nothing matches), the
previous chapter's full text (for voice continuity), the current outline
beats, the chapter's structural beat (genre-conditioned pacing/tension
guidance), the POV character's voice profile, and any promises to the reader
that are relevant or coming due.
"""
from __future__ import annotations
from typing import List, Optional

from .models import ProjectState, Chapter, Promise, beat_text
from .storage import Project

PREV_CHAPTERS_FOR_VOICE = 1


def _mentioned(names: List[str], text: str) -> List[str]:
    text_lower = text.lower()
    return [n for n in names if n.lower() in text_lower]


def _chapter_position_pct(state: ProjectState, chapters: List[Chapter], chapter: Chapter) -> Optional[float]:
    if not state.target_chapters:
        return None
    idx = next((i for i, c in enumerate(chapters) if c.id == chapter.id), None)
    if idx is None:
        return None
    return round(100.0 * idx / max(1, state.target_chapters - 1), 1) if state.target_chapters > 1 else 0.0


def _resolve_structural_beat(state: ProjectState, chapters: List[Chapter], chapter: Chapter) -> Optional[dict]:
    """Chapter.structural_beat wins outright; otherwise guess from position_pct."""
    if chapter.structural_beat:
        return next((b for b in state.genre_beats if b.get("id") == chapter.structural_beat), None)
    pct = _chapter_position_pct(state, chapters, chapter)
    if pct is None or not state.genre_beats:
        return None
    return min(state.genre_beats, key=lambda b: abs(b.get("position_pct", 0) - pct))


def _build_structural_beat_block(state: ProjectState, chapters: List[Chapter], chapter: Chapter) -> str:
    if not state.genre_beats:
        return ""
    beat = _resolve_structural_beat(state, chapters, chapter)
    lines = []
    if beat:
        lines.append(
            f"This chapter corresponds to the **{beat['name']}** beat "
            f"(~{beat.get('position_pct', '?')}% through the book, target tension: {beat.get('tension', 'rising')}).\n"
            f"What that beat needs: {beat['guidance']}"
        )
    if state.tropes_embrace:
        lines.append("Genre conventions to embrace: " + "; ".join(state.tropes_embrace))
    if state.tropes_avoid:
        lines.append("Tropes/failure modes to avoid: " + "; ".join(state.tropes_avoid))
    if state.chapter_hook_rule:
        lines.append(f"Chapter-ending rule: {state.chapter_hook_rule}")
    return "\n".join(lines)


def _build_pov_voice_block(state: ProjectState, chapter: Chapter) -> str:
    char = state.characters.get(chapter.pov)
    if not char or not char.voice_notes:
        return ""
    return (
        f"Voice profile for POV character {char.name} (write consistently in this voice; "
        f"do not head-hop into another character's interiority):\n{char.voice_notes}"
    )


def _build_motifs_block(state: ProjectState, chapters: List[Chapter], chapter: Chapter) -> str:
    """Motifs (Motif -- see models.py) stay pinned into every chapter's context from the moment
    they exist, unlike promises (which are filtered by relevance/due-date): the craft they encode
    only works through repetition, so the model needs the reminder every time, not just when the
    beats happen to mention the phrase already. Excludes only a motif explicitly tied to a LATER
    chapter than this one (first_used_in set to something still ahead in the outline)."""
    if not state.motifs:
        return ""
    idx_by_id = {c.id: i for i, c in enumerate(chapters)}
    current_idx = idx_by_id.get(chapter.id, len(chapters))
    active = [
        m for m in state.motifs.values()
        if not m.first_used_in or idx_by_id.get(m.first_used_in, 0) <= current_idx
    ]
    if not active:
        return ""
    lines = [f"- \"{m.phrase}\"" + (f" -- {m.notes}" if m.notes else "") for m in active]
    return (
        "Recurring motifs -- bring one back if a moment naturally calls for it, each recurrence "
        "should land with more weight than the last, not identically:\n" + "\n".join(lines)
    )


def _build_memories_block(state: ProjectState, chapter: Chapter, beats_text: str) -> str:
    """The Telltale-games mechanic (Memory -- see models.py): a specific past incident that keeps
    shaping how one character treats another. Unlike motifs (always pinned), memories are filtered
    by relevance like promises are -- only pinned when `subject` is this chapter's POV or is
    mentioned in the beats, since a large cast can accumulate many memories and most won't matter
    to any given chapter. Resolved memories (a grudge forgiven, trust repaired) are excluded."""
    active = [m for m in state.memories.values() if m.status == "active"]
    if not active:
        return ""
    relevant = [m for m in active if m.subject == chapter.pov or _mentioned([m.subject], beats_text)]
    if not relevant:
        return ""
    lines = [
        f"- {m.subject}, re: {m.about or '(general)'} -- {m.event}. Effect: {m.effect}"
        for m in relevant
    ]
    return (
        "Specific past incidents that should keep shaping behavior -- not just facts, an active "
        "steer on how this character acts toward who's named, given what actually happened:\n"
        + "\n".join(lines)
    )


def _build_frame_block(project: Project, state: ProjectState, chapter: Chapter) -> str:
    """When chapter.frame_of names another chapter (see models.Chapter.frame_of), pull in that
    frame chapter's POV/voice and a short excerpt -- the embedded chapter is being told FROM
    within that frame (an old man remembering, a journalist transcribing), so the drafting model
    needs to know whose voice is doing the framing, not just what happens in the embedded scene."""
    if not chapter.frame_of:
        return ""
    frame = next((c for c in project.load_outline() if c.id == chapter.frame_of), None)
    if not frame:
        return ""
    frame_text = project.load_chapter_text(frame.id)
    excerpt = f"\nExcerpt from the frame chapter, for voice: {frame_text[:800]}" if frame_text else ""
    voice = state.characters.get(frame.pov)
    voice_line = f" Voice: {voice.voice_notes}" if voice and voice.voice_notes else ""
    return (
        f"This chapter is narrated FROM WITHIN the frame of \"{frame.title}\" ({frame.id}, "
        f"POV: {frame.pov or 'unspecified'}).{voice_line} Write the embedded scene consistent "
        f"with being recalled/recounted from that vantage, not as an independent present-tense "
        f"scene unless the frame itself is present-tense.{excerpt}"
    )


def _build_promises_block(state: ProjectState, chapter: Chapter, beats_text: str) -> str:
    open_promises = [p for p in state.promises.values() if p.status in ("planted", "reinforced")]
    if not open_promises:
        return ""
    mentioned = set(_mentioned([p.description for p in open_promises], beats_text))
    relevant = [
        p for p in open_promises
        if p.description in mentioned or p.due_by in (chapter.id, chapter.structural_beat)
    ]
    if not relevant:
        return ""
    lines = [
        f"- [{p.id}] {p.description}" + (f" (due by: {p.due_by})" if p.due_by else "")
        for p in relevant
    ]
    return "Promises to the reader that may be relevant here (pay off, reinforce, or consciously leave open):\n" + "\n".join(lines)


def build_chapter_context(project: Project, state: ProjectState, chapters: List[Chapter], chapter: Chapter) -> str:
    beats_text = "\n".join(f"- {beat_text(b)}" for b in chapter.beats)

    char_names = list(state.characters.keys())
    loc_names = list(state.locations.keys())
    relevant_chars = _mentioned(char_names, beats_text) or char_names
    relevant_locs = _mentioned(loc_names, beats_text) or loc_names

    char_block = "\n".join(
        f"- {name}: {state.characters[name].description} "
        f"(current status: {state.characters[name].status or 'unspecified'})"
        for name in relevant_chars
    ) or "(none defined)"

    loc_block = "\n".join(
        f"- {name}: {state.locations[name].description}" for name in relevant_locs
    ) or "(none defined)"

    open_threads = [t for t in state.plot_threads.values() if t.status == "open"]
    thread_block = "\n".join(f"- [{t.id}] {t.description}" for t in open_threads) or "(none open)"

    idx = next((i for i, c in enumerate(chapters) if c.id == chapter.id), len(chapters))
    prev_chapters = chapters[max(0, idx - PREV_CHAPTERS_FOR_VOICE):idx]
    prev_text_block = ""
    for pc in prev_chapters:
        text = project.load_chapter_text(pc.id)
        if text:
            prev_text_block += f"\n\n--- Previous chapter ({pc.title}), for voice/continuity only ---\n{text}"

    structural_beat_block = _build_structural_beat_block(state, chapters, chapter)
    pov_voice_block = _build_pov_voice_block(state, chapter)
    promises_block = _build_promises_block(state, chapter, beats_text)
    motifs_block = _build_motifs_block(state, chapters, chapter)
    memories_block = _build_memories_block(state, chapter, beats_text)
    frame_block = _build_frame_block(project, state, chapter)

    extra_sections = ""
    if structural_beat_block:
        extra_sections += f"\n## Where this chapter sits in the story\n{structural_beat_block}\n"
    if pov_voice_block:
        extra_sections += f"\n## POV voice\n{pov_voice_block}\n"
    if promises_block:
        extra_sections += f"\n## Open promises\n{promises_block}\n"
    if motifs_block:
        extra_sections += f"\n## Recurring motifs\n{motifs_block}\n"
    if memories_block:
        extra_sections += f"\n## Remembered incidents (affects how characters treat each other)\n{memories_block}\n"
    if frame_block:
        extra_sections += f"\n## Frame narrative\n{frame_block}\n"

    tags_block = f"\n## Focus / themes to keep alive throughout\n{', '.join(state.tags)}\n" if state.tags else ""
    genre_block = f"\n## Genre\n{state.genre_id}\n" if state.genre_id else ""

    return f"""# Story: {state.title}

## Premise
{state.premise}
{genre_block}{tags_block}
## Style guide
{state.style_guide or "(none specified; use clear, vivid literary prose)"}

## Story so far (running summary)
{state.running_summary or "(this is the opening chapter)"}

## Relevant characters
{char_block}

## Relevant locations
{loc_block}

## Open plot threads
{thread_block}
{extra_sections}{prev_text_block}

## This chapter to write: {chapter.title} (POV: {chapter.pov or "unspecified"})
Target length: ~{chapter.word_target} words.

Beats to hit, in order. Vary pacing deliberately: dilate the beats that carry emotional weight
into full scenes with room to breathe, and it's fine to compress a purely transitional beat into a
sentence or two of summary on purpose -- that's a legitimate pacing choice, not a shortcut, as long
as it's a genuine choice and not every beat getting the thin treatment:
{beats_text}
"""
