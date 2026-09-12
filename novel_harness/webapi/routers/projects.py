"""The project library: list/register/open/remove novel_harness projects (registry.py), and the
one full-bible-plus-outline read (`snapshot`, straight from AgentSession.snapshot())."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Body, HTTPException

from .. import registry
from ..deps import as_http_error, get_session

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects():
    return {"projects": [asdict(e) for e in registry.list_projects()]}


@router.post("")
def add_project(payload: dict = Body(...)):
    """{"path": "...", "name": "<optional>"} -- the directory must already be an initialized
    novel_harness project (has state.json). Use POST /api/projects/new to create one instead."""
    path = payload.get("path")
    if not path:
        raise HTTPException(status_code=400, detail="'path' is required")
    try:
        entry = registry.register_project(path, payload.get("name"))
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return asdict(entry)


@router.post("/new")
def create_project(payload: dict = Body(...)):
    """{"title": "...", "premise": "...", "style_guide": "<optional>"} -- creates a brand-new
    project under registry.NOVELS_ROOT and registers it (see registry.create_project's docstring
    for why this exists as its own endpoint rather than requiring CLI access first)."""
    title, premise = payload.get("title"), payload.get("premise")
    if not title or not premise:
        raise HTTPException(status_code=400, detail="'title' and 'premise' are required")
    entry = registry.create_project(title, premise, payload.get("style_guide", ""))
    return asdict(entry)


@router.delete("/{project_id}")
def remove_project(project_id: str):
    """Removes the project from the library only -- never touches files on disk."""
    registry.remove_project(project_id)
    return {"removed": project_id}


@router.get("/{project_id}/snapshot")
def get_snapshot(project_id: str):
    session = get_session(project_id)
    try:
        return session.snapshot()
    except Exception as error:  # noqa: BLE001 -- boundary: map to a clean HTTP status
        raise as_http_error(error) from error
