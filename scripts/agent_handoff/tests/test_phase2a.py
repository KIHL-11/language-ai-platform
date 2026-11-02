"""Phase 2A: end-to-end flow, forged evidence, concurrency and scan boundaries."""

from __future__ import annotations

import subprocess
import sys

import pytest

import ghread
import gitinfo
import handoff
import lease
import record as rec
from conftest import TOOL_DIR, run_git, write_notes
from test_handoff import codes, snapshot, verify, write_record

CLI = TOOL_DIR / "handoff.py"
GITHUB_ORIGIN = "https://github.com/acme/demo.git"
RUN_URL = "https://github.com/acme/demo/actions/runs/123"


def cli(repo_path, *args):
    return subprocess.run([sys.executable, "-I", str(CLI), "--repo", str(repo_path), *args],
                          capture_output=True, text=True)


@pytest.fixture
def github_origin(monkeypatch):
    """Pretend origin is a GitHub repository; the git transport stays local."""
    monkeypatch.setattr(gitinfo, "remote_url", lambda repo, remote: GITHUB_ORIGIN)


def fake_run(sha, **overrides):
    run = {"head_sha": sha, "status": "completed", "conclusion": "success",
           "name": "Agent handoff protocol", "repository": {"full_name": "acme/demo"}}
    run.update(overrides)
    calls = []

    def fetch(path):
        calls.append(path)
        return run
    fetch.calls = calls
    return fetch


def evidence(sha, url=None, result="43 passed"):
    return [{"command": "pytest -q", "environment": "hosted CI", "sha": sha, "result": result, "url": url}]


# --------------------------------------------------------------------------- full flow


def test_snapshot_commit_transfer_verify_with_cli(repo, tmp_path):
    """Agent A works in one clone, agent B continues in a separate clone."""
    assert cli(repo.path, "lease", "acquire", "--agent", "codex").returncode == 0
    repo.commit("app.py", "print('step 2')\n", "step 2")
    repo.push()
    notes = write_notes(tmp_path / "notes.json", task={"issue": 75, "title": "drill"},
                        completed=["step 2"], unfinished=["step 3"], next_steps=["write step 3"],
                        tests=evidence(repo.head()))
    snap = cli(repo.path, "snapshot", "--agent", "codex", "--notes", str(notes))
    assert snap.returncode == 0, snap.stderr
    record_rel = snap.stdout.split("Handoff written: ")[1].splitlines()[0]
    repo.git("add", record_rel)
    repo.git("commit", "-q", "-m", "handoff: codex")
    repo.push()
    assert cli(repo.path, "lease", "release", "--agent", "codex").returncode == 0

    receiver = repo.clone("claude-worktree")
    assert cli(receiver.path, "lease", "acquire", "--agent", "claude").returncode == 0
    result = cli(receiver.path, "verify", record_rel, "--agent", "claude")
    assert result.returncode == 1, result.stdout  # only TEST_RESULT_UNVERIFIED
    assert "TEST_RESULT_UNVERIFIED" in result.stdout
    assert "BLOCK" not in result.stdout.split("Task continuity")[0]
    assert "write step 3" in result.stdout
    assert (receiver.path / "app.py").read_text() == "print('step 2')\n"


# --------------------------------------------------------------------------- forged results


def test_forged_result_with_correct_sha_is_not_a_pass(repo):
    """The SHA is right but nothing proves the tests ran or passed."""
    data, _ = snapshot(repo, completed=["step 1"],
                       tests=evidence(repo.head(), result="999 passed, 0 failed (fabricated)"))
    path = write_record(repo, data)
    report = verify(repo, path)
    assert ("OK", "TEST_SHA_CURRENT") in codes(report)
    assert ("WARN", "TEST_RESULT_UNVERIFIED") in codes(report)
    assert report.result != "PASS"


