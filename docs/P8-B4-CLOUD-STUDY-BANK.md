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

## Dictionary UI

- **Cloud Listening** passes the session owner as `contextualOwnerId`, never a
  local learner id.
- **`DictionaryPanel`'s existing contextual flow** works in cloud through
  `apiFetch`, with the session, CSRF and owner-bound response:
  - saved-status check;
  - Save Word;
  - an explicit Restore for an archived word, which sends a PATCH with its
    `expected_revision`.
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

- **`test_cloud_contextual_words.py`** (13 tests), all with the Personal stores
  set to raise:
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
  - an account switch dropping the word state.
- **Mutation check:** 11 guards were removed one at a time, and each fails
  the tests.

## Not covered (next increments)

- **B-5:** Daily Review concurrency across tabs, and each learner's time zone.
- **B-7:** a full cloud E2E in a browser.
- Study candidates (Words & Chunks suggestions) in cloud.
- A real dictionary release, and DQ-03 contextual sense selection.
