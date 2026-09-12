"""Tracks which novel_harness project directories the web UI's library page knows about.

Nothing in the core harness needs a registry -- every existing entry point (cli.py, agent_wrapper.py)
takes a --root/root path directly, per-invocation. The web UI's "open a novel" library page is the
first thing that needs to remember a *list* of projects across requests, so this is new. Stored at
~/.novel_harness/projects.json, reusing the exact cache-directory convention narration.py already
established (see narration.CACHE_DIR) rather than inventing a second location.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from typing import List, Optional

from ..storage import Project

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".novel_harness")
REGISTRY_PATH = os.path.join(CACHE_DIR, "projects.json")

# Where brand-new projects created from the browser (create_project, below) land, by default.
# Overridable via NOVEL_HARNESS_ROOT -- on a deployed instance (e.g. Render) this should point at
# a persistent disk's mount path, since the app's own container filesystem is ephemeral and a
# restart/redeploy would otherwise silently lose every novel created this way. Registering an
# EXISTING project by path (register_project) is unaffected -- it works with any path regardless
# of this setting.
NOVELS_ROOT = os.environ.get("NOVEL_HARNESS_ROOT", os.path.join(CACHE_DIR, "novels"))


@dataclass
class ProjectEntry:
    id: str
    name: str
    path: str


def _ensure_cache_dir() -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)


def list_projects() -> List[ProjectEntry]:
    if not os.path.isfile(REGISTRY_PATH):
        return []
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [ProjectEntry(**p) for p in data.get("projects", [])]


def _save(entries: List[ProjectEntry]) -> None:
    _ensure_cache_dir()
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump({"projects": [asdict(e) for e in entries]}, f, indent=2)


def register_project(path: str, name: Optional[str] = None) -> ProjectEntry:
    """Registers an EXISTING novel_harness project directory (must already have state.json --
    this does not create one; use the `new`/`init` actions for that). Re-registering the same
    path returns the existing entry unchanged rather than creating a duplicate."""
    abs_path = os.path.abspath(path)
    if not Project(abs_path).exists():
        raise FileNotFoundError(f"No novel_harness project found at {abs_path!r} (no state.json).")
    entries = list_projects()
    for e in entries:
        if os.path.abspath(e.path) == abs_path:
            return e
    if name is None:
        state = Project(abs_path).load_state()
        name = state.title or os.path.basename(abs_path)
    entry = ProjectEntry(id=uuid.uuid4().hex[:12], name=name, path=abs_path)
    entries.append(entry)
    _save(entries)
    return entry


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "untitled"
    return slug[:40]


def create_project(title: str, premise: str, style_guide: str = "") -> ProjectEntry:
    """Creates a brand-new project under NOVELS_ROOT and registers it -- the browser's equivalent
    of the CLI's `init` (bare-bones: title + premise, no genre/tags prompts; use update_bible for
    those afterward, same as the CLI's `init` + separate `genre-set`/`bible-set-tags` calls). This
    exists because, unlike a local CLI session, a deployed instance (e.g. on Render) has no shell
    to run `novel-harness init` in before the library can register anything."""
    from ..agent_wrapper import AgentSession  # deferred: avoids a circular import at module load

    base_slug = _slugify(title)
    slug, n = base_slug, 2
    while os.path.isdir(os.path.join(NOVELS_ROOT, slug)):
        slug, n = f"{base_slug}-{n}", n + 1
    path = os.path.join(NOVELS_ROOT, slug)
    os.makedirs(NOVELS_ROOT, exist_ok=True)
    AgentSession(root=path).init(title, premise, style_guide)
    return register_project(path, title)


def get_project(project_id: str) -> ProjectEntry:
    for e in list_projects():
        if e.id == project_id:
            return e
    raise KeyError(f"No registered project with id {project_id!r}.")


def remove_project(project_id: str) -> None:
    """Removes the project from the library -- never touches the project directory on disk."""
    entries = [e for e in list_projects() if e.id != project_id]
    _save(entries)
