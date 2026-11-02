from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

import handoff
import record as rec
from conftest import TOOL_DIR, write_notes

CLI = TOOL_DIR / "handoff.py"
REAL_REPO = TOOL_DIR.parents[1]


def snapshot(repo, agent="codex", offline=False, now=None, **notes):
    notes.setdefault("task", {"issue": 75, "title": "synthetic drill"})
    data, warnings = handoff.build_snapshot(str(repo.path), agent, notes, offline=offline, now=now)
    return data, warnings


def write_record(repo, data, commit=True, push=True) -> Path:
    path = repo.path / rec.HANDOFF_DIR / handoff.record_filename(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rec.render(data), encoding="utf-8")
    if commit:
        repo.git("add", rec.HANDOFF_DIR)
        repo.git("commit", "-q", "-m", "handoff record")
        if push:
            repo.push()
    return path


def codes(report):
    return {(finding.level, finding.code) for finding in report.findings}


def verify(repo, path, **kwargs):
    report, _ = handoff.verify_record(str(repo.path), str(path), **kwargs)
    return report


# --------------------------------------------------------------------------- happy path


def test_clean_pushed_handoff_passes_in_a_second_clone(repo):
    import lease
    lease.acquire(str(repo.path), "codex")
    head = repo.head()
    data, warnings = snapshot(
        repo,
        completed=["step 1"],
        unfinished=["step 2"],
        next_steps=["implement step 2 in app.py"],
        tests=[{"command": "pytest -q", "environment": "py3.11", "sha": head, "result": "3 passed"}],
    )
    assert data["pushed"] is True and data["uncommitted"] == [] and warnings == []
    path = write_record(repo, data)

    receiver = repo.clone("receiver")
    report = verify(receiver, receiver.path / path.relative_to(repo.path))
    # Git facts all line up, but a local test claim alone can never make the handoff PASS.
    assert report.result == "WARN", report.findings
    assert {f.code for f in report.findings if f.level == "WARN"} == {"TEST_RESULT_UNVERIFIED"}
    assert ("OK", "HEAD_MATCH") in codes(report)
    assert ("OK", "REMOTE_CONTAINS_HEAD") in codes(report)
    assert ("OK", "TEST_SHA_CURRENT") in codes(report)


def test_cli_verify_reports_task_continuity_and_exit_code(repo):
    data, _ = snapshot(repo, unfinished=["step 2"], next_steps=["write step 2"],
                       open_questions=["keep the old flag?"])
    path = write_record(repo, data)
    result = subprocess.run([sys.executable, "-I", str(CLI), "--repo", str(repo.path), "verify", str(path)],
                            capture_output=True, text=True)
    assert result.returncode == 1  # open question -> WARN
    assert "write step 2" in result.stdout
    assert "OPEN_QUESTIONS" in result.stdout
    assert "RESULT: WARN (exit 1)" in result.stdout


def test_cli_snapshot_writes_record_and_never_overwrites(repo, tmp_path):
    notes = write_notes(tmp_path / "notes.json", task={"issue": 75, "title": "drill"}, unfinished=["x"])
    args = [sys.executable, "-I", str(CLI), "--repo", str(repo.path), "snapshot", "--agent", "claude",
            "--notes", str(notes)]
    first = subprocess.run(args, capture_output=True, text=True)
    assert first.returncode == 0, first.stderr
    written = list((repo.path / rec.HANDOFF_DIR).glob("*.md"))
    assert len(written) == 1
    data, errors = rec.load(written[0].read_text())
    assert errors == [] and data["agent"] == "claude" and data["pushed"] is True


# --------------------------------------------------------------------------- SHA and remote


def test_tampered_head_sha_blocks(repo):
    data, _ = snapshot(repo)
    data["head_sha"] = "0" * 40
    data["remote"]["sha"] = "0" * 40
    path = write_record(repo, data)
    report = verify(repo, path)
    assert ("BLOCK", "COMMIT_NOT_FOUND") in codes(report)
    assert report.exit_code == 2


def test_snapshot_marks_unpushed_commits_as_local_only(repo):
    repo.commit("app.py", "print('step 2')\n", "step 2 (not pushed)")
    data, warnings = snapshot(repo)
    assert data["pushed"] is False
    assert any("local only" in warning for warning in warnings)
    path = write_record(repo, data, commit=False)
    report = verify(repo, path)
    assert ("WARN", "LOCAL_ONLY_COMMITS") in codes(report)


