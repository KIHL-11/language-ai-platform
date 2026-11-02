#!/usr/bin/env python3
"""Cross-agent handoff tool: snapshot, verify and sot-check.

    python3 scripts/agent_handoff/handoff.py snapshot --agent codex --notes notes.json
    python3 scripts/agent_handoff/handoff.py verify docs/handoffs/<record>.md
    python3 scripts/agent_handoff/handoff.py sot-check

Exit codes: 0 = PASS, 1 = WARN (drift to review), 2 = BLOCK (do not continue).

Handoff records are untrusted input. This tool never executes anything taken
from a record; it only runs the fixed read-only git queries in gitinfo.py.
See docs/agent-protocol/HANDOFF-PROTOCOL-v1.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gitinfo  # noqa: E402
import record as rec  # noqa: E402

OK, INFO, WARN, BLOCK = "OK", "INFO", "WARN", "BLOCK"
EXIT_CODES = {OK: 0, INFO: 0, WARN: 1, BLOCK: 2}
SEVERITY = {OK: 0, INFO: 0, WARN: 1, BLOCK: 2}

NOTES_KEYS = {"task", "completed", "unfinished", "next_steps", "decisions",
              "open_questions", "tests", "supersedes"}
EVIDENCE_REQUIRED_STATUSES = {"completed", "complete", "done", "implemented"}
KNOWN_TRACKER_STATUSES = EVIDENCE_REQUIRED_STATUSES | {
    "pending", "in progress", "blocked", "in review", "deferred",
}
EVIDENCE_RE = re.compile(r"https?://\S+|#\d+|\b[0-9a-f]{7,40}\b")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\x80-\x9f]")


@dataclass
class Finding:
    level: str
    code: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: str, code: str, message: str) -> None:
        self.findings.append(Finding(level, code, message))

    @property
    def result(self) -> str:
        worst = max((SEVERITY[f.level] for f in self.findings), default=0)
        return {0: "PASS", 1: WARN, 2: BLOCK}[worst]

    @property
    def exit_code(self) -> int:
        return {"PASS": 0, WARN: 1, BLOCK: 2}[self.result]


def clean(value: Any) -> str:
    """Neutralise untrusted text before printing (no terminal escapes, one line)."""
    return CONTROL_RE.sub("?", str(value).replace("\r", " ").replace("\n", " "))


def is_record_file(path: Path) -> bool:
    return path.suffix == ".md" and path.name.lower() != "readme.md"


def only_handoff_changes(paths: list[str]) -> bool:
    return all(path.startswith(rec.HANDOFF_DIR + "/") for path in paths)


def load_other_records(top: Path, exclude: Path | None) -> list[tuple[str, dict]]:
    directory = top / rec.HANDOFF_DIR
    loaded: list[tuple[str, dict]] = []
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*.md")):
        if not is_record_file(path) or (exclude is not None and path.resolve() == exclude):
            continue
        data, errors = rec.load(path.read_text(encoding="utf-8", errors="replace"))
        if data is not None and not errors:
            loaded.append((path.name, data))
    return loaded


def decision_conflicts(records: list[tuple[str, dict]]) -> list[str]:
    seen: dict[str, tuple[str, str]] = {}
    conflicts: list[str] = []
    for name, data in records:
        for decision in data["decisions"]:
            text = " ".join(decision["text"].split())
            if decision["id"] in seen and seen[decision["id"]][0] != text:
                conflicts.append(
                    f"decision {decision['id']} differs between {seen[decision['id']][1]} and {name}"
                )
            seen.setdefault(decision["id"], (text, name))
    return conflicts


# --------------------------------------------------------------------------- snapshot


def build_snapshot(repo: str, agent: str, notes: dict[str, Any], remote: str = "origin",
                   offline: bool = False, now: datetime | None = None) -> tuple[dict, list[str]]:
    """Collect git facts and merge the agent's notes into a validated record.

    Returns (record, warnings). Raises ValueError for anything that must stop the
    snapshot. ``pushed`` is computed from git, never taken from the agent.
    """
    unknown = sorted(set(notes) - NOTES_KEYS)
    if unknown:
        raise ValueError(f"unknown notes fields: {', '.join(unknown)}")
    branch = gitinfo.current_branch(repo)
    if branch is None:
        raise ValueError("HEAD is detached; create or switch to a task branch before writing a handoff")
    if not rec.validate_branch_name(branch):
        raise ValueError("branch name is not acceptable for a handoff record")
    if not rec.REMOTE_RE.match(remote) or remote not in gitinfo.remotes(repo):
        raise ValueError(f"remote '{clean(remote)}' is not configured")

    head = gitinfo.head_sha(repo)
    dirty = gitinfo.porcelain_paths(repo)
    remote_sha = gitinfo.remote_branch_sha(repo, remote, branch, offline=offline)
    remote_has_head = remote_sha is not None and (
        remote_sha == head
        or (gitinfo.has_commit(repo, remote_sha) and gitinfo.is_ancestor(repo, head, remote_sha))
    )

    data = {
        "protocol": rec.PROTOCOL,
        "created_at": rec.format_timestamp(now or rec.utc_now()),
        "agent": agent,
        "task": notes.get("task", {"issue": None, "title": ""}),
        "branch": branch,
        "head_sha": head,
        "remote": {"name": remote, "sha": remote_sha},
        "pushed": bool(remote_has_head and not dirty),
        "uncommitted": dirty,
        "tests": notes.get("tests", []),
        "completed": notes.get("completed", []),
        "unfinished": notes.get("unfinished", []),
        "next_steps": notes.get("next_steps", []),
        "decisions": notes.get("decisions", []),
        "open_questions": notes.get("open_questions", []),
        "supersedes": notes.get("supersedes"),
    }
    errors = rec.validate(data)
    if errors:
        raise ValueError("record would be invalid:\n  " + "\n  ".join(errors))

    warnings: list[str] = []
    if dirty:
        warnings.append(f"{len(dirty)} uncommitted path(s) recorded as NOT delivered")
    if not remote_has_head:
        warnings.append(f"{remote}/{branch} does not contain HEAD; commits are local only")
    for test in data["tests"]:
        if test["sha"] != head:
            warnings.append(f"test evidence '{clean(test['command'])}' is for {test['sha'][:12]}, not HEAD")
    if data["completed"] and not data["tests"]:
        warnings.append("completed work is listed without any test evidence")
    return data, warnings


def record_filename(data: dict) -> str:
    stamp = rec.parse_timestamp(data["created_at"]).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", data["branch"].lower()).strip("-")[:60]
    return f"{stamp}-{data['agent']}-{slug}.md"


def cmd_snapshot(args: argparse.Namespace) -> int:
    notes: dict[str, Any] = {}
    if args.notes:
        try:
            notes = json.loads(Path(args.notes).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"snapshot: cannot read notes: {exc.__class__.__name__}", file=sys.stderr)
            return 2
        if not isinstance(notes, dict):
            print("snapshot: notes must be a JSON object", file=sys.stderr)
            return 2
    try:
        data, warnings = build_snapshot(args.repo, args.agent, notes, args.remote, args.offline)
    except (ValueError, gitinfo.GitError) as exc:
        print(f"snapshot: {exc}", file=sys.stderr)
        return 2

    text = rec.render(data)
    unsafe = rec.scan_unsafe(text)
    if unsafe:
        print("snapshot: refusing to write; the record is not safe for a public repository:", file=sys.stderr)
        for label, line in unsafe:
            print(f"  line {line}: {label}", file=sys.stderr)
        return 2

    for warning in warnings:
        print(f"snapshot: WARN {warning}", file=sys.stderr)
    if args.stdout:
        sys.stdout.write(text)
        return 0

    top = Path(gitinfo.toplevel(args.repo))
    target = top / rec.HANDOFF_DIR / record_filename(data)
    if target.exists():
        print(f"snapshot: {target.relative_to(top)} already exists; not overwriting", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"Handoff written: {target.relative_to(top).as_posix()}")
    print("Commit and push it to the task branch, then the next agent runs:")
    print(f"  python3 scripts/agent_handoff/handoff.py verify {target.relative_to(top).as_posix()}")
    return 0


# --------------------------------------------------------------------------- verify


def verify_record(repo: str, record_path: str, offline: bool = False, max_age_days: int = 7,
                  now: datetime | None = None) -> tuple[Report, dict | None]:
    report = Report()
    path = Path(record_path).resolve()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.add(BLOCK, "RECORD_UNREADABLE", f"cannot read record: {exc.__class__.__name__}")
        return report, None

    for label, line in rec.scan_unsafe(text):
        report.add(BLOCK, "UNSAFE_CONTENT", f"line {line}: {label} (record must not be published)")

    data, errors = rec.load(text)
    for error in errors:
        report.add(BLOCK, "SCHEMA_INVALID", clean(error))
    if data is None:
        return report, None

    top = Path(gitinfo.toplevel(repo))
    try:
        record_rel = path.relative_to(top.resolve()).as_posix()
    except ValueError:
        record_rel = None
        report.add(WARN, "RECORD_OUTSIDE_REPO", "record is not inside this repository")

    # Receiver workspace.
    branch = gitinfo.current_branch(repo)
    head = gitinfo.head_sha(repo)
    dirty = [p for p in gitinfo.porcelain_paths(repo) if p != record_rel]
    if dirty:
        report.add(BLOCK, "RECEIVER_DIRTY",
                   f"this worktree has {len(dirty)} uncommitted path(s); start from a clean worktree: "
                   + ", ".join(clean(p) for p in dirty[:10]))
    if branch != data["branch"]:
        report.add(WARN, "BRANCH_MISMATCH",
                   f"worktree is on {clean(branch or 'detached HEAD')}, record is for {data['branch']}")

    # Recorded commit versus the receiver's HEAD.
    recorded = data["head_sha"]
    if not gitinfo.has_commit(repo, recorded):
        report.add(BLOCK, "COMMIT_NOT_FOUND",
                   f"recorded head {recorded[:12]} is not in this clone (never pushed, wrong SHA, or not fetched)")
    elif recorded == head:
        report.add(OK, "HEAD_MATCH", f"HEAD is the recorded commit {recorded[:12]}")
    elif gitinfo.is_ancestor(repo, recorded, head):
        changed = gitinfo.changed_paths(repo, recorded, head)
        if only_handoff_changes(changed):
            report.add(OK, "HEAD_MATCH", "HEAD differs from the recorded commit only by handoff records")
        else:
            report.add(WARN, "HEAD_AHEAD",
                       f"{len(changed)} file(s) changed after the handoff; review them before relying on the record")
    elif gitinfo.is_ancestor(repo, head, recorded):
        report.add(BLOCK, "HEAD_BEHIND", "this worktree is behind the recorded commit; update it before writing")
    else:
        report.add(BLOCK, "HEAD_DIVERGED", "HEAD and the recorded commit have diverged")

    # What the remote really contains.
    remote = data["remote"]["name"]
    if remote not in gitinfo.remotes(repo):
        report.add(BLOCK, "REMOTE_UNKNOWN", f"remote '{remote}' is not configured in this clone")
    else:
        _verify_remote(report, repo, data, remote, offline)

    if data["uncommitted"]:
        report.add(WARN, "LOCAL_ONLY_CHANGES",
                   f"{len(data['uncommitted'])} path(s) were uncommitted at handoff; they are NOT delivered "
                   "and are not in Git: " + ", ".join(clean(p) for p in data["uncommitted"][:10]))

    _verify_tests(report, repo, data, head)
    _verify_freshness(report, top, path, data, max_age_days, now or rec.utc_now())

    if data["open_questions"]:
        report.add(WARN, "OPEN_QUESTIONS",
                   f"{len(data['open_questions'])} open question(s) need the user's decision")
    return report, data


def _verify_remote(report: Report, repo: str, data: dict, remote: str, offline: bool) -> None:
    recorded = data["head_sha"]
    try:
        tip = gitinfo.remote_branch_sha(repo, remote, data["branch"], offline=offline)
    except gitinfo.GitError as exc:
        level = BLOCK if data["pushed"] else WARN
        report.add(level, "REMOTE_UNVERIFIED", f"cannot query {remote}: {clean(exc)}")
        return
    source = "local tracking ref (offline)" if offline else "remote"
    if tip is None:
        code, level = ("UNPUSHED_CLAIMED", BLOCK) if data["pushed"] else ("NOT_ON_REMOTE", WARN)
        report.add(level, code, f"{remote}/{data['branch']} does not exist on the {source}")
        return

    known_tip = gitinfo.has_commit(repo, tip)
    if tip == recorded:
        contains = True
    elif known_tip and gitinfo.has_commit(repo, recorded):
        contains = gitinfo.is_ancestor(repo, recorded, tip)
    else:
        contains = None

    if contains is None:
        report.add(WARN, "REMOTE_TIP_UNKNOWN",
                   f"{remote}/{data['branch']} is at {tip[:12]}, which is not in this clone; fetch and re-verify")
    elif contains:
        report.add(OK, "REMOTE_CONTAINS_HEAD", f"{remote}/{data['branch']} contains {recorded[:12]}")
    elif data["pushed"]:
        report.add(BLOCK, "UNPUSHED_CLAIMED",
                   f"record says pushed, but {remote}/{data['branch']} does not contain {recorded[:12]}")
    else:
        report.add(WARN, "LOCAL_ONLY_COMMITS",
                   f"{recorded[:12]} is not on {remote}/{data['branch']}; those commits are NOT delivered")

    recorded_remote = data["remote"]["sha"]
    if recorded_remote and recorded_remote != tip:
        if known_tip and gitinfo.has_commit(repo, recorded_remote) and gitinfo.is_ancestor(repo, recorded_remote, tip):
            report.add(INFO, "REMOTE_ADVANCED", f"{remote}/{data['branch']} moved forward since the handoff")
        elif known_tip and gitinfo.has_commit(repo, recorded_remote):
            report.add(BLOCK, "REMOTE_REWRITTEN",
                       f"{remote}/{data['branch']} no longer contains the recorded remote commit (history rewritten)")
        else:
            report.add(WARN, "REMOTE_TIP_UNKNOWN", "cannot compare the recorded remote commit; fetch and re-verify")


def _verify_tests(report: Report, repo: str, data: dict, head: str) -> None:
    if not data["tests"]:
        if data["completed"]:
            report.add(WARN, "TEST_EVIDENCE_MISSING", "completed work is listed without test evidence")
        return
    for test in data["tests"]:
        label = clean(test["command"])
        sha = test["sha"]
        if sha == head:
            report.add(OK, "TEST_EVIDENCE_CURRENT", f"'{label}' was recorded at HEAD")
        elif not gitinfo.has_commit(repo, sha) or not gitinfo.is_ancestor(repo, sha, head):
            report.add(WARN, "TEST_EVIDENCE_FOREIGN",
                       f"'{label}' was recorded at {sha[:12]}, which is not in this branch's history")
        elif only_handoff_changes(gitinfo.changed_paths(repo, sha, head)):
            report.add(OK, "TEST_EVIDENCE_CURRENT", f"'{label}' covers HEAD (only handoff records changed since)")
        else:
            report.add(WARN, "TEST_EVIDENCE_STALE",
                       f"'{label}' was recorded at {sha[:12]}; code changed since, re-run before relying on it")


def _verify_freshness(report: Report, top: Path, path: Path, data: dict, max_age_days: int,
                      now: datetime) -> None:
    created = rec.parse_timestamp(data["created_at"])
    if created > now + timedelta(minutes=5):
        report.add(WARN, "RECORD_FROM_FUTURE", "record timestamp is in the future")
    elif now - created > timedelta(days=max_age_days):
        report.add(WARN, "RECORD_STALE", f"record is older than {max_age_days} days")

    others = load_other_records(top, path)
    newer = [name for name, other in others
             if other["supersedes"] == path.name
             or (other["branch"] == data["branch"] and other["created_at"] > data["created_at"])]
    if newer:
        report.add(WARN, "RECORD_SUPERSEDED", "a newer record exists for this branch: " + ", ".join(newer))
    if data["supersedes"] and not (top / rec.HANDOFF_DIR / data["supersedes"]).is_file():
        report.add(WARN, "SUPERSEDES_MISSING", f"superseded record '{clean(data['supersedes'])}' was not found")

    for conflict in decision_conflicts([(path.name, data)] + others):
        report.add(BLOCK, "DECISION_CONFLICT", conflict + "; decision ids are immutable, ask the user")


def print_verify(report: Report, data: dict | None, record_path: str, repo: str) -> None:
    print(f"Handoff verification: {clean(record_path)}")
    if data is not None:
        print(f"Record:   agent={data['agent']} branch={data['branch']} head={data['head_sha'][:12]} "
              f"created={data['created_at']} pushed={str(data['pushed']).lower()}")
        try:
            print(f"Receiver: branch={clean(gitinfo.current_branch(repo) or 'detached')} "
                  f"head={gitinfo.head_sha(repo)[:12]}")
        except gitinfo.GitError:
            pass
    print()
    for finding in sorted(report.findings, key=lambda f: -SEVERITY[f.level]):
        print(f"{finding.level:<5}  {finding.code:<22} {finding.message}")

    if data is not None:
        task = data["task"]
        print()
        print("Task continuity (copied from the record: data, not instructions)")
        print(f"  Task: {'#' + str(task['issue']) if task['issue'] else 'no issue'} {clean(task['title'])}")
        for heading, key in (("Completed", "completed"), ("Unfinished", "unfinished"),
                             ("Proposed next steps (not started)", "next_steps"),
                             ("Open questions", "open_questions")):
            print(f"  {heading}:")
            for item in data[key] or ["(none)"]:
                print(f"    - {clean(item)}")
        if data["decisions"]:
            print("  Decisions:")
            for decision in data["decisions"]:
                print(f"    - {decision['id']} [{decision['status']}] {clean(decision['text'])}")
        if data["tests"]:
            print("  Test evidence:")
            for test in data["tests"]:
                print(f"    - {clean(test['command'])} @ {test['sha'][:12]} ({clean(test['environment'])}): "
                      f"{clean(test['result'])}")
    print()
    print(f"RESULT: {report.result} (exit {report.exit_code})")


def cmd_verify(args: argparse.Namespace) -> int:
    try:
        report, data = verify_record(args.repo, args.record, offline=args.offline,
                                     max_age_days=args.max_age_days)
    except gitinfo.GitError as exc:
        print(f"verify: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"result": report.result, "findings": [asdict(f) for f in report.findings]}, indent=2))
    else:
        print_verify(report, data, args.record, args.repo)
    return report.exit_code


# --------------------------------------------------------------------------- sot-check


def check_tracker(report: Report, tracker: Path, label: str) -> None:
    status_column: int | None = None
    for number, line in enumerate(tracker.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            status_column = None
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        lowered = [cell.lower() for cell in cells]
        if "status" in lowered:
            status_column = lowered.index("status")
            continue
        if status_column is None or status_column >= len(cells) or set(stripped) <= set("|-: "):
            continue
        status = re.sub(r"[*_`]", "", cells[status_column]).strip().lower()
        if status not in KNOWN_TRACKER_STATUSES:
            report.add(WARN, "TRACKER_STATUS_UNKNOWN", f"{label}:{number}: unrecognised status '{clean(status)}'")
        elif status in EVIDENCE_REQUIRED_STATUSES and not EVIDENCE_RE.search(line):
            report.add(BLOCK, "STATUS_WITHOUT_EVIDENCE",
                       f"{label}:{number}: '{clean(status)}' needs a CI run, PR/issue or commit reference")


def sot_check(repo: str, trackers: list[str]) -> Report:
    report = Report()
    top = Path(gitinfo.toplevel(repo))
    directory = top / rec.HANDOFF_DIR
    records: list[tuple[str, dict]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = path.relative_to(top).as_posix()
            for label, line in rec.scan_unsafe(text):
                report.add(BLOCK, "UNSAFE_CONTENT", f"{rel}:{line}: {label}")
            if not is_record_file(path):
                continue
            data, errors = rec.load(text)
            for error in errors:
                report.add(BLOCK, "SCHEMA_INVALID", f"{rel}: {clean(error)}")
            if data is not None:
                records.append((path.name, data))

    names = {name for name, _ in records}
    seen: dict[tuple[str, str], str] = {}
    for name, data in records:
        key = (data["branch"], data["created_at"])
        if key in seen:
            report.add(WARN, "DUPLICATE_RECORD", f"{seen[key]} and {name} share branch and timestamp")
        seen[key] = name
        if data["supersedes"] and data["supersedes"] not in names:
            report.add(WARN, "SUPERSEDES_MISSING", f"{name} supersedes missing record '{clean(data['supersedes'])}'")
    for conflict in decision_conflicts(records):
        report.add(BLOCK, "DECISION_CONFLICT", conflict)
    report.add(OK, "HANDOFF_RECORDS", f"{len(records)} record(s) checked in {rec.HANDOFF_DIR}/")

    for tracker in trackers:
        path = top / tracker
        if path.is_file():
            check_tracker(report, path, tracker)
            report.add(OK, "TRACKER_CHECKED", tracker)
        else:
            report.add(WARN, "TRACKER_MISSING", f"{tracker} not found")
    return report


def cmd_sot_check(args: argparse.Namespace) -> int:
    try:
        report = sot_check(args.repo, args.tracker)
    except gitinfo.GitError as exc:
        print(f"sot-check: {exc}", file=sys.stderr)
        return 2
    for finding in sorted(report.findings, key=lambda f: -SEVERITY[f.level]):
        print(f"{finding.level:<5}  {finding.code:<24} {finding.message}")
    print(f"RESULT: {report.result} (exit {report.exit_code})")
    return report.exit_code


# --------------------------------------------------------------------------- cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default=".", help="repository or worktree to inspect (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="write a handoff record for the current branch")
    snap.add_argument("--agent", required=True, help="codex, claude, gemini, ...")
    snap.add_argument("--notes", help="JSON file with task, completed, unfinished, next_steps, "
                                      "decisions, open_questions, tests, supersedes")
    snap.add_argument("--remote", default="origin")
    snap.add_argument("--offline", action="store_true", help="use remote-tracking refs instead of ls-remote")
    snap.add_argument("--stdout", action="store_true", help="print the record instead of writing it")
    snap.set_defaults(func=cmd_snapshot)

    ver = sub.add_parser("verify", help="verify a handoff record against git before continuing")
    ver.add_argument("record")
    ver.add_argument("--offline", action="store_true", help="use remote-tracking refs instead of ls-remote")
    ver.add_argument("--max-age-days", type=int, default=7)
    ver.add_argument("--json", action="store_true")
    ver.set_defaults(func=cmd_verify)

    sot = sub.add_parser("sot-check", help="check handoff records and trackers for conflicts")
    sot.add_argument("--tracker", action="append",
                     help="tracker Markdown to check (repeatable; default: the Phase 8 tracker)")
    sot.set_defaults(func=cmd_sot_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "tracker", None) is None and args.command == "sot-check":
        args.tracker = ["docs/PHASE8-FEATURE-COMPLETE-TRACKER.md"]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
