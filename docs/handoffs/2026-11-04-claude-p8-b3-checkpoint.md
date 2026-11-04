# Checkpoint: P8-B3 cloud dictionary + Cloud AI Chunk (Claude Code, 2026-11-04)

Manual checkpoint (no `handoff.py` lease; that tool lives only in Draft PR #77).
Status of record is Issue #75; this file is a pointer, not a status source.

## Stack (all Draft, nothing merged or deployed)

| PR | Branch | Head | Base |
| --- | --- | --- | --- |
| #81 B-1 | `claude/p8-b1-library-reliability` | `dfd243b` (accepted) | main |
| #82 B-2 | `claude/p8-b2-guided-listening` | `6082c9c` | #81 |
| #83 B-3 | `claude/p8-b3-cloud-dictionary-chunks` | `dd48f08` (code; this file on top) | #82 |

Worktree: `~/Downloads/language-ai-claude-p8-b3`. Merge order: #81 → #82 → #83,
only after owner acceptance.

## Owner decisions in force

1. **AI Chunk accept (2026-11-04):** keep the P8-03B2 behaviour, where accept
   saves to the Study Bank. B-3 adds only Library-sourced generation and a
   save-only `edit` action (cloud only).
2. **Dictionary:** synthetic fixture only. No Kaikki import and no DQ-02
   changes. Cloud never opens the Personal dictionary.
3. All earlier decisions still hold:
   - GitHub comments are drafts only;
   - Starlette stays on a locked, tested version;
   - Listening position follows the latest playback action;
   - the Cloud → Personal gates stay closed.

## Evidence

- **Local** (macOS, Python 3.11, disposable PostgreSQL 17):
  - backend 858 passed, 9 skipped (B-2 had 821 / 9);
  - frontend 436 passed, 57 files (B-2 had 429 / 56);
  - lint, typecheck and build clean.
- **Hosted CI:**
  - `2b65c67`: run 41770844631;
  - `adf327e`: run 41771814858;
  - `dd48f08`: run 41772237285 (858/9, 436).
  - All succeeded.
- **Mutation:** 24 mutants, all killed: 14 for the feature, 8 for the review
  fixes and 2 for the generation key.
- **Independent review:** a separate reviewer agent (no human or
  different-model review).
  - `2b65c67`: 2 Medium and 5 Low. All fixed; one Low is documented as
    intended.
  - `adf327e`: 1 Medium. The fixed per-lesson key stranded lessons; it was
    replaced by a per-attempt key.
  - `dd48f08`: no findings.

## Risks / not done

- **B-4:** dictionary and Library saves into the Study Bank with provenance and
  de-duplication, plus the SRS and Daily Review links.
- **Not done:** a real dictionary release, cloud TTS, and cloud recall and
  chunk memory (these need owner-scoped stores).
- **Migration 0011 down:** run it as owner + BYPASSRLS. A restricted role
  still refuses through the constraint, but without the message.
- **Generation key:** it is lost when the component remounts, so a retry after
  navigating away can create a second batch. That costs nothing with the mock.

## Next

B-4: Study Bank from cloud sources. Branch from #83's verified HEAD.