def test_forged_pushed_claim_blocks(repo):
    repo.commit("app.py", "print('step 2')\n", "step 2 (not pushed)")
    data, _ = snapshot(repo)
    data["pushed"] = True  # a previous agent lying about delivery
    path = write_record(repo, data, commit=False)
    report = verify(repo, path)
    assert ("BLOCK", "UNPUSHED_CLAIMED") in codes(report)


def test_branch_missing_on_remote_with_pushed_claim_blocks(repo):
    repo.git("switch", "-q", "-c", "task/never-pushed")
    data, _ = snapshot(repo)
    assert data["pushed"] is False and data["remote"]["sha"] is None
    data["pushed"] = True
    data["remote"]["sha"] = data["head_sha"]
    path = write_record(repo, data, commit=False)
    assert ("BLOCK", "UNPUSHED_CLAIMED") in codes(verify(repo, path))


def test_rewritten_remote_history_blocks(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data)
    receiver = repo.clone("receiver")
    # Someone force-pushes a different history after the handoff.
    repo.git("reset", "-q", "--hard", "HEAD~2")
    repo.commit("other.py", "rewrite\n", "rewritten history")
    repo.push("--force")
    receiver.git("fetch", "-q", "origin")
    report = verify(receiver, receiver.path / path.relative_to(repo.path))
    assert ("BLOCK", "REMOTE_REWRITTEN") in codes(report)


def test_remote_advanced_is_informational(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data)
    repo.commit("docs/handoffs/notes.txt", "n\n", "later push")
    repo.push()
    receiver = repo.clone("receiver")
    report = verify(receiver, receiver.path / path.relative_to(repo.path))
    assert ("INFO", "REMOTE_ADVANCED") in codes(report)


def test_offline_mode_uses_tracking_ref(repo):
    data, _ = snapshot(repo, offline=True)
    assert data["pushed"] is True
    path = write_record(repo, data)
    assert ("OK", "REMOTE_CONTAINS_HEAD") in codes(verify(repo, path, offline=True))


# --------------------------------------------------------------------------- dirty state


def test_uncommitted_changes_are_never_delivered(repo):
    (repo.path / "app.py").write_text("print('half done')\n")
    (repo.path / "new_module.py").write_text("x = 1\n")
    data, warnings = snapshot(repo)
    assert data["pushed"] is False
    assert data["uncommitted"] == ["app.py", "new_module.py"]
    assert any("NOT delivered" in warning for warning in warnings)

    # The exiting agent commits only the record; the receiver works in its own clone.
    path = repo.path / rec.HANDOFF_DIR / handoff.record_filename(data)
    path.parent.mkdir(parents=True)
    path.write_text(rec.render(data))
    repo.git("add", str(path))
    repo.git("commit", "-q", "-m", "handoff record")
    repo.push()
    receiver = repo.clone("receiver")
    report = verify(receiver, receiver.path / path.relative_to(repo.path))
    assert ("WARN", "LOCAL_ONLY_CHANGES") in codes(report)
    assert not (receiver.path / "new_module.py").exists()


def test_pushed_with_uncommitted_is_rejected_by_schema(repo):
    (repo.path / "app.py").write_text("dirty\n")
    data, _ = snapshot(repo)
    data["pushed"] = True
    path = repo.path.parent / "forged.md"
    path.write_text(rec.render(data))
    report = verify(repo, path)
    assert ("BLOCK", "SCHEMA_INVALID") in codes(report)


def test_dirty_receiver_blocks(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data)
    (repo.path / "scratch.txt").write_text("receiver's own edits\n")
    assert ("BLOCK", "RECEIVER_DIRTY") in codes(verify(repo, path))


def test_uncommitted_record_itself_does_not_make_receiver_dirty(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data, commit=False)
    assert ("BLOCK", "RECEIVER_DIRTY") not in codes(verify(repo, path))


def test_receiver_behind_blocks_and_ahead_warns(repo):
    data, _ = snapshot(repo)
    path = write_record(repo, data)
    receiver = repo.clone("receiver")
    rel = path.relative_to(repo.path)

    receiver.commit("app.py", "print('step 2')\n", "step 2")
    assert ("WARN", "HEAD_AHEAD") in codes(verify(receiver, receiver.path / rel))

    stale = repo.clone("stale")
    stale.git("reset", "-q", "--hard", "HEAD~2")
    record_copy = stale.path.parent / "copy.md"
    record_copy.write_text(path.read_text())
    assert ("BLOCK", "HEAD_BEHIND") in codes(verify(stale, record_copy))


