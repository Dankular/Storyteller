"""Structural dependency graph over the story's own bible/outline data -- a computed view, not a
new source of truth. Nothing here is persisted to disk; call build_dependency_graph()/
check_dependencies() fresh whenever needed, the same way context.build_chapter_context() already
recomputes its relevance view on every call instead of caching one.

Nodes: one per chapter (in outline order), plus one per character/location/plot_thread/promise,
each tagged with the chapter id it was "established" at. This comes from two sources, merged
(earliest wins):
  - **Reactive** (today's original mechanism): Character.introduced_in / Location.introduced_in /
    PlotThread.opened_in / Promise.planted_in -- set by extraction after a chapter is actually
    drafted, or left None if the entity was added to the bible up front (pre-established, safe to
    reference anywhere -- see Character.introduced_in's docstring in models.py for the convention).
  - **Authored** (new): a beat can declare {"establishes": ["character:Torvin", ...]} (see
    models.beat_establishes) to register an entity's establishing chapter at PLANNING time, before
    it's ever drafted or even added to the bible -- so a later chapter's beats can `requires` it
    and get checked before either chapter has been written.

Edges: an "establishes" edge <established_at chapter> -> entity for every entity that has one, and
a "references" edge chapter -> entity for every chapter that either (a) mentions that entity via
substring match (context._mentioned's case-insensitive check, the same one context.py already uses
for bible relevance -- inherits its one known blind spot: a pronoun/epithet-only reference won't be
caught) or (b) authors {"requires": ["character:Torvin", ...]} on one of its beats (models.beat_requires)
-- an exact, not inferred, claim, which can point at an entity that doesn't exist anywhere in the
plan at all (see check_dependencies' 4th check below). Authored and inferred edges for the same
(chapter, entity) pair collapse into one.

check_dependencies() is the "Dependency Manager": a pure-Python, no-model-call validator (same
style as pipeline.audit_promise_staleness, which it complements -- that catches a promise going
stale/overdue, this catches the opposite failure mode, a beat or a schedule using something before
it exists) that flags:
  1. a chapter's beats referencing an entity established in a LATER chapter (a forward reference),
  2. a promise whose due_by chapter is at or before its own planted_in chapter,
  3. a plot thread marked resolved_in before its own opened_in chapter,
  4. an authored `requires` naming something that isn't established anywhere in the current plan at all.

Caveat (see arXiv:2605.05737, "ReFlect"): authored requires/establishes only helps to the extent
the model populates them correctly, and this project's default model is a 9B local fine-tune --
smaller than the scale at which that paper found models could reliably fill in requested structured
state. Treat authored edges as free extra precision when the model tags a beat correctly, not a
guarantee it always will; a beat that declares nothing still gets the same substring-based checking
as before, which is the actual fallback this degrades to.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .context import _mentioned
from .models import Chapter, ContinuityFlag, ProjectState, beat_establishes, beat_requires, beat_text


@dataclass
class GraphNode:
    id: str
    kind: str  # "chapter" | "character" | "location" | "plot_thread" | "promise"
    label: str
    established_at: Optional[str] = None  # a chapter id, or None = available from the start (chapter nodes leave this None too)


@dataclass
class GraphEdge:
    source: str  # always a chapter id -- both edge kinds originate from a chapter
    target: str  # an entity node id (may not exist in the node list -- see check_dependencies #4)
    kind: str    # "establishes" | "references"


def _entity_node_id(kind: str, raw_id: str) -> str:
    """Entity node ids are prefixed by kind (chapter ids are used as-is) so a character named the
    same as a plot-thread id, say, can't collide in the node namespace."""
    return f"{kind}:{raw_id}"


def _parse_entity_ref(ref: str) -> Optional[Tuple[str, str]]:
    """Parses an authored requires/establishes entry like "character:Torvin" into (kind, raw_id).
    Returns None for anything without a ":" separator -- silently ignored by the caller, not
    guessed at (no attempt to resolve a bare name against the bible)."""
    if not ref or ":" not in ref:
        return None
    kind, raw_id = ref.split(":", 1)
    return (kind, raw_id) if kind and raw_id else None