def test_verified_ci_run_makes_a_clean_handoff_pass(repo, github_origin):
    data, _ = snapshot(repo, completed=["step 1"], tests=evidence(repo.head(), RUN_URL))
    path = write_record(repo, data)
    fetch = fake_run(repo.head(), head_sha=data["head_sha"])
    report, _ = handoff.verify_record(str(repo.path), str(path), verify_ci=True, fetch=fetch)
    assert ("OK", "CI_VERIFIED") in codes(report)
    assert report.result == "PASS", report.findings
    assert fetch.calls == ["repos/acme/demo/actions/runs/123"]


@pytest.mark.parametrize("overrides,reason", [
    ({"conclusion": "failure"}, "conclusion is failure"),
    ({"status": "in_progress", "conclusion": None}, "status is in_progress"),
    ({"head_sha": "2" * 40}, "run built 222222222222"),
    ({"repository": {"full_name": "someone/fork"}}, "reports repository someone/fork"),
])
def test_ci_run_that_contradicts_the_claim_blocks(repo, github_origin, overrides, reason):
    data, _ = snapshot(repo, tests=evidence(repo.head(), RUN_URL))
    path = write_record(repo, data)
    report, _ = handoff.verify_record(str(repo.path), str(path), verify_ci=True,
                                      fetch=fake_run(data["head_sha"], **overrides))
    mismatches = [f for f in report.findings if f.code == "CI_EVIDENCE_MISMATCH"]
    assert mismatches and reason in mismatches[0].message
    assert report.exit_code == 2


def test_ci_run_from_another_repository_is_rejected_without_a_request(repo, github_origin):
    data, _ = snapshot(repo, tests=evidence(repo.head(), "https://github.com/other/repo/actions/runs/9"))
    path = write_record(repo, data)
    fetch = fake_run(data["head_sha"])
    report, _ = handoff.verify_record(str(repo.path), str(path), verify_ci=True, fetch=fetch)
    assert ("BLOCK", "CI_EVIDENCE_MISMATCH") in codes(report)
    assert fetch.calls == []


def test_unverifiable_ci_evidence_warns(repo, github_origin):
    data, _ = snapshot(repo, tests=evidence(repo.head(), "https://ci.example.com/build/1"))
    path = write_record(repo, data)
    report, _ = handoff.verify_record(str(repo.path), str(path), verify_ci=True, fetch=fake_run("x"))
    assert ("WARN", "CI_UNVERIFIABLE") in codes(report)
    assert ("WARN", "TEST_RESULT_UNVERIFIED") in codes(report)

    def offline(path):
        raise ghread.GitHubReadError("gh api request failed")
    data2, _ = snapshot(repo, agent="claude", tests=evidence(repo.head(), RUN_URL))
    path2 = write_record(repo, data2)
    report2, _ = handoff.verify_record(str(repo.path), str(path2), verify_ci=True, fetch=offline)
    assert ("WARN", "CI_UNVERIFIABLE") in codes(report2)


def test_verified_ci_for_an_older_commit_does_not_cover_new_code(repo, github_origin):
    old = repo.head()
    repo.commit("app.py", "print('changed')\n", "change after CI")
    repo.push()
    data, _ = snapshot(repo, tests=evidence(old, RUN_URL))
    path = write_record(repo, data)
    report, _ = handoff.verify_record(str(repo.path), str(path), verify_ci=True, fetch=fake_run(old))
    assert ("OK", "CI_VERIFIED") in codes(report)
    assert ("WARN", "TEST_EVIDENCE_STALE") in codes(report)
    assert ("WARN", "TEST_RESULT_UNVERIFIED") in codes(report)


def test_github_reader_refuses_unexpected_paths():
    with pytest.raises(ghread.GitHubReadError):
        ghread.gh_get("repos/acme/demo/actions/runs/1/rerun")
    with pytest.raises(ghread.GitHubReadError):
        ghread.gh_get("graphql")


