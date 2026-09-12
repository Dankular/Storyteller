"""python -m novel_harness.webapi [--host 127.0.0.1] [--port 8000] [--reload]"""
from __future__ import annotations

import argparse

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="novel-harness-web", description="Run the novel_harness web API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Auto-reload on source changes (development)")
    args = parser.parse_args()
    uvicorn.run("novel_harness.webapi.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
