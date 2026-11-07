# Full-transcript ranking spec (CH-01/02 design revision)

**Status: design plus a pure-algorithm validation slice.** The slice is
`backend/chunk_curation_lab/`, isolated from production. Not production
ready. Every rule below that the slice implements names the function that
does it.

## 1. Inputs and trust

- **Library snapshot** (`snapshot.LibrarySnapshot`). It is built by the
  server from the owner's authorized Library item and is the only source of
  sentence text, sentence IDs, timestamps, the media/lesson ID and the
  language. `transcript_revision` is a content hash of
  `[id, text, start, end]` for every sentence, because the current Lesson
  schema has no revision field.
- **Provider observations** (`provenance.ProviderObservation`). These are
  untrusted. They carry:
  - the chunk text;
  - a cited sentence ID and the window index;
  - an origin claim;
  - 0–3 scores for the six semantic dimensions;
  - up to 2 variations;
  - any claimed media ID, quote or timestamps. These claims are ignored and
    only counted when they disagree with the snapshot.
- **Saved identities.** A map from `normalized identity` to `active`, read
  from the owner's Study Bank and used only for novelty.

## 2. Windowing (`windowing.build_windows`)

| Rule | Detail |
|---|---|
| Unit | Whole sentences, so a fitting sentence is never split across windows |
| Budget | Measured in tokens, not sentences. `budget = min(provider.context × 0.5, max_window_tokens) − prompt_overhead − max_output_tokens` |
| Token estimate | The provider tokenizer when one is available; otherwise `ceil(chars / 3.2)` for EN and `ceil(chars / 2.8)` for DE, where German compounds tokenise longer |
| Adaptation | Short-sentence podcasts get more sentences per window and long-sentence lectures fewer. "About 40 sentences" was only a starting test value |
| Oversized sentence | Split at clause punctuation, then at word boundaries. Each piece keeps the `sentence_id`, `part/parts` and its exact character range |
| Unsplittable | If a single word is larger than the budget, the whole sentence is `skipped: sentence_exceeds_window`, never truncated |
| Empty sentence | `skipped: empty_sentence`, excluded from the analysable count |
| Overlap | The last `overlap` (default 1) citable segments of the previous window are repeated as context only. They are dropped if they would not fit. Context cannot be cited |
| Ordering | Windows follow transcript order. That is used only to build windows, never to rank |

## 3. Coverage (`coverage.build_manifest`)

A sentence is **covered** only when **every** one of its segments was
citable in a window that finished successfully.

- Context appearances and overlap never count.
- Each sentence counts once.

The manifest reports:

- `total_sentences`, `analysable_sentences` and `covered_sentence_ids`;
- `skipped` ranges of consecutive sentences with the same reason
  (`not_processed`, `provider_timeout`, `sentence_exceeds_window`,
  `empty_sentence`, and so on);
- the counts of windows done and failed.

`status` is one of:

- **`complete`**: every analysable sentence is covered, no window failed,
  and every window is done;
- **`partial`**: anything else, including budget exhaustion
  (`not_processed`);
- **`empty`**: no analysable sentence.

There is no path from a gap to `complete`. It is mutation-tested as "budget
gap counted complete" and "partial reported ready".

## 4. Verification (`provenance.verify_observation`)

Each observation is checked in this order. The first failure rejects it, with
a counted reason.

0. **Shape check (Round 3, M3).** `shape_error` checks container and
   element types before any field is used:

   | Field | Requirement | Reason when it fails |
   |---|---|---|
   | `window_index` | int (bool rejected) | `invalid_window_index` |
   | `text` | str | `invalid_text` |
   | `evidence_sentence_id` | int or None (bool and float rejected) | `invalid_sentence_id` |
   | `claimed_origin` | str | `invalid_origin` |
   | `scores` | a mapping with str keys from the six semantic dimensions; values int 0–3 or None | `invalid_scores` |
   | `variations` | a list or tuple of at most 2 str, each non-blank and at most 200 characters | `invalid_variations` |
   | claimed item and quote | str or None | `invalid_claims` |
   | claimed timestamps | number or None | `invalid_claims` |

   Nothing is coerced, filtered, truncated or repaired. Any violation
   rejects the whole observation.
1. `claimed_origin != source_extracted` → `variation_not_source`.
2. Empty text, more than 120 characters, or fewer than 2 words →
   `invalid_text`.