@pytest.mark.parametrize("url,expected", [
    ("https://github.com/KIHL-11/language-ai-platform.git", ("KIHL-11", "language-ai-platform")),
    ("git@github.com:KIHL-11/language-ai-platform.git", ("KIHL-11", "language-ai-platform")),
    ("https://gitlab.com/a/b.git", None),
])
def test_remote_url_parsing(url, expected):
    assert ghread.repo_from_remote_url(url) == expected


# --------------------------------------------------------------------------- concurrency


def test_git_refuses_one_branch_in_two_worktrees(repo):
    result = subprocess.run(["git", "worktree", "add", str(repo.path.parent / "second"), "task/demo"],
                            cwd=repo.path, capture_output=True, text=True)
    assert result.returncode != 0
    assert "already" in result.stderr


def test_lease_blocks_a_second_writer_in_the_same_worktree(repo):
    lease.acquire(str(repo.path), "codex")
    with pytest.raises(lease.LeaseError, match="leased by 'codex'"):
        lease.acquire(str(repo.path), "gemini")
    with pytest.raises(ValueError, match="leased by 'codex'"):
        snapshot(repo, agent="gemini")
    data, _ = snapshot(repo, agent="codex")
    path = write_record(repo, data)
    assert ("BLOCK", "LEASE_HELD") in codes(verify(repo, path, agent="gemini"))
    assert ("BLOCK", "LEASE_HELD") not in codes(verify(repo, path, agent="codex"))
    with pytest.raises(lease.LeaseError):
        lease.release(str(repo.path), "gemini")
    assert lease.release(str(repo.path), "codex") is True
    assert ("BLOCK", "LEASE_HELD") not in codes(verify(repo, path, agent="gemini"))


def test_lease_is_private_to_the_worktree_and_never_committed(repo):
    lease.acquire(str(repo.path), "codex")
    assert repo.git("status", "--porcelain") == ""
    other = repo.path.parent / "other-wt"
    repo.git("worktree", "add", "-q", "-b", "task/other", str(other))
    assert lease.read(str(other)) is None
    lease.acquire(str(other), "claude")
    assert lease.read(str(repo.path))["agent"] == "codex"


def test_concurrent_push_to_one_branch_is_rejected_and_not_delivered(repo):
    alice = repo.clone("alice")
    bob = repo.clone("bob")
    alice.commit("app.py", "alice\n", "alice step")
    alice.push()
    bob.commit("app.py", "bob\n", "bob step")
    rejected = subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=bob.path,
                              capture_output=True, text=True)
    assert rejected.returncode != 0
    data, warnings = snapshot(bob, agent="bob")
    assert data["pushed"] is False
    assert any("local only" in w for w in warnings)


def test_code_pushed_after_handoff_warns_about_another_writer(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data)
    rel = path.relative_to(repo.path)
    receiver = repo.clone("receiver")
    other = repo.clone("other-writer")
    other.commit("app.py", "sneaky\n", "late change")
    other.push()
    # Without a fetch the receiver cannot see the new tip and says so.
    assert ("WARN", "REMOTE_TIP_UNKNOWN") in codes(verify(receiver, receiver.path / rel))
    receiver.git("fetch", "-q", "origin")
    report = verify(receiver, receiver.path / rel)
    assert ("WARN", "REMOTE_ADVANCED_CODE") in codes(report)


# --------------------------------------------------------------------------- the record itself


def test_locally_edited_record_blocks(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data)
    path.write_text(path.read_text().replace('"unfinished": []', '"unfinished": ["edited later"]'))
    assert ("BLOCK", "RECORD_MODIFIED") in codes(verify(repo, path))


def test_uncommitted_record_is_flagged(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data, commit=False)
    assert ("WARN", "RECORD_UNCOMMITTED") in codes(verify(repo, path))


# --------------------------------------------------------------------------- sensitive scan boundaries


