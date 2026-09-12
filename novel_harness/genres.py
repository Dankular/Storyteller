"""Built-in genre presets: structural beat-sheets and prose conventions.

Each preset is a starting point, not a straitjacket. Applying one (`genre-set`)
*copies* its beats/tropes/hook-rule into the project's state.json -- the
project can then diverge freely, and later edits to the presets here won't
retroactively change a project that already applied one.

Beat positions are approximate percentages through the book (0-100), used to
suggest a `structural_beat` for a chapter given `target_chapters` and the
chapter's index. They're deliberately loose: a chapter's tagged beat always
wins over a computed guess.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, List


@dataclass
class BeatSpec:
    id: str
    name: str
    position_pct: float
    guidance: str
    tension: str = "rising"  # rising | plateau | spike | resolution

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GenrePreset:
    id: str
    name: str
    beats: List[BeatSpec]
    tropes_embrace: List[str] = field(default_factory=list)
    tropes_avoid: List[str] = field(default_factory=list)
    chapter_hook_rule: str = "End every chapter on a hook, reversal, or open question."
    pacing_notes: str = ""


GENRE_PRESETS: Dict[str, GenrePreset] = {
    "thriller": GenrePreset(
        id="thriller",
        name="Thriller",
        beats=[
            BeatSpec("inciting_danger", "Inciting Danger", 0, "Open on immediate danger or an unsettling mystery -- no slow windup.", "spike"),
            BeatSpec("drawn_in", "Drawn Into the Case", 14, "Protagonist is pulled past the point of easy retreat.", "rising"),
            BeatSpec("first_reveal", "First Major Reveal", 29, "A piece of information reframes the danger or the stakes.", "spike"),
            BeatSpec("act_break_escalation", "Escalation / Act Break", 43, "Things get worse in a way that closes off an easy option.", "spike"),
            BeatSpec("midpoint_twist", "Midpoint Twist", 50, "A game-changing twist; the old plan stops working.", "spike"),
            BeatSpec("false_resolution", "False Resolution", 57, "A moment that looks like victory but isn't.", "plateau"),
            BeatSpec("all_is_lost", "All Is Lost", 75, "Genuine collapse -- not a setback, a real loss.", "spike"),
            BeatSpec("climax", "Climax", 85, "The confrontation the whole book has been aiming at.", "spike"),
            BeatSpec("resolution", "Resolution", 95, "Consequences settle; loose threads close.", "resolution"),
        ],
        tropes_embrace=["ticking clock", "short chapters at high tension", "a reveal that recontextualizes earlier chapters"],
        tropes_avoid=["the villain monologuing away all tension", "coincidence resolving the climax"],
        chapter_hook_rule="End every chapter on a hook, a reversal, or a ticking-clock beat -- never on a settled note.",
        pacing_notes="Keep high-tension chapters under ~2,000 words; let quieter character beats breathe longer.",
    ),
    "romance": GenrePreset(
        id="romance",
        name="Romance",
        beats=[
            BeatSpec("meet_cute", "Meet-Cute / Opening Image", 0, "Establish both leads and the spark (or friction) between them.", "rising"),
            BeatSpec("mounting_attraction", "Mounting Attraction", 20, "Chemistry builds despite (or because of) obstacles.", "rising"),
            BeatSpec("fun_and_games", "Fun and Games", 35, "Deliver the promise of the premise: banter, tension, near-misses.", "plateau"),
            BeatSpec("midpoint_kiss", "Midpoint Declaration/Kiss", 50, "A real escalation -- a kiss, confession, or turning point in intimacy.", "spike"),
            BeatSpec("bad_guys_close_in", "Doubt Closes In", 65, "External and internal pressure both mount; old fears resurface.", "rising"),
            BeatSpec("black_moment", "Black Moment / Breakup", 75, "A genuine rupture -- the relationship truly falls apart.", "spike"),
            BeatSpec("grand_gesture", "Grand Gesture / Climax", 88, "One or both leads risk something real to reunite.", "spike"),
            BeatSpec("hea", "Resolution (HEA/HFN)", 97, "The earned happy (or hopeful) ending.", "resolution"),
        ],
        tropes_embrace=["banter that reveals character", "physical/emotional tension escalating in small increments"],
        tropes_avoid=["miscommunication that a five-second conversation would solve", "insta-love with no groundwork"],
        chapter_hook_rule="End chapters on an emotional beat -- a held breath, an unsaid word, a touch withdrawn.",
        pacing_notes="Alternating POV chapters between the two leads is common and often expected.",
    ),
    "mystery": GenrePreset(
        id="mystery",
        name="Mystery",
        beats=[
            BeatSpec("the_crime", "The Crime / Setup", 0, "Establish the crime or central question and its stakes.", "spike"),
            BeatSpec("investigation_begins", "Investigation Begins", 12, "Protagonist commits to solving it; first clues gathered.", "rising"),
            BeatSpec("first_false_lead", "First False Lead", 28, "A suspect or theory that turns out to be wrong -- but not wasted.", "plateau"),
            BeatSpec("midpoint_reveal", "Midpoint Reveal", 50, "New evidence upends the working theory.", "spike"),
            BeatSpec("suspects_narrow", "Suspects Narrow", 65, "The field closes; the real culprit's cover starts to strain.", "rising"),
            BeatSpec("dark_night", "Dark Night of the Soul", 75, "The detective's approach fails or endangers them personally.", "spike"),
            BeatSpec("reveal_climax", "Reveal / Climax", 88, "The solution, delivered with confrontation, not exposition alone.", "spike"),
            BeatSpec("denouement", "Denouement", 97, "Loose ends -- including red herrings -- are accounted for.", "resolution"),
        ],
        tropes_embrace=["clues that are fair-play (visible on a reread)", "a red herring that reveals character when it's debunked"],
        tropes_avoid=["the solution depending on information withheld from the reader", "the detective solving it off-page"],
        chapter_hook_rule="End investigation chapters on a new clue, contradiction, or a suspect's lie catching.",
        pacing_notes="Let the investigation produce a concrete clue, suspect, or dead end in nearly every chapter.",
    ),
    "horror": GenrePreset(
        id="horror",
        name="Horror",
        beats=[
            BeatSpec("normalcy", "Normalcy", 0, "Establish the ordinary world worth losing.", "plateau"),
            BeatSpec("first_disturbance", "First Disturbance", 10, "A wrongness enters -- small, dismissible, but not to the reader.", "rising"),
            BeatSpec("escalating_dread", "Escalating Dread", 30, "The wrongness recurs and can no longer be explained away.", "rising"),
            BeatSpec("point_of_no_return", "Point of No Return", 50, "A choice or event closes off retreat to normalcy.", "spike"),
            BeatSpec("isolation", "Isolation / Losses Mount", 65, "Allies, resources, or sanity are stripped away one by one.", "rising"),
            BeatSpec("confrontation_setup", "Confrontation Setup", 78, "The true nature of the threat is understood, too late to be safe.", "spike"),
            BeatSpec("climax", "Climax / Confrontation", 88, "Direct confrontation with the threat.", "spike"),
            BeatSpec("aftermath", "Aftermath", 97, "Ambiguous or resolved -- but the cost is visible.", "resolution"),
        ],
        tropes_embrace=["restraint before the reveal", "dread built from what's implied, not just shown", "small early details that recontextualize as horrifying"],
        tropes_avoid=["over-explaining the monster/threat", "a string of jump-scares with no rising dread beneath them"],
        chapter_hook_rule="End chapters on an image or implication that lingers -- avoid resolving the unease.",
        pacing_notes="Let quiet, sensory-detail chapters alternate with sharp spikes; don't spike every chapter.",
    ),
    "epic_fantasy": GenrePreset(
        id="epic_fantasy",
        name="Epic Fantasy",
        beats=[
            BeatSpec("ordinary_world", "Ordinary World", 0, "Establish the world's rules and the protagonist's place in it.", "plateau"),
            BeatSpec("call_to_adventure", "Call to Adventure", 8, "The inciting disruption to the ordinary world.", "rising"),
            BeatSpec("crossing_threshold", "Crossing the Threshold", 18, "Committing to the journey; no easy way back.", "rising"),
            BeatSpec("fun_and_games", "The World Delivers", 35, "Deliver on the premise -- magic, politics, war, whatever was promised.", "plateau"),
            BeatSpec("midpoint_reveal", "Midpoint Reveal", 50, "Stakes raised; a truth about the world or a character reframes the quest.", "spike"),
            BeatSpec("allies_betrayals", "Allies and Betrayals", 65, "The fellowship strains -- a betrayal, a loss, a hard choice.", "rising"),
            BeatSpec("all_is_lost", "All Is Lost", 78, "The cause looks doomed.", "spike"),
            BeatSpec("climax", "Climax / Battle", 90, "The confrontation the whole arc has built toward.", "spike"),
            BeatSpec("new_world", "New World", 98, "The world (and protagonist) settle into what they've become.", "resolution"),
        ],
        tropes_embrace=["a magic/political system with real costs and limits", "found family under pressure"],
        tropes_avoid=["power creep that trivializes earlier stakes", "prophecy that removes character agency"],
        chapter_hook_rule="End chapters on a decision, arrival, or revelation that changes the next chapter's plan.",
        pacing_notes="Longer chapters are fine for world/character work; tighten during the war/climax stretch.",
    ),
    "literary": GenrePreset(
        id="literary",
        name="Literary Fiction",
        beats=[
            BeatSpec("inciting_incident", "Inciting Incident", 0, "The disruption that sets interiority in motion -- can be small.", "rising"),
            BeatSpec("rising_interiority", "Rising Interiority", 30, "The protagonist's understanding of themselves/others deepens or frays.", "plateau"),
            BeatSpec("midpoint_turn", "Midpoint Turn", 50, "A realization or reversal that recontextualizes what came before.", "spike"),
            BeatSpec("crisis", "Crisis", 75, "The consequence of the protagonist's choices comes due.", "spike"),
            BeatSpec("climax", "Climax", 88, "Often quiet -- a decision or admission, not necessarily an event.", "spike"),
            BeatSpec("resolution", "Resolution", 97, "Ambiguity is allowed; earned stillness over tidy closure.", "resolution"),
        ],
        tropes_embrace=["interiority over plot mechanics", "theme carried by image and repetition, not statement"],
        tropes_avoid=["forcing genre-plot beats where the material doesn't want them", "resolving ambiguity the story earned"],
        chapter_hook_rule="End chapters on a resonant image or line, not necessarily a plot hook.",
        pacing_notes="Treat these beat positions as loose gravity, not a schedule -- voice and theme lead here.",
    ),
    "three_act": GenrePreset(
        id="three_act",
        name="Generic Three-Act",
        beats=[
            BeatSpec("setup", "Setup", 0, "Establish world, protagonist, and the status quo worth disrupting.", "plateau"),
            BeatSpec("inciting_incident", "Inciting Incident", 12, "The event that starts the story proper.", "rising"),
            BeatSpec("act1_break", "Act 1 Break", 25, "Protagonist commits to the central conflict.", "rising"),
            BeatSpec("midpoint", "Midpoint", 50, "A turn -- false victory or false defeat -- that raises the stakes.", "spike"),
            BeatSpec("act2_break", "Act 2 Break / All Is Lost", 75, "Rock bottom; a real loss, not a setback.", "spike"),
            BeatSpec("climax", "Climax", 90, "The central conflict resolves.", "spike"),
            BeatSpec("resolution", "Resolution", 98, "New status quo.", "resolution"),
        ],
        tropes_embrace=[],
        tropes_avoid=[],
        chapter_hook_rule="End every chapter on a hook or open question.",
        pacing_notes="",
    ),
}


def get_preset(genre_id: str) -> GenrePreset:
    try:
        return GENRE_PRESETS[genre_id]
    except KeyError:
        raise KeyError(
            f"No such genre preset: {genre_id!r}. Available: {', '.join(GENRE_PRESETS)}"
        ) from None


def match_preset(raw: str) -> "GenrePreset | None":
    """Genre is free text -- the author types whatever they want, no picking from a list.
    This is just a bonus lookup: if what they typed happens to match a built-in preset's id
    or name (case-insensitively), that preset's beat-sheet/tropes get cloned in too. If it
    doesn't match anything, the text is still used as-is -- it just won't carry a beat-sheet."""
    norm = raw.strip().lower()
    for preset in GENRE_PRESETS.values():
        if norm == preset.id.lower() or norm == preset.name.lower():
            return preset
    return None


def clone_preset_beats(preset: GenrePreset) -> List[dict]:
    """Deep-copy a preset's beats into plain dicts for storage in ProjectState."""
    return [b.to_dict() for b in preset.beats]