def build_dependency_graph(state: ProjectState, chapters: List[Chapter]) -> Tuple[List[GraphNode], List[GraphEdge]]:
    idx_by_id = {c.id: i for i, c in enumerate(chapters)}

    def _earlier(a: Optional[str], b: Optional[str]) -> Optional[str]:
        """None means pre-established/available from the start, which always wins (nothing is
        earlier than the beginning). Otherwise whichever chapter has the lower outline index wins."""
        if a is None or b is None:
            return None
        return a if idx_by_id.get(a, 0) <= idx_by_id.get(b, 0) else b

    nodes_by_id: Dict[str, GraphNode] = {c.id: GraphNode(id=c.id, kind="chapter", label=c.title) for c in chapters}

    # Seed from the bible (reactive: introduced_in/opened_in/planted_in).
    bible_entities = (
        [(name, "character", char.introduced_in, name) for name, char in state.characters.items()]
        + [(name, "location", loc.introduced_in, name) for name, loc in state.locations.items()]
        + [(t.id, "plot_thread", t.opened_in, t.description) for t in state.plot_threads.values()]
        + [(p.id, "promise", p.planted_in, p.description) for p in state.promises.values()]
    )
    for raw_id, kind, established_at, label in bible_entities:
        node_id = _entity_node_id(kind, raw_id)
        nodes_by_id[node_id] = GraphNode(id=node_id, kind=kind, label=label, established_at=established_at)

    edges: List[GraphEdge] = []
    edge_seen = set()

    def _add_edge(source: str, target: str, kind: str) -> None:
        key = (source, target, kind)
        if key not in edge_seen:
            edge_seen.add(key)
            edges.append(GraphEdge(source=source, target=target, kind=kind))

    # A "frames" edge for a chapter narrated from within another (models.Chapter.frame_of) --
    # target is another chapter node (already seeded above), not an entity.
    for chapter in chapters:
        if chapter.frame_of and chapter.frame_of in nodes_by_id:
            _add_edge(chapter.id, chapter.frame_of, "frames")

    # Layer in authored `establishes` claims, in outline order -- can register a brand-new node
    # (an entity that doesn't exist in the bible yet at all) or pull an existing one's
    # established_at earlier if the claim predates what was already known.
    for chapter in chapters:
        for beat in chapter.beats:
            for ref in beat_establishes(beat):
                parsed = _parse_entity_ref(ref)
                if parsed is None:
                    continue
                kind, raw_id = parsed
                node_id = _entity_node_id(kind, raw_id)
                if node_id in nodes_by_id:
                    nodes_by_id[node_id].established_at = _earlier(nodes_by_id[node_id].established_at, chapter.id)
                else:
                    nodes_by_id[node_id] = GraphNode(id=node_id, kind=kind, label=raw_id, established_at=chapter.id)
                _add_edge(chapter.id, node_id, "establishes")

    # Establishes edges from whatever established_at each entity ended up with (bible-only
    # entities not touched by an authored claim above still need their edge added here).
    for node in nodes_by_id.values():
        if node.kind != "chapter" and node.established_at:
            _add_edge(node.established_at, node.id, "establishes")

    # References: substring-inferred (against the full current node set, including anything an
    # authored `establishes` just registered) plus authored `requires` (exact, and allowed to
    # point at a target that isn't a node at all -- see check_dependencies #4).
    entity_nodes = [n for n in nodes_by_id.values() if n.kind != "chapter"]
    for chapter in chapters:
        beats_text = "\n".join(beat_text(b) for b in chapter.beats)
        if beats_text:
            for node in entity_nodes:
                if node.label and _mentioned([node.label], beats_text):
                    _add_edge(chapter.id, node.id, "references")
        for beat in chapter.beats:
            for ref in beat_requires(beat):
                parsed = _parse_entity_ref(ref)
                if parsed is None:
                    continue
                kind, raw_id = parsed
                _add_edge(chapter.id, _entity_node_id(kind, raw_id), "references")

    return list(nodes_by_id.values()), edges