3. Unknown window → `unknown_window`.
4. Missing or unknown sentence → `unknown_sentence`.
5. The sentence is not citable in that window (context only, or another
   window's) → `not_citable_in_window`.
6. **Grounding stays inside the window's citable characters (Round 3,
   M2).**
   - The citable ranges are the character ranges of that sentence's parts
     in `window.citable`. Consecutive parts in the same window are joined
     across the whitespace between them.
   - Context parts are never included.
   - Grounding (the production `_ground_phrase`) runs **inside each range,
     in character order**, and the first match inside a range is the span.
     So a phrase that occurs twice in a sentence is grounded at the
     occurrence this window actually saw.
   - If the phrase is in the sentence but in no citable range →
     `outside_citable_range`. This covers a phrase that is only in another
     part, only in a context part, or split across two windows.
   - If it is not in the sentence at all, or the span fails Study Bank chunk
     rules → `ungrounded`.
   - There is never repair, relabelling or "completing" a span from the full
     sentence, and the snapshot is never modified.
7. Accepted. The evidence is copied from the **snapshot**: item, lesson,
   revision, sentence text and fingerprint, timestamps, and the grounded
   character range. The display text is the exact source substring.
   - `reliability` is 3 when the provider text equals the source span
     ignoring case, so a sentence-initial capital is not penalised.
   - It is 2 when the match needed punctuation or whitespace normalisation.

**Variations** (`origin = ai_variation`) are kept only as text on the
candidate. They are never evidence and never a recommendation.

**Exception boundary (Round 3, M3).** The reduce verifies each observation
inside its own `try`. An unexpected exception rejects only that observation,
as `verification_error`. It never aborts the reduce and never makes the
observation valid.

## 5. Identity and de-duplication

`identity = prepare_chunk_text(source_span, language).normalized_text`. This
is the exact function the Study Bank uses, so curation de-duplication
equals Study Bank de-duplication.

- **English** is case-folded.
- **German keeps case**, because nouns are capitalised. As a consequence a
  sentence-initial "Auf jeden Fall" and a mid-sentence "auf jeden Fall" are
  **different identities**, in the Study Bank today and in curation. This is
  recorded as risk R-M4 and an Owner decision; the slice does not change it.

Observations with the same identity form one candidate.

- **Sources** are keyed by `(item, sentence, span)`; distinct keys are all
  kept.
  - **Merging one key (Round 3, M1).** All observations of a key point at the
    same server-verified span, so every field except `reliability` is
    identical; this is asserted.
  - The merged `reliability` is the **maximum** of the server-verified
    values. Reliability only records that the server saw the provider text
    equal the source text, ignoring case. It is not a provider claim, it
    does not change the span or the source, and it does not touch any
    semantic score, so taking the maximum cannot raise unproven quality.
  - It replaces the earlier dictionary in which the last record won.
- **Source order** is: higher reliability, then timestamps present, then
  sentence fingerprint, then span. **Never by transcript position or
  arrival order.** The primary source is the first in this order.

## 6. Quality model (`scoring.py`)

### 6.1 Dimensions

All dimensions are integers 0–3.

| Dimension | Who scores | Definition | 0 | 3 | Weight |
|---|---|---|---|---|---|
| reusability | scorer | Used in everyday speech across many situations | Topic jargon or a one-off | Very frequent in daily talk | 22 |
| naturalness | scorer | Idiomatic and grammatical as a native speaker would say it | Unnatural or broken | Fully natural | 18 |
| transfer | scorer | Can be moved into new speaking scenarios with small changes | Bound to this video | Fits many CH-03 scenarios | 14 |
| context_completeness | scorer | Meaning is clear from the chunk and its sentence | Unclear without the video | Self-explanatory | 12 |
| independence | scorer | Works without proper nouns or this video's specifics | Depends on names or places | Fully independent | 10 |
| level_fit | scorer | Matches the lesson level (B1/B2/C1) | Far off | Right at level | 10 |
| novelty | **server** | Not already in the owner's Study Bank | Saved and active | Not saved (archived = 1) | 7 |
| evidence_reliability | **server** | How exactly the text matches its source (§4) | — | Exact, ignoring case | 7 |

The weights are integers summing to 100.

- **Why these weights.** Reusability and naturalness carry 40%, because the
  goal is everyday spoken transfer. Transfer and context come next because
  they feed CH-03 practice. Independence and level matter but are partly
  gated (§6.3).
- **Novelty is low**, because reviewing something the learner already has
  still has some value.