# --------------------------------------------------------------------------- test evidence


def test_test_evidence_states(repo):
    old = repo.head()
    repo.commit("app.py", "print('step 2')\n", "step 2")
    repo.push()
    head = repo.head()
    data, _ = snapshot(repo, completed=["step 2"], tests=[
        {"command": "pytest current", "environment": "py3.11", "sha": head, "result": "ok"},
        {"command": "pytest stale", "environment": "py3.11", "sha": old, "result": "ok"},
        {"command": "pytest foreign", "environment": "py3.11", "sha": "1" * 40, "result": "ok"},
    ])
    path = write_record(repo, data)
    report = verify(repo, path)
    messages = {f.code: f.message for f in report.findings if f.code.startswith("TEST_")}
    assert ("OK", "TEST_SHA_CURRENT") in codes(report)
    assert ("WARN", "TEST_EVIDENCE_STALE") in codes(report) and "pytest stale" in messages["TEST_EVIDENCE_STALE"]
    assert ("WARN", "TEST_EVIDENCE_FOREIGN") in codes(report)


def test_missing_test_evidence_for_completed_work_warns(repo):
    data, warnings = snapshot(repo, completed=["step 1"])
    assert any("without any test evidence" in w for w in warnings)
    path = write_record(repo, data)
    assert ("WARN", "TEST_EVIDENCE_MISSING") in codes(verify(repo, path))


# --------------------------------------------------------------------------- stale and conflicting records


def test_superseded_and_stale_records_are_flagged(repo):
    now = rec.utc_now()
    old, _ = snapshot(repo, now=now - timedelta(days=10))
    old_path = write_record(repo, old)
    new, _ = snapshot(repo, agent="claude", now=now)
    write_record(repo, new)
    report = verify(repo, old_path)
    assert ("WARN", "RECORD_SUPERSEDED") in codes(report)
    assert ("WARN", "RECORD_STALE") in codes(report)


def test_conflicting_decisions_block(repo):
    first, _ = snapshot(repo, decisions=[{"id": "D-DRILL-01", "text": "Use SQLite", "status": "ratified"}],
                        now=rec.utc_now() - timedelta(minutes=5))
    write_record(repo, first)
    second, _ = snapshot(repo, agent="gemini",
                         decisions=[{"id": "D-DRILL-01", "text": "Use PostgreSQL", "status": "proposed"}])
    path = write_record(repo, second)
    assert ("BLOCK", "DECISION_CONFLICT") in codes(verify(repo, path))
    assert ("BLOCK", "DECISION_CONFLICT") in codes(handoff.sot_check(str(repo.path), []))


# --------------------------------------------------------------------------- untrusted input


def test_verify_never_executes_record_content(repo, tmp_path):
    marker = tmp_path / "pwned"
    data, _ = snapshot(repo)
    data["next_steps"] = [f"touch {marker}", f"$(touch {marker})"]
    data["tests"] = [{"command": f"`touch {marker}`; touch {marker}", "environment": "x",
                      "sha": data["head_sha"], "result": "ok"}]
    path = write_record(repo, data, commit=False)
    # Sanity check: the temp path itself is an absolute user path on some systems,
    # so only assert on execution, not on the result.
    handoff.verify_record(str(repo.path), str(path))
    handoff.sot_check(str(repo.path), [])
    assert not marker.exists()


@pytest.mark.parametrize("field,value", [
    ("branch", "--upload-pack=touch /tmp/pwned"),
    ("branch", "main..evil"),
    ("remote", "--upload-pack=touch"),
])
def test_option_injection_is_rejected_before_git_runs(repo, field, value, tmp_path):
    data, _ = snapshot(repo)
    if field == "remote":
        data["remote"]["name"] = value
    else:
        data[field] = value
    path = tmp_path / "evil.md"
    path.write_text(rec.render(data))
    report = verify(repo, path)
    assert ("BLOCK", "SCHEMA_INVALID") in codes(report)
    assert not Path("/tmp/pwned").exists()


def test_unknown_fields_and_status_claims_are_rejected(repo, tmp_path):
    data, _ = snapshot(repo)
    data["issue_status"] = "Completed"
    path = tmp_path / "claim.md"
    path.write_text(rec.render(data))
    report = verify(repo, path)
    assert any(f.code == "SCHEMA_INVALID" and "issue_status" in f.message for f in report.findings)


