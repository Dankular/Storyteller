"""The dependency graph, plus the one genuinely new query the plan called for: "which chapters
does this entity affect" -- not a new mechanism, just a filter over the same edges
AgentSession.dependency_graph() already returns. This is what the frontend's affected-chapters
regenerate picker is built on."""
from __future__ import annotations

from fastapi import APIRouter

from ...depgraph import _entity_node_id
from ..deps import as_http_error, get_session

router = APIRouter(prefix="/api/projects/{project_id}", tags=["dependency"])


@router.get("/dependency-graph")
def get_dependency_graph(project_id: str):
    session = get_session(project_id)
    try:
        return session.dependency_graph()
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.post("/dependency-check")
def run_dependency_check(project_id: str):
    session = get_session(project_id)
    try:
        return session.dependency_check()
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error


@router.get("/entities/{kind}/{entity_id}/affected-chapters")
def affected_chapters(project_id: str, kind: str, entity_id: str):
    """Every chapter whose beats reference this entity (authored `requires` or substring-inferred
    -- see depgraph.py), in outline order. Used before a bible edit is regenerated: the frontend
    shows this list with none pre-checked and lets the user pick which ones to actually
    regenerate, rather than cascading automatically."""
    session = get_session(project_id)
    try:
        graph = session.dependency_graph()
        chapters = session.project.load_outline()
    except Exception as error:  # noqa: BLE001
        raise as_http_error(error) from error

    target = _entity_node_id(kind, entity_id)
    referencing_ids = []
    for edge in graph["edges"]:
        if edge["kind"] == "references" and edge["target"] == target and edge["source"] not in referencing_ids:
            referencing_ids.append(edge["source"])

    by_id = {c.id: c for c in chapters}
    return {
        "entity": target,
        "affected_chapters": [
            {"id": cid, "title": by_id[cid].title, "status": by_id[cid].status}
            for cid in referencing_ids if cid in by_id
        ],
    }
