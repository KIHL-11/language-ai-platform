# P8-B4: Cloud Listening → Dictionary → Save Word → Study Bank → Daily Review

Workstream B increment B-4, stacked on B-3 (Draft PR #83, `c89b94b`). Synthetic
EN/DE lessons, the synthetic cloud dictionary, the mock AI and a disposable
PostgreSQL only. There are no DQ-02 changes and no DQ-03 sense selection.

## What existed and what changed

- **What already existed.** M5.2's `ContextualWordService` validated the word
  against the Library sentence, looked it up, and built the `StudyItemInput`
  with full media provenance. The PostgreSQL Study Bank upsert already
  provided the required save semantics:
  - **Lemma identity:** a word's identity is its dictionary lemma
    (`normalized_identity`), so different forms share one card.
  - **Concurrency:** `INSERT … ON CONFLICT DO NOTHING`, then `FOR UPDATE`.
  - **Provenance merge:** a new source is appended, and an identical one is
    never added twice.
  - **No resets:** review state, due time, counts and notes are never
    touched.
  - **Archive:** an archived card stays archived.
  - **Daily Review:** a new card starts with `ReviewState.new(now)`, so it is
    due at once.
- **What blocked cloud.** The status and save routes were behind
  `PersonalStorageDependency`.
- **What changed.** The two routes now resolve `StudyBankAccess`.
  - **Cloud** builds the same service from three owner-bound pieces:
    - `OwnerLibraryView`: the owner's PostgreSQL Library, where another
      owner's item is a 404;
    - `OwnerStudyBankView`: the owner's PostgreSQL Study Bank, which refuses
      any other `user_id`;
    - the B-3 cloud dictionary.
  - The Personal Library, Study Bank and dictionary are never reached; a test
    sets them to raise on access.
  - **Personal** is unchanged and keeps the M5.2 tests.
- **Trust boundary.** The client sends only `user_id`, which must equal the
  session (403 otherwise), `sentence_id` and the surface `word`.
  - Extra fields such as `lemma`, `meaning` or `provenance` are rejected
    with a 422.
  - The server derives the lemma, part of speech, meaning, example and the
    full provenance itself: item, lesson, URL, title, sentence text,
    translation and timing.
- **Fail closed.** Without a session the route returns 401. Without the cloud
  dictionary it returns 503 `cloud_dictionary_not_configured`. Without the
  cloud Study Bank or Library it returns 503
  `cloud_study_bank_storage_not_configured`.
- **Gates.** The six B-2 Cloud → Personal gates stay closed, and
  `study-candidates` stays closed too.

## Cloud-only strictness (review fixes)

- **Ambiguous lemmas.** If the dictionary cannot decide between different
  lemmas in this sentence (for example "She leaves at nine." could be leaf or
  leave), cloud returns 422 `ambiguous_lemma` instead of guessing a card. The
  panel says why and offers no Save Word.
  - Several senses of one lemma are still saved.
  - Sense choice is DQ-03.
  - Personal M5.2 keeps its behaviour. A test pins that.
- **Status matches the save.** The status check reads the exact card the
  upsert would write, keyed on kind, both languages and the normalized lemma
  (`get_by_identity`, which uses the unique index). It no longer scans every
  word or ignores the target language.
- **A lesson that cannot be provenance** (title over 1,000 characters, or an
  over-long URL) is a 422 in cloud, not the session middleware's misleading
  503.
- **Provenance cap.** A card holds at most 1,000 sources. Saving a new source
  beyond that is a controlled 422 and changes nothing; re-saving a known
  source still works. Raising the cap is a later decision.
- **Known limit, unchanged:** the identity rule casefolds the lemma and
  ignores part of speech, so German pairs such as Arm/arm share a card. This
  is the shared Study Bank identity used by Personal too; changing it belongs
  with DQ-03.

## Dictionary UI

- **Cloud Listening** passes the session owner as `contextualOwnerId`, never a
  local learner id.
- **`DictionaryPanel`'s existing contextual flow** works in cloud through
  `apiFetch`, with the session, CSRF and owner-bound response:
  - saved-status check;
  - Save Word;
  - an explicit Restore for an archived word, which sends a PATCH with its
    `expected_revision`. If another tab changed the card first (409), the
    panel reloads its status, so the retry uses the current revision.
- **Account changes.** Switching account remounts the workspace, so no word
  state carries over.

## AI Chunk → Study Bank (reused unchanged)

The P8-03B2 accept / edit_and_accept transaction already writes through the
same upsert. Tests confirm:
- the chunk lands in the current owner's Study Bank only;
- accepting the same chunk from a second lesson reuses the card, appends its
  provenance and keeps SRS;
- an archived chunk stays archived;
- Daily Review queues and rates it through the API.

No transaction or scheduler code changed; no real gap was found.

## Tests

- **`test_cloud_contextual_words.py`** (18 tests), all with the Personal stores
  set to raise. The review added: ambiguous lemma refused (noun and verb
  stay separate cards), Personal still saving it, status matching the target
  language, the malformed-lesson 422, and the provenance cap. The full list:
  - EN save with exact provenance; DE inflection under its lemma;
  - another form and another lesson reusing the card and merging provenance,
    with no duplicate for an identical source;
  - review state and notes surviving a re-save;
  - archive surviving both a re-save and a new source, until an explicit
    Restore;
  - a new word queued in Daily Review and rated, with a stale rating getting
    409;
  - 8 concurrent saves making one card;
  - forged lemma, meaning or provenance (422) and a forged owner (403);
  - a word not in the sentence, a word with no entry, or a bad sentence
    getting 422;
  - another owner getting 404 and seeing an empty bank;
  - RLS checked with a NOSUPERUSER NOBYPASSRLS role;
  - fail-closed responses;
  - the seven closed gates;
  - the owner-bound view refusing other users.
- **`test_cloud_ai_chunk_study_bank.py`** (4 tests): cross-lesson provenance
  without SRS reset, archive kept, the Daily Review queue and rating via the
  API, and owner isolation.
- **Frontend `CloudLibrary.test.tsx`:**
  - Save Word as the session owner with CSRF, where the client sends only
    `user_id`, `sentence_id` and `word`, and no local learner id;
  - an archived word offering an explicit Restore with its revision;
  - an account switch dropping the word state, including a status response
    still in flight when the account changes;
  - no Save Word for an ambiguous lemma;
  - Restore after a 409 reloading the status and succeeding with the new
    revision.
- **Mutation check:** 17 guards were removed one at a time, and each fails
  the tests: 11 for the feature and 6 for the review fixes.

## Independent review (`216c508`)

A separate reviewer agent found no Critical or High issues. It confirmed
there is no cloud path to the Personal stores, no forgery, and that the
`_upsert` semantics hold, including 6-thread, cross-lesson, mixed-form saves.

| Severity | Finding | Fix |
| --- | --- | --- |
| Medium | ambiguous lookup could merge leaf/leave and split one word | cloud refuses multi-lemma ambiguity; UI hides Save Word |
| Medium | status ignored target language and scanned every word | identity lookup on the unique key |
| Low | malformed lesson gave 503 `cloud_session_unavailable` | cloud 422 |
| Low | Retry Restore kept a stale revision | reload status on 409 |
| Low | concurrency test mutated app state per thread | one override around the pool |
| Low | no in-flight account-switch or cap test | both added |

## Not covered (next increments)

- **B-5:** Daily Review concurrency across tabs, and each learner's time zone.
- **B-7:** a full cloud E2E in a browser.
- Study candidates (Words & Chunks suggestions) in cloud.
- A real dictionary release, and DQ-03 contextual sense selection.
