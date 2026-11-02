# Source of truth

Every kind of project fact has exactly one authoritative place. Anything else
may link to it but must not restate it as its own claim.

| Fact | Authority | Everything else |
| --- | --- | --- |
| Delivered code | The branch and commit on GitHub (`origin`) | Unpushed commits and uncommitted files are local only and never "done". |
| Task status (pending, in progress, blocked, done) | The GitHub Issue for the task (#75 for Phase 8 and its workstream issues) | Trackers and handoff records link to the issue. Handoff records have no status field. |
| Release dimensions (IMPLEMENTED, HOSTED CI, INDEPENDENT REVIEW, REAL-MAC TESTED, REAL-PROVIDER VERIFIED, PRODUCTION READY) | The Issue, backed by evidence links | A dimension is only complete with a CI run, PR or commit reference. |
| Test evidence | A hosted CI run URL with its commit SHA; local runs must name the SHA and environment | Evidence for a different SHA is stale until re-run. |
| Engineering rules | `AGENTS.md` | `CLAUDE.md` and `GEMINI.md` only import it. |
| Session continuity | Handoff records in `docs/handoffs/` | Records describe and point; they do not decide or authorise anything. |
| Durable decisions | The design doc or tracker for the work, each with an id | Handoff records may reference or propose decision ids; an id is never reused with different text. |

Out-of-repository notes (personal memory folders, chat archives) are optional
local context. They are not available to other agents or machines and never
override the repository or GitHub.

## Precedence when sources disagree

1. Git remote state (what is actually on `origin`).
2. GitHub Issue state and its linked evidence.
3. Repository documents (trackers, design docs).
4. Handoff records.

When a lower source contradicts a higher one, the lower one is wrong and must
be corrected; it is never used to "update" the higher one automatically.

## Known divergence at protocol introduction (2026-11-02)

Recorded here so no agent resolves it silently:

- PR #76 (`codex/p8-feature-complete-integration`, head `25c8f51`) was merged
  into `main` as `eb7e902f89bae78c9ed694a0113420557a15368b`.
- Merging the PR does not complete Issue #75. The integration tracker
  `docs/PHASE8-FEATURE-COMPLETE-TRACKER.md` lists foundation work F as
  "In progress" and workstreams A–E and F integration as "Pending", which
  matches the code merged so far.
- The Issue #75 checkpoint comment from 2026-10-30 still says no integration PR
  exists. That comment predates the merge; updating it is the owner's call.
- No tracker row may move to Completed/Done/Implemented without an evidence
  reference. `handoff.py sot-check` enforces this for the tracker.
