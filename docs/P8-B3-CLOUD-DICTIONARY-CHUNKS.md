# P8-B3: Cloud dictionary and Cloud AI Chunk

Workstream B increment B-3, stacked on B-2 (Draft PR #82, `6082c9c`). Synthetic
data, a disposable PostgreSQL and the mock AI only. There is no Kaikki import,
no DQ-02 change and no paid model. Personal Desktop behaviour is unchanged.

## B-3A Cloud dictionary

- **Source.** `services/dictionary/cloud.py` builds a provider from the packaged
  fixture `services/dictionary/fixtures/cloud_synthetic_en_de.json`, which holds
  15 hand-written EN/DE entries licensed CC0 as synthetic test data. They cover:
  - words, inflections and lemmas (ran → run, Häuser → Haus, geht → gehen);
  - part of speech and Chinese meanings;
  - cross-language translations and examples;
  - context-dependent senses (bank / Bank / run);
  - a form shared by two lemmas (leaves → leaf / leave), a phrase, and a
    separable verb.
- **Reuse.** Cloud lookups run through the existing `DictionaryService`, so
  ranking, context selection and the response shape are identical to Personal.
  Pronunciation is empty in cloud: there are no recordings and no TTS without a
  cloud budget.
- **Fail closed.** `POST /api/dictionary/lookup` in cloud:
  - needs a session (401 otherwise);
  - uses only `app.state.cloud_dictionary_service`, or the synthetic fixture
    when `CLOUD_DICTIONARY_SOURCE=synthetic-fixture`.
  - Any other value, or none, returns 503 `cloud_dictionary_not_configured`.
  - The Personal SQLite release is never opened. Injecting the local service
    is refused too, and no setting accepts a file path.
  - Personal mode still uses its local dictionary.
- **Listening.** In cloud, clicking a subtitle word opens the dictionary panel
  through `apiFetch` (session cookie, CSRF, owner-bound response). The panel
  shows the entry without any Study Bank save or status check, since saving is
  B-4. The lookup client moved from raw `fetch` to `apiFetch`; Personal still
  sends the same plain request.

## B-3B Cloud AI Chunk

**Owner decision (2026-11-04).** The existing P8-03B2 system is reused as is.
Accepting a candidate still saves it to the Study Bank in the same transaction,
exactly as the cloud Words & Chunks page already does. B-3 adds:

- **Generation from the cloud lesson.** A lesson's "Find chunks" view sends a
  `library_passage` request (up to 12 sentences) under the session owner.
  - The server resolves the lesson from the owner's PostgreSQL Library and runs
    the mock adapter.
  - Every candidate keeps full provenance: library item, lesson, sentence id,
    source URL, timing, translation, and the exact quote with its range.
  - English and German both work.
- **Save edits only (`edit` action).** The learner's text, meaning, usage and
  example are saved, and the candidate stays `proposed` at version + 1.
  - It writes no Study Bank item. The decision transaction enforces this, and
    migration `0011` adds a check that an `edit` row never carries a study item.
  - The edit must stay grounded in the immutable source quote.
  - It uses the same idempotency key, stale-version 409 and immutable-evidence
    checks as every decision.
  - An edited candidate can still be accepted (with the edited content) or
    rejected.
- **UI.** `AiChunkOrganizer` takes an optional `librarySource`.
  - Each candidate shows its source sentence and time.
  - "Save edits" is enabled only when something changed.
  - On a 409 the batch is reloaded and the other tab's state is shown, never
    overwritten.
  - Manual-text mode is unchanged.
- **Migration `0011_ai_chunk_edit_action`.** It allows `edit` in
  `cloud_ai_chunk_decisions`, with the study-item guard. The down migration
  refuses while edit decisions exist, rather than deleting them.

## Security boundary

- B-2's six Cloud → Personal gates stay closed: chunk memory and recall,
  integration target languages and speaking-suggestion save.
- B-3 reads and writes only through the session owner and the owner-scoped
  PostgreSQL Library, candidate and Study Bank stores.
- Cross-account generation, read and edit return 404 (`KeyError` or
  candidate-not-found); a forged `user_id` returns 403.

## Tests

- **`test_cloud_dictionary.py`** (22 tests):
  - EN/DE words, inflections, meanings, translations and examples;
  - context senses, ambiguity, a shared form and a phrase;
  - no language crossing, and the fixture being synthetic;
  - cloud using only the synthetic source with the local release set to
    raise on any access;
  - fail-closed for every other setting and for an injected local service;
  - 401 without a session, and Personal unchanged.
- **`test_cloud_ai_chunk_library.py`** (13 tests):
  - EN/DE generation from a Library lesson with full provenance;
  - edits saved without a Study Bank write and durable on re-read;
  - edit then accept;
  - an ungrounded edit refused;
  - a stale edit (409) and a replay returning the same result;
  - 4 concurrent edits with exactly one winner;
  - a duplicate generation running the mock once;
  - cross-account generate, read and edit refused;
  - the API using the session identity (forged `user_id` → 403);
  - the edit-content requirement;
  - a Personal edit;
  - 0011 rollback refusing while edits exist and restoring the constraint.
- **Frontend** (`CloudLibrary.test.tsx`, 3 new):
  - a subtitle lookup with CSRF and no Study Bank section;
  - Find chunks with provenance, Save edits sending `edit`, then Accept;
  - a 409 reloading the other tab's state.
- **Mutation:** 14 guards were removed one at a time and each fails the tests.

## Not covered

- B-4: saving words from the dictionary, Library and AI candidates into the
  Study Bank with provenance and de-duplication, and the SRS/Daily Review
  links. In cloud, accepting an AI candidate already saves it (P8-03B2).
- A real dictionary release (DQ-02, owner decision) and cloud TTS.
- Cloud recall and chunk memory: they reopen only on owner-scoped cloud
  stores.
