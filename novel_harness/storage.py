"""Project persistence: on-disk layout and load/save helpers.

Layout of a project directory:
  state.json               -> ProjectState (bible: characters, locations, threads, promises, genre, style, summary)
  outline.json              -> list[Chapter]
  outline_proposal.json     -> list[dict], a not-yet-committed proposal from plan_outline() (review/edit, then commit)
  book_plan_proposal.json   -> list[dict], a not-yet-committed whole-book proposal from plan_book() (each chapter's
                                `plants` get folded into its beats and pre-registered as Promise entries on commit)
  continuity_log.json       -> list[ContinuityFlag] (kinds: continuity | pov | promise | beat_coverage | tension |
                                manuscript_audit | pipeline_error)
  manuscript/<id>.md        -> chapter prose
"""
from __future__ import annotations
import json
import os
import re
from dataclasses import asdict
from typing import List

from .models import ProjectState, Chapter, ContinuityFlag, Character, Location, Memory, Motif, PlotThread, Promise, beat_establishes, beat_text


def _register_planned_entities(state: ProjectState, chapter: Chapter) -> None:
    """When a committed outline/book-plan chapter names a POV character, or one of its beats
    authors an `establishes` ref (see models.py's Chapter.beats docstring) for an entity that
    doesn't exist in the bible yet, create a stub entry for it now -- at commit time, before any
    chapter is ever drafted -- instead of leaving it to only materialize reactively once a chapter
    that happens to mention it gets drafted and fact-extraction notices it. Without this, a
    freshly-planned outline can name a POV character or reference `establishes: ["character:X"]`
    that never appears anywhere in `state.characters`, so the Characters page has nothing to show
    or edit even though the outline already depends on it existing -- exactly the class of gap
    `commit_book_plan_proposal`'s `plants` -> Promise pre-registration already solved for promises;
    this generalizes that same idea to every entity kind an authored `establishes` tag can name.
    Never overwrites an entity that already exists (a plant's own promise pre-registration, run
    before this, always wins over the stub this would otherwise create for the same id)."""
    if chapter.pov and chapter.pov not in state.characters:
        state.characters[chapter.pov] = Character(name=chapter.pov, introduced_in=chapter.id)

    for beat in chapter.beats:
        for ref in beat_establishes(beat):
            if ":" not in ref:
                continue
            kind, raw_id = ref.split(":", 1)
            if not raw_id:
                continue
            if kind == "character" and raw_id not in state.characters:
                state.characters[raw_id] = Character(name=raw_id, introduced_in=chapter.id)
            elif kind == "location" and raw_id not in state.locations:
                state.locations[raw_id] = Location(name=raw_id, introduced_in=chapter.id)
            elif kind == "plot_thread" and raw_id not in state.plot_threads:
                state.plot_threads[raw_id] = PlotThread(id=raw_id, description=beat_text(beat), opened_in=chapter.id)
            elif kind == "promise" and raw_id not in state.promises:
                state.promises[raw_id] = Promise(id=raw_id, description=beat_text(beat), planted_in=chapter.id, origin="planned")


