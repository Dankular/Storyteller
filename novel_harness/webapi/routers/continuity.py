"""Continuity flags -- list (via snapshot's own continuity array) and resolve by index (its
position in that same array, exactly as continuity-show/continuity-resolve already document)."""
from __future__ import annotations

from fastapi import APIRouter

from ..deps import as_http_error, get_session

router = APIRouter(prefix="/api/projects/{project_id}", tags=["continuity"])


@router.get("/continuity")
def list_continuity(project_id: str):
    session = get_session(project_id)
    try:
        return {"continuity": session.snapshot()["continuity"]}
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.post("/continuity/{index}/resolve")
def resolve_continuity(project_id: str, index: int):
    session = get_session(project_id)
    try:
        return session.continuity_resolve(index)
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error
