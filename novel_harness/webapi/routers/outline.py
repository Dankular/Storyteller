"""Outline/chapter edits. Deleting a chapter reuses bible.py's generic
DELETE /entities/{kind}/{entity_id} endpoint (AgentSession.remove already handles kind="chapter"),
so there's no separate delete route here."""
from __future__ import annotations

from fastapi import APIRouter, Body

from ..deps import as_http_error, get_session

router = APIRouter(prefix="/api/projects/{project_id}", tags=["outline"])


@router.post("/chapters")
def add_chapters(project_id: str, payload: dict = Body(...)):
    """{"chapters": [{"id": "...", "title": "...", "beats": [...], ...}, ...]}. Each beat may be a
    plain string or {"text","requires","establishes"} -- see models.py's Chapter.beats docstring."""
    session = get_session(project_id)
    try:
        return session.add_chapters(payload["chapters"])
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.patch("/chapters/{chapter_id}")
def update_chapter(project_id: str, chapter_id: str, payload: dict = Body(...)):
    """Partial update: title/pov/beats/word_target/structural_beat/status. `id`/`file` are
    ignored even if given -- see AgentSession.update_chapter's docstring."""
    session = get_session(project_id)
    try:
        return session.update_chapter(chapter_id, payload)
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.get("/chapters/{chapter_id}/text")
def get_chapter_text(project_id: str, chapter_id: str):
    """The chapter's manuscript prose, if it's been drafted -- "" otherwise. Reads the file
    directly (Project.load_chapter_text, via AgentSession's public `.project` attribute) rather
    than going through an AgentSession action: there's no agent-facing equivalent for "just read
    the text" since an agent already gets it back from `generate`'s own response, or can read the
    manuscript file directly since it has filesystem access the browser doesn't."""
    session = get_session(project_id)
    try:
        return {"text": session.project.load_chapter_text(chapter_id)}
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error