def check_dependencies(state: ProjectState, chapters: List[Chapter]) -> List[ContinuityFlag]:
    idx_by_id = {c.id: i for i, c in enumerate(chapters)}
    nodes, edges = build_dependency_graph(state, chapters)
    node_by_id = {n.id: n for n in nodes}
    flags: List[ContinuityFlag] = []
    seen = set()  # (chapter, entity) pairs already flagged -- a chapter referencing the same
                  # entity across several beats collapses to one edge already (see _add_edge), but
                  # keying on the pair (not just the entity) still lets a DIFFERENT chapter that
                  # references the same offending entity get its own flag instead of being silently
                  # skipped once the first chapter has been reported.

    for edge in edges:
        if edge.kind != "references" or (edge.source, edge.target) in seen:
            continue
        entity = node_by_id.get(edge.target)
        if entity is None:
            # Only an authored `requires` can point at a target with no node at all -- a
            # substring-inferred edge only ever targets an entity that already exists as a node.
            seen.add((edge.source, edge.target))
            flags.append(ContinuityFlag(
                chapter_id=edge.source,
                issue=f"Beats require '{edge.target}', which isn't established anywhere in the current plan",
                severity="warning",
                kind="dependency",
            ))
            continue
        if not entity.established_at:
            continue
        if edge.source not in idx_by_id or entity.established_at not in idx_by_id:
            continue  # can't compare order against a chapter id that isn't in the current outline
        if idx_by_id[entity.established_at] > idx_by_id[edge.source]:
            seen.add((edge.source, edge.target))
            flags.append(ContinuityFlag(
                chapter_id=edge.source,
                issue=f"Beats reference {entity.kind} '{entity.label}' before it's established (in chapter {entity.established_at})",
                severity="warning",
                kind="dependency",
            ))

    for p in state.promises.values():
        if p.planted_in and p.due_by and p.due_by in idx_by_id and p.planted_in in idx_by_id:
            if idx_by_id[p.due_by] <= idx_by_id[p.planted_in]:
                flags.append(ContinuityFlag(
                    chapter_id=p.planted_in,
                    issue=f"Promise [{p.id}] is due by chapter {p.due_by}, at or before its own setup chapter {p.planted_in}",
                    severity="warning",
                    kind="dependency",
                ))

    for t in state.plot_threads.values():
        if t.opened_in and t.resolved_in and t.opened_in in idx_by_id and t.resolved_in in idx_by_id:
            if idx_by_id[t.resolved_in] < idx_by_id[t.opened_in]:
                flags.append(ContinuityFlag(
                    chapter_id=t.resolved_in,
                    issue=f"Plot thread [{t.id}] is marked resolved in chapter {t.resolved_in}, before it's opened in {t.opened_in}",
                    severity="warning",
                    kind="dependency",
                ))

    return flags


def check_relationship_tensions(state: ProjectState, chapters: List[Chapter]) -> List[ContinuityFlag]:
    """Pure-Python, no model call: for each chapter, flags a beat that mentions BOTH sides of a
    known, unresolved, negative-polarity Relationship (see models.py) -- "you're about to write
    these two into a scene together and they have unfinished business." This is deliberately NOT a
    contradiction detector: pure-Python substring matching cannot verify whether the drafted prose
    actually *honors* the friction (that would require reading for tone/subtext, which is context.py
    _build_relationships_block's job, feeding the model the fact directly) -- this only catches
    the cases where the fact was never even surfaced as relevant at plan time, same "note, not
    proof" spirit as the substring-match caveat documented in this module's docstring."""
    negative = [r for r in state.relationships.values() if r.status == "active" and r.polarity == "negative"]
    if not negative:
        return []
    flags: List[ContinuityFlag] = []
    for chapter in chapters:
        beats_text = "\n".join(beat_text(b) for b in chapter.beats) or chapter.direction
        if not beats_text:
            continue
        for r in negative:
            if _mentioned([r.a], beats_text) and _mentioned([r.b], beats_text):
                flags.append(ContinuityFlag(
                    chapter_id=chapter.id,
                    issue=(
                        f"Beats bring together {r.a} and {r.b}, who have an unresolved negative "
                        f"relationship ({r.kind}" + (f": {r.reason}" if r.reason else "") +
                        ") -- make sure the friction is accounted for, or resolve it explicitly."
                    ),
                    severity="note",
                    kind="relationship",
                ))
    return flags
