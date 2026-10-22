# Complete Practice Utterances

Listening and Sync Shadowing use the same `Lesson.sentences` timeline. New
Lessons reconstruct complete lexical utterances before that timeline is
created; raw subtitle cues and raw Whisper segments are not learning units.

## Caption reconstruction

The VTT parser retains inline timestamps and explicit `<v Speaker>` labels.
For cues without word timing, token times are conservatively distributed over
the cue. Temporally overlapping or adjacent rolling-caption windows use exact,
normalized suffix/prefix token overlap. Only the novel suffix is emitted.
Exact active-window revisions and contained revisions are ignored. This logic
is time-gated: repeated speech after a gap remains repeated speech, and an
adjacent one-word repetition is not discarded merely because its text matches.

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
  exact-token rather than semantic or fuzzy.
- No speaker identity or diarization is invented when VTT lacks an explicit
  voice label.
- Larger Shadowing models trade better short-utterance recognition for higher
  first-load download size, memory use, and CPU latency on real Macs.
