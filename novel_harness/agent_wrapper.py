"""Machine-facing wrapper for agents building a novel with :mod:`novel_harness`.

The regular CLI is intentionally friendly to human authors and may prompt or mix
progress output with results.  This module provides a small JSON-in/JSON-out
interface with no prompts.  It is also usable as a Python API via ``AgentSession``.

Example::

    python -m novel_harness.agent_wrapper --root my-novel init \
      '{"title":"The Gearworks","premise":"A dismissed apprentice..."}'
    python -m novel_harness.agent_wrapper --root my-novel snapshot '{}'

Progress from model calls is sent to stderr; stdout always contains one JSON
response.  Plans are saved as proposal files and must be committed explicitly.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from typing import Any, Callable

from .depgraph import build_dependency_graph, check_dependencies
from .llm import DEFAULT_MODEL, LLMClient
from .models import Character, Chapter, Location, PlotThread, Promise
from .pipeline import (
    apply_genre, audit_manuscript, continue_and_extract, generate_chapter, generate_character_sheet,
    generate_title, plan_book, plan_outline,
)
from .storage import Project


def _progress(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", file=sys.stderr, flush=True)


def _merge_into(existing, cls, data: dict):
    """Create a new `cls` instance from `data` if `existing` is None, or merge `data`'s fields
    into `existing` in place otherwise. This is what makes update_bible/update_chapter a true
    partial update: passing just {"name": "Mira Voss", "voice_notes": "..."} for an existing
    character updates only voice_notes, instead of constructing a fresh Character from that one
    field and silently resetting description/status/arc_notes/voice_id to their dataclass
    defaults (the bug this replaced)."""
    if existing is None:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
    for k, v in data.items():
        if k in cls.__dataclass_fields__:
            setattr(existing, k, v)
    return existing


class AgentSession:
    """Typed, non-interactive operations over one novel project directory."""

    def __init__(self, root: str = ".", model: str = DEFAULT_MODEL, progress: Callable[[str], None] | None = None):
        self.project = Project(root)
        self.model = model
        # Defaults to the stderr printer (CLI/agent-wrapper behavior, unchanged) -- a caller that
        # wants progress lines somewhere else (e.g. the web API's job engine, see
        # webapi/jobs.py) passes its own callback here instead.
        self.progress = progress or _progress

    def _state(self):
        return self.project.load_state()

    def init(self, title: str, premise: str, style_guide: str = "") -> dict:
        if self.project.exists():
            raise ValueError(f"A project already exists at {self.project.root}")
        Project.init(self.project.root, title, premise, style_guide)
        return self.snapshot()

    def snapshot(self) -> dict:
        state = self._state()
        chapters = self.project.load_outline()
        flags = self.project.load_continuity()
        return {
            "state": state.to_json(),
            "outline": [asdict(c) for c in chapters],
            "continuity": [asdict(f) for f in flags],
            "proposals": {
                "outline": self.project.load_outline_proposal(),
                "book_plan": self.project.load_book_plan_proposal(),
            },
        }

    def update_bible(self, data: dict) -> dict:
        state = self._state()
        if "style_guide" in data: state.style_guide = data["style_guide"]
        if "target_chapters" in data: state.target_chapters = int(data["target_chapters"])
        if "tags" in data: state.tags = list(data["tags"])
        if "genre" in data:
            apply_genre(state, data["genre"], client=LLMClient(model=self.model))
        for value in data.get("characters", []):
            name = value.get("name")
            if not name:
                raise ValueError(f"character update requires 'name': {value!r}")
            state.characters[name] = _merge_into(state.characters.get(name), Character, value)
        for value in data.get("locations", []):
            name = value.get("name")
            if not name:
                raise ValueError(f"location update requires 'name': {value!r}")
            state.locations[name] = _merge_into(state.locations.get(name), Location, value)
        for value in data.get("plot_threads", []):
            tid = value.get("id")
            if not tid:
                raise ValueError(f"plot_thread update requires 'id': {value!r}")
            state.plot_threads[tid] = _merge_into(state.plot_threads.get(tid), PlotThread, value)
        for value in data.get("promises", []):
            pid = value.get("id")
            if not pid:
                raise ValueError(f"promise update requires 'id': {value!r}")
            state.promises[pid] = _merge_into(state.promises.get(pid), Promise, value)
        self.project.save_state(state)
        return self.snapshot()

    def update_chapter(self, chapter_id: str, data: dict) -> dict:
        """Partial update of an existing chapter's title/pov/beats/word_target/structural_beat/
        status. `id` and `file` are ignored even if given -- identity and the on-disk manuscript
        path are not editable through this action (see `remove` for deleting a chapter outright)."""
        chapter = self.project.get_chapter(chapter_id)
        safe_data = {k: v for k, v in data.items() if k not in ("id", "file")}
        chapter = _merge_into(chapter, Chapter, safe_data)
        self.project.update_chapter(chapter)
        return {"chapter": asdict(chapter), "outline": [asdict(c) for c in self.project.load_outline()]}

    def remove(self, kind: str, id: str, force: bool = False) -> dict:
        removers = {
            "character": self.project.remove_character,
            "location": self.project.remove_location,
            "plot_thread": self.project.remove_thread,
            "promise": self.project.remove_promise,
        }
        if kind == "chapter":
            removed = self.project.remove_chapter(id, force=force)
        elif kind in removers:
            removed = removers[kind](id)
        else:
            raise ValueError(f"Unknown kind '{kind}' (expected character|location|plot_thread|promise|chapter)")
        return {"removed_kind": kind, "removed": asdict(removed), "snapshot": self.snapshot()}

    def rename(self, kind: str, old_id: str, new_id: str) -> dict:
        renamers = {
            "character": self.project.rename_character,
            "location": self.project.rename_location,
            "plot_thread": self.project.rename_thread,
            "promise": self.project.rename_promise,
        }
        if kind not in renamers:
            raise ValueError(f"Unknown kind '{kind}' (expected character|location|plot_thread|promise)")
        renamed = renamers[kind](old_id, new_id)
        return {"renamed_kind": kind, "renamed": asdict(renamed), "snapshot": self.snapshot()}

    def promise_resolve(self, promise_id: str, chapter_id: str) -> dict:
        promise = self.project.resolve_promise(promise_id, chapter_id)
        return {"promise": asdict(promise)}

    def continuity_resolve(self, index: int) -> dict:
        flag = self.project.resolve_continuity_flag(index)
        return {"flag": asdict(flag)}

    def voice_search(self, query: str = "", gender: str | None = None, age: str | None = None,
                      accent: str | None = None, language: str | None = None, limit: int = 20) -> dict:
        from . import narration  # deferred: keeps narration's heavier deps optional for text-only use
        results = narration.search_voices(query=query, gender=gender, age=age, accent=accent, language=language, limit=limit)
        return {"voices": results}

    def voice_assign(self, target: str, voice_id: str) -> dict:
        """Requires the optional narration deps (soundfile/librosa/torch/torchaudio/transformers --
        see requirements.txt); a missing dependency surfaces as a normal structured error through
        main()'s exception boundary, not a crash. Downloads+transcribes the reference clip now so
        `generate`'s `options.narrate` doesn't have to."""
        from . import narration
        voice = narration.get_voice(voice_id)
        if not voice:
            raise KeyError(f"No such voice id in the catalog: {voice_id!r} (use the voice_search action first)")
        narration.prepare_voice(voice_id, progress=self.progress)
        state = self._state()
        if target.lower() == "narrator":
            state.narrator_voice_id = voice_id
        else:
            if target not in state.characters:
                raise KeyError(f"No such character: {target!r}. Existing: {', '.join(state.characters) or '(none)'}")
            state.characters[target].voice_id = voice_id
        self.project.save_state(state)
        return {"target": target, "voice": {"id": voice_id, "name": voice.get("name", voice_id)}}

    def character_generate(self, name: str | None = None, age: str | None = None) -> dict:
        """One LLM call -> {name, description, status, voice_notes}, grounded in the project's own
        premise/genre/tags -- add the result to the bible with `update_bible` after reviewing it
        (this action never writes to state.json itself, matching `plan`'s review-then-commit
        separation)."""
        sheet = generate_character_sheet(LLMClient(model=self.model), self._state(), name, age)
        return {"character_sheet": sheet}

    def title_generate(self, tags: list[str] | None = None) -> dict:
        state = self._state()
        title = generate_title(LLMClient(model=self.model), state.premise, state.genre_id, tags if tags is not None else state.tags)
        return {"title": title}

    def add_chapters(self, chapters: list[dict]) -> dict:
        existing = self.project.load_outline()
        ids = {c.id for c in existing}
        added = []
        for value in chapters:
            chapter = Chapter(**{k: v for k, v in value.items() if k in Chapter.__dataclass_fields__})
            if chapter.id in ids:
                raise ValueError(f"Chapter id already exists: {chapter.id}")
            existing.append(chapter); ids.add(chapter.id); added.append(chapter)
        self.project.save_outline(existing)
        return {"added": [asdict(c) for c in added], "outline": [asdict(c) for c in existing]}

    def plan(self, count: int, whole_book: bool = False) -> dict:
        state, chapters = self._state(), self.project.load_outline()
        client = LLMClient(model=self.model)
        if whole_book:
            proposal = plan_book(client, state, chapters, count)
            self.project.save_book_plan_proposal(proposal)
            return {"proposal_type": "book_plan", "proposal": proposal, "commit_required": True}
        proposal = plan_outline(client, state, chapters, count=count)
        self.project.save_outline_proposal(proposal)
        return {"proposal_type": "outline", "proposal": proposal, "commit_required": True}

    def commit(self, whole_book: bool = False) -> dict:
        added = (self.project.commit_book_plan_proposal() if whole_book
                 else self.project.commit_outline_proposal())
        return {"committed": [asdict(c) for c in added]}

    def generate(self, chapter_id: str, options: dict | None = None) -> dict:
        options = options or {}
        chapter = generate_chapter(
            LLMClient(model=self.model), self.project, chapter_id,
            revise=options.get("revise", True), critique=options.get("critique", True),
            critique_rounds=int(options.get("critique_rounds", 2)),
            check=options.get("check", True), beat_check=options.get("beat_check", True),
            patch_missing=options.get("patch_missing", True), pov_check=options.get("pov_check", True),
            tension_check=options.get("tension_check", True), progress=self.progress,
        )
        result = {"chapter": asdict(chapter), "text": self.project.load_chapter_text(chapter_id)}
        if options.get("narrate"):
            from .pipeline import narrate_chapter
            state = self._state()
            result["narration"] = narrate_chapter(
                LLMClient(model=self.model), self.project, state, chapter, result["text"], progress=self.progress
            )
        return result

    def continue_chapter(self, chapter_id: str, edited_text: str | None = None) -> dict:
        """The fast, repeatable co-writing loop: appends one continuation segment (not a rewrite)
        and updates the bible from it -- 2 model calls, not generate's up to ~13. `edited_text`,
        if given, is saved as the chapter's current text first, so it's how a caller (the web UI's
        editor, or an agent doing paragraph-by-paragraph co-writing) hands over its own edits as
        settled canon before the model continues from them."""
        result = continue_and_extract(LLMClient(model=self.model), self.project, chapter_id, edited_text, progress=self.progress)
        result["chapter"] = asdict(result["chapter"])
        return result

    def audit(self) -> dict:
        flags = audit_manuscript(LLMClient(model=self.model), self.project, self.project.load_outline(), progress=self.progress)
        if flags: self.project.add_continuity_flags(flags)
        return {"flags": [asdict(f) for f in flags]}

    def dependency_graph(self) -> dict:
        """A computed structural view over the current bible+outline -- character/location/
        plot_thread/promise nodes tagged with the chapter id each was established at, plus
        chapter->entity edges for what each chapter's beats establish/reference. Usable any time,
        even before any chapter is drafted, to sanity-check an outline's structural shape before
        spending model calls on it."""
        nodes, edges = build_dependency_graph(self._state(), self.project.load_outline())
        return {"nodes": [asdict(n) for n in nodes], "edges": [asdict(e) for e in edges]}

    def dependency_check(self) -> dict:
        """Pure-Python, no model call: flags a chapter's beats referencing something established
        in a later chapter, a promise due at or before its own setup, or a thread resolved before
        it's opened. Runs automatically as part of `generate`; this is the standalone entry point
        for checking the whole current outline+bible on demand. Flags are appended to
        continuity_log.json (kind="dependency"), resolvable later via `continuity_resolve`."""
        flags = check_dependencies(self._state(), self.project.load_outline())
        if flags:
            self.project.add_continuity_flags(flags)
        return {"flags": [asdict(f) for f in flags]}


_ACTIONS = [
    "init", "snapshot", "update_bible", "add_chapters", "update_chapter", "remove", "rename",
    "promise_resolve", "continuity_resolve", "voice_search", "voice_assign", "character_generate",
    "title_generate", "dependency_graph", "dependency_check", "plan", "commit", "generate",
    "continue_chapter", "audit",
]


def _dispatch(session: AgentSession, action: str, payload: dict) -> dict:
    if action == "init": return session.init(payload["title"], payload["premise"], payload.get("style_guide", ""))
    if action == "snapshot": return session.snapshot()
    if action == "update_bible": return session.update_bible(payload)
    if action == "add_chapters": return session.add_chapters(payload["chapters"])
    if action == "update_chapter":
        payload = dict(payload)
        chapter_id = payload.pop("chapter_id")
        return session.update_chapter(chapter_id, payload)
    if action == "remove": return session.remove(payload["kind"], payload["id"], bool(payload.get("force", False)))
    if action == "rename": return session.rename(payload["kind"], payload["old_id"], payload["new_id"])
    if action == "promise_resolve": return session.promise_resolve(payload["id"], payload["chapter_id"])
    if action == "continuity_resolve": return session.continuity_resolve(int(payload["index"]))
    if action == "voice_search":
        return session.voice_search(
            query=payload.get("query", ""), gender=payload.get("gender"), age=payload.get("age"),
            accent=payload.get("accent"), language=payload.get("language"), limit=int(payload.get("limit", 20)),
        )
    if action == "voice_assign": return session.voice_assign(payload["target"], payload["voice_id"])
    if action == "character_generate": return session.character_generate(payload.get("name"), payload.get("age"))
    if action == "title_generate": return session.title_generate(payload.get("tags"))
    if action == "dependency_graph": return session.dependency_graph()
    if action == "dependency_check": return session.dependency_check()
    if action == "plan": return session.plan(int(payload.get("count", 3)), bool(payload.get("whole_book", False)))
    if action == "commit": return session.commit(bool(payload.get("whole_book", False)))
    if action == "generate": return session.generate(payload["chapter_id"], payload.get("options"))
    if action == "continue_chapter": return session.continue_chapter(payload["chapter_id"], payload.get("edited_text"))
    if action == "audit": return session.audit()
    raise ValueError(f"Unknown action '{action}'")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="novel-agent", description="JSON wrapper for novel_harness")
    parser.add_argument("--root", default=".")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("action", choices=_ACTIONS)
    parser.add_argument("payload", nargs="?", default="{}", help="One JSON object; use '-' to read it from stdin")
    args = parser.parse_args(argv)
    try:
        raw = sys.stdin.read() if args.payload == "-" else args.payload
        payload = json.loads(raw)
        if not isinstance(payload, dict): raise ValueError("payload must be a JSON object")
        print(json.dumps({"ok": True, "result": _dispatch(AgentSession(args.root, args.model), args.action, payload)}, indent=2))
    except Exception as error:  # wrapper boundary: agents need structured failures
        print(json.dumps({"ok": False, "error": {"type": type(error).__name__, "message": str(error)}}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