class Project:
    def __init__(self, root: str):
        self.root = root
        self.state_path = os.path.join(root, "state.json")
        self.outline_path = os.path.join(root, "outline.json")
        self.outline_proposal_path = os.path.join(root, "outline_proposal.json")
        self.book_plan_proposal_path = os.path.join(root, "book_plan_proposal.json")
        self.continuity_path = os.path.join(root, "continuity_log.json")
        self.manuscript_dir = os.path.join(root, "manuscript")

    # ---- lifecycle ----
    @staticmethod
    def init(root: str, title: str, premise: str, style_guide: str = "") -> "Project":
        os.makedirs(root, exist_ok=True)
        p = Project(root)
        os.makedirs(p.manuscript_dir, exist_ok=True)
        p.save_state(ProjectState(title=title, premise=premise, style_guide=style_guide))
        p.save_outline([])
        p.save_continuity([])
        return p

    def exists(self) -> bool:
        return os.path.isfile(self.state_path)

    # ---- state (story bible) ----
    def load_state(self) -> ProjectState:
        with open(self.state_path, "r", encoding="utf-8") as f:
            return ProjectState.from_json(json.load(f))

    def save_state(self, state: ProjectState) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state.to_json(), f, indent=2)

    # ---- bible mutation: remove/rename/resolve (add/update is a merge callers do on the loaded
    # state directly -- see agent_wrapper.update_bible's _merge_into) ----
    def remove_character(self, name: str) -> Character:
        state = self.load_state()
        if name not in state.characters:
            raise KeyError(f"No such character: {name!r}. Existing: {', '.join(state.characters) or '(none)'}")
        removed = state.characters.pop(name)
        self.save_state(state)
        return removed

    def rename_character(self, old: str, new: str) -> Character:
        """Renames the bible entry and updates every outline chapter's `pov` that references the
        old name -- the one structural reference to a character name outside state.json. Does not
        touch beat text or already-written manuscript prose (see README's known limitations)."""
        state = self.load_state()
        if old not in state.characters:
            raise KeyError(f"No such character: {old!r}. Existing: {', '.join(state.characters) or '(none)'}")
        if new in state.characters:
            raise ValueError(f"A character named {new!r} already exists.")
        char = state.characters.pop(old)
        char.name = new
        state.characters[new] = char
        self.save_state(state)
        chapters = self.load_outline()
        if any(c.pov == old for c in chapters):
            for c in chapters:
                if c.pov == old:
                    c.pov = new
            self.save_outline(chapters)
        return char

    def remove_location(self, name: str) -> Location:
        state = self.load_state()
        if name not in state.locations:
            raise KeyError(f"No such location: {name!r}. Existing: {', '.join(state.locations) or '(none)'}")
        removed = state.locations.pop(name)
        self.save_state(state)
        return removed

    def rename_location(self, old: str, new: str) -> Location:
        state = self.load_state()
        if old not in state.locations:
            raise KeyError(f"No such location: {old!r}. Existing: {', '.join(state.locations) or '(none)'}")
        if new in state.locations:
            raise ValueError(f"A location named {new!r} already exists.")
        loc = state.locations.pop(old)
        loc.name = new
        state.locations[new] = loc
        self.save_state(state)
        return loc

    def remove_thread(self, thread_id: str) -> PlotThread:
        state = self.load_state()
        if thread_id not in state.plot_threads:
            raise KeyError(f"No such plot thread: {thread_id!r}. Existing: {', '.join(state.plot_threads) or '(none)'}")
        removed = state.plot_threads.pop(thread_id)
        self.save_state(state)
        return removed

    def rename_thread(self, old: str, new: str) -> PlotThread:
        state = self.load_state()
        if old not in state.plot_threads:
            raise KeyError(f"No such plot thread: {old!r}. Existing: {', '.join(state.plot_threads) or '(none)'}")
        if new in state.plot_threads:
            raise ValueError(f"A plot thread with id {new!r} already exists.")
        thread = state.plot_threads.pop(old)
        thread.id = new
        state.plot_threads[new] = thread
        self.save_state(state)
        return thread

    def remove_promise(self, promise_id: str) -> Promise:
        state = self.load_state()
        if promise_id not in state.promises:
            raise KeyError(f"No such promise: {promise_id!r}. Existing: {', '.join(state.promises) or '(none)'}")
        removed = state.promises.pop(promise_id)
        self.save_state(state)
        return removed

    def rename_promise(self, old: str, new: str) -> Promise:
        state = self.load_state()
        if old not in state.promises:
            raise KeyError(f"No such promise: {old!r}. Existing: {', '.join(state.promises) or '(none)'}")
        if new in state.promises:
            raise ValueError(f"A promise with id {new!r} already exists.")
        promise = state.promises.pop(old)
        promise.id = new
        state.promises[new] = promise
        self.save_state(state)
        return promise

    def resolve_promise(self, promise_id: str, chapter_id: str) -> Promise:
        state = self.load_state()
        if promise_id not in state.promises:
            raise KeyError(f"No such promise: {promise_id!r}. Existing: {', '.join(state.promises) or '(none)'}")
        promise = state.promises[promise_id]
        promise.status = "paid"
        promise.paid_in = chapter_id
        self.save_state(state)
        return promise

    def remove_motif(self, motif_id: str) -> Motif:
        state = self.load_state()
        if motif_id not in state.motifs:
            raise KeyError(f"No such motif: {motif_id!r}. Existing: {', '.join(state.motifs) or '(none)'}")
        removed = state.motifs.pop(motif_id)
        self.save_state(state)
        return removed

    def rename_motif(self, old: str, new: str) -> Motif:
        state = self.load_state()
        if old not in state.motifs:
            raise KeyError(f"No such motif: {old!r}. Existing: {', '.join(state.motifs) or '(none)'}")
        if new in state.motifs:
            raise ValueError(f"A motif with id {new!r} already exists.")
        motif = state.motifs.pop(old)
        motif.id = new
        state.motifs[new] = motif
        self.save_state(state)
        return motif

    def remove_memory(self, memory_id: str) -> Memory:
        state = self.load_state()
        if memory_id not in state.memories:
            raise KeyError(f"No such memory: {memory_id!r}. Existing: {', '.join(state.memories) or '(none)'}")
        removed = state.memories.pop(memory_id)
        self.save_state(state)
        return removed

    def rename_memory(self, old: str, new: str) -> Memory:
        state = self.load_state()
        if old not in state.memories:
            raise KeyError(f"No such memory: {old!r}. Existing: {', '.join(state.memories) or '(none)'}")
        if new in state.memories:
            raise ValueError(f"A memory with id {new!r} already exists.")
        memory = state.memories.pop(old)
        memory.id = new
        state.memories[new] = memory
        self.save_state(state)
        return memory

    def resolve_memory(self, memory_id: str) -> Memory:
        state = self.load_state()
        if memory_id not in state.memories:
            raise KeyError(f"No such memory: {memory_id!r}. Existing: {', '.join(state.memories) or '(none)'}")
        memory = state.memories[memory_id]
        memory.status = "resolved"
        self.save_state(state)
        return memory

    # ---- outline ----
    def load_outline(self) -> List[Chapter]:
        with open(self.outline_path, "r", encoding="utf-8") as f:
            return [Chapter(**c) for c in json.load(f)]

    def save_outline(self, chapters: List[Chapter]) -> None:
        with open(self.outline_path, "w", encoding="utf-8") as f:
            json.dump([asdict(c) for c in chapters], f, indent=2)

    def get_chapter(self, chapter_id: str) -> Chapter:
        chapters = self.load_outline()
        for c in chapters:
            if c.id == chapter_id:
                return c
        available = ", ".join(c.id for c in chapters) or "(none yet -- use `outline-add` or `outline-plan` first)"
        raise KeyError(f"No such chapter: {chapter_id!r}. Available chapters: {available}")

    def update_chapter(self, chapter: Chapter) -> None:
        chapters = self.load_outline()
        for i, c in enumerate(chapters):
            if c.id == chapter.id:
                chapters[i] = chapter
                self.save_outline(chapters)
                return
        raise KeyError(f"No such chapter: {chapter.id}")

    def remove_chapter(self, chapter_id: str, force: bool = False) -> Chapter:
        """Refuses to delete a chapter that's already been drafted/revised/finalized unless
        `force=True` -- protects written prose from an accidental one-line deletion. When forced
        (or when the chapter was still only "planned"), also removes manuscript/<id>.md if it
        exists. Does not cascade into promises/continuity flags that reference this chapter id --
        matches the harness's existing tolerance for dangling references (e.g.
        audit_promise_staleness already just no-ops on an id it can't find)."""
        chapters = self.load_outline()
        idx = next((i for i, c in enumerate(chapters) if c.id == chapter_id), None)
        if idx is None:
            available = ", ".join(c.id for c in chapters) or "(none)"
            raise KeyError(f"No such chapter: {chapter_id!r}. Available chapters: {available}")
        chapter = chapters[idx]
        if chapter.status != "planned" and not force:
            raise ValueError(
                f"Chapter {chapter_id!r} has status {chapter.status!r} (already drafted) -- pass "
                f"force=True to delete it and its manuscript text anyway."
            )
        del chapters[idx]
        self.save_outline(chapters)
        text_path = self.chapter_text_path(chapter_id)
        if os.path.isfile(text_path):
            os.remove(text_path)
        return chapter

    # ---- outline proposal (from plan_outline(), reviewed before committing) ----
    def load_outline_proposal(self) -> List[dict]:
        if not os.path.isfile(self.outline_proposal_path):
            return []
        with open(self.outline_proposal_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_outline_proposal(self, proposal: List[dict]) -> None:
        with open(self.outline_proposal_path, "w", encoding="utf-8") as f:
            json.dump(proposal, f, indent=2)

    def commit_outline_proposal(self) -> List[Chapter]:
        """Append the reviewed proposal onto outline.json, skipping any id that already exists.
        Also stub-registers any POV character or authored `establishes` ref the new chapters name
        that isn't in the bible yet -- see _register_planned_entities."""
        proposal = self.load_outline_proposal()
        chapters = self.load_outline()
        state = self.load_state()
        existing_ids = {c.id for c in chapters}
        added = []
        for p in proposal:
            if p.get("id") in existing_ids:
                continue
            chapter = Chapter(
                id=p["id"],
                title=p.get("title", ""),
                pov=p.get("pov", ""),
                beats=p.get("beats", []),
                word_target=p.get("word_target", 2500),
                structural_beat=p.get("structural_beat") or None,
            )
            chapters.append(chapter)
            added.append(chapter)
            _register_planned_entities(state, chapter)
        self.save_outline(chapters)
        self.save_state(state)
        os.remove(self.outline_proposal_path)
        return added

    # ---- book plan proposal (from plan_book(), reviewed before committing) ----
    def load_book_plan_proposal(self) -> List[dict]:
        if not os.path.isfile(self.book_plan_proposal_path):
            return []
        with open(self.book_plan_proposal_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_book_plan_proposal(self, proposal: List[dict]) -> None:
        with open(self.book_plan_proposal_path, "w", encoding="utf-8") as f:
            json.dump(proposal, f, indent=2)

    def commit_book_plan_proposal(self) -> List[Chapter]:
        """Append the reviewed whole-book proposal onto outline.json, skipping any id that
        already exists. Each chapter's `plants` are folded into its beats (so the drafting model
        actually writes them) AND pre-registered as Promise entries with due_by=payoff_chapter
        (origin="planned") -- so the promise ledger knows about a payoff obligation before that
        chapter is ever drafted, not just after extraction reactively detects it. Also
        stub-registers any POV character or authored `establishes` ref (characters/locations/
        threads/promises not already covered by a plant) the new chapters name -- see
        _register_planned_entities."""
        proposal = self.load_book_plan_proposal()
        chapters = self.load_outline()
        state = self.load_state()
        existing_ids = {c.id for c in chapters}
        existing_promise_ids = set(state.promises.keys())
        added = []
        for p in proposal:
            cid = p.get("id")
            if not cid or cid in existing_ids:
                continue
            beats = list(p.get("beats", []))
            for plant in p.get("plants", []):
                desc = plant.get("description", "")
                if not desc:
                    continue
                payoff = plant.get("payoff_chapter") or None
                beats.append(
                    f"Plant a setup: {desc}" + (f" (must be able to pay off in {payoff})" if payoff else "")
                )
                pid = re.sub(r"[^a-z0-9]+", "-", desc.lower()).strip("-")[:40] or f"promise-{len(existing_promise_ids) + 1}"
                base_pid, n = pid, 2
                while pid in existing_promise_ids:
                    pid, n = f"{base_pid}-{n}", n + 1
                state.promises[pid] = Promise(id=pid, description=desc, planted_in=cid, due_by=payoff, origin="planned")
                existing_promise_ids.add(pid)
            chapter = Chapter(
                id=cid,
                title=p.get("title", ""),
                pov=p.get("pov", ""),
                beats=beats,
                word_target=p.get("word_target", 2500),
                structural_beat=p.get("structural_beat") or None,
            )
            chapters.append(chapter)
            existing_ids.add(cid)
            added.append(chapter)
            _register_planned_entities(state, chapter)
        self.save_outline(chapters)
        self.save_state(state)
        os.remove(self.book_plan_proposal_path)
        return added

    # ---- continuity log ----
    def load_continuity(self) -> List[ContinuityFlag]:
        with open(self.continuity_path, "r", encoding="utf-8") as f:
            return [ContinuityFlag(**c) for c in json.load(f)]

    def save_continuity(self, flags: List[ContinuityFlag]) -> None:
        with open(self.continuity_path, "w", encoding="utf-8") as f:
            json.dump([asdict(c) for c in flags], f, indent=2)

    def add_continuity_flags(self, new_flags: List[ContinuityFlag]) -> None:
        flags = self.load_continuity()
        flags.extend(new_flags)
        self.save_continuity(flags)

    def resolve_continuity_flag(self, index: int) -> ContinuityFlag:
        """continuity_log.json entries have no id of their own -- `index` is the flag's position
        in the list load_continuity() returns (the same array snapshot()/`continuity-show` print),
        which is stable since flags are only ever appended, never reordered or removed."""
        flags = self.load_continuity()
        if index < 0 or index >= len(flags):
            raise IndexError(f"No continuity flag at index {index} (log has {len(flags)} entries).")
        flags[index].resolved = True
        self.save_continuity(flags)
        return flags[index]

    # ---- manuscript text ----
    def chapter_text_path(self, chapter_id: str) -> str:
        return os.path.join(self.manuscript_dir, f"{chapter_id}.md")

    def save_chapter_text(self, chapter_id: str, text: str) -> str:
        path = self.chapter_text_path(chapter_id)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def load_chapter_text(self, chapter_id: str) -> str:
        path = self.chapter_text_path(chapter_id)
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
