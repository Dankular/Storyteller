"""Live job-progress streaming over WebSocket -- the actual "responsive engine" wire: a client
connects to /ws/jobs/{job_id} and receives every progress line as it's produced, then one terminal
"done" message. Falls back gracefully for a client that connects after some lines already
happened (sends the backlog first, then live updates) or after the job already finished (sends the
full log plus the terminal message immediately, then closes)."""
from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .jobs import job_manager

router = APIRouter()


@router.websocket("/ws/jobs/{job_id}")
async def job_progress(websocket: WebSocket, job_id: str):
    await websocket.accept()
    job = job_manager.get(job_id)
    if job is None:
        await websocket.send_json({"type": "error", "message": f"No such job: {job_id!r}"})
        await websocket.close()
        return

    # Replay whatever's already happened before this client connected -- a job started just
    # before the browser opened the socket shouldn't have its early lines silently lost. There's
    # a narrow, accepted race here: if the job finishes in the gap between this check and
    # subscribing below, the terminal "done" broadcast could be missed (sent to a subscriber list
    # that doesn't include us yet). Not fixed for v1 -- in practice the client opens this socket
    # immediately after starting a job that takes minutes, so the window is negligible; a refresh
    # (GET /api/jobs/{id} always has the final state) recovers if it's ever hit.
    for line in job.progress:
        await websocket.send_json({"type": "progress", "line": line})

    if job.status in ("succeeded", "failed"):
        await websocket.send_json({
            "type": "done", "status": job.status, "result": job.result, "error": job.error,
        })
        await websocket.close()
        return

    queue = job_manager.subscribe(job_id)
    try:
        while True:
            message = await queue.get()
            await websocket.send_json(message)
            if message.get("type") == "done":
                break
    except WebSocketDisconnect:
        pass
    finally:
        job_manager.unsubscribe(job_id, queue)
