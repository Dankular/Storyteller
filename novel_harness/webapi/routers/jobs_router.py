"""Polling/reconnect fallback for a job's status + full progress log. Live progress is
WS /ws/jobs/{id} (see ../ws.py) -- this is the same data, just pulled instead of pushed, for a
client that missed the WebSocket stream or wants a one-shot status check."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..jobs import job_manager

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(job_id: str):
    job = job_manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"No such job: {job_id!r}")
    return job.to_dict()
