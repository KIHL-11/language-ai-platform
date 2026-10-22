# Complete Practice Utterances

Listening and Sync Shadowing use the same `Lesson.sentences` timeline. New
Lessons reconstruct complete lexical utterances before that timeline is
created; raw subtitle cues and raw Whisper segments are not learning units.

## Caption reconstruction

The VTT parser retains inline timestamps and explicit `<v Speaker>` labels.
It treats each physical caption line as a display snapshot before combining a
cue; it never blindly concatenates every timestamped line. Same-timestamp
snapshots may replace an active display tail, including cumulative expansion,
middle-token correction, and punctuation-only correction. Sliding rows use
exact lexical suffix/prefix overlap, ignoring punctuation for matching while
retaining the newest punctuation in rendered text.

Each internal token records its caption window and line provenance. An inline
marker anchors the first lexical token after that marker. Additional tokens
whose positions are merely distributed to the next marker/cue boundary remain
explicitly `estimated`; they are not represented as source-provided word
timing. This distinction stays internal and is not added to persisted Lesson
schemas.

Revision removal is time-gated. Identical inline timestamps are strong display
revision evidence. A later source timestamp after the earlier token end is
evidence of new speech, so intentional repetitions and hesitations remain.
Estimated middle rewrites that cannot be classified safely are preserved and
flagged `ambiguous_revision`, rather than silently deleting possible speech.

The canonical token stream is split at strong punctuation (`.`, `!`, `?`, or
`…`), an explicitly supplied voice change, or at least 900 ms of lexical
silence. A 500–900 ms pause is a boundary only after clause punctuation such
as a comma, semicolon, or colon. The media tail is flushed once. There is no
character-count split; in particular, 160 characters is not a boundary.

Known non-speech labels (`music`, `musik`, `singing`, `singen`, `applause`,
`laughter`, `cheering`, and `inaudible`) are removed before tokenization.
Noise-only windows therefore cannot become practice cards, while short real
utterances such as “Ja.” remain valid.

Media Whisper fallback requests word timestamps and feeds those words through
the same utterance boundary implementation. A provider segment is never
treated as an authoritative sentence boundary.

Before subtitle utterances become a new Lesson, a deterministic quality gate
checks invalid timing, unresolved revision ambiguity, overlapping adjacent-card
carry-over, and utterances longer than 30 seconds. Estimated timing alone is a
diagnostic, not a rejection. A critical subtitle result uses the existing local
Whisper path once; it does not add a remote provider or repeat media download.
Long text is never split only to satisfy the duration guide.

The regression fixture
`backend/tests/fixtures/easy_german_screenshot_pattern.vtt` is deliberately
labelled as a sanitized reconstruction of the reported screenshot structure.
It is not the original Easy German VTT, and its non-anchor times are not claimed
as audio-verified ground truth. The real source URL/VTT/audio was unavailable in
the cloud workspace, so final source/audio validation remains a Real-Mac task.

## Existing Library repair

Existing saved items are never rewritten by reading them. The explicit
`POST /api/library/items/{item_id}/repair-transcript` operation first returns a
side-effect-free preview. Committing requires both the preview's
`expected_updated_at` timestamp and `confirm_sentence_progress_reset=true`.
The Library update is lock-protected and atomic.

Old sentence IDs map to new IDs by deterministic positive time-range overlap.
Guided and Listening positions follow that mapping. Sentence Shadowing
progress is retained only for a one-to-one mapping; unmatched or many-to-one
progress is listed in the preview and reset on confirmed repair. Derived
reference-timing cache data is invalidated.

Study Bank provenance and ReviewState are separate primary data and are not
rewritten. Their saved sentence text/timing remains an immutable historical
snapshot. Cross-system Today/Shadowing links are offered only when that timing
still overlaps the currently addressed Library sentence, preventing an old ID
that was reused after repair from opening an unrelated utterance. This policy
avoids silently corrupting curated or scheduled learning history.

## Shadowing ASR

Scored Shadowing has a purpose-specific local configuration:

- English: `WHISPER_SHADOWING_MODEL_EN`, default `small.en`
- German/multilingual: `WHISPER_SHADOWING_MODEL_MULTI`, default `small`
- fixed Lesson source language
- beam size 5, temperature 0, word timestamps enabled
- previous-text conditioning disabled
- no expected-text prompt, hotwords, or forced target text

Models remain lazily loaded, process-cached, CPU/int8, and local. Changing the
model invalidates the derived reference-word cache signature. The first use of
an uncached model requires the usual local faster-whisper model download; no
paid API is used.

Microphone uploads are transiently converted by ffmpeg to mono 16 kHz WAV,
with an 80 Hz high-pass and dynamic level normalization. These filters do not
change playback speed or intentionally shift the utterance timeline. Input and
output temp files are removed when analysis ends, and raw recordings are not
stored.

VAD is intentionally not enabled for scored short utterances: aggressive
speech trimming can remove genuine short German speech and change the timing
origin used by sync metrics. Empty recognition remains an explicit
insufficient-speech result. The UI reports recognized text, speech detection,
and the local ASR configuration. Reference playback bleed cannot be reliably
eliminated in software, so headphones remain recommended; no metric should be
interpreted as a pronunciation score.

## Limitations

- Repair uses the saved Lesson transcript/timing because raw downloaded VTT is
  not retained. It cannot recover words absent from that snapshot.
- Auto-caption timestamps can be approximate, and the overlap algorithm is
  exact-token rather than semantic or fuzzy. Unresolved edits are flagged and
  sent to local audio transcription instead of being guessed.
- No speaker identity or diarization is invented when VTT lacks an explicit
  voice label.
- Larger Shadowing models trade better short-utterance recognition for higher
  first-load download size, memory use, and CPU latency on real Macs.