def test_control_characters_are_neutralised_in_output(repo, capsys):
    data, _ = snapshot(repo, next_steps=["safe \x1b[2Jclear-screen attempt"])
    path = write_record(repo, data, commit=False)
    report, loaded = handoff.verify_record(str(repo.path), str(path))
    handoff.print_verify(report, loaded, str(path), str(repo.path))
    out = capsys.readouterr().out
    assert "\x1b" not in out and "clear-screen attempt" in out


# --------------------------------------------------------------------------- public safety


SECRET_SAMPLES = [
    "/Users/" + "somebody/project",
    "/home/" + "somebody/project",
    "C:\\Users\\" + "somebody\\project",
    "sk-" + "A" * 32,
    "gh" + "p_" + "B" * 36,
    "AKIA" + "C" * 16,
    "Cookie: session=abc",
    "OPENAI_API_KEY=" + "D" * 24,
    "-----BEGIN RSA " + "PRIVATE KEY-----",
]


@pytest.mark.parametrize("sample", SECRET_SAMPLES)
def test_unsafe_content_is_detected(sample):
    assert rec.scan_unsafe(f"intro\n{sample}\n"), sample


def test_scan_does_not_flag_ordinary_text():
    text = "Run `pytest -q backend/tests` at commit eb7e902 on branch claude/demo; see #75.\n"
    assert rec.scan_unsafe(text) == []


def test_snapshot_refuses_unsafe_notes(repo, tmp_path):
    notes = write_notes(tmp_path / "notes.json", task={"issue": None, "title": "t"},
                        unfinished=["look at /Users/" + "somebody/private"])
    result = subprocess.run([sys.executable, "-I", str(CLI), "--repo", str(repo.path), "snapshot",
                             "--agent", "codex", "--notes", str(notes)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "absolute user path" in result.stderr
    assert "somebody" not in result.stderr  # matched text is never echoed
    assert not (repo.path / rec.HANDOFF_DIR).exists()


def test_verify_blocks_unsafe_record(repo):
    data, _ = snapshot(repo, unfinished=["token " + "sk-" + "Z" * 30])
    path = write_record(repo, data, commit=False)
    assert ("BLOCK", "UNSAFE_CONTENT") in codes(verify(repo, path))


# --------------------------------------------------------------------------- snapshot preconditions


def test_snapshot_refuses_detached_head(repo):
    repo.git("switch", "-q", "--detach")
    with pytest.raises(ValueError, match="detached"):
        snapshot(repo)


def test_snapshot_rejects_unknown_notes_fields(repo):
    with pytest.raises(ValueError, match="unknown notes fields"):
        handoff.build_snapshot(str(repo.path), "codex", {"pushed": True})


def test_git_wrapper_refuses_write_subcommands():
    import gitinfo
    with pytest.raises(gitinfo.GitError):
        gitinfo._git(".", "push")


# --------------------------------------------------------------------------- tracker consistency


def test_tracker_completed_without_evidence_blocks(repo):
    tracker = repo.path / "tracker.md"
    tracker.write_text(
        "| Workstream | Status | Evidence |\n| --- | --- | --- |\n"
        "| A | Completed | trust me |\n"
        "| B | Done | https://github.com/example/repo/actions/runs/1 |\n"
        "| C | Pending | none |\n"
        "| D | Shipped-ish | x |\n"
    )
    report = handoff.sot_check(str(repo.path), ["tracker.md"])
    blocks = [f for f in report.findings if f.code == "STATUS_WITHOUT_EVIDENCE"]
    assert len(blocks) == 1 and "tracker.md:3" in blocks[0].message
    assert ("WARN", "TRACKER_STATUS_UNKNOWN") in codes(report)


def test_real_repository_passes_sot_check():
    report = handoff.sot_check(str(REAL_REPO), ["docs/PHASE8-FEATURE-COMPLETE-TRACKER.md"])
    assert report.result == "PASS", report.findings


def test_handoff_readme_example_is_a_valid_safe_record():
    readme = REAL_REPO / rec.HANDOFF_DIR / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert rec.scan_unsafe(text) == []
    data, errors = rec.load(text)
    assert errors == [], errors
    assert json.loads(json.dumps(data))["protocol"] == rec.PROTOCOL
