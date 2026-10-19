# Sync Shadowing

Sync Shadowing is a persistent, sentence-level practice workspace for saved
Learning Library media. Its loop is **Reference → Sync Attempt → Analyze →
Retry**. It is separate from the basic Shadowing stage in Guided Lesson and does
not alter the canonical seven-stage order. Guided Shadowing remains a short
lesson exercise; Sync Shadowing accumulates bounded, media-specific progress.

## Authoritative media and recording

Saved Library audio is the authoritative scored timeline. The workspace seeks
that audio to the Lesson sentence's `start`, plays the attempt at original
`1.0x`, and stops at the sentence boundary plus a small trailing capture
window. Learners can preview the reference at 0.75x, 1.0x, or 1.25x, but scored
attempts always return to 1.0x. The MVP intentionally does not synchronize a
YouTube picture, archive full video, or play two audio sources at once.

Microphone access starts only after **Start Sync Attempt**. Once permission is
ready, the UI shows a visual 3–2–1–Go countdown, starts `MediaRecorder`, and then
starts the reference. It requests echo cancellation, noise suppression, and
automatic gain control where supported. Headphones are recommended because
these constraints cannot guarantee removal of speaker bleed. Each attempt is
bounded by `min(30, max(10, target_duration * 2))` seconds. Tracks, timers, and
object URLs are released after the attempt, reset, navigation, or unmount.

The recording blob exists temporarily in the browser for playback and is sent
only to the local backend. The backend accepts at most 10 MiB, transcodes it in
a temporary directory to mono 16 kHz WAV, and deletes the input and WAV when
analysis ends or fails. Raw recordings are never stored in Library JSON or the
media directory and are never sent to Azure, OpenAI, Gemini, or another cloud
service.

Without saved audio, the workspace remains available for transcript-match
practice, but reports that precise reference timing is unavailable. It does not
guess word timing from text or hidden source offsets.

## Local transcription and reference cache

M4.5 uses local `faster-whisper` with word timestamps. English uses the
configured English model (`WHISPER_MODEL_EN`, then the existing
`WHISPER_MODEL`, default `base.en`); German uses the configured multilingual
model (`WHISPER_MODEL_MULTI`, default `base`). Models are lazily initialized,
cached by model name, and protected against duplicate concurrent first-use
construction. Tests inject fake transcriptions and never download a model.

For saved audio, the backend extracts only the selected sentence range to a
temporary WAV and obtains reference word times relative to that segment.
Derived results are cached in the item's owned directory as
`shadowing_reference_v1.json`. A cache signature includes sentence ID, source
language, sentence text, and audio file identity. Generation is process-locally
serialized and cache writes are atomic. Replacing audio removes item-owned
derived files; deleting the Library item removes the directory. A malformed
cache is safe to regenerate because it is not primary user data.

Reference Whisper tokens are aligned to the Lesson sentence before their timing
is trusted. Alignment below 70% disables precise timing with
`reference_alignment_unreliable`; transcript evidence remains available.

## Deterministic text and timing evidence

Text normalization uses Unicode NFKC, case-folding, whitespace/token
normalization, punctuation-insensitive comparison, and retained apostrophe word
forms where practical. A deterministic Levenshtein-style alignment classifies
each token as matched, missed, extra, or substituted. It performs no semantic,
embedding, or LLM judgment.

Transcript Match is local-ASR word-sequence evidence, not pronunciation
accuracy:

```text
edit_distance = missed + extra + substituted
denominator = max(target_word_count, recognized_word_count, 1)
transcript_match = max(0, 1 - edit_distance / denominator) * 100
```

When reliable timed words exist:

- Reference and attempt speech spans run from their first to last usable word.
- `start_delay_ms` is attempt first aligned word start minus reference first
  aligned word start. Positive is late; negative is early.
- Duration delta is attempt speech duration minus reference speech duration;
  the ratio is its absolute value divided by reference speech duration.
- Word-timing MAE compares matched word starts relative to each stream's first
  matched word, removing initial start delay. It requires at least three timed
  matched words.
- Pause MAE compares consecutive aligned inter-word gaps and requires at least
  two usable gaps.

Unavailable evidence is returned as `null` and displayed as **Unavailable**.
No pronunciation, accent, pitch, intonation, fluency, or composite score is
invented.

## Sync target and progress

