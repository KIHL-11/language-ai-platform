# M5 Contextual Vocabulary and Chunk Learning

## Product role and roadmap

M5 reduces the interruption between hearing authentic English or German and
understanding it. M5.1 adds a read-only contextual dictionary to visible words
in Listening. It is intentionally separate from Study Bank and Daily Review.

- M5.1: clickable dictionary lookup, progressive explanations, pronunciation.
- M5.2: explicit contextual saving to Words & Chunks with media provenance.
- M5.3: bounded, user-approved video chunk recommendations.
- M5.4: saved-chunk speaking practice with transcript-grounded reuse evidence.

M5.1 does not save words, alter `ReviewState`, or rate SRS cards.

## Architecture

```text
Listening ClickableText
  -> POST /api/dictionary/lookup
  -> DictionaryService (bounded LRU cache and sense selection)
  -> DictionaryProvider protocol
  -> read-only SQLiteDictionaryProvider
  -> locally imported Kaikki/Wiktextract JSONL

Pronunciation button
  -> licensed Wikimedia recording URL when present
  -> otherwise explicit /api/tts Edge TTS request
```

The provider boundary prevents the UI and API contracts from depending on the
Kaikki JSONL shape. SQLite is opened in `mode=ro`; the complete JSONL corpus is
never loaded into request memory. The importer streams one line at a time and
atomically replaces the index only after a successful build.

## Sources, coverage, and licensing