@pytest.mark.parametrize("sample", [
    "contact " + "jane.doe" + "@" + "gmail.com",
    "see ~/" + "AgentMemory/notes.md",
    "$" + "HOME/project",
    "/root/" + "project",
    "/mnt/c/" + "Users/someone",
    "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
    "transcript in .claude/" + "projects/x.jsonl",
])
def test_scan_detects_phase2a_patterns(sample):
    assert rec.scan_unsafe(sample), sample


@pytest.mark.parametrize("sample", [
    "handoff-test" + "@example.invalid",
    "12345+bot" + "@users.noreply.github.com",
    "path docs/handoffs/README.md and scripts/agent_handoff/handoff.py",
])
def test_scan_allows_safe_references(sample):
    assert rec.scan_unsafe(sample) == []


@pytest.mark.parametrize("sample", [
    "Learner Zhang Wei studied 42 phrases about her divorce",  # names and personal facts
    "call me on +86 138 0000 0000",                            # phone numbers
    "学员王小明的学习记录",                                       # free-text personal data
    "sk-" + "abc\ndef" + "ghijklmnopqrstuvwxyz",                # a key split across lines
    "c2stQUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=",                 # base64-encoded key
])
def test_scan_limits_are_documented_not_detected(sample):
    """Known blind spots. The scan is a tripwire, not a privacy guarantee."""
    assert rec.scan_unsafe(sample) == []


# --------------------------------------------------------------------------- issue and tracker


def _tracker(repo, rows):
    body = "Master: Issue #75.\n\n| Workstream | Status | Evidence |\n| --- | --- | --- |\n" + rows
    (repo.path / "tracker.md").write_text(body)


def _issue(state):
    def fetch(path):
        assert path == "repos/acme/demo/issues/75"
        return {"state": state, "title": "P8-FC", "updated_at": "2026-10-30T11:41:38Z"}
    return fetch


def test_open_issue_with_pending_tracker_is_consistent(repo, github_origin):
    _tracker(repo, "| A | Pending | none |\n| F | In progress | tests |\n")
    report = handoff.sot_check(str(repo.path), ["tracker.md"], issue=75, fetch=_issue("open"))
    assert ("OK", "ISSUE_TRACKER_CONSISTENT") in codes(report)
    assert report.result == "PASS"


def test_closed_issue_with_unfinished_tracker_warns(repo, github_origin):
    _tracker(repo, "| A | Pending | none |\n")
    report = handoff.sot_check(str(repo.path), ["tracker.md"], issue=75, fetch=_issue("closed"))
    assert ("WARN", "ISSUE_CLOSED_TRACKER_OPEN") in codes(report)


def test_done_tracker_with_open_issue_warns(repo, github_origin):
    _tracker(repo, "| A | Done | https://github.com/acme/demo/actions/runs/5 |\n")
    report = handoff.sot_check(str(repo.path), ["tracker.md"], issue=75, fetch=_issue("open"))
    assert ("WARN", "TRACKER_DONE_ISSUE_OPEN") in codes(report)


def test_bare_issue_reference_is_not_completion_evidence(repo):
    _tracker(repo, "| A | Completed | see #75 |\n")
    report = handoff.sot_check(str(repo.path), ["tracker.md"])
    assert ("BLOCK", "STATUS_WITHOUT_EVIDENCE") in codes(report)


def test_unreachable_issue_is_reported_not_assumed(repo, github_origin):
    _tracker(repo, "| A | Pending | none |\n")

    def offline(path):
        raise ghread.GitHubReadError("gh unavailable")
    report = handoff.sot_check(str(repo.path), ["tracker.md"], issue=75, fetch=offline)
    assert ("WARN", "ISSUE_UNCHECKED") in codes(report)


# --------------------------------------------------------------------------- same-second ordering


def test_same_second_records_are_ordered_by_history(repo):
    now = rec.utc_now()
    first, _ = snapshot(repo, now=now)
    first_path = write_record(repo, first)
    repo.commit("app.py", "print('next')\n", "next step")
    repo.push()
    second, _ = snapshot(repo, agent="claude", now=now)
    write_record(repo, second)
    assert ("WARN", "RECORD_SUPERSEDED") in codes(verify(repo, first_path))


