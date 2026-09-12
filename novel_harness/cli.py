"""Command-line interface for the novel-writing harness."""
from __future__ import annotations
import argparse
import json
import time

from .storage import Project
from .models import Chapter, Character, Location, Memory, Motif, PlotThread, Promise, Relationship, beat_text, beat_requires, beat_establishes
from .depgraph import build_dependency_graph, check_dependencies, check_relationship_tensions
from .llm import LLMClient, DEFAULT_MODEL
from .pipeline import (
    generate_chapter, maybe_compress_summary, plan_outline, generate_voice_profile,
    generate_title, generate_character_sheet, plan_book, audit_manuscript, min_chapters_for_remaining_beats,
    apply_genre, propose_swerve, search_outline_continuations,
)


def cmd_init(args):
    Project.init(args.root, args.title, args.premise, args.style_guide or "")
    print(f"Initialized project at {args.root}")


def _yes_no(prompt: str, default: bool = True) -> bool:
    raw = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _generate_with_preview(generate_fn, label: str, preview_fn=None):
    """Call generate_fn() (no args), preview the result, and loop on 'r' to regenerate.
    Returns the accepted value, or None if declined -- the caller decides what "declined"
    means (skip the step, fall back to a manual prompt, etc). This is the one pattern behind
    title/character/chapter generation in `new`: generate first, ask second, never ask for
    something the model can draft from context already provided."""
    preview_fn = preview_fn or (lambda v: str(v))
    value = generate_fn()
    while True:
        print(f"\n--- {label} ---")
        print(preview_fn(value))
        choice = input("Use this? [Y/n/r=regenerate]: ").strip().lower()
        if choice in ("r", "regen", "regenerate"):
            value = generate_fn()
            continue
        return value if choice in ("", "y", "yes") else None


def cmd_new(args):
    """Guided project setup, start to finish: premise, genre, tags -> first character ->
    first chapter's beats -> generate it. Every step can be pre-filled with a flag (in which
    case that prompt is skipped) or skipped outright with --skip-character/--skip-chapter/
    --skip-generate -- pass everything and it runs fully non-interactively."""
    if Project(args.root).exists():
        raise SystemExit(f"A project already exists at {args.root} (state.json found). Use a different --root.")

    description = args.description
    if description is None:
        description = input("Describe your story -- premise, a sentence or two: ").strip()
    if not description:
        raise SystemExit("A premise/description is required.")

    genre_id = args.genre
    if genre_id is None:
        genre_id = input("Genre (type anything, or leave blank): ").strip() or None

    tags_raw = args.tags
    if tags_raw is None:
        tags_raw = input("Focus tags/themes to keep front-of-mind, comma-separated (optional): ").strip()
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

    title = args.title
    if not title:
        client = LLMClient(model=args.model)
        title = _generate_with_preview(lambda: generate_title(client, description, genre_id, tags), "Title")
        if not title:
            title = input("Title: ").strip() or "Untitled"

    project = Project.init(args.root, title, description, args.style_guide or "")
    state = project.load_state()
    state.tags = tags
    if genre_id:
        apply_genre(state, genre_id, client=LLMClient(model=args.model))
    project.save_state(state)

    print(f"\nProject created at {args.root}")
    print(f"Title: {title}")
    print(f"Genre: {state.genre_id or '(none)'}")
    print(f"Tags: {', '.join(tags) or '(none)'}")

    # --- first character: generated from context, not asked field-by-field ---
    # One direct prompt, not a yes/no gate followed by a name prompt -- typing a name IS
    # saying yes. Only an explicit "skip" declines.
    character_name = None
    if not args.skip_character:
        manual = args.character_description is not None and args.character_status is not None
        name = args.character_name
        skip_character = False
        if name is None and not manual:
            raw = input("\nCharacter name (blank to have one generated, 'skip' to skip): ").strip()
            if raw.lower() in ("skip", "no", "none", "n"):
                skip_character = True
            else:
                name = raw or None

        if not skip_character:
            if manual:
                name = name or input("Character name: ").strip()
                state = project.load_state()
                state.characters[name] = Character(
                    name=name, description=args.character_description, status=args.character_status,
                )
                project.save_state(state)
                character_name = name
                print(f"Added character: {name}")
            else:
                age = args.character_age
                if age is None:
                    age = input("Age (optional): ").strip() or None

                client = LLMClient(model=args.model)
                state = project.load_state()

                def _fmt_sheet(sheet: dict) -> str:
                    return (
                        f"Name:        {sheet.get('name') or name or 'Unnamed'}\n"
                        f"Description: {sheet.get('description', '')}\n"
                        f"Status:      {sheet.get('status', '')}\n"
                        f"Voice:       {sheet.get('voice_notes', '')}"
                    )

                sheet = _generate_with_preview(
                    lambda: generate_character_sheet(client, state, name, age), "Character sheet", _fmt_sheet
                )
                if sheet:
                    final_name = sheet.get("name") or name or "Unnamed"
                    state.characters[final_name] = Character(
                        name=final_name,
                        description=sheet.get("description", ""),
                        status=sheet.get("status", ""),
                        voice_notes=sheet.get("voice_notes", ""),
                    )
                    project.save_state(state)
                    character_name = final_name
                    print(f"Added character: {final_name}")
                else:
                    print("Skipped adding a character.")

    # --- first chapter: generated from context via the same outline-planning pass
    # `outline-plan` uses later, not asked for beats field-by-field ---
    chapter_id = None
    if not args.skip_chapter:
        want_chapter = args.chapter_beats is not None or _yes_no("\nOutline your first chapter now?")
        if want_chapter:
            manual = args.chapter_beats is not None
            if manual:
                chapter_id = "ch01"
                beats = [b.strip() for b in args.chapter_beats.split("|") if b.strip()]
                chapters = project.load_outline()
                chapters.append(Chapter(
                    id=chapter_id, title=args.chapter_title or "Chapter One",
                    pov=args.chapter_pov or character_name or "", beats=beats,
                    word_target=args.chapter_words or 2000,
                ))
                project.save_outline(chapters)
                print(f"Added chapter outline: {chapter_id}")
            else:
                client = LLMClient(model=args.model)
                state = project.load_state()

                def _fmt_chapter(proposal: list) -> str:
                    if not proposal:
                        return "(no proposal returned)"
                    c = proposal[0]
                    beats_str = "\n".join(f"  - {b}" for b in c.get("beats", []))
                    return (
                        f"Title: {c.get('title', '')}\n"
                        f"POV:   {c.get('pov') or character_name or '(unspecified)'}\n"
                        f"Beat:  {c.get('structural_beat') or '(none)'}\n"
                        f"Words: ~{c.get('word_target', 2000)}\n"
                        f"Beats:\n{beats_str}"
                    )

                proposal = _generate_with_preview(
                    lambda: plan_outline(client, state, [], count=1), "Chapter 1 outline", _fmt_chapter
                )
                if proposal:
                    c = proposal[0]
                    chapter_id = c.get("id") or "ch01"
                    chapters = project.load_outline()
                    chapters.append(Chapter(
                        id=chapter_id, title=c.get("title", "Chapter One"),
                        pov=c.get("pov") or character_name or "", beats=c.get("beats", []),
                        word_target=c.get("word_target", 2000), structural_beat=c.get("structural_beat") or None,
                    ))
                    project.save_outline(chapters)
                    print(f"Added chapter outline: {chapter_id}")
                else:
                    print("Skipped outlining a chapter.")

        # --- generate it ---
        if chapter_id and not args.skip_generate and (args.generate_now or _yes_no(
            f"Generate chapter '{chapter_id}' now? (calls the model -- can take several minutes)"
        )):
            client = LLMClient(model=args.model)

            def progress(msg: str) -> None:
                print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

            chapter = generate_chapter(client, project, chapter_id, progress=progress)
            print(f"\nChapter '{chapter.id}' -> {chapter.status}. Text at {chapter.file}")

    print("\nDone. Use `outline-add`/`outline-plan` + `generate` to continue the story.")


