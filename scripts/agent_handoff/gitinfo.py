"""Fixed, read-only git queries used by the handoff tools.

Every call goes through ``_git`` with an argument list (never a shell) and a
subcommand allowlist. Values that come from a handoff record (branch, remote,
SHAs) must be validated by record.py before they reach this module.
"""

from __future__ import annotations

import os
import subprocess

READ_ONLY_SUBCOMMANDS = frozenset({
    "rev-parse", "symbolic-ref", "status", "ls-remote", "merge-base",
    "cat-file", "diff", "remote",
})
NETWORK_TIMEOUT = 60
LOCAL_TIMEOUT = 30


class GitError(RuntimeError):
    pass


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "GIT_TERMINAL_PROMPT": "0",
        # Keep `git status` from refreshing the index, so queries stay read-only.
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    })
    return env


def _git(repo: str, *args: str, ok_codes: tuple[int, ...] = (0,), timeout: int = LOCAL_TIMEOUT) -> subprocess.CompletedProcess:
    if not args or args[0] not in READ_ONLY_SUBCOMMANDS:
        raise GitError(f"refusing non-allowlisted git subcommand: {args[:1]}")
    try:
        result = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            env=_env(),
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitError(f"git {args[0]} failed: {exc.__class__.__name__}") from None
    if result.returncode not in ok_codes:
        message = result.stderr.strip().splitlines()[-1:] or ["no error output"]
        raise GitError(f"git {args[0]} exited {result.returncode}: {message[0]}")
    return result


def toplevel(repo: str) -> str:
    return _git(repo, "rev-parse", "--show-toplevel").stdout.strip()


def head_sha(repo: str) -> str:
    return _git(repo, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()


def current_branch(repo: str) -> str | None:
    result = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", ok_codes=(0, 1))
    return result.stdout.strip() or None


def porcelain_paths(repo: str) -> list[str]:
    """Paths with staged, unstaged or untracked changes (relative to the repo root)."""
    out = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    entries = out.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.append(path)
        if "R" in status or "C" in status:
            index += 1  # skip the rename/copy source path
    return sorted(set(paths))


def remotes(repo: str) -> list[str]:
    return _git(repo, "remote").stdout.split()


def remote_branch_sha(repo: str, remote: str, branch: str, offline: bool = False) -> str | None:
    """Tip of ``branch`` on ``remote``; None when the branch does not exist there.

    Online mode asks the remote itself (``git ls-remote``) and never updates local
    refs. Offline mode reads the local remote-tracking ref instead.
    """
    ref = f"refs/heads/{branch}"
    if offline:
        result = _git(repo, "rev-parse", "--verify", "--quiet",
                      f"refs/remotes/{remote}/{branch}^{{commit}}", ok_codes=(0, 1))
        return result.stdout.strip() or None
    # `remote` is a configured remote name that cannot start with "-" (see record.REMOTE_RE).
    out = _git(repo, "ls-remote", "--heads", remote, ref, timeout=NETWORK_TIMEOUT).stdout
    for line in out.splitlines():
        sha, _, name = line.partition("\t")
        if name == ref:
            return sha
    return None


def has_commit(repo: str, sha: str) -> bool:
    result = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}", ok_codes=(0, 1, 128))
    return result.returncode == 0


def is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    result = _git(repo, "merge-base", "--is-ancestor", ancestor, descendant, ok_codes=(0, 1))
    return result.returncode == 0


def changed_paths(repo: str, old: str, new: str) -> list[str]:
    out = _git(repo, "diff", "--name-only", "-z", "--no-renames", old, new).stdout
    return sorted(path for path in out.split("\0") if path)
