# CH-01 Step 1: honest AI Chunk results (mock fallback, mode, coverage)

Owner-approved minimal fix after the CH-00 read-only audit (2026-11-06).
Branch `claude/ch-01-step1-chunk-honesty`, stacked on Draft #83
(`c89b94b`). #84 and #85 do not touch these files. Synthetic data only; no
AI provider is called. Study Bank, SRS, Guided and the seven-stage flow are
unchanged.

## What changed

- **R1, the mock no longer invents a candidate.** `DeterministicMockChunkAdapter`
  used to take the first four words of the first sentence whenever none of
  its built-in phrases matched. It now proposes nothing. No random or
  heuristic replacement was added. `proposal_version` moves from 1 to 2, so
  new candidates show that the behaviour changed. Stored candidates keep
  their version.
- **R1, every batch says how it was proposed.** `AiChunkBatch.proposal_mode`
  is `mock` or `model`.
  - The API sets it on each create or read response from the service's
    current adapter (`AiChunkOrganizerService.describe`).
  - It is not persisted, so there is no migration. Stored and replayed
    batches are labelled when they are returned.
  - Only the mock exists today, so every batch says `mock`. Step 2 must
    persist the mode once a real provider can produce batches.
- **R1, the UI does not pass the mock off as AI curation.**
  - A mock batch shows: "Offline mock, not AI curation: no AI provider is
    enabled. The mock only recognises a small built-in phrase list…"
  - An empty mock batch says the mock found none of its phrases, "This does
    not mean the text has no useful chunks."
  - The page subtitle no longer claims a mode.
- **R2, Cloud coverage is visible.** The Cloud lesson view still sends at
  most 12 sentences, the existing server contract. It now always shows the
  coverage:
  - for a long lesson: "Only 12/80 sentences are analysed (the first 12). This
    is not a full-lesson recommendation";
  - for a short one: "All 5 sentences are analysed."

  The button now reads "Find chunks" instead of "Find chunks in this lesson".

## Not changed

- **R3: Learn Live Dialogue position bias** (`liveDialogue.ts:63-89`, and
  `learnerGoal` at `:48-56`).
  - It still takes the first 5 lesson chunks in transcript order, and the
    goal names the first one.
  - This is accepted behaviour and stays as it is by Owner decision. It is
    Learn Live Dialogue, not P07 Sync Shadowing.
  - It is recorded here for Step 2.
- **Unchanged contracts:**
  - the 12-sentence / 6000-character / 8-candidate server limits;
  - the decision API (accept / edit_and_accept / edit / reject);
  - Study Bank de-duplication;
  - Chunk Memory sync.

## New findings

- **Low (pre-existing, not fixed): the German mock phrase "im großen und ganzen"
  can never match.** `normalize_phrase` casefolds `ß` to `ss`, but the
  built-in key keeps `ß`. It is pinned by a strict `xfail` test, so fixing it
  will turn that test red until the marker is removed. It affects only the
  offline mock.

## Tests

- **`backend/tests/test_ai_chunk_mock_honesty.py`:** 9 passed, plus 1 strict
  xfail.
  - EN/DE 40-sentence synthetic transcripts with no built-in phrase return no
    candidates.
  - Phrases at sentences 1, 20 and 38 are all found, in both EN and DE.
  - A phrase moved to sentence 40 is still found.
  - Personal API: an empty mock batch, labelled `mock` on create and read.
  - A 12-sentence Library passage takes the phrase in sentence 12 and never
    sentence 1.
  - A non-mock adapter is labelled `model`.
  - Cloud API batches are labelled `mock`.
- **`AiChunkOrganizer.library.test.tsx`** (+5 tests):
  - "Only 12/80", with exactly the first 12 ids sent;
  - "All 5 sentences";
  - the mock notice;
  - the honest empty mock message;
  - no notice for a model batch.

  `CloudLibrary.test.tsx` only has the new button name.
- **Mutation:** 9 of 9 killed:
  - fallback restored;
  - mock loses its mode;
  - every adapter labelled mock;
  - create not labelled;
  - read not labelled;
  - coverage always "all";
  - notice removed;
  - notice on any batch;
  - empty mock message reverted.
