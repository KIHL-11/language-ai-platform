"""Read-only GitHub lookups through the `gh` CLI (GET requests only).

Used to check that claimed CI evidence is a real, successful run of the same
commit in the same repository, and to compare a tracker with its Issue. Every
request path is built here from validated parts; nothing is taken verbatim from
a handoff record.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Callable

RUN_URL_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})/actions/runs/(\d{1,20})(?:/job/\d{1,20})?/?$"
)
REMOTE_URL_RES = (
    re.compile(r"^https://github\.com/([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"),
    re.compile(r"^(?:ssh://)?git@github\.com[:/]([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100}?)(?:\.git)?/?$"),
)
TIMEOUT = 30

Fetcher = Callable[[str], Any]


class GitHubReadError(RuntimeError):
    pass


def gh_get(path: str) -> Any:
    """GET https://api.github.com/<path> with the user's existing gh login."""
    if not re.fullmatch(r"repos/[A-Za-z0-9-]+/[A-Za-z0-9._-]+/(?:actions/runs|issues)/\d+", path):
        raise GitHubReadError("refusing unexpected GitHub API path")
    env = dict(os.environ, GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1")
    try:
        result = subprocess.run(
            ["gh", "api", "--method", "GET", "-H", "Accept: application/vnd.github+json", path],
            capture_output=True, text=True, env=env, timeout=TIMEOUT, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitHubReadError(f"gh unavailable: {exc.__class__.__name__}") from None
    if result.returncode != 0:
        raise GitHubReadError("gh api request failed (not logged in, no access, or not found)")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise GitHubReadError("gh api returned non-JSON output") from None


def repo_from_remote_url(url: str) -> tuple[str, str] | None:
    for pattern in REMOTE_URL_RES:
        match = pattern.match(url.strip())
        if match:
            return match.group(1), match.group(2)
    return None


def parse_run_url(url: str) -> tuple[str, str, int] | None:
    match = RUN_URL_RE.match(url.strip())
    if not match:
        return None
    return match.group(1), match.group(2), int(match.group(3))


def check_run(url: str, sha: str, expected_repo: tuple[str, str] | None,
              fetch: Fetcher = gh_get) -> tuple[bool | None, str]:
    """Return (verified, explanation).

    True  - the run exists in the expected repository, finished successfully,
            and was built from exactly ``sha``.
    False - the run contradicts the claim (other commit, other repo, not success).
    None  - could not be checked (not an Actions run URL, gh unavailable, ...).
    """
    parsed = parse_run_url(url)
    if parsed is None:
        return None, "URL is not a GitHub Actions run URL"
    owner, name, run_id = parsed
    if expected_repo is None:
        return None, "origin is not a GitHub repository"
    if (owner.lower(), name.lower()) != (expected_repo[0].lower(), expected_repo[1].lower()):
        return False, f"run belongs to {owner}/{name}, not {expected_repo[0]}/{expected_repo[1]}"
    try:
        run = fetch(f"repos/{owner}/{name}/actions/runs/{run_id}")
    except GitHubReadError as exc:
        return None, str(exc)
    if not isinstance(run, dict):
        return None, "unexpected API response"
    full_name = str((run.get("repository") or {}).get("full_name", "")).lower()
    if full_name and full_name != f"{owner}/{name}".lower():
        return False, f"run reports repository {full_name}"
    if run.get("head_sha") != sha:
        return False, f"run built {str(run.get('head_sha'))[:12]}, not {sha[:12]}"
    if run.get("status") != "completed":
        return False, f"run status is {run.get('status')}"
    if run.get("conclusion") != "success":
        return False, f"run conclusion is {run.get('conclusion')}"
    workflow = str(run.get("name") or "unknown workflow")
    return True, f"run {run_id} ({workflow}) succeeded on {sha[:12]}"


def issue_state(owner: str, name: str, number: int, fetch: Fetcher = gh_get) -> dict[str, Any]:
    issue = fetch(f"repos/{owner}/{name}/issues/{number}")
    if not isinstance(issue, dict) or "state" not in issue:
        raise GitHubReadError("unexpected API response for issue")
    return {
        "state": issue["state"],
        "title": issue.get("title", ""),
        "updated_at": issue.get("updated_at", ""),
        "is_pull_request": "pull_request" in issue,
    }