def cmd_bible_show(args):
    state = Project(args.root).load_state()
    print(f"Title: {state.title}\nPremise: {state.premise}\n")
    if state.genre_id:
        print(f"Genre: {state.genre_id} ({len(state.genre_beats)} structural beats)")
    if state.target_chapters:
        print(f"Target length: {state.target_chapters} chapters")
    if state.tags:
        print(f"Tags: {', '.join(state.tags)}")
    print("\nCharacters:")
    for c in state.characters.values():
        print(f"  - {c.name}: {c.description} [status: {c.status or 'unspecified'}]")
        if c.voice_notes:
            print(f"      voice: {c.voice_notes}")
    print("Locations:")
    for l in state.locations.values():
        print(f"  - {l.name}: {l.description}")
    print("Plot threads:")
    for t in state.plot_threads.values():
        print(f"  - [{t.status}] {t.id}: {t.description}")
    print("Promises:")
    for p in state.promises.values():
        due = f", due by {p.due_by}" if p.due_by else ""
        print(f"  - [{p.status}] {p.id}: {p.description} (planted in {p.planted_in or '?'}{due})")
    print("Motifs:")
    for m in state.motifs.values():
        first = f", first used in {m.first_used_in}" if m.first_used_in else ""
        print(f"  - {m.id}: \"{m.phrase}\"{first}")
        if m.notes:
            print(f"      {m.notes}")
    print("Memories:")
    for mem in state.memories.values():
        about = f" re: {mem.about}" if mem.about else ""
        print(f"  - [{mem.status}] {mem.id}: {mem.subject}{about} -- {mem.event}")
        print(f"      effect: {mem.effect}")
    print("Relationships:")
    for r in state.relationships.values():
        print(f"  - [{r.status}] {r.id}: {r.a} & {r.b} -- {r.kind} ({r.polarity})" + (f": {r.reason}" if r.reason else ""))
    if state.motif_candidates:
        print("Pending motif candidates (motif-candidate-promote / motif-candidate-dismiss):")
        for cand in state.motif_candidates:
            print(f"  - {cand.get('id')}: \"{cand.get('phrase')}\"" + (f" -- {cand['notes']}" if cand.get("notes") else ""))
    print("\nRunning summary:\n" + (state.running_summary or "(empty)"))


def cmd_bible_add_character(args):
    project = Project(args.root)
    state = project.load_state()
    state.characters[args.name] = Character(name=args.name, description=args.description, status=args.status or "")
    project.save_state(state)
    print(f"Added character: {args.name}")


def cmd_bible_add_location(args):
    project = Project(args.root)
    state = project.load_state()
    state.locations[args.name] = Location(name=args.name, description=args.description)
    project.save_state(state)
    print(f"Added location: {args.name}")


def cmd_bible_add_thread(args):
    project = Project(args.root)
    state = project.load_state()
    state.plot_threads[args.id] = PlotThread(id=args.id, description=args.description)
    project.save_state(state)
    print(f"Added plot thread: {args.id}")


def cmd_bible_add_motif(args):
    """A recurring phrase/image meant to gain weight each time it resurfaces -- distinct from a
    promise (a setup awaiting a specific payoff): a motif isn't resolved, it's reinforced. Pinned
    into every chapter's context from here on (context.py's _build_motifs_block)."""
    project = Project(args.root)
    state = project.load_state()
    state.motifs[args.id] = Motif(id=args.id, phrase=args.phrase, notes=args.notes or "")
    project.save_state(state)
    print(f"Added motif: {args.id}")


def cmd_motif_remove(args):
    Project(args.root).remove_motif(args.id)
    print(f"Removed motif: {args.id}")


def cmd_motif_rename(args):
    Project(args.root).rename_motif(args.old, args.new)
    print(f"Renamed motif '{args.old}' -> '{args.new}'.")


def cmd_bible_add_memory(args):
    """The Telltale-games mechanic ("X will remember that"): a specific past incident that keeps
    shaping how `subject` treats `about` going forward -- distinct from a promise (resolves once,
    to the reader) and from a character_update (overall status, not a per-relationship steer).
    Pinned into context whenever subject is relevant (context.py's _build_memories_block)."""
    project = Project(args.root)
    state = project.load_state()
    state.memories[args.id] = Memory(
        id=args.id, subject=args.subject, about=args.about or "", event=args.event,
        effect=args.effect, chapter_id=args.chapter,
    )
    project.save_state(state)
    print(f"Added memory: {args.id}")


