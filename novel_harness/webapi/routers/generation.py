"""Long-running pipeline operations, wrapped as background jobs (jobs.py) so a multi-minute
`generate` call doesn't block the request. Each endpoint returns {"job_id": ...} immediately;
follow up with GET /api/jobs/{id} (poll) or WS /ws/jobs/{id} (live progress, see ws.py).

`commit` is the one exception -- it's a fast, deterministic file operation in AgentSession already
(no model call), so it stays a plain synchronous endpoint like bible.py/outline.py's."""
from __future__ import annotations

from fastapi import APIRouter, Body

from ..deps import as_http_error, get_session
from ..jobs import job_manager

router = APIRouter(prefix="/api/projects/{project_id}", tags=["generation"])


@router.post("/chapters/{chapter_id}/generate")
def generate_chapter(project_id: str, chapter_id: str, payload: dict | None = Body(default=None)):
    """{"options": {...}} -- same `options` shape as agent_wrapper's `generate` action (revise/
    critique/critique_rounds/check/beat_check/patch_missing/pov_check/tension_check/narrate)."""
    options = (payload or {}).get("options")

    def work(progress):
        session = get_session(project_id, progress=progress)
        return session.generate(chapter_id, options)

    job = job_manager.create("generate", project_id, work)
    return {"job_id": job.id}


@router.post("/chapters/{chapter_id}/continue")
def continue_chapter(project_id: str, chapter_id: str, payload: dict | None = Body(default=None)):
    """{"edited_text": "..."} -- the "Send" button: saves edited_text as the chapter's current
    text (capturing whatever the editor's textarea holds, including the user's own
    selection/delete/reword edits), appends one continuation segment, and updates the bible from
    the result. Cheap enough (2 model calls) to click repeatedly, unlike /generate's full pipeline."""
    edited_text = (payload or {}).get("edited_text")

    def work(progress):
        session = get_session(project_id, progress=progress)
        return session.continue_chapter(chapter_id, edited_text)

    job = job_manager.create("continue", project_id, work)
    return {"job_id": job.id}


@router.post("/plan")
def plan(project_id: str, payload: dict | None = Body(default=None)):
    """{"count": 3, "whole_book": false}"""
    payload = payload or {}
    count = int(payload.get("count", 3))
    whole_book = bool(payload.get("whole_book", False))

    def work(progress):
        # plan_outline/plan_book don't take a progress callback (each is one model call) --
        # `progress` is accepted here for a uniform `work` signature but unused.
        session = get_session(project_id)
        return session.plan(count, whole_book)

    job = job_manager.create("plan", project_id, work)
    return {"job_id": job.id}


@router.patch("/plan-proposal")
def update_plan_proposal(project_id: str, payload: dict = Body(...)):
    """Overwrites the saved (not yet committed) proposal file -- {"whole_book": bool, "proposal":
    [...]}. This is a browser-only need: an agent reviewing outline_proposal.json/
    book_plan_proposal.json already has filesystem access to hand-edit that file directly (see
    AGENTS.md's recommended loop); the browser doesn't, so the web UI needs this endpoint to let a
    person edit the proposal before commit the same way. Not part of AgentSession's action
    surface -- reaches Project's existing save_outline_proposal/save_book_plan_proposal directly."""
    session = get_session(project_id)
    try:
        if bool(payload.get("whole_book", False)):
            session.project.save_book_plan_proposal(payload["proposal"])
        else:
            session.project.save_outline_proposal(payload["proposal"])
        return session.snapshot()["proposals"]
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.post("/commit")
def commit(project_id: str, payload: dict | None = Body(default=None)):
    session = get_session(project_id)
    try:
        return session.commit(bool((payload or {}).get("whole_book", False)))
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.post("/audit")
def audit(project_id: str):
    def work(progress):
        session = get_session(project_id, progress=progress)
        return session.audit()

    job = job_manager.create("audit", project_id, work)
    return {"job_id": job.id}
