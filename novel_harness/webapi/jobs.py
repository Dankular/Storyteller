"""Background job engine for long-running novel_harness pipeline operations (generate_chapter,
plan_outline/plan_book, audit_manuscript, narrate_chapter, voice-assign's prepare_voice) invoked
from the web API.

The pipeline itself is fully synchronous (llm.py talks to the InferDex endpoint with blocking
urllib calls), so a job runs in a small bounded thread pool off the asyncio event loop -- nothing
about pipeline.py/depgraph.py/narration.py changes for this; they already accept an arbitrary
`Callable[[str], None]` progress callback (see pipeline.Progress), which is exactly the seam this
plugs into. Progress lines are bridged from the worker thread back onto the event loop via
`loop.call_soon_threadsafe` so WebSocket subscribers (see ws.py) see them arrive live -- the same
moment the CLI's stderr progress() closure would have printed them, not batched up at the end.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# Only two workers: every job ultimately calls the same local inference endpoint sequentially
# anyway (see llm.py) -- more concurrency here wouldn't parallelize actual model calls, it would
# just risk piling up requests against one box. Two is enough to let e.g. a quick audit finish
# without waiting behind a multi-minute chapter generation.
MAX_WORKERS = 2


@dataclass
class Job:
    id: str
    kind: str          # "generate" | "plan" | "audit" | "narrate" | "voice_assign"
    project_id: str
    status: str = "queued"   # queued | running | succeeded | failed
    progress: List[str] = field(default_factory=list)  # append-only log, mirrors what the CLI would print
    streamed_text: str = ""  # accumulated on_chunk output so far -- see _chunk() below; replayed
                              # on WebSocket connect exactly like `progress` already is, so a
                              # client that subscribes a moment after the job started streaming
                              # (a real, if usually brief, race -- job submission and the model's
                              # first token can both beat a slow WebSocket handshake) doesn't
                              # silently miss the opening of the text.
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "project_id": self.project_id, "status": self.status,
            "progress": self.progress, "result": self.result, "error": self.error,
            "created_at": self.created_at, "started_at": self.started_at, "finished_at": self.finished_at,
        }


class JobManager:
    """One instance shared by the whole app (see app.py's lifespan). `create()` is called from a
    request handler (on the event loop); the work itself runs in a worker thread."""

    def __init__(self, max_workers: int = MAX_WORKERS):
        self._jobs: Dict[str, Job] = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="novel-job")
        self._subscribers: Dict[str, List["asyncio.Queue[dict]"]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at app startup (see app.py) so worker threads can safely hop back onto the
        event loop to fan a progress line out to WebSocket subscribers -- asyncio.Queue isn't
        thread-safe to push into directly from another thread."""
        self._loop = loop

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list_for_project(self, project_id: str) -> List[Job]:
        return [j for j in self._jobs.values() if j.project_id == project_id]

    def create(self, kind: str, project_id: str, work: Callable[[Callable[[str], None], Callable[[str], None]], Any]) -> Job:
        """`work(progress, on_chunk) -> result` is called in a worker thread; it may call either
        callback any number of times before returning. `progress(line)` appends a throttled status
        line (shown in the job tray / GET /api/jobs/{id}'s log). `on_chunk(text)` broadcasts a raw
        content fragment immediately, for live character-by-character display -- most jobs
        (plan/audit/generate) have no use for it and just ignore the second parameter; only
        continue_chapter's work closure (routers/generation.py) actually wires it up. Returns the
        Job immediately (status "queued", soon "running") -- the caller responds to the HTTP
        request with job.id right away and the client follows up via GET /api/jobs/{id} or
        WS /ws/jobs/{id}."""
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, project_id=project_id)
        self._jobs[job.id] = job
        self._subscribers[job.id] = []

        def _progress(line: str) -> None:
            job.progress.append(line)
            self._broadcast(job.id, {"type": "progress", "line": line})

        def _chunk(text: str) -> None:
            # Unthrottled, unlike _progress -- this is the raw-content stream for live
            # character-by-character display (see pipeline.continue_chapter's on_chunk param).
            # Accumulated into job.streamed_text and replayed on connect (ws.py), same as
            # job.progress -- not exposed via to_dict()/GET polling though, since once the job is
            # done, the result's `text`/`appended` fields are the authoritative content and a
            # poller has no need for the intermediate stream.
            job.streamed_text += text
            self._broadcast(job.id, {"type": "chunk", "text": text})

        def _run() -> None:
            job.status = "running"
            job.started_at = time.time()
            self._broadcast(job.id, {"type": "status", "status": "running"})
            try:
                job.result = work(_progress, _chunk)
                job.status = "succeeded"
            except Exception as error:  # noqa: BLE001 -- worker-thread boundary: report, never crash the pool
                job.error = f"{type(error).__name__}: {error}"
                job.status = "failed"
            finally:
                job.finished_at = time.time()
                self._broadcast(job.id, {
                    "type": "done", "status": job.status, "result": job.result, "error": job.error,
                })

        self._executor.submit(_run)
        return job

    def subscribe(self, job_id: str) -> "asyncio.Queue[dict]":
        """Must be called from the event loop (a WebSocket handler)."""
        q: "asyncio.Queue[dict]" = asyncio.Queue()
        self._subscribers.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: "asyncio.Queue[dict]") -> None:
        subs = self._subscribers.get(job_id)
        if subs and q in subs:
            subs.remove(q)

    def _broadcast(self, job_id: str, message: dict) -> None:
        """Safe to call from a worker thread (that's the whole point) or the event loop."""
        if self._loop is None:
            return
        for q in list(self._subscribers.get(job_id, [])):
            self._loop.call_soon_threadsafe(q.put_nowait, message)


# One instance for the whole process -- app.py binds it to the event loop at startup; every
# router imports this same object rather than each managing its own pool.
job_manager = JobManager()