The supported import format is the JSON Lines output produced by
[Wiktextract](https://github.com/tatuylonen/wiktextract) and distributed by
[Kaikki](https://kaikki.org/dictionary/). Wiktextract documents extraction of
glosses, parts of speech, forms, translations, examples, IPA, and Wikimedia
audio URLs. Actual coverage varies by Wiktionary edition, language, entry, and
dump date; none of those fields is assumed to exist.

Important license boundaries:

- Wiktextract software is MIT licensed.
- Extracted Wiktionary content retains the applicable Wiktionary content
  license. The upstream Wiktextract repository identifies relevant test/content
  data as CC BY-SA 3.0 or GFDL. Confirm the current terms of the specific dump
  before redistributing an index.
- The UI attributes each returned entry to Wiktionary data extracted by
  Wiktextract/Kaikki and links to its source entry.
- Wikimedia Commons pronunciation files have per-file licenses. M5.1 links to
  allow-listed HTTPS Wikimedia audio and does not copy or redistribute it. The
  dictionary text license must not be assumed to cover audio.
- Cambridge, Oxford, Duden, and other proprietary dictionaries are not scraped
  or copied.

The repository contains schema-shaped deterministic test fixtures only. They
are not a dictionary corpus and are not evidence of real Kaikki coverage. The
managed cloud used for M5.1 could not reach `kaikki.org`, so no real-data smoke
is claimed by automated tests.

## Installing or updating local dictionary data

Download the desired current JSONL exports directly from Kaikki in accordance
with their terms. For Level 1 definitions, use both the English Wiktionary
edition for English entries and the German Wiktionary edition for German
entries. An English-edition definition of a German word is not presented as a
German monolingual definition.

Build one atomic index from both files:

```bash
cd backend
../.venv/bin/python scripts/import_kaikki_dictionary.py \
  --database storage/dictionary.sqlite3 \
  --content-license "CC BY-SA 3.0 or GFDL" \
  --english-input /path/to/english-edition.jsonl \
  --english-source-url https://kaikki.org/dictionary/ \
  --german-input /path/to/german-edition.jsonl \
  --german-source-url https://kaikki.org/dewiktionary/
```

Use the actual current license label published for the downloaded editions;
the value above reflects the upstream Wiktextract/Wiktionary notice inspected
during M5.1 and must be rechecked when the dump terms change.

The default runtime location is `backend/storage/dictionary.sqlite3`. Set the
non-secret `DICTIONARY_DB_PATH` to use another path. Re-run the same command to
update data; an invalid JSONL build leaves the previous database untouched.
The database lives under ignored runtime storage and must not be committed.

## Lookup, morphology, and contextual selection

The importer indexes lemmas and source-provided forms. Irregular English forms,
German plurals/cases, and irregular verbs resolve only when present in the
lexical dataset—there is no unsafe suffix-stripping guess. Multi-token forms
are component-indexed; an entry such as `herausfinden` can resolve from a
source-provided form such as `finde heraus` when its ordered components occur
in the current sentence. Ambiguous candidate lemmas remain visible.

Sense selection is deterministic and source-grounded. It compares lexical
overlap between the current sentence and source definitions/examples. A unique
positive match is prioritized. Otherwise source ordering is used and multiple
senses are marked ambiguous under **More meanings**. It is not semantic AI
ranking. For multi-sense entries, a translation is associated only when its
source sense label can be linked to that definition; uncertain translations
remain unavailable rather than being shown for the wrong sense.

## Three-level explanation contract

For English, levels are English -> English, English -> German, then English ->
Simplified Chinese. For German they are German -> German, German -> English,
then German -> Simplified Chinese. Level 1 is open by default; Levels 2 and 3
are independently expandable. Missing data is explicit. Dictionary definitions
and dictionary translations carry field-level source metadata; M5.1 does not
generate or present AI paraphrases as dictionary content.

The source Listening sentence is shown separately from the dictionary entry.
No contextual sentence translation is invented when the source lacks one.

## Pronunciation

Source IPA and allow-listed Wikimedia recording URLs are returned when present.
When no recording is available, an explicitly labeled Edge TTS option is shown:

- English: `en-US` and `en-GB`
- German: `de-DE`

Opening an entry never plays or downloads pronunciation. Playback starts only
after the learner presses a pronunciation button. TTS requires network access
to the Edge service and is not a human dictionary recording. The Listening
media controller is not sought, paused, or restarted by pronunciation lookup.

## API and caching

`POST /api/dictionary/lookup` accepts `language`, a bounded lexical `word`, a
bounded source `sentence`, and `target_language=zh-CN`. It returns optional
lemma/POS/pronunciation fields, the three explanation levels, contextual
selection status, forms, examples, alternate senses, and attribution. Unknown
and unconfigured states are normal structured responses. Invalid input is 422;
an unreadable or unsupported index is a controlled 503.

Successful and unavailable lookups use a process-local 1,024-entry LRU cache.
Lookups are read-only and do not persist full transcript text.

## Listening UX, accessibility, and privacy

Unicode-aware tokenization preserves original text and punctuation, including
German umlauts/`ß` and apostrophe contractions. Word buttons are keyboard
accessible. Lookup occurs only on click, never once per rendered transcript
word. Closing the compact panel does not change playback position, sentence,
or auto-follow state.

Blind mode and hidden source text render placeholders instead of lexical
buttons. Hidden words therefore do not appear in labels, titles, focus order,
or a dictionary panel. An explicit **Reveal text** action restores lookup.

Dictionary lookup does not mutate Library, Listening progress, Study Bank,
Chunk Memory, or Daily Review. External recording/TTS traffic occurs only after
an explicit pronunciation action. No credentials are used or returned.

## Known M5.1 limitations

- Real coverage depends entirely on the locally installed dump editions/date.
- Some entries lack translations, Chinese, IPA, examples, forms, or audio.
- Contextual selection is lexical, not semantic; ambiguity is surfaced.
- Separable and inflected forms require a source-provided indexed form.
- Commons per-file audio license metadata is not yet imported.
- Edge TTS is a network fallback, not offline or a dictionary recording.
- No word saving, contextual AI explanation, chunk recommendation, or speaking
  integration is implemented until M5.2–M5.4.

## Real-Mac acceptance checklist

1. Install current English- and German-edition Kaikki data with the command
   above, then update/relaunch Language AI.app.
2. Open an English saved media item in Listening and click a visible word.
3. Confirm Level 1 English is open; independently expand German and Chinese.
4. Confirm the source sentence and source attribution remain distinct.
5. Test an English irregular form such as `went`/`children` supported by the
   installed dataset.
6. Play US and UK pronunciation options where present; verify actual audible
   output and correct labels for recordings versus TTS.
7. Open German media and test a normal word, umlaut plural such as `Häuser`, an
   irregular form such as `ging`, and a source-supported separable verb.
8. Confirm German Level 1, English Level 2, and Chinese Level 3 behavior.
9. Play German pronunciation and verify audible `de-DE` output.
10. While media is playing, open and close the panel and confirm playback
    position, transcript navigation, and auto-follow remain unchanged.
11. Enter Blind mode and verify hidden words are absent from mouse, keyboard,
    accessibility labels, and dictionary content until explicit Reveal.
12. Verify Listening and Sync Shadowing still use the same sentences.
13. Compare Study Bank and Daily Review before/after lookup and confirm no item,
    due date, or rating changed.

M5.1 remains **IMPLEMENTED, REAL-MAC VERIFICATION PENDING** until these checks,
including real dictionary coverage and audible English/German pronunciation,
are completed on the target Mac.
