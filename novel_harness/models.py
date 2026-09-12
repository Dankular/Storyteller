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
            "genre_id": self.genre_id,
            "genre_beats": self.genre_beats,
            "tropes_embrace": self.tropes_embrace,
            "tropes_avoid": self.tropes_avoid,
            "chapter_hook_rule": self.chapter_hook_rule,
            "target_chapters": self.target_chapters,
            "tags": self.tags,
            "narrator_voice_id": self.narrator_voice_id,
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
            genre_id=d.get("genre_id"),
            genre_beats=d.get("genre_beats", []),
            tropes_embrace=d.get("tropes_embrace", []),
            tropes_avoid=d.get("tropes_avoid", []),
            chapter_hook_rule=d.get("chapter_hook_rule", ""),
            target_chapters=d.get("target_chapters", 0),
            tags=d.get("tags", []),
            narrator_voice_id=d.get("narrator_voice_id"),
        )