def cmd_memory_remove(args):
    Project(args.root).remove_memory(args.id)
    print(f"Removed memory: {args.id}")


def cmd_memory_rename(args):
    Project(args.root).rename_memory(args.old, args.new)
    print(f"Renamed memory '{args.old}' -> '{args.new}'.")


def cmd_memory_resolve(args):
    Project(args.root).resolve_memory(args.id)
    print(f"Resolved memory: {args.id}")


def cmd_bible_add_relationship(args):
    """A standing relational fact between two entities -- "X knows Y", "Z and Y hate each other
    from a fight" -- pinned into context deterministically whenever both are relevant, and checked
    by check_relationship_tensions, instead of relying on the model to remember it unprompted."""
    project = Project(args.root)
    state = project.load_state()
    state.relationships[args.id] = Relationship(
        id=args.id, a=args.a, b=args.b, kind=args.kind, polarity=args.polarity,
        reason=args.reason or "", chapter_id=args.chapter,
    )
    project.save_state(state)
    print(f"Added relationship: {args.id}")


def cmd_relationship_remove(args):
    Project(args.root).remove_relationship(args.id)
    print(f"Removed relationship: {args.id}")


def cmd_relationship_rename(args):
    Project(args.root).rename_relationship(args.old, args.new)
    print(f"Renamed relationship '{args.old}' -> '{args.new}'.")


def cmd_relationship_resolve(args):
    Project(args.root).resolve_relationship(args.id)
    print(f"Resolved relationship: {args.id}")


def cmd_motif_candidates_show(args):
    state = Project(args.root).load_state()
    if not state.motif_candidates:
        print("No pending motif candidates.")
        return
    for cand in state.motif_candidates:
        print(f"- {cand.get('id')}: \"{cand.get('phrase')}\"" + (f" -- {cand['notes']}" if cand.get("notes") else ""))
        if cand.get("chapter_id"):
            print(f"    flagged in: {cand['chapter_id']}")


def cmd_motif_candidate_promote(args):
    motif = Project(args.root).promote_motif_candidate(args.id, args.motif_id, args.notes)
    print(f"Promoted candidate '{args.id}' to motif: {motif.id}")


def cmd_motif_candidate_dismiss(args):
    Project(args.root).dismiss_motif_candidate(args.id)
    print(f"Dismissed motif candidate: {args.id}")


def cmd_swerve_propose(args):
    """A story editor call brought in specifically to break a plan that's gotten too predictable --
    see pipeline.propose_swerve. Does NOT write to the bible; review the JSON and add it yourself
    with bible-add-thread (or update_bible, for an agent) if it's worth pursuing."""
    client = LLMClient(model=args.model)
    state = Project(args.root).load_state()
    result = propose_swerve(client, state)
    print(json.dumps(result, indent=2))


def cmd_outline_search(args):
    """Beam search over candidate outline continuations -- see pipeline.search_outline_continuations.
    Nothing is committed; prints each branch's chapters and score. To use one, pass its chapters
    JSON to a script calling AgentSession.outline_search_select, or hand-copy the winning branch's
    chapters into outline_proposal.json and run outline-commit-proposal."""
    client = LLMClient(model=args.model)
    project = Project(args.root)
    state = project.load_state()
    chapters = project.load_outline()
    branches = search_outline_continuations(
        client, state, chapters, depth=args.depth, branching=args.branching, beam_width=args.beam_width,
        use_llm_judgment=not args.no_llm_judgment, judge_weight=args.judge_weight,
        progress=lambda m: print(f"[{time.strftime('%H:%M:%S')}] {m}"),
    )
    for i, b in enumerate(branches):
        print(f"\n=== Branch {i + 1}: score {b.score:.1f} ===")
        for c in b.chapters:
            print(f"  - {c.get('id')}: {c.get('title')} (pov: {c.get('pov')})")
    print("\nFull JSON (copy a branch's \"chapters\" into outline_proposal.json + outline-commit-proposal to use it):")
    print(json.dumps([{"chapters": b.chapters, "score": b.score} for b in branches], indent=2))


def cmd_bible_set_style(args):
    project = Project(args.root)
    state = project.load_state()
    state.style_guide = args.text
    project.save_state(state)
    print("Style guide updated.")


def cmd_bible_set_target_length(args):
    project = Project(args.root)
    state = project.load_state()
    state.target_chapters = args.chapters
    project.save_state(state)
    print(f"Target length set to {args.chapters} chapters.")


def cmd_bible_set_tags(args):
    project = Project(args.root)
    state = project.load_state()
    state.tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    project.save_state(state)
    print(f"Tags set: {', '.join(state.tags) or '(none)'}")


def cmd_genre_set(args):
    """Genre is free text -- type whatever describes your story, with no fixed list to pick
    from. A small set of built-in structural beat-sheets (thriller/romance/mystery/horror/
    epic_fantasy/literary/three_act -- see genres.py) get cloned in for free if your exact
    wording happens to match one of their ids/names; anything else, the model synthesizes an
    equivalent bespoke beat-sheet for what you actually typed (see `pipeline.apply_genre`/
    `generate_genre_profile`), so every genre gets real structural guidance, not just those few."""
    project = Project(args.root)
    state = project.load_state()
    preset = apply_genre(state, args.id, client=LLMClient(model=args.model))
    project.save_state(state)
    if preset:
        print(f"Genre set to '{preset.name}'. {len(preset.beats)} beats cloned into state.json -- edit freely.")
        if preset.pacing_notes:
            print(f"Pacing notes: {preset.pacing_notes}")
    else:
        print(f"Genre set to '{args.id}'. The model generated a {len(state.genre_beats)}-beat structural "
              f"sheet for it, cloned into state.json just like a preset would be. Edit freely.")


def cmd_genre_show(args):
    state = Project(args.root).load_state()
    if not state.genre_id:
        print("No genre set. Use `genre-set <id>` with any genre text you like.")
        return
    print(f"Genre: {state.genre_id}")
    print("Beats:")
    for b in state.genre_beats:
        print(f"  [{b.get('position_pct', '?'):>5}%] {b['id']:>20} ({b.get('tension', '?')}) - {b['name']}: {b['guidance']}")
    print("Tropes to embrace: " + (", ".join(state.tropes_embrace) or "(none)"))
    print("Tropes to avoid: " + (", ".join(state.tropes_avoid) or "(none)"))
    print(f"Chapter-ending rule: {state.chapter_hook_rule or '(none)'}")


