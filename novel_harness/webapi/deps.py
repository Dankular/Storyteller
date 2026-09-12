"""Shared request-handling helpers for the routers -- resolving a project_id (registry.py) into an
AgentSession (agent_wrapper.py), and turning the harness's own exception vocabulary (KeyError,
ValueError, FileNotFoundError, IndexError -- the same ones cli.py's main() already catches for a
clean CLI error message) into the matching HTTP status instead of a raw 500.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import HTTPException

from ..agent_wrapper import AgentSession
from ..llm import DEFAULT_MODEL
from . import registry


def get_session(project_id: str, progress: Optional[Callable[[str], None]] = None) -> AgentSession:
    try:
        entry = registry.get_project(project_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return AgentSession(root=entry.path, model=DEFAULT_MODEL, progress=progress)


def as_http_error(error: Exception) -> HTTPException:
    """Maps a harness exception to an HTTP status the same way cli.py's main() already maps them
    to a clean one-line CLI error -- a bad id/index is a 404 or 400, not a 500."""
    if isinstance(error, (KeyError, FileNotFoundError)):
        return HTTPException(status_code=404, detail=str(error).strip('"'))
    if isinstance(error, IndexError):
        return HTTPException(status_code=404, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=400, detail=str(error))
    return HTTPException(status_code=500, detail=f"{type(error).__name__}: {error}")
