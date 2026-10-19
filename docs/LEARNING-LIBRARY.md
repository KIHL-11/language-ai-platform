# Persistent Learning Library

My Library is the backend-owned, single-user collection of previously generated
Lessons. It stores enough validated state to reopen a Lesson, continue its
latest canonical training position, or restart it from Active Recall without
extracting the source or running analysis again.

## Storage and schema

Library metadata is stored in the ignored local file:

```text
backend/storage/learning_library.json
```

The JSON document has schema version `3` and an `items` array. Each item stores
an ID, deterministic source key, UTC creation and update timestamps, the full
validated Lesson snapshot, latest validated progress, and persistent-audio
metadata, plus independent Listening Workspace and Sync Shadowing progress.
Version-1 and version-2 files remain readable and receive safe missing progress
defaults in memory; reads alone never rewrite storage. The next successful
mutation atomically writes version 3. Invalid Listening or Shadowing progress
resets only that progress, preserving the Lesson, Guided progress, and audio.
Writes use a temporary file followed by an atomic replace and are
serialized within the backend process. Malformed data and unsupported future
versions fail clearly instead of being overwritten.

The source key combines the media identity, source language, target language,
and level. YouTube watch, short, embed, mobile, and `youtu.be` URLs use the
validated video ID when available. Other URLs receive only conservative local
normalization: scheme and host casing are normalized and fragments are removed,
while the path and query remain distinct. Saving the same source/configuration
updates the existing item, preserves its ID and creation time, and replaces its
Lesson snapshot. Different levels remain separate items.

## Persistent audio

When a Lesson refers to an existing safe `/media/...` file, the backend copies
the file into:

```text
backend/storage/library_media/<library-item-id>/
```

Only files that resolve inside the known backend media directory are eligible.
Absolute paths, traversal, symlink escapes, remote URLs, data URLs, blob URLs,
and missing files are not copied. The Lesson is still saved when source audio is
missing and the UI marks persistent audio unavailable. Audio is served only for
its owning item through:

```text
GET /api/library/items/{item_id}/audio
```

The storage directory is not exposed as a general static directory. Deleting an
item removes only that item's Library-owned audio; temporary generated media and
other Library items are untouched. My Library intentionally does not archive
full YouTube videos.

## Personal Session and training

The version-1 browser Personal Session remains the source of truth for what is
active now. My Library is the source of truth for durable saved Lessons and each
item's latest canonical progress.

- A generated Lesson is usable immediately, then saved to My Library in the
  background. A save failure shows a retryable warning and never turns a
  successful generation into a failure.
- A valid restored Personal Session is best-effort upserted into My Library.
  Source-key upsert keeps this idempotent across refreshes.
- Canonical training state changes are synced at existing state boundaries.
  Restoring a session does not advance progress, and development stage overrides
  are not persisted as real progress.
- **Continue Training** makes the saved Lesson and canonical stage/sentence the
  active Personal Session, then uses the existing TrainingSession.
- **Train Again** reuses the snapshot, resets progress to Active Recall, and
  does not download, transcribe, or analyze the source again.
- **View Transcript** displays the existing Lesson sentences, translations, and
  timestamps without creating a second transcript format.

Deleting a Library item requires confirmation. It does not delete the current
Personal Session or Chunk Memory, so an already active Lesson remains usable and
previously practiced chunks remain available.

Listening Workspace state belongs to the Library rather than Personal Session.
Its `ListeningProgress` stores the bounded media position, validated current
sentence ID, one of the supported playback rates, source/translation preferences,
and `listen`, `blind`, or `dictation` mode. The dedicated PATCH endpoint updates
only this state. See [LISTENING-WORKSPACE.md](LISTENING-WORKSPACE.md).

Sync Shadowing state is also Library-owned and stores bounded counters, latest
measured evidence, and best-observed metrics per sentence without recording
audio or unbounded attempt history. See [SYNC-SHADOWING.md](SYNC-SHADOWING.md).

## Privacy and intentional limits

Library data stays local. It does not contain audio blobs, recordings, cookies,
authentication tokens, API keys, environment files, or Realtime client secrets,
and saving/listing does not send Library data to an external model. The feature
does not add authentication, multi-user storage, cloud sync, playlists, folders,
tags, training history, or analytics.

Lesson keywords and candidate chunks are preserved exactly as source Lesson
data. They are not automatically promoted. M4.2 implements explicit selection
into the separate Study Bank documented in [STUDY-BANK.md](STUDY-BANK.md).
Deleting a Library item does not delete curated items because each Study Bank
provenance record retains its own small source snapshot.

## Manual macOS smoke test after merge

Persistent audio and restart behavior must not be considered verified until the
following smoke test is completed on the target Mac:

1. Update the Personal Desktop app with the documented update script.
2. Open `Language AI.app`.
3. Generate one English or German YouTube Lesson.
4. Confirm the Lesson appears in **My Library**.
5. Enter training and advance partway through a stage.
6. Close the browser.
7. Reopen `Language AI.app`.
8. Open **My Library**, choose the Lesson, and select **Continue Training**.
9. Confirm the saved stage and sentence position are restored.
10. Close and restart the backend or Mac app, then reopen the saved Lesson.
11. Confirm persistent Library audio still plays.
12. Select **Train Again** and confirm training starts at Active Recall without
    regeneration.
13. Delete the Library item after accepting the confirmation prompt.
14. Confirm existing Chunk Memory remains untouched.

Known limitations: only the latest progress is stored; audio cannot be recovered
if no safe source file existed at save time; storage uses a local JSON file and
process-local locking rather than a multi-process database; and real macOS
restart/audio behavior requires the manual smoke above.
