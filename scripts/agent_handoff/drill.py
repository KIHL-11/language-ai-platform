#!/usr/bin/env python3
"""End-to-end acceptance drill for the handoff protocol.

    python3 scripts/agent_handoff/drill.py simulate
        Phase 2A. One process plays every role in a throwaway synthetic repo.
        This is a SIMULATION of the protocol mechanics; no real agent runs.

    python3 scripts/agent_handoff/drill.py setup <dir>
        Phase 2B. Create a synthetic bare origin plus task for real agents.
    python3 scripts/agent_handoff/drill.py audit <dir> --expect codex,claude,gemini
        Phase 2B. Check the record chain the real agents left behind.
    python3 scripts/agent_handoff/drill.py fingerprint [--project <path>]
        Before/after safety fingerprint (hashes only, nothing is printed in clear).

Everything happens under the given or a temporary directory. Git runs with an
isolated configuration; the drill never touches the real project, user-level
agent settings, or the network.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOL_DIR))

import handoff  # noqa: E402
import lease  # noqa: E402
import record as rec  # noqa: E402

DRILL_BRANCH = "drill/handoff-v1"
TOOL_FILES = ("handoff.py", "record.py", "gitinfo.py", "ghread.py", "lease.py", "drill.py")


def isolated_env(base: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_GLOBAL": str(base / ".gitconfig-drill"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Drill", "GIT_AUTHOR_EMAIL": "drill@example.invalid",
        "GIT_COMMITTER_NAME": "Drill", "GIT_COMMITTER_EMAIL": "drill@example.invalid",
    })
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(name, None)
    return env


class Sandbox:
    def __init__(self, base: Path):
        self.base = base
        self.origin = base / "origin.git"
        self.env = isolated_env(base)
        os.environ.update({k: v for k, v in self.env.items() if k.startswith("GIT_")})
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
            os.environ.pop(name, None)

    def git(self, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True,
                              check=check)

    def write(self, repo: Path, rel: str, content: str) -> None:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def commit(self, repo: Path, message: str, *paths: str) -> str:
        self.git(repo, "add", *(paths or (".",)))
        self.git(repo, "commit", "-q", "-m", message)
        return self.head(repo)

    def head(self, repo: Path) -> str:
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def push(self, repo: Path) -> None:
        self.git(repo, "push", "-q", "origin", "HEAD")

    def clone(self, name: str) -> Path:
        target = self.base / name
        self.git(self.base, "clone", "-q", "--branch", DRILL_BRANCH, str(self.origin), str(target))
        return target

    def seed(self, with_tool_copy: bool) -> Path:
        self.git(self.base, "init", "-q", "--bare", "-b", "main", str(self.origin))
        seed = self.base / "seed"
        self.git(self.base, "init", "-q", "-b", "main", str(seed))
        self.git(seed, "remote", "add", "origin", str(self.origin))
        for rel, content in SEED_FILES.items():
            self.write(seed, rel, content)
        if with_tool_copy:
            for name in TOOL_FILES:
                shutil.copy2(TOOL_DIR / name, seed / "scripts" / "agent_handoff" / name)
        self.commit(seed, "synthetic drill task")
        self.git(seed, "switch", "-q", "-c", DRILL_BRANCH)
        self.git(seed, "push", "-q", "-u", "origin", DRILL_BRANCH)
        return seed


SEED_FILES = {
    "TASK.md": (
        "# Synthetic drill task\n\n"
        "Implement these in `drill_calc.py`, one step per agent session, each with a unittest in\n"
        "`test_drill_calc.py` (run: `python3 -m unittest -q`):\n\n"
        "1. `add(a, b)`\n2. `subtract(a, b)`\n3. `multiply(a, b)`\n\n"
        "Decision D-DRILL-01 (ratified): integers only; no floats.\n"
    ),
    "AGENTS.md": (
        "# Drill rules\n\n"
        "This is a synthetic repository for testing agent handoffs. Follow\n"
        "`HANDOFF.md`. Never push to `main`. Work only on `drill/handoff-v1`.\n"
        "Never use real data, credentials or paid APIs.\n"
    ),
    "CLAUDE.md": "@AGENTS.md\n",
    "GEMINI.md": "@./AGENTS.md\n",
    "HANDOFF.md": (
        "# Handoff steps\n\n"
        "Start: `python3 scripts/agent_handoff/handoff.py lease acquire --agent <you>`, then verify the\n"
        "newest record in docs/handoffs/ with `handoff.py verify <record> --agent <you>`, report it, and\n"
        "wait for the operator. Hand off: write notes JSON outside the repo, run\n"
        "`handoff.py snapshot --agent <you> --notes <file>`, commit and push the record, then\n"
        "`handoff.py lease release --agent <you>`.\n"
    ),
    "drill_calc.py": '"""Synthetic drill module."""\n',
    "test_drill_calc.py": "import unittest\n\nimport drill_calc  # noqa: F401\n",
    "docs/handoffs/README.md": "Handoff records for the synthetic drill.\n",
    "scripts/agent_handoff/.keep": "",
    ".gitignore": "__pycache__/\n*.pyc\n",
}


# --------------------------------------------------------------------------- simulate (Phase 2A)


def write_handoff(sb: Sandbox, repo: Path, agent: str, now=None, **notes) -> Path:
    notes.setdefault("task", {"issue": None, "title": "synthetic drill"})
    data, _ = handoff.build_snapshot(str(repo), agent, notes, now=now)
    path = repo / rec.HANDOFF_DIR / handoff.record_filename(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rec.render(data), encoding="utf-8")
    sb.commit(repo, f"handoff: {agent}", str(path.relative_to(repo)))
    sb.push(repo)
    return path


def result_codes(repo: Path, record: Path, **kwargs) -> tuple[str, set[str]]:
    report, _ = handoff.verify_record(str(repo), str(record), **kwargs)
    return report.result, {f"{f.level}:{f.code}" for f in report.findings}


def scenario_chain(sb: Sandbox) -> list[tuple[str, bool, str]]:
    """Codex -> Claude -> Gemini -> Codex, each hop in a fresh clone."""
    out = []
    steps = [("codex", "add", "+"), ("claude", "subtract", "-"), ("gemini", "multiply", "*")]
    previous: Path | None = None
    for hop, (agent, func, op) in enumerate(steps, start=1):
        work = sb.clone(f"hop{hop}-{agent}")
        lease.acquire(str(work), agent)
        if previous is not None:
            result, found = result_codes(work, work / previous, agent=agent)
            ok = result == "WARN" and {c for c in found if c.startswith(("WARN", "BLOCK"))} == {
                "WARN:TEST_RESULT_UNVERIFIED"}
            out.append((f"hop {hop}: {agent} verifies previous record", ok, f"{result} {sorted(found)}"))
        code = (work / "drill_calc.py").read_text() + f"\n\ndef {func}(a, b):\n    return a {op} b\n"
        sb.write(work, "drill_calc.py", code)
        head = sb.commit(work, f"step {hop}: {func}", "drill_calc.py")
        sb.push(work)
        record = write_handoff(sb, work, agent, completed=[f"step {hop}: {func}"],
                               unfinished=[f"step {n}" for n in range(hop + 1, 4)],
                               next_steps=[f"implement step {hop + 1}"] if hop < 3 else ["final review"],
                               tests=[{"command": "python3 -m unittest -q", "environment": "simulated",
                                       "sha": head, "result": "claimed pass (not executed)"}])
        lease.release(str(work), agent)
        previous = record.relative_to(work)
    final = sb.clone("hop4-codex")
    result, found = result_codes(final, final / previous, agent="codex")
    out.append(("hop 4: codex verifies gemini's record", result == "WARN", f"{result} {sorted(found)}"))
    audit_ok, lines = audit_chain(sb.base, ["codex", "claude", "gemini"], keep_clone=final)
    out.append(("audit of record chain", audit_ok, "; ".join(lines)))
    return out


def expect(name: str, base: Path, setup, wanted: str) -> tuple[str, bool, str]:
    """Run one fault scenario in its own sandbox so scenarios cannot affect each other."""
    try:
        base.mkdir(parents=True)
        sb = Sandbox(base)
        sb.seed(with_tool_copy=False)
        result, found = setup(sb)
    except Exception as exc:  # report, never hide
        return name, False, f"error: {exc.__class__.__name__}: {exc}"
    return name, wanted in found, f"{result} {sorted(c for c in found if not c.startswith('OK'))}"


def _base(sb: Sandbox, name: str) -> Path:
    return sb.clone(name)


def fault_wrong_sha(sb):
    repo = _base(sb, "f-wrong-sha")
    data, _ = handoff.build_snapshot(str(repo), "codex", {})
    data["head_sha"] = "0" * 40
    data["remote"]["sha"] = "0" * 40
    path = repo.parent / "f-wrong-sha.md"
    path.write_text(rec.render(data))
    return result_codes(repo, path)


def fault_forged_pushed(sb):
    repo = _base(sb, "f-forged")
    sb.write(repo, "drill_calc.py", "x = 1\n")
    sb.commit(repo, "unpushed", "drill_calc.py")
    data, _ = handoff.build_snapshot(str(repo), "claude", {})
    data["pushed"] = True
    path = repo.parent / "f-forged.md"
    path.write_text(rec.render(data))
    return result_codes(repo, path)


def fault_unpushed_commit(sb):
    repo = _base(sb, "f-unpushed")
    sb.write(repo, "drill_calc.py", "x = 2\n")
    sb.commit(repo, "unpushed", "drill_calc.py")
    data, _ = handoff.build_snapshot(str(repo), "claude", {})
    path = repo.parent / "f-unpushed.md"
    path.write_text(rec.render(data))
    return result_codes(repo, path)


def fault_dirty_sender(sb):
    repo = _base(sb, "f-dirty-sender")
    sb.write(repo, "drill_calc.py", "half done\n")
    data, _ = handoff.build_snapshot(str(repo), "gemini", {})
    path = repo / rec.HANDOFF_DIR / handoff.record_filename(data)
    path.write_text(rec.render(data))
    sb.commit(repo, "record only", str(path.relative_to(repo)))
    sb.push(repo)
    receiver = sb.clone("f-dirty-receiver-side")
    return result_codes(receiver, receiver / path.relative_to(repo))


def fault_dirty_receiver(sb):
    repo = _base(sb, "f-dirty-recv")
    path = write_handoff(sb, repo, "codex")
    sb.write(repo, "scratch.txt", "local edits\n")
    return result_codes(repo, path)


def fault_stale_record(sb):
    repo = _base(sb, "f-stale")
    old = write_handoff(sb, repo, "codex", now=rec.utc_now() - timedelta(days=9))
    write_handoff(sb, repo, "claude")
    return result_codes(repo, old)


def fault_forged_test_result(sb):
    repo = _base(sb, "f-fake-tests")
    path = write_handoff(sb, repo, "codex", completed=["everything"], tests=[{
        "command": "full suite", "environment": "claimed", "sha": sb.head(repo),
        "result": "1000 passed (fabricated)"}])
    return result_codes(repo, path)


def fault_decision_conflict(sb):
    repo = _base(sb, "f-decision")
    write_handoff(sb, repo, "codex", now=rec.utc_now() - timedelta(minutes=3),
                  decisions=[{"id": "D-DRILL-01", "text": "integers only", "status": "ratified"}])
    path = write_handoff(sb, repo, "gemini",
                         decisions=[{"id": "D-DRILL-01", "text": "floats allowed", "status": "proposed"}])
    return result_codes(repo, path)


def fault_same_worktree_writer(sb):
    repo = _base(sb, "f-lease")
    lease.acquire(str(repo), "claude")
    path = write_handoff(sb, repo, "claude")
    return result_codes(repo, path, agent="gemini")


def fault_concurrent_push(sb):
    repo = _base(sb, "f-concurrent")
    path = write_handoff(sb, repo, "codex")
    receiver = sb.clone("f-concurrent-receiver")
    other = sb.clone("f-concurrent-other")
    sb.write(other, "drill_calc.py", "late write\n")
    sb.commit(other, "late write", "drill_calc.py")
    sb.push(other)
    sb.git(receiver, "fetch", "-q", "origin")
    return result_codes(receiver, receiver / path.relative_to(repo))


def fault_edited_record(sb):
    repo = _base(sb, "f-edited")
    path = write_handoff(sb, repo, "codex")
    path.write_text(path.read_text().replace('"open_questions": []', '"open_questions": ["edited"]'))
    return result_codes(repo, path)


def fault_unsafe_notes(sb):
    repo = _base(sb, "f-unsafe")
    data, _ = handoff.build_snapshot(str(repo), "codex", {"unfinished": ["see /Users/" + "someone/x"]})
    hits = rec.scan_unsafe(rec.render(data))
    return ("BLOCK" if hits else "PASS"), {"BLOCK:UNSAFE_CONTENT" for _ in hits[:1]}


FAULTS = [
    ("wrong SHA", fault_wrong_sha, "BLOCK:COMMIT_NOT_FOUND"),
    ("forged pushed=true", fault_forged_pushed, "BLOCK:UNPUSHED_CLAIMED"),
    ("unpushed commit", fault_unpushed_commit, "WARN:LOCAL_ONLY_COMMITS"),
    ("uncommitted work at handoff", fault_dirty_sender, "WARN:LOCAL_ONLY_CHANGES"),
    ("dirty receiving worktree", fault_dirty_receiver, "BLOCK:RECEIVER_DIRTY"),
    ("superseded old record", fault_stale_record, "WARN:RECORD_SUPERSEDED"),
    ("forged test result, correct SHA", fault_forged_test_result, "WARN:TEST_RESULT_UNVERIFIED"),
    ("conflicting decision", fault_decision_conflict, "BLOCK:DECISION_CONFLICT"),
    ("second agent in same worktree", fault_same_worktree_writer, "BLOCK:LEASE_HELD"),
    ("code pushed after handoff", fault_concurrent_push, "WARN:REMOTE_ADVANCED_CODE"),
    ("record edited after commit", fault_edited_record, "BLOCK:RECORD_MODIFIED"),
    ("personal path in notes", fault_unsafe_notes, "BLOCK:UNSAFE_CONTENT"),
]


def cmd_simulate(args: argparse.Namespace) -> int:
    base = Path(tempfile.mkdtemp(prefix="handoff-drill-"))
    print("MODE: SIMULATED - one process plays every agent role; no real Codex/Claude/Gemini ran.")
    rows: list[tuple[str, bool, str]] = []
    try:
        (base / "chain").mkdir()
        sb = Sandbox(base / "chain")
        sb.seed(with_tool_copy=False)
        rows.extend(scenario_chain(sb))
        for index, (name, func, wanted) in enumerate(FAULTS):
            rows.append(expect(f"fault: {name} -> {wanted}", base / f"fault-{index:02d}", func, wanted))
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)
    width = max(len(name) for name, _, _ in rows)
    for name, ok, detail in rows:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail if (args.verbose or not ok) else ''}".rstrip())
    failed = sum(1 for _, ok, _ in rows if not ok)
    print(f"\n{len(rows) - failed}/{len(rows)} checks met expectations (simulated)")
    if args.keep:
        print("sandbox kept (contains only synthetic data)")
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------- Phase 2B helpers


def audit_chain(base: Path, expected: list[str], keep_clone: Path | None = None) -> tuple[bool, list[str]]:
    sb = Sandbox(base)
    clone = keep_clone or sb.clone(f"audit-{os.getpid()}")
    sb.git(clone, "fetch", "-q", "origin")
    sb.git(clone, "reset", "-q", "--hard", f"origin/{DRILL_BRANCH}")
    lines: list[str] = []
    ok = True
    # Order records by the commit that added them (Git history), not by the
    # self-declared timestamp, which has one-second resolution.
    history = sb.git(clone, "rev-list", "--reverse", "HEAD").stdout.split()
    position = {sha: index for index, sha in enumerate(history)}
    records = []
    for path in sorted((clone / rec.HANDOFF_DIR).glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        data, errors = rec.load(path.read_text(encoding="utf-8"))
        if errors or rec.scan_unsafe(path.read_text(encoding="utf-8")):
            ok = False
            lines.append(f"{path.name}: invalid or unsafe")
            continue
        added = sb.git(clone, "log", "--diff-filter=A", "--format=%H", "-1", "--",
                       str(path.relative_to(clone))).stdout.strip()
        records.append((position.get(added, -1), path.name, data))
    records.sort()
    agents = [data["agent"] for _, _, data in records]
    if agents != expected:
        ok = False
    lines.append(f"agents in order: {','.join(agents) or '(none)'} (expected {','.join(expected)})")
    for (_, name, data), (_, _, nxt) in zip(records, records[1:]):
        if not sb.git(clone, "merge-base", "--is-ancestor", data["head_sha"], nxt["head_sha"],
                      check=False).returncode == 0:
            ok = False
            lines.append(f"{name}: next record does not build on it")
    for _, name, data in records:
        if not data["pushed"]:
            ok = False
            lines.append(f"{name}: not pushed at handoff")
        if sb.git(clone, "merge-base", "--is-ancestor", data["head_sha"], "HEAD", check=False).returncode != 0:
            ok = False
            lines.append(f"{name}: recorded head is not on the branch")
    conflicts = handoff.decision_conflicts([(name, data) for _, name, data in records])
    if conflicts:
        ok = False
        lines.extend(conflicts)
    tree = ast.parse((clone / "drill_calc.py").read_text(encoding="utf-8"))  # parsed, never executed
    funcs = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    missing = {"add", "subtract", "multiply"} - funcs
    if len(expected) >= 3 and missing:
        ok = False
    lines.append(f"task functions present: {sorted(funcs & {'add', 'subtract', 'multiply'})}")
    lines.append("note: the agent field is self-declared; real-agent evidence is the operator log")
    return ok, lines


def cmd_setup(args: argparse.Namespace) -> int:
    base = Path(args.dir).expanduser().resolve()
    if base.exists() and any(base.iterdir()):
        print(f"setup: {args.dir} is not empty; choose a new directory", file=sys.stderr)
        return 2
    base.mkdir(parents=True, exist_ok=True)
    sb = Sandbox(base)
    sb.seed(with_tool_copy=True)
    shutil.rmtree(base / "seed")
    (base / "OPERATOR-LOG.md").write_text(OPERATOR_LOG, encoding="utf-8")
    print(f"Synthetic drill ready. Origin: {sb.origin}")
    print("For each hop, clone a fresh worktree and start the agent there, e.g.:")
    for hop, agent in enumerate(("codex", "claude", "gemini", "codex"), start=1):
        print(f"  git clone -q --branch {DRILL_BRANCH} {sb.origin} {base / f'hop{hop}-{agent}'}")
    print(f"Fill in {base / 'OPERATOR-LOG.md'} as you go; audit with:")
    print(f"  python3 {TOOL_DIR / 'drill.py'} audit {base} --expect codex,claude,gemini")
    return 0


OPERATOR_LOG = """# Phase 2B operator log (local only, do not commit)

