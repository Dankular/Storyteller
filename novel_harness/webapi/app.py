"""FastAPI application: wires together the project registry, the bible/outline/dependency/
continuity/generation routers (each a thin wrapper over agent_wrapper.AgentSession), the job
engine's WebSocket, and -- in production -- serves the built React app (web/dist) as static files.

In development, run this (uvicorn, CORS open to the Vite dev server) alongside `npm run dev` in
web/ rather than building the frontend each time.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import ws
from .jobs import job_manager
from .routers import bible, continuity, dependency, generation, jobs_router, outline, projects

# The repo root, three levels up from this file (novel_harness/webapi/app.py) -- used only to find
# web/dist for static serving. Matches this project's existing convention of paths anchored to a
# source checkout rather than an installed package location (see narration.py's BREEZE_CLI_PATH).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FRONTEND_DIST = os.path.join(REPO_ROOT, "web", "dist")

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # Vite's default dev server port


@asynccontextmanager
async def lifespan(app: FastAPI):
    job_manager.bind_loop(asyncio.get_running_loop())
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="novel_harness web API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware, allow_origins=DEV_ORIGINS, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    for router_module in (projects, bible, outline, dependency, continuity, generation, jobs_router):
        app.include_router(router_module.router)
    app.include_router(ws.router)

    if os.path.isdir(FRONTEND_DIST):
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
