# Words & Chunks Study Bank

The Study Bank is the learner's explicit, durable collection of useful words and
chunks. Lesson keywords and chunks are candidates only: generating, opening, or
training a Lesson does not automatically add them to long-term study material.
Candidate generation reads the saved Lesson snapshot locally and makes no
network, dictionary, LLM, Whisper, OpenAI, Gemini, or Realtime request.

## Storage and model

The ignored backend file `backend/storage/study_bank.json` uses schema version
`1`, process-local locking, a temporary file, and atomic `os.replace` writes.
Malformed JSON and unsupported future versions fail without overwriting source
data. No new database or third-party dependency is used.

Every StudyItem stores:

- stable ID, learner ID, `word` or `chunk` kind, source/target languages;
- display text, normalized identity, meaning, active status, UTC timestamps;
- optional lemma, part of speech, example, usage, and learner note;
- one or more provenance snapshots.

Provenance retains the Library item ID, Lesson ID, original source URL, media
title, sentence ID, sentence and translation, and precise start/end timestamps.
It remains usable if the Library item is later deleted. Library deletion never
deletes Study Bank items or historical Chunk Memory.

Words deduplicate by normalized usable lemma, falling back to normalized surface
text. Chunks reuse the existing safe subtitle cleanup and normalization. Identity
also includes learner, source language, and kind, so English/German and
word/chunk entries remain separate. Repeated saves preserve the ID and creation
time, merge only new provenance, and do not replace non-empty learner meaning or
notes with candidate data.

## Candidate quality and explicit saving

`GET /api/library/items/{item_id}/study-candidates` derives stable candidates
from `sentence.keywords` and `sentence.chunks` without persisting them.

- **Recommended**: clean analyzed words or useful multi-word spoken expressions,
  for example `figure it out` or `Los geht's.`
- **Review**: short, potentially fragmentary, generic, or unusually long items
  that may still be useful, for example `To go?` or `alle.`
- **Rejected**: caption labels, music/applause/singing noise, repeated
  interjections such as `Ah Ah Ah`, punctuation-only text, or malformed residue.

Rejected candidates are hidden by default but can be inspected. They cannot be
selected in the normal curation UI. **Save** and **Save Selected** use one
idempotent bulk endpoint; merely viewing or refreshing candidates performs no
write.

The top-level **Words & Chunks** page lists the actual curated bank with word or
chunk, language, active/archived, and search filters. Each entry exposes all
provenance. Archive is preferred over permanent delete; permanent delete requires
confirmation.

## Active Recall and legacy Chunk Memory

The old `/api/chunks/memory/from-lesson` route remains available for backward
compatibility, but TrainingSession no longer invokes it automatically. The
version-1 Personal Session field `lessonMemoryImported` remains readable as a
deprecated compatibility field and does not trigger bulk import.

Explicitly saving a curated chunk ensures a compatible Chunk Memory record exists
and restores that record if it had been archived. Existing practice counts,
mastery, successes, failures, and timestamps are preserved. Words remain only in
the Study Bank.

Normal learner-facing Active Recall intersects active Study Bank chunk identities
with eligible Chunk Memory records before applying the existing priority logic.
Archiving or deleting Study Bank curation removes eligibility without deleting
practice history. If there are no active curated chunks, Active Recall returns a
legitimate empty state and does not fall back to legacy automatically imported
candidates. Existing `chunk_bank.json` records are never migrated or deleted.

## Future boundary

The provenance timestamps are intended for M4.3 Listening Workspace. Word and
chunk items are intended for M4.4 Daily Review and the future Speaking Agent.
M4.2 does not add a media player, A/B repeat, SRS scheduling, due counts, mastery
for words, Sync Shadowing scoring, agent conversations, or cloud sync.

## Required real-Mac smoke after merge

Do not claim this smoke is complete from cloud validation. On the real Mac:

1. Update the Personal Desktop app.
2. Open `Language AI.app`.
3. Open the saved Paris German media in **My Library**.
4. Open **Curate Words & Chunks**.
5. Confirm obvious subtitle garbage is not Recommended.
6. Select two or three useful German words.
7. Select two or three useful German chunks.
8. Save the selection.
9. Open **Words & Chunks** and confirm the saved material appears.
10. Close and reopen `Language AI.app`; confirm the Study Bank persists.
11. Open Active Recall and confirm only curated chunks are used.
12. Archive one curated chunk and confirm it disappears from normal Active Recall.
13. Restore it and confirm it becomes eligible again.
