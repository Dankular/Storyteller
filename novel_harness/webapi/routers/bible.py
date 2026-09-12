"""Story bible edits -- all thin wrappers around AgentSession's own partial-update/remove/rename/
resolve methods (agent_wrapper.py). No bible logic lives here."""
from __future__ import annotations

from fastapi import APIRouter, Body

from ..deps import as_http_error, get_session

router = APIRouter(prefix="/api/projects/{project_id}", tags=["bible"])


@router.patch("/bible")
def update_bible(project_id: str, payload: dict = Body(...)):
    """Partial update -- see AgentSession.update_bible's docstring in agent_wrapper.py. Each
    character/location entry is keyed by "name", each plot_thread/promise by "id"; only fields
    actually given change on an existing entry."""
    session = get_session(project_id)
    try:
        return session.update_bible(payload)
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.delete("/entities/{kind}/{entity_id}")
def remove_entity(project_id: str, kind: str, entity_id: str, force: bool = False):
    session = get_session(project_id)
    try:
        return session.remove(kind, entity_id, force)
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.post("/entities/{kind}/rename")
def rename_entity(project_id: str, kind: str, payload: dict = Body(...)):
    """{"old_id": "...", "new_id": "..."}"""
    session = get_session(project_id)
    try:
        return session.rename(kind, payload["old_id"], payload["new_id"])
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.post("/promises/{promise_id}/resolve")
def resolve_promise(project_id: str, promise_id: str, payload: dict = Body(...)):
    """{"chapter_id": "..."}"""
    session = get_session(project_id)
    try:
        return session.promise_resolve(promise_id, payload["chapter_id"])
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.post("/memories/{memory_id}/resolve")
def resolve_memory(project_id: str, memory_id: str):
    """Marks a memory resolved (a grudge forgiven, trust repaired) -- see
    AgentSession.memory_resolve. Unlike promise resolution this needs no chapter_id: a memory isn't
    tied to a specific payoff chapter the way a promise is."""
    session = get_session(project_id)
    try:
        return session.memory_resolve(memory_id)
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error