`Sync target met` requires recognized speech, Transcript Match at least 90%, and
duration-delta ratio at most 20%. With a reliable reference it also requires
absolute start delay at most 600 ms; available word-timing MAE must be at most
500 ms and available pause MAE at most 450 ms. A short sentence is not failed
only because an optional MAE cannot be calculated.

Three consecutive target hits produce **Stable sync achieved**. A miss resets
the consecutive counter; total hits remain. This is repeated success against
the explicit text/timing criteria, not permanent mastery or native-like speech.

Learning Library schema version 3 adds `shadowing_progress`:

- last sentence and last-practiced UTC timestamp;
- per-sentence attempt count, target-hit count, consecutive-hit count, latest
  timestamp, and latest metrics;
- bounded best-observed Transcript Match and lower-is-better duration ratio,
  absolute start delay, word timing, and pause metrics.

Best values may come from different attempts and are not presented as one
perfect attempt. No raw audio or unbounded event history is persisted.
Lightweight Library summaries expose attempts, target-hit sentence count,
stable sentence count, and last activity for Shadowing Home.

Library v1 and v2 files remain readable without a GET/startup rewrite and
receive default shadowing progress in memory. The next successful mutation
writes v3 atomically while preserving Lesson, Guided progress, ListeningProgress,
persistent audio, identity, and timestamps. Malformed or future-version primary
storage is not overwritten.

The attempt endpoint runs ffmpeg/Whisper outside the Library lock, then verifies
the submitted `expected_attempt_count` and updates progress once under the
process-local store lock. A stale or double submission returns HTTP 409 instead
of overwriting newer progress.

## Routes and product boundaries

The main local endpoint is:

```text
POST /api/library/items/{item_id}/shadowing/attempt
multipart: audio, sentence_id, expected_attempt_count, client_recording_duration
```

Shadowing Home and the Library detail open the same workspace. Listening's
current-sentence action opens that workspace at the active sentence. Reopening
restores the last sentence and persisted metrics, but never a microphone stream
or recording blob. Guided Shadowing, Listening, Study Bank, Daily Review, Chunk
Memory, Personal Session, and the fixed Guided stage order remain independent.

## Known limitations

- Local ASR can make recognition mistakes; Transcript Match is not
  pronunciation quality.
- Word timing quality depends on Whisper timestamps and browser/audio-device
  latency.
- Speaker playback may leak into the microphone; headphones are recommended.
- There is no phoneme, accent, pitch, intonation, or cloud pronunciation
  assessment.
- There is no cloud sync, full-video archive, raw-recording archive, or
  unlimited attempt history.
- The first English or German use may require the configured local model to
  already be cached or be downloaded separately; M4.5 itself uses no paid API.
- Sync Shadowing is not the future Speaking Agent and does not change Realtime
  or agent behavior.

## Required real-Mac smoke after merge

Cloud validation does not verify the real microphone, device latency, local
Whisper inference, or macOS audio. After merge:

A. Update the Personal Desktop app.
B. Open `Language AI.app`.
C. Confirm top navigation includes **Shadowing**.
D. Open the Paris German media.
E. Open **Sync Shadowing**.
F. Choose a short, clear German sentence.
G. Play Reference.
H. Test 0.75x preview.
I. Click **Start Sync Attempt**.
J. Allow microphone permission.
K. Verify countdown 3–2–1–Go.
L. Verify the reference plays at 1.0x while the microphone records.
M. Speak along, preferably with headphones.
N. Verify recording stops automatically.
O. Wait for local analysis.
P. Confirm recognized German text appears.
Q. Inspect Transcript Match, missed, extra, substituted, and duration difference.
R. When available, inspect start delay, word-timing MAE, and pause MAE.
S. Play My Recording.
T. Try Again.
U. Start deliberately late and verify start delay worsens.
V. Skip a word and verify transcript/missed-word evidence changes.
W. Produce a good attempt and check the Sync target behavior.
X. Repeat successful attempts to test consecutive target hits.
Y. Close and reopen the app.
Z. Confirm last sentence/progress persists but raw recording does not.

Also verify Listening, Daily Review, Words & Chunks, Guided Lesson, and Guided
Shadowing still work and that no Azure, OpenAI, or Gemini request occurs. Do not
mark M4.5 verified until this real-Mac smoke succeeds.