# --------------------------------------------------------------------------- drill script


DRILL = TOOL_DIR / "drill.py"


def test_simulated_drill_meets_every_expectation(tmp_path):
    result = subprocess.run([sys.executable, "-I", str(DRILL), "simulate"], capture_output=True, text=True,
                            timeout=600)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("MODE: SIMULATED")
    assert "16/16 checks met expectations (simulated)" in result.stdout


def test_drill_setup_creates_a_synthetic_origin_and_audit_rejects_an_empty_chain(tmp_path):
    target = tmp_path / "drill"
    setup = subprocess.run([sys.executable, "-I", str(DRILL), "setup", str(target)],
                           capture_output=True, text=True)
    assert setup.returncode == 0, setup.stderr
    assert (target / "origin.git").is_dir() and (target / "OPERATOR-LOG.md").is_file()
    clone = tmp_path / "check"
    run_git(tmp_path, "clone", "-q", "--branch", "drill/handoff-v1", str(target / "origin.git"), str(clone))
    assert (clone / "scripts" / "agent_handoff" / "handoff.py").is_file()
    assert (clone / "TASK.md").is_file()
    assert rec.scan_unsafe((clone / "AGENTS.md").read_text()) == []

    audit = subprocess.run([sys.executable, "-I", str(DRILL), "audit", str(target)],
                           capture_output=True, text=True)
    assert audit.returncode == 1
    assert "agents in order: (none)" in audit.stdout

    again = subprocess.run([sys.executable, "-I", str(DRILL), "setup", str(target)],
                           capture_output=True, text=True)
    assert again.returncode == 2  # never reuses a non-empty directory


def test_fingerprint_prints_hashes_only(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text('{"secret": "do-not-print"}')
    result = subprocess.run([sys.executable, "-I", str(DRILL), "fingerprint"], capture_output=True, text=True,
                            env={**__import__("os").environ, "HOME": str(home)})
    assert result.returncode == 0
    assert "do-not-print" not in result.stdout
    assert '"codex_config": "absent"' in result.stdout


def test_running_the_tool_inside_the_repo_leaves_the_tree_clean(repo, tmp_path):
    """Regression: imports used to drop __pycache__ into the inspected worktree."""
    import shutil
    target = repo.path / "scripts" / "agent_handoff"
    target.mkdir(parents=True)
    for name in ("handoff.py", "record.py", "gitinfo.py", "ghread.py", "lease.py"):
        shutil.copy2(TOOL_DIR / name, target / name)
    repo.git("add", "scripts")
    repo.git("commit", "-q", "-m", "vendor tool without a .gitignore")
    repo.push()
    notes = write_notes(tmp_path / "notes.json", task={"issue": None, "title": "t"})
    result = subprocess.run([sys.executable, "-I", "scripts/agent_handoff/handoff.py", "snapshot",
                             "--agent", "codex", "--notes", str(notes), "--stdout"],
                            cwd=repo.path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert '"pushed": true' in result.stdout
    assert not (target / "__pycache__").exists()
    assert repo.git("status", "--porcelain") == ""


def test_branch_that_modifies_the_verifier_is_flagged(repo):
    repo.git("push", "-q", "origin", "main")
    repo.git("fetch", "-q", "origin")
    data, _ = snapshot(repo)
    path = write_record(repo, data)
    assert ("WARN", "TOOL_CHANGED_ON_BRANCH") not in codes(verify(repo, path))
    repo.commit("scripts/agent_handoff/handoff.py", "def verify():\n    return 'PASS'\n", "tamper")
    repo.push()
    data2, _ = snapshot(repo, agent="claude")
    path2 = write_record(repo, data2)
    assert ("WARN", "TOOL_CHANGED_ON_BRANCH") in codes(verify(repo, path2))
