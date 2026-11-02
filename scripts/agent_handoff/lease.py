"""Single-writer lease for one worktree.

The lease file lives in the worktree's private git directory
(``git rev-parse --absolute-git-dir``), so it is never committed and each
worktree has its own. It is created atomically; a held lease is never taken
over automatically.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import gitinfo
import record as rec

LEASE_FILE = "agent-handoff-lease.json"


class LeaseError(RuntimeError):
    pass


def lease_path(repo: str) -> Path:
    return Path(gitinfo.git_dir(repo)) / LEASE_FILE


def read(repo: str) -> dict[str, Any] | None:
    path = lease_path(repo)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return {"agent": "unreadable", "acquired_at": "", "branch": ""}
    if not isinstance(data, dict) or not isinstance(data.get("agent"), str):
        return {"agent": "unreadable", "acquired_at": "", "branch": ""}
    return data


def acquire(repo: str, agent: str, now: datetime | None = None) -> dict[str, Any]:
    if not rec.AGENT_RE.match(agent):
        raise LeaseError("agent must be a lowercase identifier such as codex, claude or gemini")
    data = {
        "agent": agent,
        "acquired_at": rec.format_timestamp(now or rec.utc_now()),
        "branch": gitinfo.current_branch(repo) or "",
    }
    path = lease_path(repo)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        holder = read(repo) or {}
        if holder.get("agent") == agent:
            return holder
        raise LeaseError(f"worktree is leased by '{holder.get('agent')}' since {holder.get('acquired_at')}")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return data


def release(repo: str, agent: str, force: bool = False) -> bool:
    """Release the lease. Returns False when there was none.

    ``force`` is for the human operator clearing a lease left by a crashed
    session; agents must not use it to take over another agent's worktree.
    """
    holder = read(repo)
    if holder is None:
        return False
    if holder.get("agent") != agent and not force:
        raise LeaseError(f"lease is held by '{holder.get('agent')}', not '{agent}'")
    lease_path(repo).unlink()
    return True
