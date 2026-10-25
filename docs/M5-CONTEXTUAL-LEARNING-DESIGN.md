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
- Extracted Wiktionary content retains the applicable current Wiktionary
  edition/dump license. As checked on 2026-10-24, Wikimedia's current
  [production configuration](https://github.com/wikimedia/operations-mediawiki-config/blob/master/wmf-config/InitialiseSettings.php)
  gives Wiktionary the default **Creative Commons
  Attribution-ShareAlike 4.0** notice; English and German Wiktionary have no
  license override in that configuration. The importer deliberately has no
  built-in license default: record the current dump label with
  `--content-license`, and recheck the Kaikki download notice before each real
  import. Attribution and ShareAlike obligations apply when redistributing an
  index; content that carries additional source-specific terms remains subject
  to those terms.
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

Download the desired current JSONL gzip exports directly from Kaikki in
accordance with their terms. The supported edition exports are:

- English Wiktionary edition:
  `https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz`
- German Wiktionary edition:
  `https://kaikki.org/dewiktionary/raw-wiktextract-data.jsonl.gz`

The importer decompresses these files as streams; no expanded JSONL copy is
needed. Each raw edition contains entries for multiple languages. The English
source therefore imports only `lang_code == "en"`, and the German source only
`lang_code == "de"`. All mismatched entries are read to validate the complete
gzip stream but are excluded from SQLite. This ensures an English-edition
definition of a German word is not presented as German monolingual content.

Build one atomic index from both files:

```bash
cd backend
.venv/bin/python scripts/import_kaikki_dictionary.py \
  --database storage/dictionary.sqlite3 \
  --content-license "CC BY-SA 4.0" \
  --english-input /path/to/english-raw-wiktextract-data.jsonl.gz \
  --english-source-url https://kaikki.org/dictionary/raw-wiktextract-data.jsonl.gz \
  --german-input /path/to/german-raw-wiktextract-data.jsonl.gz \
  --german-source-url https://kaikki.org/dewiktionary/raw-wiktextract-data.jsonl.gz
```

Reconfirm the label on the current Kaikki/Wiktionary source notices immediately
before the real import. The command will not silently substitute a historical
label.

The default runtime location is `backend/storage/dictionary.sqlite3`. Set the
non-secret `DICTIONARY_DB_PATH` to use another path. Re-run the same command to
update data. Invalid JSON, corrupt gzip data, a truncated gzip trailer, or a
decompression checksum failure aborts the build, removes the temporary index,
and leaves the previous database untouched.
The database lives under ignored runtime storage and must not be committed.

The optional inflection list shown under **More** uses a separate disposable
Sidecar index. The source dictionary has no `forms(entry_id)` index, so querying
that list directly can scan the multi-gigabyte `forms` table. Build the Sidecar
after installing or replacing the source dictionary:

```bash
cd backend
.venv/bin/python scripts/build_dictionary_forms_index.py \
  --database storage/dictionary.sqlite3
```

The default output is `backend/storage/dictionary.forms.sqlite3`. Set the
non-secret `DICTIONARY_FORMS_INDEX_PATH` only when a different runtime location
is needed. The builder opens the source with SQLite `mode=ro` and
`PRAGMA query_only=ON`, streams rows with bounded memory, retains the first 30
forms in source order, validates the completed index, and atomically publishes
it. A process lock prevents concurrent builders. An interrupted build leaves an
existing valid Sidecar untouched; incomplete build files are removed on the
next safe build.

The Sidecar records the source path, schema version, size, nanosecond mtime,
device and inode. A missing, stale, unsupported, or corrupt Sidecar never causes
a runtime fallback to the unindexed source-table scan. Core definitions,
translations, pronunciation, examples and alternative meanings remain usable,
while **More** displays a rebuild instruction for inflections. Sidecar files,
build artifacts and lock files are generated runtime data and must not be
committed.

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
bounded source `sentence`, `target_language=zh-CN`, and optional
`include_inflections`. It returns optional
lemma/POS/pronunciation fields, the three explanation levels, contextual
selection status, forms, a structured inflection-index status/message, examples,
alternate senses, and attribution. Unknown and unconfigured states are normal
structured responses. Invalid input is 422; an unreadable or unsupported source
dictionary is a controlled 503. A Sidecar problem is a non-fatal structured
state because it affects only optional forms.

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
