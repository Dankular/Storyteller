"""Data models for the novel-writing harness."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, List, Dict, Optional, Union


@dataclass
class Character:
    name: str
    description: str = ""
    status: str = ""          # current state, updated as the story progresses
    arc_notes: str = ""
    voice_notes: str = ""     # diction, sentence rhythm, verbal tics, interiority style -- pinned whenever this character is POV
    voice_id: Optional[str] = None  # a voice-catalog id (see narration.py), assigned via `voice-assign` -- this character's TTS voice for --narrate
    introduced_in: Optional[str] = None  # a chapter id, set by extraction when organically discovered mid-story;
                                          # None means pre-established (added to the bible up front) -- see depgraph.py


@dataclass
class Location:
    name: str
    description: str = ""
    introduced_in: Optional[str] = None  # same convention as Character.introduced_in -- see depgraph.py


@dataclass
class PlotThread:
    id: str
    description: str
    opened_in: Optional[str] = None
    resolved_in: Optional[str] = None
    status: str = "open"      # open | resolved


@dataclass
class Promise:
    """A setup (Chekhov's gun) made to the reader, tracked until it pays off."""
    id: str
    description: str
    planted_in: Optional[str] = None
    due_by: Optional[str] = None   # a chapter id, or a structural beat id like "climax"
    status: str = "planted"        # planted | reinforced | paid | dropped
    origin: str = "manual"         # manual | auto | planned (pre-registered by a whole-book plan before the chapter is drafted)
    paid_in: Optional[str] = None


@dataclass
class Memory:
    """The Telltale-games mechanic ("Clementine will remember that"): a specific past incident
    between characters that keeps shaping how one treats the other, distinct from both a Promise
    (a setup that resolves once, to the reader) and a general Character.status update (the
    character's overall current situation, not a per-relationship consequence). `subject` is whose
    behavior is affected; `about` is who/what it concerns (another character, typically); `effect`
    is the behavioral instruction pinned into context whenever `subject` is relevant (context.py's
    _build_memories_block) -- not just a fact for the record, an active steer on how `subject`
    should act. Auto-proposed by extraction (extract_and_update_state's new_memories) the same way
    new_promises already is, or added by hand (bible-add-memory)."""
    id: str
    subject: str                      # the character whose future behavior is affected
    about: str                        # who/what it concerns -- usually another character name, may be blank
    event: str                        # what happened, briefly
    effect: str                       # how it should shape subject's behavior toward `about` going forward
    chapter_id: Optional[str] = None  # where this happened
    status: str = "active"            # active | resolved (a grudge forgiven, trust repaired, etc.)
    origin: str = "manual"            # manual | auto -- mirrors Promise.origin's convention


@dataclass
class Relationship:
    """A standing relational fact between two named bible entities (almost always two characters,
    but either side may be any entity name/id -- a character and a location works too, e.g. "banned
    from") -- exactly "X knows Y" / "Z and Y hate each other from a fight" / "this won't work
    because of X". Distinct from Memory (an event-derived, ONE-DIRECTIONAL behavioral consequence:
    "X remembers Y did Z, so acts differently toward Y") and from PlotThread (an ongoing STORY
    thread, not a relational fact): a Relationship is symmetric, standing background truth, not
    tied to a single triggering event necessarily. Deliberately kept off the model's plate -- rather
    than trusting the model to remember "X and Y hate each other" from chapters back, this is pinned
    into context by the harness itself, deterministically, every time both sides are relevant
    (context.py's _build_relationships_block), and checked by check_relationship_tensions()
    (depgraph.py) so a beat bringing two adversarial parties together gets flagged for the
    author/model to account for rather than silently drafted past. Also the raw, harness-computed
    material propose_swerve draws on (an unresolved negative relationship to reignite, or two
    entities with NO relationship at all yet to first collide) -- the model narrates a complication,
    the harness picks which structural opportunity is actually interesting."""
    id: str
    a: str
    b: str
    kind: str                    # free text label: "rivals", "married", "estranged siblings", "acquainted", ...
    polarity: str = "neutral"    # positive | negative | neutral | complicated
    reason: str = ""             # why, e.g. "a fight over the inheritance"
    chapter_id: Optional[str] = None
    status: str = "active"       # active | resolved (a feud ended, a bond mended)
    origin: str = "manual"       # manual | auto


@dataclass
class Chapter:
    id: str
    title: str
    pov: str = ""
    # Each beat is either a plain string (the common case -- checked against the bible only by
    # substring match, see depgraph.py) or a dict {"text": "...", "requires": [...], "establishes": [...]}
    # where requires/establishes are entity refs in the form "kind:raw_id" (kind is one of
    # character/location/plot_thread/promise, matching depgraph._entity_node_id exactly) -- an
    # *authored* dependency edge instead of an inferred one. beat_text()/beat_requires()/
    # beat_establishes() below read either shape uniformly; every consumer of chapter.beats should
    # go through them rather than assuming a bare string.
    beats: List[Union[str, Dict[str, Any]]] = field(default_factory=list)
    word_target: int = 2500
    status: str = "planned"   # planned | drafted | revised | final
    file: Optional[str] = None
    structural_beat: Optional[str] = None  # references an id in ProjectState.genre_beats
    frame_of: Optional[str] = None  # another chapter id this one is narrated FROM/WITHIN -- e.g. a
                                     # present-day frame chapter that a flashback chapter sits
                                     # inside (Barnaby-style: an old man remembering, a journalist
                                     # interviewing). Purely a structural/context-assembly hint --
                                     # see context.py's frame block and depgraph.py's "frames" edge.
                                     # None (the common case) means an ordinary, unframed chapter.
    ending_style: Optional[str] = None  # free-text override of ProjectState.chapter_hook_rule for
                                         # THIS chapter only, e.g. "end quietly, let this one
                                         # breathe" or "cut away mid-sentence" -- without this, every
                                         # chapter in the book ends on the same shape by construction
                                         # (see context.py's _build_structural_beat_block). None
                                         # (the common case) falls back to the global rule.
    mode: str = "outline"            # outline | discovery -- see `direction` below
    direction: str = ""              # used only when mode == "discovery": a loose one-or-two
                                      # sentence creative direction instead of a beat checklist --
                                      # the chapter is drafted to follow it where it naturally leads
                                      # rather than to hit a prescribed sequence, and `beats` is
                                      # populated RETROACTIVELY after drafting (pipeline.py's
                                      # extract_beats_retroactively) purely for downstream display/
                                      # dependency-graph consumers, not as a plan. `beats` is unused
                                      # (and should stay empty) for a discovery chapter before it's
                                      # drafted.


def beat_text(beat: Union[str, Dict[str, Any]]) -> str:
    """The beat's prose-guidance text, regardless of which of the two shapes above it is."""
    return beat if isinstance(beat, str) else beat.get("text", "")


def beat_requires(beat: Union[str, Dict[str, Any]]) -> List[str]:
    """Entity refs (e.g. "character:Torvin") this beat authors as a dependency -- empty for a
    plain-string beat, which only ever gets checked by depgraph.py's substring inference."""
    return [] if isinstance(beat, str) else list(beat.get("requires", []))


def beat_establishes(beat: Union[str, Dict[str, Any]]) -> List[str]:
    """Entity refs this beat authors as newly established -- empty for a plain-string beat."""
    return [] if isinstance(beat, str) else list(beat.get("establishes", []))


@dataclass
class Motif:
    """A recurring phrase or image meant to gain weight each time it resurfaces (the "Still us?" /
    "Always" refrain craft technique) -- distinct from a Promise (which is a setup awaiting a
    specific payoff) and from a PlotThread (an ongoing plot line): a motif isn't "resolved," it's
    *reinforced*, and its value comes from repetition with escalating stakes, not from a single
    payoff moment. Pinned into every chapter's context (context.py) from first_used_in onward so
    the model actually has the chance to bring it back rather than using it once and forgetting it."""
    id: str
    phrase: str                       # the recurring line/image itself, verbatim, e.g. "Still us? / Always."
    notes: str = ""                   # what it means / why it should recur, for the model and the author
    first_used_in: Optional[str] = None  # a chapter id, or None if planted before any chapter is drafted


@dataclass
class ContinuityFlag:
    chapter_id: str
    issue: str
    severity: str = "note"    # note | warning | contradiction
    resolved: bool = False
    kind: str = "continuity"  # continuity | pov | promise | beat_coverage | tension | dependency | manuscript_audit | pipeline_error


@dataclass
class ProjectState:
    title: str
    premise: str
    style_guide: str = ""
    running_summary: str = ""
    characters: Dict[str, Character] = field(default_factory=dict)
    locations: Dict[str, Location] = field(default_factory=dict)
    plot_threads: Dict[str, PlotThread] = field(default_factory=dict)
    promises: Dict[str, Promise] = field(default_factory=dict)
    motifs: Dict[str, Motif] = field(default_factory=dict)
    memories: Dict[str, Memory] = field(default_factory=dict)
    relationships: Dict[str, Relationship] = field(default_factory=dict)

    # Motifs the model itself flagged as recurrence-worthy during extraction (pipeline.py's
    # new_motif_candidates), NOT auto-committed into `motifs` -- unlike a promise or memory, a
    # motif is an editorial/creative choice about what's worth reinforcing, not a fact, so it waits
    # for a human/agent to review and either promote_motif_candidate or dismiss_motif_candidate
    # (storage.py). Each entry: {"id": "<slug>", "phrase": "...", "notes": "...", "chapter_id": "..."}.
    motif_candidates: List[dict] = field(default_factory=list)

    # Genre profile -- populated by `genre-set`, a copy of a preset (see genres.py)
    # that the project can then diverge from freely.
    genre_id: Optional[str] = None
    genre_beats: List[dict] = field(default_factory=list)
    tropes_embrace: List[str] = field(default_factory=list)
    tropes_avoid: List[str] = field(default_factory=list)
    chapter_hook_rule: str = ""

    target_chapters: int = 0  # 0 = unbounded; used to estimate a chapter's % position

    # Free-text focus words -- themes/tropes/vibe the author wants kept front-of-mind in every
    # chapter (e.g. "found family, slow-burn romance, morally gray protagonist"). Distinct from
    # genre_beats/tropes_embrace (which come from a genre preset): tags are story-specific, set
    # via `new` or `bible-set-tags`, independent of whether a genre is set at all.
    tags: List[str] = field(default_factory=list)

    narrator_voice_id: Optional[str] = None  # voice-catalog id used for non-dialogue narration lines in --narrate

    # How many chapters were "written" (status != planned) the last time pipeline.audit_manuscript
    # actually ran -- compared against the CURRENT written count (pipeline._report_audit_nudge) to
    # advise running `audit` again once enough chapters have piled up since. 0 means never audited.
    last_manuscript_audit_count: int = 0

    def to_json(self) -> dict:
        return {
            "title": self.title,
            "premise": self.premise,
            "style_guide": self.style_guide,
            "running_summary": self.running_summary,
            "characters": {k: asdict(v) for k, v in self.characters.items()},
            "locations": {k: asdict(v) for k, v in self.locations.items()},
            "plot_threads": {k: asdict(v) for k, v in self.plot_threads.items()},
            "promises": {k: asdict(v) for k, v in self.promises.items()},
            "motifs": {k: asdict(v) for k, v in self.motifs.items()},
            "memories": {k: asdict(v) for k, v in self.memories.items()},
            "relationships": {k: asdict(v) for k, v in self.relationships.items()},
            "motif_candidates": self.motif_candidates,
            "genre_id": self.genre_id,
            "genre_beats": self.genre_beats,
            "tropes_embrace": self.tropes_embrace,
            "tropes_avoid": self.tropes_avoid,
            "chapter_hook_rule": self.chapter_hook_rule,
            "target_chapters": self.target_chapters,
            "tags": self.tags,
            "narrator_voice_id": self.narrator_voice_id,
            "last_manuscript_audit_count": self.last_manuscript_audit_count,
        }

    @staticmethod
    def from_json(d: dict) -> "ProjectState":
        return ProjectState(
            title=d.get("title", ""),
            premise=d.get("premise", ""),
            style_guide=d.get("style_guide", ""),
            running_summary=d.get("running_summary", ""),
            characters={k: Character(**v) for k, v in d.get("characters", {}).items()},
            locations={k: Location(**v) for k, v in d.get("locations", {}).items()},
            plot_threads={k: PlotThread(**v) for k, v in d.get("plot_threads", {}).items()},
            promises={k: Promise(**v) for k, v in d.get("promises", {}).items()},
            motifs={k: Motif(**v) for k, v in d.get("motifs", {}).items()},
            memories={k: Memory(**v) for k, v in d.get("memories", {}).items()},
            relationships={k: Relationship(**v) for k, v in d.get("relationships", {}).items()},
            motif_candidates=d.get("motif_candidates", []),
            genre_id=d.get("genre_id"),
            genre_beats=d.get("genre_beats", []),
            tropes_embrace=d.get("tropes_embrace", []),
            tropes_avoid=d.get("tropes_avoid", []),
            chapter_hook_rule=d.get("chapter_hook_rule", ""),
            target_chapters=d.get("target_chapters", 0),
            tags=d.get("tags", []),
            narrator_voice_id=d.get("narrator_voice_id"),
            last_manuscript_audit_count=d.get("last_manuscript_audit_count", 0),
        )
