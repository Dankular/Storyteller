"""Web API for novel_harness: a FastAPI backend wrapping agent_wrapper.AgentSession for a browser
UI (see web/). Run with `python -m novel_harness.webapi`.

This is a human-facing surface, not part of the agent contract in AGENTS.md -- agents keep using
agent_wrapper.py directly. Optional dependency group: fastapi/uvicorn/websockets (see
requirements.txt) -- nothing else in novel_harness imports this package, so the core harness and
CLI stay usable without them installed.
"""
