# M4.7 Cross-system Learning Integration

## Product role

M4.7 adds a read-only integration layer and a concise **Today** page that answers “What should I practice next?” from existing authoritative data. It does not introduce a universal mastery score or another progress database.

The application still starts in **Learn**. Today is an explicit top-level destination alongside Learn, Listening, Shadowing, Speaking, Daily Review, My Library, and Words & Chunks.

## Authority boundaries

| Signal | Authoritative store | M4.7 use |
| --- | --- | --- |
| Due/new review state | Study Bank `ReviewState` | Daily Review summary and weak-chunk facts |
| Spoken target use | finalized `SpeakingSession.target_usage` | bounded recent use/evidence |
| Source-sentence sync | Library `shadowing_progress` | attempted/stable source facts and deep link |
| Guided recall | Chunk Memory | factual attempts/successes/failures only |
| Listening continuation | Library `listening_progress` | media-level resume action |

Study Bank `ReviewState` is the only source of truth for spaced-repetition scheduling. Only explicit Daily Review ratings alter it. Model assertions and assistant transcripts do not count as Speaking use. `mastery_score` is not treated as overall mastery. These signals have different meanings, so averaging them would produce an unjustified score; M4.7 intentionally shows the evidence separately.

Speaking, Shadowing, Listening, and Today never infer or write SRS ratings.

The resulting learning loop is explicit: curate media expressions → review them on schedule → practice selected difficult chunks in Speaking or their source sentences in Shadowing → continue the original media in Listening → return to Today for freshly derived advice. No step silently rates another.

## Integration API

- `GET /api/integration/overview?user_id=...&language=en|de` returns bounded Review summary, up to five weak chunks, up to three Listening continuations, up to five unsaved Speaking suggestions, and up to six deterministic next actions.
- `GET /api/integration/study-items/{item_id}?user_id=...` returns derived `StudyPracticeEvidence` and enforces item ownership.
- `GET /api/integration/target-languages` returns the user's existing target-language choices for one source language.
- `POST /api/integration/speaking/sessions/{session_id}/suggestions/{index}/save` explicitly saves a canonical stored Speaking suggestion.

GET operations do not reserve work or mutate any store.

## Deterministic recommendations

A weak chunk must already have review evidence: last rating Again/Hard, be reviewed and due, or have at least one lapse. New untouched chunks are labelled New, not weak. Ordering is Again, Hard, due with more lapses, other due; chunks without recent Speaking use are then preferred, followed by stable timestamp/ID tie breaks.

Next actions prefer due review, a weak expression for Speaking, a different weak expression with a non-stable Shadowing source, an unsaved Speaking suggestion, continued Listening, then new cards when no reviewed card is due. Recommendations are advice only.

Recent Speaking aggregation is explicitly bounded to the latest 100 sessions. It is not presented as a lifetime total.

## Study Bank schema v3

Study Bank v3 changes provenance into a discriminated union:

- `media`: the existing Library/Lesson/sentence snapshot, with `source_type: "media"`.
- `speaking`: bounded session ID, suggestion index, originating turn, provider, mode, optional media-title snapshot, and optional visible transcript evidence.

V1 and v2 files remain readable. Their existing provenance is interpreted as media provenance in memory. Reading or starting the app does not rewrite old storage. The next successful Study Bank mutation writes v3 atomically while preserving item IDs, active state, fields, timestamps, provenance, and the complete ReviewState. Malformed data is not overwritten and future versions fail clearly.

All provenance consumers guard on `source_type`; Speaking provenance is never faked as Library media.

## Saving Speaking suggestions

Suggestions remain inert until the learner clicks **Save to Words & Chunks**. The server takes text and kind from the stored Speaking session, so a caller cannot forge arbitrary suggestion content. The learner must explicitly select a target language: one existing choice is preselected, several require a choice, and no existing choice requires manual input.

Saving uses normal Study Bank normalized identity and upsert rules. A new item receives the ordinary new-card ReviewState. An equivalent item merges the exact Speaking provenance without duplicating it or resetting SRS history.

A saved chunk is also synchronized to Chunk Memory with `source_stage="speaking"` and null lesson/media/sentence IDs. Words remain Study Bank-only. Study Bank is written first. If that succeeds but compatibility synchronization fails, the API reports a clear partial-synchronization error and leaves the valid Study item intact; there is intentionally no distributed transaction.

## Cross-workflow links

- Today can open the existing Daily Review workflow.
- Weak chunks can open Speaking with an explicit, preselected target; this never connects a provider or requests a microphone until **Start Speaking**.
- Media provenance can open the existing Shadowing workspace at the selected sentence.
- Continue Listening opens the existing saved Library item and player.
- After a Daily Review session, Again/Hard chunks can be passed explicitly to Speaking.

Deleted navigation targets are handled as unavailable rather than recreated.

## Privacy, cost, and limitations

The integration layer is local and deterministic. It makes no AI/provider calls, starts no microphone, and adds no network service. Opening Today or following a Speaking recommendation does not start Gemini or OpenAI.

Current limitations:

- there is no cross-system composite or mastery score;
- recent Speaking evidence is bounded, not lifetime history;
- Listening cannot claim per-expression repetition counts;
- deleted Library media cannot contribute Shadowing evidence;
- a Speaking-chunk save can require manual recovery if Study Bank succeeds and Chunk Memory persistence fails;
- M4.7 does not infer Daily Review ratings from other forms of practice.
- Speaking evidence covers M4.6 targeted chunks, not every spontaneous phrase in a transcript.
- Shadowing evidence describes source-sentence synchronization, not pronunciation mastery.
- Guided recall and Daily Review remain separate performance models.
- recommendations are deterministic heuristics, not AI predictions.

Deleting a Library item removes its live Listening/Shadowing linkage but does not erase the Study Bank provenance snapshot. Deleting a Study Bank item follows existing curation semantics and does not delete historical Chunk Memory statistics. Deleting a Speaking session makes an unsaved suggestion unavailable; already saved Study items retain their bounded provenance snapshot.