For every hop record what really ran. A hop counts as REAL only if the named
agent CLI itself executed it; anything else is SIMULATED.

| Hop | Agent CLI + version | REAL / SIMULATED | verify result | Faults injected | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
"""


def cmd_audit(args: argparse.Namespace) -> int:
    expected = [a.strip() for a in args.expect.split(",") if a.strip()]
    ok, lines = audit_chain(Path(args.dir).expanduser().resolve(), expected)
    for line in lines:
        print(f"- {line}")
    print(f"AUDIT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _hash_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return "absent"


def _hash_names(path: Path) -> str:
    try:
        names = sorted(p.name for p in path.iterdir())
    except OSError:
        return "absent"
    return hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]


def cmd_fingerprint(args: argparse.Namespace) -> int:
    """Hashes only, so the output can be diffed before/after without exposing content."""
    home = Path.home()
    fp = {
        "claude_settings": _hash_file(home / ".claude" / "settings.json"),
        "claude_skills_names": _hash_names(home / ".claude" / "skills"),
        "codex_config": _hash_file(home / ".codex" / "config.toml"),
        "codex_prompts_names": _hash_names(home / ".codex" / "prompts"),
        "gemini_settings": _hash_file(home / ".gemini" / "settings.json"),
        "gemini_commands_names": _hash_names(home / ".gemini" / "commands"),
    }
    if args.project:
        refs = subprocess.run(["git", "-C", args.project, "for-each-ref", "--format=%(refname) %(objectname)"],
                              capture_output=True, text=True)
        fp["project_refs"] = hashlib.sha256(refs.stdout.encode()).hexdigest()[:16]
        status = subprocess.run(["git", "-C", args.project, "status", "--porcelain"],
                                capture_output=True, text=True, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        fp["project_status"] = hashlib.sha256(status.stdout.encode()).hexdigest()[:16]
    pids = subprocess.run(["lsof", "-nP", "-iTCP:8000", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    fp["port_8000_pids"] = ",".join(sorted(pids.stdout.split())) or "none"
    print(json.dumps(fp, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sim = sub.add_parser("simulate", help="Phase 2A simulated drill in a temporary directory")
    sim.add_argument("--keep", action="store_true")
    sim.add_argument("--verbose", action="store_true")
    sim.set_defaults(func=cmd_simulate)
    setup = sub.add_parser("setup", help="Phase 2B: create a synthetic origin for real agents")
    setup.add_argument("dir")
    setup.set_defaults(func=cmd_setup)
    audit = sub.add_parser("audit", help="Phase 2B: audit the record chain")
    audit.add_argument("dir")
    audit.add_argument("--expect", default="codex,claude,gemini")
    audit.set_defaults(func=cmd_audit)
    fp = sub.add_parser("fingerprint", help="hash user-level agent settings, project refs and port 8000")
    fp.add_argument("--project")
    fp.set_defaults(func=cmd_fingerprint)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