def cmd_voice_set(args):
    project = Project(args.root)
    state = project.load_state()
    if args.name not in state.characters:
        raise SystemExit(f"No such character: {args.name} (add with bible-add-character first)")
    state.characters[args.name].voice_notes = args.notes
    project.save_state(state)
    print(f"Voice profile set for {args.name}.")


def cmd_voice_generate(args):
    project = Project(args.root)
    state = project.load_state()
    chapters = project.load_outline()
    if args.name not in state.characters:
        raise SystemExit(f"No such character: {args.name} (add with bible-add-character first)")
    client = LLMClient(model=args.model)
    profile = generate_voice_profile(client, project, chapters, state.characters[args.name])
    state.characters[args.name].voice_notes = profile
    project.save_state(state)
    print(f"Voice profile for {args.name}:\n{profile}")


def cmd_voice_search(args):
    from . import narration
    results = narration.search_voices(
        query=args.query or "", gender=args.gender, age=args.age,
        accent=args.accent, language=args.language, limit=args.limit,
    )
    if not results:
        print("No matching voices.")
        return
    for v in results:
        print(f"{v['id']}  {v.get('name', '')}  [{v.get('gender','?')}/{v.get('age','?')}/{v.get('accent','?')}/{v.get('language','?')}]")
        if v.get("description"):
            print(f"    {v['description'][:140]}")


