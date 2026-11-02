from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))


def run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


class Repo:
    """A synthetic working clone with a bare `origin`, isolated from user git config."""

    def __init__(self, path: Path, origin: Path):
        self.path = path
        self.origin = origin

    def git(self, *args: str) -> str:
        return run_git(self.path, *args)

    def commit(self, name: str, content: str = "x\n", message: str | None = None) -> str:
        target = self.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self.git("add", name)
        self.git("commit", "-q", "-m", message or f"change {name}")
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def push(self, *extra: str) -> None:
        self.git("push", "-q", *extra, "origin", "HEAD")

    def clone(self, name: str, branch: str = "task/demo") -> "Repo":
        target = self.path.parent / name
        run_git(self.path.parent, "clone", "-q", "--branch", branch, str(self.origin), str(target))
        _configure(target)
        return Repo(target, self.origin)


def _configure(path: Path) -> None:
    run_git(path, "config", "user.name", "Handoff Test")
    run_git(path, "config", "user.email", "handoff-test@example.invalid")
    run_git(path, "config", "commit.gpgsign", "false")


@pytest.fixture(autouse=True)
def isolated_git(monkeypatch, tmp_path):
    # Never read or write the developer's real git configuration.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-global"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path) -> Repo:
    origin = tmp_path / "origin.git"
    run_git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    run_git(tmp_path, "init", "-q", "-b", "main", str(work))
    _configure(work)
    created = Repo(work, origin)
    created.git("remote", "add", "origin", str(origin))
    created.commit("README.md", "demo\n", "initial")
    created.git("switch", "-q", "-c", "task/demo")
    created.commit("app.py", "print('step 1')\n", "step 1")
    created.git("push", "-q", "-u", "origin", "task/demo")
    return created


def write_notes(path: Path, **notes) -> Path:
    path.write_text(json.dumps(notes), encoding="utf-8")
    return path
