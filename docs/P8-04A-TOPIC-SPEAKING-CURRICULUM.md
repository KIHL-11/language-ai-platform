# P8-04A Topic Speaking Curriculum

Status: implemented for Personal Desktop deterministic mock practice. Cloud
attempt persistence and live cloud providers remain deliberately unavailable.

## Product boundary

The versioned catalog provides reviewed English and German scenarios at B1,
B2, and C1 presentation levels. These labels tune the material; they are not a
CEFR assessment. The first catalog version includes friends reuniting,
shopping returns, airport/hotel problems, shared housing, job interviews,
workplace decisions, and university discussions.

Selecting a topic only reads the public catalog. Previewing a topic reads up to
five active, owner-scoped Study Bank chunks. It does not start a provider,
request a microphone, or change Study Bank/ReviewState. Topic relevance is a
deterministic lexical preference followed by the existing Again, Hard, due,
new, and other-active priority. Fewer or zero targets is valid.

## Lifecycle and APIs

The learner-visible lifecycle is:

`setup -> preview/consent -> connecting -> live -> ended -> feedback -> saved`

- `GET /api/topics` and `GET /api/topics/{topic_id}` expose bounded public
  templates.
- `POST /api/topics/{topic_id}/preview` derives the owner from the trusted
  request identity and returns only that owner's active chunks.
- The attempt, turn, and finish routes are Personal Desktop only. They reuse the
  bounded `SpeakingSessionStore` with the `topic-mock` provider marker.
- Cloud attempts return a controlled 503 before any process-global/JSON store,
  microphone, provider token, or external provider can be reached. P8-04B must
  add owner-scoped durable Speaking storage before this gate can open.
- Existing Speaking context, session history, event, finish, and tool routes are
  guarded by the same cloud storage gate; authenticated cloud identity alone is
  not treated as proof that the legacy JSON store is tenant-safe.

The mock partner uses reviewed prompts from the catalog. It accepts explicit
text turns only and makes no network request. Attempts use the existing
optimistic event counter, finalized-turn limits, atomic local writes, and
interrupted/completed statuses.

## Evidence and learning-state safety

Target use is the existing Unicode-normalized exact phrase match over finalized
learner turns. Checkpoints require deterministic term evidence and retain the
verbatim learner turn. A very small allow-listed correction set may produce a
suggestion only when its exact source pattern appears in the learner turn.

The feedback explicitly does **not** measure pronunciation, fluency, or CEFR.
No attempt rates, archives, restores, or otherwise modifies Study Bank,
ReviewState, Daily Review, Library, Chunk Memory, or AI Chunk candidates.

## Known limitations and next gate

- The local mock is text-only and intentionally not an intelligent provider.
- Lexical checkpoint evidence can miss valid paraphrases; it never treats a
  paraphrase as proven target usage.
- Topic sessions appear in the existing bounded Personal Speaking history; no
  separate analytics or attempt database is introduced.
- Cloud attempts, provider credentials, microphone use, spending controls, and
  account-level Speaking history are deferred to owner-scoped P8-04B storage.