- **Evidence reliability is low**, because every candidate is already
  grounded; this only separates exact matches from loose ones.

The weights are configuration and go into `curation_config_hash` (see
CURATION-JOB-STATE-MACHINE).

### 6.2 Aggregation and uncertainty

- **Per dimension, two levels (Codex F1 remediation).**
  1. For each distinct source (evidence key), take the **minimum** of its
     non-missing observed scores.
  2. Then take the lower median across distinct sources.

  `None` is ignored at both levels; an entirely missing dimension stays
  missing. The source minimum is order-independent and idempotent:
  repeating any existing observation 1, 2 or 100 times cannot change that
  source's vote. The former within-source median was order-independent but
  not multiplicity-independent: duplicating a high report could raise it.
  Different sources still each receive exactly one vote in the lower median.

  This is deliberately conservative. A new conflicting low score may lower
  a recommendation; it cannot be erased by repeating higher scores. It
  does not prove a model's semantic judgement correct. Real scorer quality
  remains a separate release gate. Raw `observations` is a diagnostic count
  and may grow on replay; recommendation status, score and provenance must
  not change when an existing report is repeated.
- **Score.** `Σ weightᵢ·valueᵢ / Σ weightᵢ` over the dimensions present,
  computed exactly as a `Fraction`, on a 0–3 scale.
- **`weight_coverage`.** The sum of the weights present, divided by 100.
- **`unscored`.** A candidate is unscored when `reusability` or
  `naturalness` is missing, or when the weight coverage is below 0.80. It
  gets **no** score and can **never** be recommended.
  - If every candidate is unscored, the batch status is `unscored`.
  - This is exactly what the offline mock produces: no fake quality.

### 6.3 Gates and floor

- **Gates.** `naturalness = 0` or `independence = 0` gives status `gated`,
  excluded whatever the total.
- **Quality floor.** `score ≥ 1.8` (on 0–3) is needed for recommendation.
  Below that the status is `below_floor`.

### 6.4 Ranking and ties

The rank key uses **quality only**, in this order:

1. score, descending;
2. the minimum of the core dimensions, descending (robustness);
3. weight coverage, descending (confidence);
4. the number of distinct source sentences, descending (corroboration).

**Ties.** Candidates with an identical key form a **tie group**.

- Groups are taken best first while the total stays ≤ 12.
- A group that **straddles** the limit is excluded **whole**, with status
  `not_selected_tie` and the note `tie_group_excluded_at_limit`. Neither the
  alphabet, a hash, nor first appearance ever decides inclusion.
- Inside a selected group the display order is by identity, and each member
  lists `tied_with`, so the UI can show them as equal.

### 6.5 Count

- The target is 8–12 with a hard maximum of 12.
- Fewer than 8 (including 0) is valid and gets the note
  `few_high_quality_candidates`.
- The result is never padded with below-floor, gated or unscored
  candidates.

## 7. Result status (`reduce.reduce_candidates`)

Statuses are applied in this precedence:

1. `empty`: the coverage is empty.
2. `partial`: the coverage is not complete. Findings so far are shown and
   labelled partial.
3. `unscored`: there are candidates but none has a real score.
4. `ready`.

"No usable candidates" is `ready` with zero recommendations and an
explanatory note, not an error.

## 8. Order-independence argument

**Claim.** For a fixed set of observations, the result does not depend on
their order.

**Argument.** Grouping uses a dictionary keyed by identity; groups are read
only through:

- **sets:** source keys, variations;
- **order-free aggregates:** per-source `min`, lower median across sources, `max`, `len`,
  and exact `Fraction` arithmetic;
- **`sorted()` on total keys:** sources (quality, then fingerprint), tie
  groups (quality key), members inside a group (identity), and the other
  list (identity).

No step reads "first" or "last" from the input. Rejection counts are a
multiset.

**Tests.**

- All 720 orders of a 6-observation set, including a duplicate, a rejected
  one and a variation claim, give one fingerprint.
- 200 seeded shuffles of a 400-sentence EN and a 400-sentence DE run match
  the reference exactly.

## 9. Position fairness

Fairness is shown by **invariance**, not by per-window quotas.

- Moving the same high-quality set to the start, middle or end, or putting
  its three parts in any of the 6 region orders, selects the same set with
  the same scores in EN and DE.
- Low-quality candidates placed first never beat high-quality ones placed
  last.
- No test or rule asserts "N per window".
