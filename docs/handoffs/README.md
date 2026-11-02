# Handoff records

Each file in this directory (except this README) is one structured handoff
record written by `scripts/agent_handoff/handoff.py snapshot`. The protocol is
described in [docs/agent-protocol/HANDOFF-PROTOCOL-v1.md](../agent-protocol/HANDOFF-PROTOCOL-v1.md).

File name: `<UTC timestamp>-<agent>-<branch slug>.md`, for example
`20261102T091500Z-codex-codex-p8-05b-email-login.md`. Records are never edited
after they are committed; a newer record supersedes an older one.

## Format

A short human summary followed by exactly one fenced JSON block whose info
string is `json handoff-record`. All fields are required and no other fields
are allowed:

| Field | Type | Written by |
| --- | --- | --- |
| `protocol` | `"lai-handoff/1"` | snapshot |
| `created_at` | UTC timestamp `YYYY-MM-DDTHH:MM:SSZ` | snapshot |
| `agent` | lowercase id (`codex`, `claude`, `gemini`, ...) | `--agent` |
| `task` | `{"issue": number or null, "title": text}` | notes |
| `branch` | current branch (detached HEAD is refused) | snapshot |
| `head_sha` | full 40-character commit SHA | snapshot |
| `remote` | `{"name": "origin", "sha": remote branch tip or null}` | snapshot |
| `pushed` | true only if the tree is clean and the remote contains HEAD | snapshot (computed, never declared) |
| `uncommitted` | paths with uncommitted changes; never delivered | snapshot |
| `tests` | list of `{"command", "environment", "sha", "result", "url" (optional)}` | notes |
| `completed`, `unfinished`, `next_steps`, `open_questions` | lists of short text | notes |
| `decisions` | list of `{"id", "text", "status": "proposed" or "ratified"}` | notes |
| `supersedes` | file name of the record this one replaces, or null | notes |

There is deliberately no status field: task status belongs to the GitHub Issue.

What must never appear in a record (the repository is public): absolute paths
under a user's home directory, real learning data, API keys or tokens, cookies,
session transcripts, and personal information. Refer to worktrees and files by
repository-relative path only.

## Example (synthetic)

# Handoff: codex on example/handoff-demo

- Created: 2026-11-02T09:15:00Z
- Task: #75 - synthetic example only
- Head: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`
- Delivered to origin: yes

## Record

```json handoff-record
{
  "protocol": "lai-handoff/1",
  "created_at": "2026-11-02T09:15:00Z",
  "agent": "codex",
  "task": {"issue": 75, "title": "synthetic example only"},
  "branch": "example/handoff-demo",
  "head_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "remote": {"name": "origin", "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "pushed": true,
  "uncommitted": [],
  "tests": [
    {
      "command": "python -m pytest -q scripts/agent_handoff/tests",
      "environment": "ubuntu-latest py3.12 (hosted CI)",
      "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "result": "all passed",
      "url": null
    }
  ],
  "completed": ["step 1 of the synthetic task"],
  "unfinished": ["step 2 of the synthetic task"],
  "next_steps": ["implement step 2; re-run the handoff tool tests"],
  "decisions": [{"id": "D-EXAMPLE-01", "text": "Example decision text", "status": "proposed"}],
  "open_questions": [],
  "supersedes": null
}
```