def cmd_voice_assign(args):
    """Assigns a catalog voice_id to a character (by name) or the narrator (--target narrator),
    downloading and auto-transcribing its reference clip now so `narrate` doesn't have to."""
    from . import narration
    project = Project(args.root)
    state = project.load_state()

    def progress(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    voice = narration.get_voice(args.voice_id)
    if not voice:
        raise SystemExit(f"No such voice id in the catalog: {args.voice_id} (use `voice-search` first)")
    narration.prepare_voice(args.voice_id, progress=progress)  # download + transcribe now, not at narrate-time

    if args.target.lower() == "narrator":
        state.narrator_voice_id = args.voice_id
        print(f"Narrator voice set to '{voice.get('name', args.voice_id)}'.")
    else:
        if args.target not in state.characters:
            raise SystemExit(f"No such character: {args.target} (add with bible-add-character first)")
        state.characters[args.target].voice_id = args.voice_id
        print(f"Voice for {args.target} set to '{voice.get('name', args.voice_id)}'.")
    project.save_state(state)


def cmd_narrate(args):
    from .pipeline import narrate_chapter
    project = Project(args.root)
    state = project.load_state()
    chapter = project.get_chapter(args.chapter_id)
    text = project.load_chapter_text(args.chapter_id)
    if not text:
        raise SystemExit(f"Chapter '{args.chapter_id}' has no manuscript text yet -- run `generate` first.")
    client = LLMClient(model=args.model)

    def progress(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    output_path = narrate_chapter(client, project, state, chapter, text, progress=None if args.quiet else progress)
    print(f"Narration written to {output_path}")


def cmd_promise_add(args):
    project = Project(args.root)
    state = project.load_state()
    state.promises[args.id] = Promise(
        id=args.id,
        description=args.description,
        planted_in=args.planted_in or None,
        due_by=args.due_by or None,
        origin="manual",
    )
    project.save_state(state)
    print(f"Added promise: {args.id}")


def cmd_promise_show(args):
    state = Project(args.root).load_state()
    if not state.promises:
        print("No promises tracked.")
        return
    for p in state.promises.values():
        due = f", due by {p.due_by}" if p.due_by else ""
        print(f"[{p.status:>10}] ({p.origin}) {p.id}: {p.description} -- planted in {p.planted_in or '?'}{due}"
              + (f", paid in {p.paid_in}" if p.paid_in else ""))


def cmd_promise_resolve(args):
    Project(args.root).resolve_promise(args.id, args.chapter_id)
    print(f"Promise '{args.id}' marked paid in {args.chapter_id}.")


def cmd_promise_remove(args):
    Project(args.root).remove_promise(args.id)
    print(f"Removed promise: {args.id}")


def cmd_promise_rename(args):
    Project(args.root).rename_promise(args.old, args.new)
    print(f"Renamed promise '{args.old}' -> '{args.new}'.")


def cmd_bible_remove_character(args):
    Project(args.root).remove_character(args.name)
    print(f"Removed character: {args.name}")


def cmd_bible_rename_character(args):
    Project(args.root).rename_character(args.old, args.new)
    print(f"Renamed character '{args.old}' -> '{args.new}' (updated matching chapter POVs too).")


def cmd_bible_remove_location(args):
    Project(args.root).remove_location(args.name)
    print(f"Removed location: {args.name}")


def cmd_bible_rename_location(args):
    Project(args.root).rename_location(args.old, args.new)
    print(f"Renamed location '{args.old}' -> '{args.new}'.")


def cmd_thread_remove(args):
    Project(args.root).remove_thread(args.id)
    print(f"Removed plot thread: {args.id}")


def cmd_thread_rename(args):
    Project(args.root).rename_thread(args.old, args.new)
    print(f"Renamed plot thread '{args.old}' -> '{args.new}'.")


def cmd_outline_remove(args):
    Project(args.root).remove_chapter(args.id, force=args.force)
    print(f"Removed chapter: {args.id}")


def cmd_continuity_resolve(args):
    flag = Project(args.root).resolve_continuity_flag(args.index)
    print(f"Resolved flag [{flag.kind}/{flag.severity}] {flag.chapter_id}: {flag.issue}")


def cmd_character_generate(args):
    project = Project(args.root)
    state = project.load_state()
    client = LLMClient(model=args.model)
    sheet = generate_character_sheet(client, state, args.name, args.age)
    print(json.dumps(sheet, indent=2))
    print("\nNot saved -- review it, then add with `bible-add-character` (or edit state.json directly).")


def cmd_title_generate(args):
    state = Project(args.root).load_state()
    tags = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else state.tags
    title = generate_title(LLMClient(model=args.model), state.premise, state.genre_id, tags)
    print(title)


def cmd_outline_add(args):
    project = Project(args.root)
    chapters = project.load_outline()
    beats = [b.strip() for b in args.beats.split("|") if b.strip()] if args.beats else []
    chapters.append(Chapter(
        id=args.id, title=args.title, pov=args.pov or "", beats=beats, word_target=args.words,
        structural_beat=args.structural_beat or None, frame_of=args.frame_of or None,
        ending_style=args.ending_style or None, mode=args.mode, direction=args.direction or "",
    ))
    project.save_outline(chapters)
    print(f"Added chapter outline: {args.id}")


def cmd_outline_show(args):
    chapters = Project(args.root).load_outline()
    for c in chapters:
        beat_tag = f", beat: {c.structural_beat}" if c.structural_beat else ""
        frame_tag = f", framed within: {c.frame_of}" if c.frame_of else ""
        ending_tag = f", ending: {c.ending_style}" if c.ending_style else ""
        mode_tag = f", mode: {c.mode}" if c.mode != "outline" else ""
        print(f"[{c.status:>8}] {c.id}: {c.title} (POV: {c.pov or '-'}, ~{c.word_target}w{beat_tag}{frame_tag}{ending_tag}{mode_tag})")
        if c.mode == "discovery":
            print(f"      direction: {c.direction or '(none given)'}")
        for b in c.beats:
            print(f"      - {beat_text(b)}")
            requires, establishes = beat_requires(b), beat_establishes(b)
            if requires:
                print(f"          requires: {', '.join(requires)}")
            if establishes:
                print(f"          establishes: {', '.join(establishes)}")


def cmd_outline_plan(args):
    project = Project(args.root)
    state = project.load_state()
    chapters = project.load_outline()
    client = LLMClient(model=args.model)
    proposal = plan_outline(client, state, chapters, count=args.count)
    project.save_outline_proposal(proposal)
    print(json.dumps(proposal, indent=2))
    print(f"\nProposal written to outline_proposal.json in {args.root}. Review/edit it, then run "
          f"`outline-commit-proposal` to append it to outline.json.")


def cmd_outline_commit_proposal(args):
    project = Project(args.root)
    added = project.commit_outline_proposal()
    if not added:
        print("Nothing to commit (empty or fully-duplicate proposal).")
        return
    print(f"Committed {len(added)} chapter(s): " + ", ".join(c.id for c in added))


def cmd_book_plan(args):
    project = Project(args.root)
    state = project.load_state()
    chapters = project.load_outline()
    count = args.chapters
    min_required = min_chapters_for_remaining_beats(state, chapters)
    if count < min_required:
        print(f"Requested {count} chapters, but {min_required} structural beat(s) remain unclaimed -- "
              f"bumping to {min_required} so every beat gets a home and plants have room to pay off.")
        count = min_required
    client = LLMClient(model=args.model)
    proposal = plan_book(client, state, chapters, count)
    project.save_book_plan_proposal(proposal)
    print(json.dumps(proposal, indent=2))
    print(f"\nProposal written to book_plan_proposal.json in {args.root}. Each chapter's 'plants' will "
          f"become both a beat and a tracked promise (due_by its payoff_chapter) on commit. Review/edit "
          f"it, then run `book-plan-commit`.")


def cmd_book_plan_commit(args):
    project = Project(args.root)
    added = project.commit_book_plan_proposal()
    if not added:
        print("Nothing to commit.")
        return
    state = project.load_state()
    planned = [p for p in state.promises.values() if p.origin == "planned"]
    print(f"Committed {len(added)} chapter(s): " + ", ".join(c.id for c in added))
    print(f"Registered {len(planned)} planned promise(s) with payoff chapters across the book.")


def cmd_manuscript_audit(args):
    project = Project(args.root)
    chapters = project.load_outline()
    client = LLMClient(model=args.model)

    def progress(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    flags = audit_manuscript(client, project, chapters, progress=None if args.quiet else progress)
    if not flags:
        written = sum(1 for c in chapters if c.status != "planned")
        if written < 2:
            print("Need at least 2 written chapters to cross-check.")
        else:
            print("No cross-chapter issues found.")
        return
    project.add_continuity_flags(flags)
    for f in flags:
        print(f"[{f.severity}] {f.chapter_id}: {f.issue}")


def cmd_dependency_check(args):
    """The 'Dependency Manager': pure Python, no model call. Flags a chapter's beats referencing
    something established later, a promise due at or before its own setup, or a thread resolved
    before it's opened -- runs automatically as part of `generate` too; this checks the whole
    current outline+bible on demand, including chapters that haven't been drafted yet."""
    project = Project(args.root)
    flags = check_dependencies(project.load_state(), project.load_outline())
    if not flags:
        print("No dependency issues found.")
        return
    project.add_continuity_flags(flags)
    for f in flags:
        print(f"[{f.severity}] {f.chapter_id}: {f.issue}")


def cmd_graph_show(args):
    project = Project(args.root)
    nodes, edges = build_dependency_graph(project.load_state(), project.load_outline())
    chapter_nodes = [n for n in nodes if n.kind == "chapter"]
    entity_nodes = [n for n in nodes if n.kind != "chapter"]
    print("Chapters (in outline order):")
    for n in chapter_nodes:
        print(f"  - {n.id}: {n.label}")
    print("\nEntities:")
    for n in entity_nodes:
        established = f"established in {n.established_at}" if n.established_at else "pre-established (available from the start)"
        print(f"  [{n.kind:>11}] {n.label} -- {established}")
    print("\nReferences (chapter -> entity mentioned in its beats):")
    ref_edges = [e for e in edges if e.kind == "references"]
    if not ref_edges:
        print("  (none)")
    for e in ref_edges:
        print(f"  {e.source} -> {e.target}")
    frame_edges = [e for e in edges if e.kind == "frames"]
    if frame_edges:
        print("\nFrame narratives (chapter -> the chapter it's narrated from within):")
        for e in frame_edges:
            print(f"  {e.source} -> {e.target}")


def cmd_generate(args):
    project = Project(args.root)
    client = LLMClient(model=args.model)

    def progress(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    chapter = generate_chapter(
        client, project, args.chapter_id,
        revise=not args.no_revise,
        critique=not args.no_critique,
        critique_rounds=args.critique_rounds,
        check=not args.no_check,
        beat_check=not args.no_beat_check,
        patch_missing=not args.no_patch_missing,
        pov_check=not args.no_pov_check,
        tension_check=not args.no_tension_check,
        progress=None if args.quiet else progress,
    )
    print(f"Chapter '{chapter.id}' -> {chapter.status}. Text at {chapter.file}")

    if args.narrate:
        from .pipeline import narrate_chapter
        state = project.load_state()
        text = project.load_chapter_text(chapter.id)
        output_path = narrate_chapter(client, project, state, chapter, text, progress=None if args.quiet else progress)
        print(f"Narration written to {output_path}")


def cmd_continuity_show(args):
    flags = list(enumerate(Project(args.root).load_continuity()))
    if args.kind != "all":
        flags = [(i, f) for i, f in flags if f.kind == args.kind]
    if not flags:
        print("No continuity flags recorded.")
        return
    for i, f in flags:
        mark = "x" if f.resolved else " "
        print(f"[{mark}] #{i} ({f.kind}/{f.severity}) {f.chapter_id}: {f.issue}")
    if args.kind != "all":
        print("\n(#index above is each flag's true position in the full log -- safe to pass to "
              "`continuity-resolve` even when filtered by --kind.)")


def cmd_status(args):
    project = Project(args.root)
    chapters = project.load_outline()
    counts = {}
    for c in chapters:
        counts[c.status] = counts.get(c.status, 0) + 1
    print(f"Chapters: {len(chapters)} total -> {counts}")
    flags = project.load_continuity()
    open_flags = [f for f in flags if not f.resolved]
    print(f"Open continuity/pov/promise flags: {len(open_flags)}")
    state = project.load_state()
    open_promises = [p for p in state.promises.values() if p.status in ("planted", "reinforced")]
    print(f"Open promises: {len(open_promises)}")


def build_parser():
    p = argparse.ArgumentParser(prog="novel-harness", description="A harness for long-form novel writing with LLMs.")
    p.add_argument("--root", default=".", help="Project directory (default: current directory)")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("init", help="Bare-bones project creation: title + premise, no prompts.")
    sp.add_argument("title")
    sp.add_argument("premise")
    sp.add_argument("--style-guide", default="")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("new", help="Guided setup, start to finish: premise/genre/tags -> character -> first chapter -> generate.")
    sp.add_argument("--description", "--premise", dest="description", default=None, help="Story premise/description; prompted if omitted")
    sp.add_argument("--genre", default=None, help="Genre, free text (e.g. 'cozy mystery'); prompted if omitted")
    sp.add_argument("--tags", default=None, help="Comma-separated focus tags/themes; prompted if omitted")
    sp.add_argument("--title", default=None, help="Title; if omitted, offers to generate one from the premise")
    sp.add_argument("--style-guide", default="")
    sp.add_argument("--model", default=DEFAULT_MODEL, help="Model to use for title/voice/chapter generation")
    sp.add_argument("--character-name", default=None, help="First character's name; the rest (description, status, voice) is generated from context")
    sp.add_argument("--character-age", default=None, help="First character's age (optional, feeds generation)")
    sp.add_argument("--character-description", default=None, help="Skip generation: full manual description (requires --character-status too)")
    sp.add_argument("--character-status", default=None, help="Skip generation: full manual status (requires --character-description too)")
    sp.add_argument("--skip-character", action="store_true", help="Don't add a character or prompt for one")
    sp.add_argument("--chapter-beats", default=None, help="Skip generation: pipe-separated beats for a fully manual first chapter")
    sp.add_argument("--chapter-title", default=None, help="Manual mode only (with --chapter-beats): chapter title")
    sp.add_argument("--chapter-pov", default=None, help="Manual mode only (with --chapter-beats): POV character")
    sp.add_argument("--chapter-words", type=int, default=None, help="Manual mode only (with --chapter-beats): target word count (default 2000)")
    sp.add_argument("--skip-chapter", action="store_true", help="Don't outline a first chapter or prompt for one")
    sp.add_argument("--generate-now", action="store_true", help="Generate the first chapter immediately, no prompt")
    sp.add_argument("--skip-generate", action="store_true", help="Never generate the first chapter, no prompt")
    sp.set_defaults(func=cmd_new)

    sp = sub.add_parser("bible-show")
    sp.set_defaults(func=cmd_bible_show)

    sp = sub.add_parser("bible-add-character")
    sp.add_argument("name")
    sp.add_argument("description")
    sp.add_argument("--status", default="")
    sp.set_defaults(func=cmd_bible_add_character)

    sp = sub.add_parser("bible-remove-character")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_bible_remove_character)

    sp = sub.add_parser("bible-rename-character", help="Renames the bible entry and matching chapter POVs; does not touch beat text or manuscript prose.")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_bible_rename_character)

    sp = sub.add_parser("character-generate", help="Generate a character sheet (name/description/status/voice_notes) grounded in the project's premise/genre/tags -- review, then add with bible-add-character.")
    sp.add_argument("name", nargs="?", default=None, help="Omit to have a name invented too")
    sp.add_argument("--age", default=None)
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.set_defaults(func=cmd_character_generate)

    sp = sub.add_parser("bible-add-location")
    sp.add_argument("name")
    sp.add_argument("description")
    sp.set_defaults(func=cmd_bible_add_location)

    sp = sub.add_parser("bible-remove-location")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_bible_remove_location)

    sp = sub.add_parser("bible-rename-location")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_bible_rename_location)

    sp = sub.add_parser("bible-add-thread")
    sp.add_argument("id")
    sp.add_argument("description")
    sp.set_defaults(func=cmd_bible_add_thread)

    sp = sub.add_parser("thread-remove")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_thread_remove)

    sp = sub.add_parser("thread-rename")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_thread_rename)

    sp = sub.add_parser("bible-add-motif", help="A recurring phrase/image meant to gain weight each time it resurfaces, pinned into every chapter's context from here on.")
    sp.add_argument("id")
    sp.add_argument("phrase", help="The recurring line/image itself, verbatim")
    sp.add_argument("--notes", default="", help="What it means / why it should recur")
    sp.set_defaults(func=cmd_bible_add_motif)

    sp = sub.add_parser("motif-remove")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_motif_remove)

    sp = sub.add_parser("motif-rename")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_motif_rename)

    sp = sub.add_parser("bible-add-memory", help="The Telltale-games mechanic ('X will remember that'): a specific past incident that keeps shaping how subject treats about going forward.")
    sp.add_argument("id")
    sp.add_argument("subject", help="The character whose future behavior is affected")
    sp.add_argument("about", help="Who/what it concerns -- usually another character's name; pass '' for general")
    sp.add_argument("event", help="What happened, briefly")
    sp.add_argument("effect", help="How subject should act differently toward about going forward")
    sp.add_argument("--chapter", default=None, help="Chapter id where this happened")
    sp.set_defaults(func=cmd_bible_add_memory)

    sp = sub.add_parser("memory-remove")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_memory_remove)

    sp = sub.add_parser("memory-rename")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_memory_rename)

    sp = sub.add_parser("memory-resolve", help="Marks a memory resolved (a grudge forgiven, trust repaired) -- stops it being pinned into context.")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_memory_resolve)

    sp = sub.add_parser("bible-add-relationship", help="A standing relational fact between two entities ('X knows Y', 'Z and Y hate each other from a fight') -- pinned into context deterministically whenever both are relevant.")
    sp.add_argument("id")
    sp.add_argument("a")
    sp.add_argument("b")
    sp.add_argument("kind", help="Free text: rivals, married, estranged siblings, acquainted, owes a debt to, ...")
    sp.add_argument("--polarity", default="neutral", choices=["positive", "negative", "neutral", "complicated"])
    sp.add_argument("--reason", default="", help="Why, briefly, e.g. 'a fight over the inheritance'")
    sp.add_argument("--chapter", default=None)
    sp.set_defaults(func=cmd_bible_add_relationship)

    sp = sub.add_parser("relationship-remove")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_relationship_remove)

    sp = sub.add_parser("relationship-rename")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_relationship_rename)

    sp = sub.add_parser("relationship-resolve", help="Marks a relationship resolved (a feud ended, a bond mended) -- stops it being pinned into context.")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_relationship_resolve)

    sp = sub.add_parser("motif-candidates-show", help="Lists motifs the model flagged during extraction as recurrence-worthy but not yet promoted.")
    sp.set_defaults(func=cmd_motif_candidates_show)

    sp = sub.add_parser("motif-candidate-promote", help="Turns a pending motif candidate into a real motif.")
    sp.add_argument("id", help="The candidate's id (see motif-candidates-show)")
    sp.add_argument("--motif-id", dest="motif_id", default=None, help="Defaults to the candidate's own id")
    sp.add_argument("--notes", default=None)
    sp.set_defaults(func=cmd_motif_candidate_promote)

    sp = sub.add_parser("motif-candidate-dismiss")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_motif_candidate_dismiss)

    sp = sub.add_parser("swerve-propose", help="Proposes ONE genuine narrative complication, built from harness-computed structural material (unresolved relationships, never-connected character pairs) when available. Does not write to the bible.")
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.set_defaults(func=cmd_swerve_propose)

    sp = sub.add_parser("outline-search", help="Beam search over candidate outline continuations, scored by a pure-Python structural check AND (by default) an LLM judgment call for narrative interest -- see pipeline.search_outline_continuations. Nothing is committed.")
    sp.add_argument("--depth", type=int, default=3, help="How many chapters ahead to search")
    sp.add_argument("--branching", type=int, default=3, help="Distinct next-chapter options explored per branch per step")
    sp.add_argument("--beam-width", type=int, default=3, dest="beam_width", help="How many top branches to keep at each step")
    sp.add_argument("--no-llm-judgment", action="store_true", dest="no_llm_judgment", help="Score branches by the pure-Python structural check only, skipping the LLM narrative-interest judgment call (roughly halves the call count)")
    sp.add_argument("--judge-weight", type=float, default=1.0, dest="judge_weight", help="Multiplier on the LLM judgment score's contribution to a branch's total reward")
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.set_defaults(func=cmd_outline_search)

    sp = sub.add_parser("bible-set-style")
    sp.add_argument("text")
    sp.set_defaults(func=cmd_bible_set_style)

    sp = sub.add_parser("bible-set-target-length")
    sp.add_argument("chapters", type=int)
    sp.set_defaults(func=cmd_bible_set_target_length)

    sp = sub.add_parser("bible-set-tags")
    sp.add_argument("tags", help="Comma-separated focus tags/themes, e.g. 'found family, slow-burn romance'")
    sp.set_defaults(func=cmd_bible_set_tags)

    sp = sub.add_parser("genre-set")
    sp.add_argument("id", help="Genre, free text -- any wording. Matches a built-in beat-sheet if it happens to line "
                                "up exactly; otherwise the model generates an equivalent one for whatever you typed.")
    sp.add_argument("--model", default=DEFAULT_MODEL, help="Model used only when no built-in preset matches")
    sp.set_defaults(func=cmd_genre_set)

    sp = sub.add_parser("genre-show")
    sp.set_defaults(func=cmd_genre_show)

    sp = sub.add_parser("title-generate", help="Generate a title from the project's premise/genre and given/stored tags.")
    sp.add_argument("--tags", default=None, help="Comma-separated tags to use instead of the project's stored tags")
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.set_defaults(func=cmd_title_generate)

    sp = sub.add_parser("voice-set")
    sp.add_argument("name")
    sp.add_argument("notes")
    sp.set_defaults(func=cmd_voice_set)

    sp = sub.add_parser("voice-generate")
    sp.add_argument("name")
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.set_defaults(func=cmd_voice_generate)

    sp = sub.add_parser("voice-search", help="Search the 11K-entry TTS voice catalog, for --narrate/voice-assign.")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--gender", default=None, choices=["male", "female", "neutral"])
    sp.add_argument("--age", default=None)
    sp.add_argument("--accent", default=None)
    sp.add_argument("--language", default=None, help="Two-letter code, e.g. en")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_voice_search)

    sp = sub.add_parser("voice-assign", help="Assign a catalog voice (see `voice-search`) to a character or the narrator, for --narrate.")
    sp.add_argument("target", help="A character name, or 'narrator'")
    sp.add_argument("voice_id")
    sp.set_defaults(func=cmd_voice_assign)

    sp = sub.add_parser("narrate", help="Synthesize a chapter's audio (requires transformers<5 -- see requirements.txt).")
    sp.add_argument("chapter_id")
    sp.add_argument("--model", default=DEFAULT_MODEL, help="LLM used for speaker attribution, not the TTS model")
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_narrate)

    sp = sub.add_parser("promise-add")
    sp.add_argument("id")
    sp.add_argument("description")
    sp.add_argument("--planted-in", default="")
    sp.add_argument("--due-by", default="", help="A chapter id or structural beat id")
    sp.set_defaults(func=cmd_promise_add)

    sp = sub.add_parser("promise-show")
    sp.set_defaults(func=cmd_promise_show)

    sp = sub.add_parser("promise-resolve")
    sp.add_argument("id")
    sp.add_argument("chapter_id")
    sp.set_defaults(func=cmd_promise_resolve)

    sp = sub.add_parser("promise-remove")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_promise_remove)

    sp = sub.add_parser("promise-rename")
    sp.add_argument("old")
    sp.add_argument("new")
    sp.set_defaults(func=cmd_promise_rename)

    sp = sub.add_parser("outline-add")
    sp.add_argument("id")
    sp.add_argument("title")
    sp.add_argument("--pov", default="")
    sp.add_argument("--words", type=int, default=2500)
    sp.add_argument("--beats", default="", help="Pipe-separated list of beats, e.g. 'She arrives|They argue|She leaves'")
    sp.add_argument("--structural-beat", default="", help="Genre beat id this chapter serves, see `genre-show`")
    sp.add_argument("--frame-of", default="", help="Another chapter id this one is narrated from within (e.g. a flashback framed by a present-day chapter)")
    sp.add_argument("--ending-style", default="", dest="ending_style", help="Free-text override of how THIS chapter ends, e.g. 'end quietly, let this one breathe' -- else falls back to the book's chapter_hook_rule")
    sp.add_argument("--mode", default="outline", choices=["outline", "discovery"], help="'discovery' drafts from a loose --direction instead of a beat checklist; beats get populated retroactively after drafting")
    sp.add_argument("--direction", default="", help="Used only with --mode discovery: a loose one-or-two sentence creative direction instead of beats")
    sp.set_defaults(func=cmd_outline_add)

    sp = sub.add_parser("outline-show")
    sp.set_defaults(func=cmd_outline_show)

    sp = sub.add_parser("outline-remove", help="Delete a chapter outright. Refuses once it's been drafted unless --force.")
    sp.add_argument("id")
    sp.add_argument("--force", action="store_true", help="Delete even if already drafted/revised/final, and remove its manuscript file")
    sp.set_defaults(func=cmd_outline_remove)

    sp = sub.add_parser("outline-plan")
    sp.add_argument("--count", type=int, default=3)
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.set_defaults(func=cmd_outline_plan)

    sp = sub.add_parser("outline-commit-proposal")
    sp.set_defaults(func=cmd_outline_commit_proposal)

    sp = sub.add_parser("book-plan", help="Plan the REST of the book in one pass, with explicit plant -> payoff-chapter mapping.")
    sp.add_argument("chapters", type=int, help="How many new chapters to plan")
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.set_defaults(func=cmd_book_plan)

    sp = sub.add_parser("book-plan-commit")
    sp.set_defaults(func=cmd_book_plan_commit)

    sp = sub.add_parser("manuscript-audit", help="Cross-check the ENTIRE written manuscript in one pass (run every several chapters, not per-chapter).")
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.add_argument("--quiet", action="store_true")
    sp.set_defaults(func=cmd_manuscript_audit)

    sp = sub.add_parser("dependency-check", help="Pure-Python structural check: a beat referencing something before it exists, a promise due before its own setup, a thread resolved before it's opened. No model call.")
    sp.set_defaults(func=cmd_dependency_check)

    sp = sub.add_parser("graph-show", help="Print the structural dependency graph: chapters, entities with when each was established, and which chapters reference which entities.")
    sp.set_defaults(func=cmd_graph_show)

    sp = sub.add_parser("generate")
    sp.add_argument("chapter_id")
    sp.add_argument("--model", default=DEFAULT_MODEL)
    sp.add_argument("--no-revise", action="store_true", help="Skip the self-revision pass (also skips critique)")
    sp.add_argument("--no-critique", action="store_true", help="Revise directly, skipping the structural-critique pass that guides it")
    sp.add_argument("--critique-rounds", type=int, default=2, metavar="N", help="Critique+revise cycles: round 1 always revises, round 2+ only if a fresh critique of the just-revised text still finds something (capped at 4). --critique-rounds 1 reproduces the old one-shot behavior. Ignored with --no-critique.")
    sp.add_argument("--no-check", action="store_true", help="Skip the continuity check")
    sp.add_argument("--no-beat-check", action="store_true", help="Skip the beat-coverage check")
    sp.add_argument("--no-patch-missing", action="store_true", help="Flag missing beats instead of auto-patching them in")
    sp.add_argument("--no-pov-check", action="store_true", help="Skip the POV/voice-consistency check")
    sp.add_argument("--no-tension-check", action="store_true", help="Skip the premature-resolution/tension check")
    sp.add_argument("--narrate", action="store_true", help="Also synthesize audio narration after generating (requires transformers<5, voices assigned)")
    sp.add_argument("--quiet", action="store_true", help="Suppress live stage-by-stage progress output")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser("continuity-show")
    sp.add_argument("--kind", default="all", choices=["all", "continuity", "pov", "promise", "beat_coverage", "tension", "dependency", "pipeline_error", "manuscript_audit"])
    sp.set_defaults(func=cmd_continuity_show)

    sp = sub.add_parser("continuity-resolve", help="Mark a flag resolved. <index> is its position in `continuity-show`'s (unfiltered) output, 0-based.")
    sp.add_argument("index", type=int)
    sp.set_defaults(func=cmd_continuity_resolve)

    sp = sub.add_parser("status")
    sp.set_defaults(func=cmd_status)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        raise SystemExit("\nInterrupted.")
    except (KeyError, ValueError, FileNotFoundError, RuntimeError) as error:
        # Expected, actionable failures (bad chapter/character id, missing project, malformed
        # model output, endpoint errors, ...) -- a clean one-line message, not a Python
        # traceback. Anything NOT in this list is a real bug and should still show its traceback.
        raise SystemExit(f"Error: {error}") from None


if __name__ == "__main__":
    main()
