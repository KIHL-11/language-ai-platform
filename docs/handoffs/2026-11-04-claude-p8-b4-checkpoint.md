# Checkpoint: P8-B4 cloud Save Word → Study Bank → Daily Review (Claude Code, 2026-11-04)

Manual checkpoint (no `handoff.py` lease; that tool lives only in Draft PR #77).
Status of record is Issue #75; this file is a pointer, not a status source.

## Stack (all Draft, nothing merged or deployed)

| PR | Branch | Head | Base |
| --- | --- | --- | --- |
| #81 B-1 | `claude/p8-b1-library-reliability` | `dfd243b` (accepted) | main |
| #82 B-2 | `claude/p8-b2-guided-listening` | `6082c9c` | #81 |
| #83 B-3 | `claude/p8-b3-cloud-dictionary-chunks` | `c89b94b` (accepted) | #82 |
| #84 B-4 | `claude/p8-b4-cloud-study-bank` | `43b3464` (code; this file on top) | #83 |

Worktree: `~/Downloads/language-ai-claude-p8-b4`.

## What B-4 delivered

- **The cloud flow** Listening → subtitle word → Dictionary → Save Word now
  reaches the owner's PostgreSQL Study Bank and Daily Review.
- **Reuse.** M5.2's service runs on owner-bound Library and Study Bank views
  plus the B-3 cloud dictionary. It never touches Personal stores.
- **Trust boundary.** The client names only the sentence and the surface
  word. The server derives lemma, meaning and provenance.
- **Save semantics** come from the existing upsert and are proven in cloud:
  - lemma dedupe and provenance merge;
  - no reset of SRS or notes;
  - no implicit restore;
  - new words enter Daily Review;
  - concurrent saves stay consistent.
- **Cloud is stricter than Personal:**
  - an ambiguous multi-lemma lookup is refused;
  - status uses the upsert's exact identity key;
  - a malformed lesson returns 422.
- **AI Chunk accept (P8-03B2)** is verified unchanged; no gap was found.

## Evidence

- **Local** (macOS, Python 3.11, disposable PostgreSQL 17):
  - backend 880 passed / 9 skipped (B-3 had 858);
  - Personal M5.2: 11 passed;
  - frontend 441 passed / 57 files;
  - lint, typecheck and build clean.
- **Hosted CI:**
  - `216c508`: run 41776475351;
  - `43b3464`: run 41777863727, SUCCESS (880/9, 441).
- **Mutation checks:** 17 mutants, all killed.
- **Independent review** (separate reviewer agent; no human or
  different-model review):
  - `216c508`: 2 Medium, 4 Low. All fixed.
  - `43b3464`: nothing at Medium or above. One Low on over-refusal and one
    pre-existing note are documented.

## Risks / not done

- **Identity rule:** casefolded lemma with no part of speech, so German
  Arm/arm share a card. This is shared with Personal and belongs with DQ-03.
- **Over-refusal:** some ambiguous words are refused (safe; see the doc).
- **Provenance cap:** 1,000 sources per card. New sources beyond that are a
  422.
- **Not done:** study candidates in cloud, a real dictionary release, DQ-03.
- **Issue #58:** stays open until the owner accepts the stack.

## Next

- **B-5:** Daily Review concurrency across tabs, and each learner's time zone.
- **B-7:** a full cloud E2E in a browser.
- Branch from #84's verified HEAD.
