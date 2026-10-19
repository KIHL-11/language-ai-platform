# Listening Workspace

Listening Workspace is a standalone, long-term practice mode for saved Learning
Library media. It is not a TrainingStage and does not change the canonical
Active Recall → Blind Listening → Comprehension → Mini Dictation → Shadowing →
Retell → Live Dialogue sequence. Guided Lesson remains under **Learn**;
**Listening** reopens the stored Lesson snapshot without extraction or analysis.

## Media and timeline architecture

The UI depends on the small `MediaController` interface (`play`, `pause`,
`seek`, current time, duration, playback rate, readiness, and cleanup) rather
than a provider-specific player. An HTML audio adapter handles persistent
Library audio. Recognized YouTube watch, short, embed, mobile, and `youtu.be`
URLs use a native IFrame Player adapter. Its external script is loaded only when
a YouTube workspace opens, is shared across mounts, needs no Data API key, sets
autoplay off, and is destroyed with its timers/listeners on unmount. Ordinary
application startup makes no YouTube, credential, microphone, WebRTC, OpenAI,
Realtime, or Whisper request.

The player clock is sampled by one 200 ms UI timer. `sentence.start <= time <
sentence.end` selects the active sentence. During a timestamp gap, the prior
active sentence remains selected rather than jumping ahead. Highlight and
current-sentence display use the original Lesson sentence objects; no second
transcript representation is created. Auto-follow scrolls only when that active
sentence changes and respects reduced-motion preferences.

Transcript rows are keyboard-accessible buttons that seek to the sentence
start without forcing playback. Controls provide play/pause, five-second seeks,
0.5/0.75/1/1.25 rates, sentence repeat, and one custom A-B loop. Sentence and
custom loops are mutually exclusive, require a range of at least 0.5 seconds,
and clamp to known duration.

## Degraded and offline-ish behavior

The product does not archive full YouTube video. Online YouTube playback depends
on YouTube connectivity and embedding permission. If loading it fails, saved
Library audio is used with an explicit message. Unsupported sources also use
saved audio. If neither is available, transcript navigation, Blind/Dictation,
and candidate access remain available in transcript-only mode.

Lesson timestamps are lesson-relative. Full-source Lessons map accurately to a
remote player. Some legacy Lessons made from a clipped source were rebased to
zero without explicit source-offset metadata; the workspace does not guess that
hidden offset, so remote visual seeking may be inaccurate for those items.
Their saved local audio remains authoritative.

## Practice modes and Study Bank

Listen mode independently controls source and translation visibility. Blind
temporarily hides both and supports an explicit reveal without overwriting the
saved preferences. Dictation targets the current sentence and reuses
`training/dictation.ts` for deterministic word comparison. Changing sentence
clears the temporary attempt and never changes Guided Lesson progress.

The existing study-candidate endpoint is loaded best-effort and grouped by
sentence provenance. Recommended and review Words/Chunks for the current
sentence can be saved through the existing Study Bank bulk API; rejected
candidates are hidden. Candidate failure never disables media or transcript
practice, and no parallel vocabulary store is introduced.

## Persistence and Library migration

Learning Library schema version 2 introduced `listening_progress`:

- `last_position_seconds`
- `current_sentence_id`
- `playback_rate`
- `source_visible`
- `translation_visible`
- `mode` (`listen`, `blind`, or `dictation`)

Version-1 stores remain readable without write-on-start. M4.5 subsequently
evolves the combined Library document to version 3 for independent Sync
Shadowing progress; old items keep the same listening defaults and semantics.
Invalid old listening state resets only this field. Summaries expose
position/continuation state for Listening Home.

The UI updates locally on every clock sample but persists only after roughly
five seconds of meaningful movement and on pause, seek, rate, preference, mode,
or workspace exit. PATCH requests are serialized so an older asynchronous write
cannot overtake a newer snapshot. Dictation input, reveal state, and loop points
remain transient.

## Product boundaries

M4.3 adds no mastery score, SRS, microphone, pronunciation scoring, arbitrary
text selection, cloud sync, authentication, AI explanation, paid API, or video
archive. Sync Shadowing and timing/scoring work remain the M4.5 boundary.

## Required real-Mac smoke after merge

Cloud tests do not verify real YouTube embedding or macOS audio. After updating
the Personal Desktop app:

A. Update the Personal Desktop app.
B. Open `Language AI.app`.
C. Open **Listening**.
D. Choose the saved Paris German Lesson.
E. Confirm the original YouTube video appears when online.
F. Press **Play**.
G. Confirm the current transcript sentence tracks playback.
H. Click a later subtitle and confirm video seeks.
I. Enable sentence repeat.
J. Confirm that sentence loops.
K. Switch to 0.75x.
L. Confirm actual playback speed changes.
M. Set custom A and B and confirm looping.
N. Enter Blind mode.
O. Reveal subtitles again.
P. Enter Dictation mode and complete one sentence.
Q. Save one Word from the current sentence.
R. Save one Chunk from the current sentence.
S. Verify both appear in **Words & Chunks**.
T. Listen until a memorable position.
U. Close the browser/app.
V. Reopen `Language AI.app`.
W. Open **Listening → Paris**.
X. Confirm saved position/settings restore.
Y. Temporarily break/disable network or otherwise test provider failure if practical.
Z. Confirm saved local audio/transcript fallback remains usable.

Do not mark M4.3 VERIFIED until this real-Mac smoke succeeds.
