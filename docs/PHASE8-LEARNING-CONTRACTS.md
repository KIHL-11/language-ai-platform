# Phase 8 — AI Chunk and Topic Speaking Contracts (design proposal)

Status: DESIGN / NOT IMPLEMENTED. Does not change any existing SRS counters, Study Bank data, provider accounts or microphone permissions.

## 1. Reuse first

The repository already has Study Bank, Chunk Memory, Daily Review, explicit curation, Speaking Agent, three Speaking modes (review_weak, media, custom), contextual target selection (default five, maximum eight), and an M4.7 integration layer. Extend these rather than introduce competing learning-state engines. Existing Speaking evidence is based on finalized learner transcript substring matching; it is not fluency, CEFR, pronunciation or semantic mastery scoring. Preserve this distinction.

## 2. AI Chunk Organizer (P8-03)

Sources: saved Library sentence ranges, user-selected media passage, explicit Speaking session transcript/summary when the learner opts in, and manually submitted text. No provider call just because a page opens. Obtain material only from authenticated, owner-scoped sources. Limit length, batch count and cost before sending content to a configured model. Avoid sending entire media histories or raw audio. Treat transcript content as untrusted; do not execute text as agent instructions.

Suggested pipeline: collect bounded source -> normalize source/provenance -> deterministic garbage filtering -> AI proposes language-specific expressions -> validate structure and source evidence -> deduplicate against Study Bank -> human review -> explicit save -> existing Daily Review. If AI is unavailable, deterministic curation remains usable. Model output is a suggestion and may not override manually saved meaning or note.

A candidate object contains: candidate_id, source_language (en/de), kind (chunk/word), text, normalized_identity, suggested_meaning_zh, usage_note, example, topic_tags, source_refs (Library item + sentence/time range or Speaking session + finalized turn), explanation_of_value, quality_flags, proposal_model, proposal_version. Never forge a timestamp, quote or translation absent from evidence. Text must be validated and limited; do not persist prompts/provider credentials in candidate records. Multiple identical candidates unify their source_refs without erasing earlier provenance.

Candidate lifecycle: proposed -> accepted / edited_and_accepted / rejected / expired. Viewing never auto-saves; rejection never changes SRS. Saving is idempotent per owner+language+kind+normalized usable identity, reuses active records and preserves reviewed state and archive status by existing semantics. An archived item needs an explicit Restore flow. Add batch idempotency and structured partial-error handling when Chunk Memory compatibility update fails; prefer an eventual transactional/outbox path when moving to PostgreSQL.

Suggested endpoint contracts (**not implemented**): POST /api/ai/chunk-suggestions (request contains source_ref, language, max_candidates); GET /api/ai/chunk-suggestions/{batch_id}; POST /api/ai/chunk-suggestions/{batch_id}/decisions (explicit accept/edit/reject, version, idempotency_key). Server derives owner; no client-trusted user_id. The normal Review API stays authoritative for Again/Hard/Good/Easy; AI cannot silently assign rating or recalculate due dates.

## 3. Topic Speaking curriculum (P8-04)

Create a curated, versioned catalog for English and German at B1/B2/C1. Initial themes: friends reunion, shopping, airport/hotel, WG housing, interview, workplace meeting, university discussion. For each topic: topic_id, language, level, label, scenario, learner role, partner role, measurable communication task, optional prompts, optional support vocab, 3–5 suggested target Chunk IDs when available, approved model prompt template, version, state (draft/published). Topic choices are learner-facing; no automatic provider connection or microphone grant on selection.

Single attempt state machine (proposed): setup -> preview targets and consent -> connecting -> live -> ended -> feedback -> saved. Reuse Speaking Agent provider adapter, session duration limits, bounded transcripts, token grants, explicit Start Speaking, and disconnect behavior. Record topic_attempt_id, owner_user_id, selected topic version, linked speaking_session_id, chosen target IDs, completion state, termination reason, actual use evidence and self-assessment separately. An interruption is not a successful completion.

Post-attempt feedback: show which target expressions appeared verbatim in finalized learner turns, suggested corrections (each with actual supporting text), task completion evidence and learner self-assessment. Proposed new chunks remain suggestions until explicit save. Reuse Daily Review and M4.7 deep links for weak/due targets; do not infer an SRS rating from a conversation or claim an objective CEFR improvement without validated assessment evidence.

Suggested endpoints (**not implemented**): GET /api/topics?language=en&level=B2; POST /api/topic-attempts; POST /api/topic-attempts/{id}/finish; GET /api/topic-attempts/{id}/feedback. Must verify owner of every linked Study Item, Library item and Speaking session. A public topic template must not expose private user text.

## 4. Synthetic acceptance scenarios

- English get: understand vs obtain are not mixed; German kommen/komme and Haustür examples retain correct provenance; AI missing Chinese is clearly marked uncertain instead of fabricated.
- Existing active word/chunk saved twice merges provenance and preserves due_at, intervals and counts. Archived material stays archived until user restores.
- Two users saving identical phrases receive separate Study Bank records. Forged source_ref, speaking_session_id or learner_id does not leak or change another account's data.
- AI returns malformed JSON, fabricated evidence, profanity in source, huge text, duplicated candidate, unsupported language, unavailable model, quota failure: fail safely and still allow manual curation.
- Topic training offers 3–5 targets when available; no targets is a valid choice. Selecting a topic never activates microphone or mints AI credential.
- Terminated and interrupted sessions are stored with their actual status; only finalized learner turns count as target usage. Tool suggestions never enter Study Bank without approval.
- Full English and German flow: Listening/Library -> candidate -> Save -> Daily Review -> Topic Speaking -> evidence -> Review, without unintended edits to source records.

## 5. Deferred decisions

Exact prompting/model selection, model pricing and provider handling, topical taxonomy, difficulty calibration, cost budget/limits, retention for transcript-based candidate requests, analytics opt-in and whether semantic matching is useful. These require separate tracked decisions and tests; none are assumed complete by this document.
