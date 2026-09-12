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

    def work(progress, on_chunk):
        # generate_chapter's own streamed stages (draft/revise) go through the throttled
        # `progress` summaries only, same as before -- see /continue below for the one endpoint
        # that actually wires up on_chunk, and its docstring for why generate doesn't too.
        session = get_session(project_id, progress=progress)
        return session.generate(chapter_id, options)

    job = job_manager.create("generate", project_id, work)
    return {"job_id": job.id}


@router.post("/chapters/{chapter_id}/continue")
def continue_chapter(project_id: str, chapter_id: str, payload: dict | None = Body(default=None)):
    """{"edited_text": "..."} -- the "Send" button: saves edited_text as the chapter's current
    text (capturing whatever the editor's textarea holds, including the user's own
    selection/delete/reword edits), appends one continuation segment, and updates the bible from
    the result. Cheap enough (2 model calls) to click repeatedly, unlike /generate's full pipeline.

    Unlike every other job here, this one's `on_chunk` is actually wired up: the appended segment
    IS the final text (no subsequent revise/critique pass can still rewrite it away), so streaming
    it live to the editor is never misleading -- generate_chapter's intermediate draft stream, by
    contrast, gets rewritten by the revise pass, so showing it live would show text the user won't
    actually end up with."""
    edited_text = (payload or {}).get("edited_text")

    def work(progress, on_chunk):
        session = get_session(project_id, progress=progress)
        return session.continue_chapter(chapter_id, edited_text, on_chunk=on_chunk)

    job = job_manager.create("continue", project_id, work)
    return {"job_id": job.id}


@router.post("/plan")
def plan(project_id: str, payload: dict | None = Body(default=None)):
    """{"count": 3, "whole_book": false}"""
    payload = payload or {}
    count = int(payload.get("count", 3))
    whole_book = bool(payload.get("whole_book", False))

    def work(progress, on_chunk):
        # plan_outline/plan_book don't take a progress/on_chunk callback (each is one model call
        # with no intermediate content worth streaming) -- both params are accepted here only for
        # the uniform `work` signature JobManager.create expects, and otherwise unused.
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
    def work(progress, on_chunk):
        session = get_session(project_id, progress=progress)
        return session.audit()

    job = job_manager.create("audit", project_id, work)
    return {"job_id": job.id}


@router.post("/swerve")
def swerve_propose(project_id: str):
    """One model call proposing a genuine narrative complication -- see
    AgentSession.swerve_propose/pipeline.propose_swerve. Does not write to the bible; the result is
    for review, then added via /bible (typically as a new plot_thread) if it's worth pursuing."""
    def work(progress, on_chunk):
        session = get_session(project_id, progress=progress)
        return session.swerve_propose()

    job = job_manager.create("swerve", project_id, work)
    return {"job_id": job.id}


@router.post("/outline-search")
def outline_search(project_id: str, payload: dict | None = Body(default=None)):
    """{"depth": 3, "branching": 3, "beam_width": 3} -- beam search over candidate outline
    continuations, scored by pure-Python bible checks. See AgentSession.outline_search/
    pipeline.search_outline_continuations. Nothing is committed."""
    payload = payload or {}

    def work(progress, on_chunk):
        session = get_session(project_id, progress=progress)
        return session.outline_search(
            depth=int(payload.get("depth", 3)), branching=int(payload.get("branching", 3)),
            beam_width=int(payload.get("beam_width", 3)),
        )

    job = job_manager.create("outline_search", project_id, work)
    return {"job_id": job.id}


@router.post("/outline-search/select")
def outline_search_select(project_id: str, payload: dict = Body(...)):
    """{"chapters": [...]} -- one branch's chapters from an outline_search job's result. Saves it
    as the pending outline proposal; the normal PATCH /plan-proposal review + POST /commit flow
    applies from there unchanged."""
    session = get_session(project_id)
    try:
        return session.outline_search_select(payload["chapters"])
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error
