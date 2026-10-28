# DQ-02 Phase 6 — Dictionary Migration Architecture

Status: **architecture accepted for isolated validation; not production authorization**

Baseline: `b2f9323d0affd857f4a75903901e0765c54bc0f1` (merged PR #47)

## Decision

Kaikki dictionary updates are built as immutable, versioned releases outside the
active runtime path. Each release contains the dictionary SQLite database, its
forms Sidecar, a provenance manifest, and a quality report. The backend reads both
SQLite files through one stable `dictionary_current` directory symlink. Cutover
stops the backend, atomically replaces that one symlink, and restarts the process.

No import command may target the installed `dictionary.sqlite3`. A release is not
eligible for cutover until its sources, outputs, SQLite health, Sidecar identity,
quality comparison, disk budget, and rollback release have all been verified.

This document does not authorize downloading full Kaikki dumps, rebuilding or
replacing the real dictionary, changing learning data, or deploying a release.

## Current architecture and constraints

### Dictionary build

`backend/scripts/import_kaikki_dictionary.py` streams JSONL or gzip JSONL into
`import_kaikki_sources`. The importer:

- restricts English-edition rows to `lang_code == "en"` and German-edition rows
  to `lang_code == "de"`;
- writes `<destination>.tmp`, commits, closes it, and replaces the requested
  destination only after a successful import;
- removes the temporary file after parse/decompression/import failure;
- records only schema version, joined source URLs, and one content-license label
  in SQLite metadata;
- does not run `integrity_check`, `foreign_key_check`, quality comparison, output
  hashing, or directory `fsync` as part of the CLI.

Its atomic replacement protects the path passed to it, but it is not a safe
production migration driver. The migration driver must always pass an unused
candidate path inside a new release directory.

### Forms Sidecar identity

`build_dictionary_forms_index.py` opens the dictionary read-only, streams forms,
builds `<sidecar>.building`, validates the query plan and integrity, fsyncs the
file and directory, and atomically publishes the Sidecar. It stores the source:

- resolved absolute path;
- byte size;
- nanosecond mtime;
- device and inode;
- dictionary schema version.

Runtime lookup rejects a missing, corrupt, unsupported, or identity-mismatched
Sidecar without scanning the large source forms table.

Consequently, a Sidecar built in a staging directory becomes stale if its source
release is later renamed. The final release directory name must be chosen before
database import and must never change.

### Runtime caches

`dictionary_api.dictionary_service` is constructed once when the backend imports
the API module. `DictionaryService` has a 1,024-result lookup cache. The provider
has a separate 512-result forms cache keyed by database and Sidecar file stamps.
Replacing files without restarting can therefore serve old core lookup results,
even if the forms cache notices new file identities. A process restart is a
mandatory part of cutover and rollback.

### macOS lifecycle

The Personal Desktop backend is a per-user LaunchAgent named
`com.language-ai-platform`. `update_personal_app_macos.sh` rebuilds the frontend,
uses `launchctl kickstart -k`, and checks only `/api/health`. That health endpoint
does not open or validate the dictionary.

The general updater is not the dictionary migration primitive. The future
migration command must explicitly stop the LaunchAgent, switch the dictionary
release, restart it, and run dictionary-specific smoke queries. Phase 7 must also
configure the LaunchAgent with non-secret paths equivalent to:

```text
DICTIONARY_DB_PATH=<repository>/backend/storage/dictionary_current/dictionary.sqlite3
DICTIONARY_FORMS_INDEX_PATH=<repository>/backend/storage/dictionary_current/dictionary.forms.sqlite3
```

## Release layout

All paths must be on the same filesystem so symlink replacement is atomic:

```text
backend/storage/
├── dictionary_current -> dictionary_releases/20261027T120000Z-<sha12>
└── dictionary_releases/
    └── 20261027T120000Z-<sha12>/
        ├── dictionary.sqlite3
        ├── dictionary.forms.sqlite3
        ├── manifest.json
        ├── quality-report.json
        └── READY
```

The release directory is created with its final name and is never renamed.
`READY` is written last, after validation, and contains the SHA-256 of the final
manifest. Published release files become read-only. A `.building` release without
`READY` is never selectable.

The initial migration from legacy flat files first creates and validates a
versioned rollback release from the existing database. It builds a fresh Sidecar
against that rollback release path; copying an old Sidecar would fail identity
validation.

The one-time legacy conversion is explicitly non-destructive:

1. Stop the LaunchAgent and save its exact plist plus loaded configuration.
2. Hash the closed legacy database and learning stores; if WAL/journal state is
   present, use SQLite backup rather than deleting or checkpointing user files.
3. Create the final `legacy-<database-sha-prefix>` directory without moving the
   installed database. Copy/backup into `dictionary.sqlite3`, validate it, and
   rebuild `dictionary.forms.sqlite3` against that final new path.
4. Write `rollback-verification.json` containing copied output hashes and
   Sidecar identity, then write `READY` containing that verification file's hash.
   Legacy source provenance may remain explicitly unverified; output integrity
   and rollback usability may not.
5. Generate a new plist whose dictionary variables point through
   `dictionary_current`, preserve the old plist as rollback evidence, and verify
   the serialized plist before loading it.
6. If bootstrap, dictionary smoke, or learning hash checks fail, restore the old
   plist and start the untouched flat-file configuration. Do not delete either
   representation.

`install_personal_app_macos.sh` regenerates and loads a plist, while
`update_personal_app_macos.sh` can restart the existing agent. Phase 7 must update
the generator and its tests so a reinstall preserves the versioned dictionary
variables. The migration validator must compare the loaded `launchctl print`
environment with the expected resolved paths; editing a plist on disk is not
proof that the running service uses it.

## Data provenance manifest

The machine-readable example is
[`DICTIONARY-BUILD-MANIFEST-v1.example.json`](DICTIONARY-BUILD-MANIFEST-v1.example.json).
It is validated by
[`DICTIONARY-BUILD-MANIFEST-v1.schema.json`](DICTIONARY-BUILD-MANIFEST-v1.schema.json).

Required rules:

1. `manifest_version` and every required field are validated before build and
   before cutover. Unknown major versions fail closed.
2. A rolling Kaikki URL is an acquisition locator, not a snapshot identifier.
   Snapshot identity is the SHA-256 plus byte size of the downloaded gzip.
3. Downloads use a `.part` file. Hash and gzip trailer validation must complete
   before atomic publication under a content-addressed filename.
4. `dump_date` and `extraction_date` are recorded only with verifiable evidence
   from an immutable URL, source metadata, or documented response header. Unknown
   dates are `null` with status `unverified`; file mtime is not invented as a dump
   date.
5. HTTP ETag, Last-Modified, Content-Length, final resolved URL, and retrieval
   timestamp are evidence, not identity. Weak/missing ETags do not block a build
   when SHA-256 and size are present.
6. License label, notice URL, attribution text, and verification time are recorded
   per edition. If the two editions require incompatible entry-level labels, the
   current single-license importer is insufficient and Phase 7 must stop for a
   schema/importer decision.
7. The importer commit is the full Git SHA, the worktree must be clean, and the
   command arguments contain artifact names rather than secret or machine-private
   paths.
8. Final dictionary and Sidecar hashes are calculated after all writes. The
   manifest is then written atomically and never edited in place.

There are two deliberately separate validation levels:

- **structure validation** accepts a well-typed example or build-in-progress
  manifest. It checks complete fields, HTTPS URLs, timezone-aware dates, safe
  filenames, count/rate types, and telemetry reconciliation;
- **READY eligibility** is a read-only check against actual files. It rejects
  `example_only=true`, placeholder hashes, zero-byte sources/outputs, unverified
  licenses, incomplete telemetry, nonzero gold failures, mismatched database
  statistics, unsafe/symlinked paths, invalid SQLite/Sidecar identity, and a
  missing or unverified rollback release.

`backend/scripts/validate_dictionary_release.py` implements the second level. It
does not create `READY`; a future publisher may write `READY` only after this
command succeeds and must write the final manifest SHA-256 as its contents.

The manifest deliberately separates raw-source facts, build implementation,
SQLite statistics, import telemetry, validation results, and output identity.
This prevents a new Kaikki snapshot from being mistaken for an importer-only
quality change.

## Build procedure

### 1. Preflight and source acquisition

1. Require a clean repository at the approved importer SHA.
2. Confirm the release root and candidate are on the production filesystem.
3. Calculate the disk requirement described below; fail before download/build if
   available bytes are below the requirement.
4. Fetch each rolling URL once into a unique `.part` artifact with bounded retry
   and timeout. Do not silently resume if server validators changed.
5. Record response metadata, validate Content-Length when supplied, stream
   SHA-256, validate the complete gzip trailer, and atomically publish a filename
   containing edition, verified date if available, size, and hash prefix.
6. Reconfirm the current source license notices. Never copy a historical license
   label without verification.

Phase 6 does not perform these downloads.

### 2. Preserve and verify the current release

Before any build, open the current dictionary with SQLite `mode=ro` and
`query_only=ON`; do not use immutable mode against a live database. Record:

- size, mtime, inode/device, SHA-256;
- schema and metadata rows;
- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`;
- entry/sense/form/pronunciation counts;
- current Sidecar status and identity;
- the complete baseline quality report.

Before cutover, stop the backend and produce a recoverable rollback release. For
a closed database with no WAL/journal files, a verified byte copy is acceptable.
If WAL state exists, use SQLite's read-only source backup API to create the
rollback database, then validate the backup independently. Never delete or alter
the source WAL merely to simplify copying. Build a new rollback Sidecar against
the rollback database path. Verify rollback dictionary lookups before proceeding.

### 3. Isolated candidate build

1. Allocate the final immutable release directory and build ID.
2. Run the importer with `--database` pointing only inside that new directory.
3. Capture wall time, peak RSS, source counters, rejection reasons, and output
   counts in a build report. Phase 7 must add importer telemetry for sense-local
   translations and entry-level alignment; these facts cannot be recovered from
   schema v1 after import.
4. Fsync the completed dictionary and its directory before validation.
5. Never open or replace the installed production dictionary during this phase.

### 4. Candidate validation

Open the candidate read-only and require:

- `integrity_check == "ok"`;
- an empty `foreign_key_check` result;
- supported schema version and required metadata;
- expected indexes in `EXPLAIN QUERY PLAN` for lemma, form, component, sense, and
  pronunciation lookups;
- bounded JSON fields that parse successfully;
- no unexpected WAL/journal/shm files;
- quality gates and gold cases below;
- stable cold/hot lookup latency over multiple distinct uncached words.

### 5. Build the Sidecar

Only after the candidate database is final and validated, build the Sidecar at
its final path in the same immutable release directory. The release directory
must not be renamed afterward. Require:

- Sidecar `integrity_check == "ok"`;
- primary-key query plan;
- exact source identity match;
- `lookup_forms(...).status == "available"` for English and German samples;
- source dictionary hash and mtime unchanged by the Sidecar build.

Write the final manifest, quality report, and `READY` marker only after these
checks pass.

## Quality comparison

### Structural metrics

Report old and candidate values, absolute delta, and percentage delta separately
for English and German:

- entries and senses;
- forms and pronunciations;
- entries with at least one Chinese translation and their percentage;
- senses with at least one Chinese translation and their percentage;
- valid Simplified/Traditional/mixed Chinese values and rejected Cyrillic-only
  values;
- common-word Chinese coverage over a versioned, reviewed word list.

### Import alignment telemetry

Schema v1 does not retain translation provenance. Phase 7 instrumentation must
count during import, by edition and target language:

- direct sense-local translations seen, retained, deduplicated, and truncated;
- entry-level translations seen;
- accepted as unique multi-token matches;
- accepted as reliable one-token lexical-head matches;
- accepted as unscoped single-sense fallback;
- rejected as ambiguous/tied, zero evidence, generic/stopword/lemma-only,
  explicit single-sense mismatch, unsupported morphology, or over limit.

Sense-local retention must be 100% except for documented validation, duplicate,
or per-language limit rules. Entry-level discard rate is reported, not hidden.

### Gold contextual cases

The report must include selected lemma, POS, definition, Chinese value, status,
selection method, candidates, and forbidden translations for at least:

| Case | Required evidence |
|---|---|
| `I get what you mean.` / get | understand sense; no obtain/become list |
| `I need to get a new laptop.` / get | obtain/acquire sense |
| `He got sick yesterday.` / got | lemma `get`; become/state sense where source supports it |
| `Ich komme morgen nach Hause.` / komme | lemma `kommen` |
| `Die Entwicklung dauert mehrere Jahre.` | lemma and source-backed definition; missing Chinese is honest |
| river bank | river/shore sense, not financial |
| financial bank | financial institution sense, not river |
| English `die` | English content token is not removed as a German stopword |
| German compound fixture | unsupported compound similarity does not fabricate alignment |

Wrong-association rate is `incorrect associations / reviewed associations` over
a versioned gold set. The acceptance target is zero known incorrect associations.
Missing translations count as coverage gaps, not incorrect associations.

### Snapshot versus importer attribution

If raw source SHA-256 values are unchanged, old/new output deltas may be attributed
to importer changes. If the snapshot changed, the report must label aggregate
deltas as **confounded by source snapshot**.

The preferred comparison matrix is:

1. old importer + old snapshot (existing baseline);
2. new importer + the same old snapshot (importer effect);
3. new importer + new snapshot (snapshot plus importer result).

If the old raw gzip is unavailable, row 2 cannot be reproduced and no report may
claim that all coverage change came from Strict Sense Alignment.

### Performance gates

Measure multiple distinct uncached terms, then cache hits:

- common core lookup cold target: under 1 second;
- cache hit target: under 500 ms;
- Sidecar forms lookup: indexed and bounded, with no source `SCAN forms` fallback;
- payload sizes and alternate senses remain bounded.

Results are recorded with Mac model, macOS version, Python version, database
sizes, and whether filesystem caches were warm. These are targets, not fabricated
guarantees from cloud fixture results.

## Cutover

Cutover requires separate Owner approval and a validated candidate/rollback pair.

1. Revalidate source/output hashes, manifest schema, `READY`, quality gates, and
   disk headroom.
2. Stop the LaunchAgent with `launchctl bootout`; confirm the label/process is
   gone. Do not use ordinary `/api/health` failure alone as proof of shutdown.
3. With writers stopped, record hashes, sizes, mtimes, and existence for learning
   stores without logging their contents: `study_bank.json`, `chunk_bank.json`,
   `learning_library.json`, `speaking_sessions.json`, and Library media
   metadata/files.
4. Create or revalidate the closed-database rollback release one final time.
5. Create `dictionary_current.next` pointing to the candidate release. Fsync the
   containing directory, atomically replace `dictionary_current` with
   `os.replace`, and fsync the directory again.
6. Bootstrap/start the LaunchAgent. Process restart clears dictionary and forms
   caches.
7. Require `/api/health`, dictionary configuration, core lookup, Chinese level,
   and `include_inflections=true` smoke checks for both languages.
8. Re-hash learning stores and require exact equality. Any drift blocks acceptance
   and triggers investigation/rollback.
9. Keep the rollback release and source artifacts for the approved retention
   period. Do not delete them as part of cutover.

The symlink target, candidate release, and rollback release are logged by build
ID and hash, never by unverified inference.

## Rollback

Rollback uses the same controlled process:

1. Stop the LaunchAgent and confirm termination.
2. Revalidate the rollback release, including its Sidecar identity.
3. Atomically replace `dictionary_current` with a symlink to the rollback release
   and fsync the parent directory.
4. Restart the backend and run the old-release dictionary smoke suite.
5. Verify learning-store hashes remain unchanged.
6. Preserve the failed candidate, logs, manifest, and quality report for diagnosis.

If failure occurs before symlink replacement, production remains unchanged. If
the process dies during `os.replace`, `dictionary_current` resolves to either the
old or new complete release, never a half-pair. If restart or smoke validation
fails after replacement, rollback is mandatory; definitions-only degradation due
to a stale Sidecar is not accepted as a successful migration.

## Learning-data protection

Dictionary SQLite and its Sidecar are read-only lexical assets. No migration step
calls Study Bank, Daily Review, Library, Speaking, Chunk Memory, or review APIs.
Pre/post cutover hashes enforce this boundary.

Saved Study Bank items contain snapshots such as meaning, note, provenance, and
ReviewState. A dictionary replacement must not rewrite them. Historical cards
with inaccurate Chinese therefore remain unchanged by design.

A future historical-card audit requires separate explicit authorization. Its safe
shape is:

1. read Study Bank and the new dictionary without mutation;
2. produce a report keyed by item ID with saved meaning, current source-backed
   candidate, provenance, ambiguity, and confidence reason;
3. exclude user-custom meaning/note from automatic replacement;
4. require item-by-item or explicitly scoped Owner approval;
5. preserve IDs, provenance, active state, ReviewState, due dates, counters, and
   timestamps unless the approved operation explicitly says otherwise;
6. back up and atomically write Study Bank only in that later task.

There is no bulk automatic correction in DQ-02 Phase 6 or Phase 7 dictionary
cutover.

## Disk and resource budget

Use byte counts from manifests and `stat`, never a fixed free-space guess.

Let:

- `R` = all compressed source artifacts;
- `D` = larger of current and projected candidate dictionary;
- `S` = larger of current and projected Sidecar;
- `J` = conservative SQLite journal/build overhead, initially `ceil(0.25 * D)`;
- `Q` = quality reports and other temporary files;
- `H` = `max(10 GiB, ceil(0.25 * (R + 3D + 3S + J + Q)))`.

For the first versioned migration, require:

```text
required_free = R + 3D + 3S + J + Q + H
```

The three copies budget live assets, verified rollback assets, and the candidate.
If filesystem clones are used, budget their full logical size anyway unless
allocated-block guarantees and rollback behavior are explicitly verified.

Previously verified compressed sizes were 3,082,192,147 bytes for English and
310,139,416 bytes for German (`R = 3,392,331,563` bytes, about 3.16 GiB), but
Phase 7 must issue fresh HEAD/GET evidence and must not reuse those numbers as
current facts. With an illustrative `D=2.5 GiB`, `S=0.5 GiB`, `Q=0.25 GiB`, and
the minimum 10 GiB headroom, the requirement is roughly 23 GiB. This is an
example, not a Mac preflight result.

Import is line-streamed and Sidecar construction fetches batches of 2,000 rows,
so memory should be bounded by SQLite caches, one parsed source record, and batch
buffers. Phase 7 must record peak RSS and stop if memory pressure threatens the
desktop; 16 GiB historical capacity is not itself an acceptance result.

## Failure-injection plan

### Risk matrix

| Risk | Severity | Likelihood | Detection | Mitigation / gate |
|---|---|---|---|---|
| wrong translation associated with a sense | High | Medium | versioned gold cases, alignment telemetry, sampled review | zero known wrong associations; no cutover on failure |
| valid translation coverage drops | Medium | High | per-language entry/sense coverage and common-word delta | report separately; Owner-approved threshold; never fill by guessing |
| source snapshot change misattributed to importer | High | High when rolling URLs are used | source SHA comparison and comparison-matrix status | label confounded; use same-snapshot rebuild when available |
| corrupt or partial source artifact | High | Low | size, SHA-256, gzip trailer and JSON parsing | content-addressed artifact; fail before candidate readiness |
| corrupt candidate SQLite | Critical | Low | integrity, foreign-key, JSON and query-plan checks | reject candidate; live release untouched |
| stale/mismatched Sidecar | High | Medium without versioned paths | source identity and real lookup status | immutable release path; build Sidecar last; require `available` |
| partial DB/Sidecar cutover | Critical | Low with chosen design | symlink target and post-start smoke | stop backend; one atomic directory-symlink switch |
| stale in-process dictionary cache | High | High without restart | build ID/lookup mismatch after cutover | mandatory process restart; no live in-process swap |
| backend fails to restart | High | Low | LaunchAgent state, health and dictionary smoke | automatic symlink rollback and old-release restart |
| learning data changes during migration | Critical | Low | pre/post hashes and file inventory | migration never calls learning APIs; rollback and investigate on drift |
| rollback is not actually recoverable | Critical | Medium if copied blindly | independent integrity, Sidecar and lookup validation | build/validate rollback release before cutover |
| insufficient disk or memory pressure | High | Medium | byte budget, free-space/RSS monitoring | fail early; retain safety headroom; no cleanup of live/backup |
| inaccurate or incompatible license metadata | High | Low | per-edition notice verification | stop for Owner/schema decision; never infer historical label |
| manifest tampering or release/path confusion | High | Low | schema, hashes, `READY`, resolved-root checks | immutable release, bounded root, atomic manifest publication |

Severity describes user/data impact, not frequency. Critical risks are automatic
cutover blockers even when likelihood is low.

### Fault cases

| Injection | Required result |
|---|---|
| wrong source SHA/size | fail before import; no candidate `READY` |
| truncated/corrupt gzip | importer fails; candidate temp removed; live untouched |
| importer killed mid-build | incomplete release ignored; live untouched |
| insufficient disk | fail preflight before download/build/cutover |
| SQLite corruption | integrity gate fails; no Sidecar/cutover |
| foreign-key violation | validation fails closed |
| missing required index/query-plan regression | quality gate fails |
| Sidecar interruption | no published candidate Sidecar; live untouched |
| source changed during Sidecar build | Sidecar builder fails |
| release directory renamed after Sidecar build | `index_stale`; candidate rejected |
| malformed/missing manifest or `READY` | candidate rejected |
| crash before symlink replace | old release remains current |
| crash during symlink replace | current resolves wholly to old or new release |
| backend restart failure | switch back to rollback and restart old release |
| post-cutover dictionary smoke failure | mandatory rollback |
| learning-store hash drift | block acceptance, rollback, preserve evidence |
| stale cache evidence after switch | process restart required; no in-process cutover |
| rollback Sidecar mismatch | rollback release is invalid; cutover prohibited |

Fixture and unit tests must use temporary stores only. Failure injection never
targets the real dictionary or real learning files.

## Cloud fixture validation performed in Phase 6

A tiny English fixture was imported twice into immutable version directories,
and a Sidecar was built for each. Both dictionaries returned
`integrity_check=ok`, zero foreign-key errors, and `available` Sidecars. Atomically
switching one `current` directory symlink from release A to release B preserved
`available` status. Renaming release A after its Sidecar was built produced
`index_stale`, confirming the path-identity constraint.

Fixture sizes were 57,344-byte dictionaries and 16,384-byte Sidecars. They are
functional evidence only and must not be used for production capacity estimates.

## Future Mac automation

Phase 7 should implement two narrow tools rather than one destructive command:

1. `prepare_dictionary_release.py`
   - validates manifest/source hashes;
   - calculates disk budget;
   - imports into a new immutable release;
   - collects importer telemetry;
   - runs SQLite, query, quality, performance, and Sidecar validation;
   - writes manifest/report/`READY`;
   - never stops or switches the backend.
2. `cutover_dictionary_macos.sh` plus a small Python atomic-symlink helper
   - requires an explicit prepared build ID and Owner confirmation flag;
   - creates/verifies the rollback release;
   - snapshots learning-store hashes;
   - stops LaunchAgent, atomically switches, restarts, and validates;
   - automatically rolls back on any post-switch failure;
   - never downloads or imports data.

Dry-run is mandatory for both. Paths must be resolved and constrained under the
approved dictionary release root. Neither tool accepts arbitrary deletion targets.
Generated artifacts and logs must not contain API keys, cookies, user learning
contents, or private transcript data.

## Phase 7 entry conditions and acceptance

Phase 7 may begin only when the Owner separately approves full-source download,
candidate build, additional disk use, and later production cutover as distinct
actions. Entry conditions:

- current Mac branch/commit and clean worktree verified;
- current disk and memory preflight measured;
- source URLs, headers, hashes, dates, licenses, and attribution verified;
- migration automation reviewed with destructive-path safeguards;
- rollback release design tested on fixtures;
- versioned gold cases and common-word list approved;
- learning-store hash inventory defined;
- no unresolved schema/license incompatibility.

Candidate-build acceptance requires manifest validation, clean importer commit,
complete gzip/hash checks, successful SQLite/Sidecar validation, quality report,
zero known wrong associations in the gold set, 100% explainable sense-local
retention, reported entry-level rejection reasons, acceptable performance, and
adequate rollback/headroom.

Production cutover additionally requires explicit Owner approval, a verified
rollback release, stopped backend, successful atomic switch/restart/smoke tests,
and unchanged learning-store hashes. Real-Mac validation must be reported as such;
cloud fixture results cannot mark the production migration complete.

## Unresolved risks requiring explicit decisions

- Rolling Kaikki URLs do not provide immutable version identity; SHA-256 remains
  authoritative and dates may remain unverified.
- The old raw snapshot may be unavailable, preventing clean separation of
  snapshot and importer effects.
- Schema v1 lacks translation provenance, so direct/aligned/rejected counts need
  Phase 7 build-time instrumentation.
- Strict lexical alignment deliberately reduces coverage and cannot guarantee
  semantic correctness for every source hint.
- Current `/api/health` is not dictionary-aware.
- Current LaunchAgent generation does not set versioned dictionary paths.
- Importer publication lacks explicit file/directory fsync; the wrapper must add
  durability before a release is marked ready, or Phase 7 may harden the importer.
- macOS symlink/LaunchAgent automation and power-loss behavior require real-Mac
  failure-injection validation before production use.

## Phase 7A — migration automation (cloud-tested, not Mac-verified)

Status: **implemented and cloud-tested on synthetic fixtures with a mocked
macOS lifecycle. Not real-Mac verified. No production cutover performed.**

Baseline: `234f0587b0f1321a7b8af636e259ebf2f017d3ad` (merged PR #48).
Operator procedure: [`DQ-02-PHASE7A-MIGRATION-RUNBOOK.md`](DQ-02-PHASE7A-MIGRATION-RUNBOOK.md).

### Components

| Component | Location | Mutates |
|---|---|---|
| Importer telemetry | `services/dictionary/import_telemetry.py`, hooks in `provider.import_kaikki_sources(telemetry=...)` | nothing (observes only) |
| Durable importer publication | `provider.import_kaikki_sources` fsyncs the finished file before `replace` and the directory after | the requested candidate path only |
| Release store / atomic switch | `services/dictionary/release_store.py` | new release dirs, `dictionary_current` symlink |
| Legacy rollback conversion | `services/dictionary/legacy_release.py` | creates `dictionary_releases/legacy-<sha16>` only |
| Candidate gates | `services/dictionary/release_quality.py` | nothing (read-only) |
| Candidate preparation | `services/dictionary/release_prepare.py`, `backend/scripts/prepare_dictionary_release.py` | creates one new release dir |
| LaunchAgent control | `services/dictionary/launch_agent.py` | LaunchAgent plist (atomic, backed up) |
| Cutover / rollback | `services/dictionary/cutover.py`, `backend/scripts/dictionary_cutover.py`, `scripts/cutover_dictionary_macos.sh` | symlink, plist, service state, `dictionary_migrations/` evidence |
| Dictionary-aware health | `GET /api/dictionary/status` | nothing; returns `configured`, `release_id`, `forms_index_status`, never paths |
| Reinstall preservation | `generate_launch_agent.py --dictionary-current`, `install_personal_app_macos.sh` | plist generation only |

### Telemetry definitions

Counters are keyed by source edition and translation target language
(`en`, `de`, `zh-CN`) in `build-report.json`; the manifest stores totals.

- **Sense-local**: `seen = retained + deduplicated + truncated` exactly
  (READY requires equality). Translations nested under a sense with no usable
  gloss are reported separately as `discarded_with_invalid_sense`, because that
  sense is never imported.
- **Entry-level**: `seen = accepted.total + rejected.total` exactly. Accepted:
  `unique_multi_token`, `reliable_single_token_head`, `unscoped_single_sense`.
  Rejected:
  - `ambiguous_or_tied` — tied best score, or an unscoped translation on a
    multi-sense entry;
  - `zero_evidence` — no exact content-token overlap (or no valid senses);
  - `generic_stopword_or_lemma_only` — the hint contains only stopwords, usage
    labels, or the lemma itself;
  - `single_sense_mismatch` — a scoped hint that does not match the only sense;
  - `unsupported_morphology` — no exact overlap, but a stem/compound
    substring relation exists (the importer deliberately does not guess these);
  - `weak_single_token_evidence` — a unique one-token match that is not the
    hint's only content token or not the definition's lexical head
    (PR #47 "incidental" rule). This key was added to manifest v1 in Phase 7A;
    no v1 manifest had been published before;
  - `over_limit` — aligned, but the sense already held 10 values in that
    language.
  An aligned value that already existed on the sense counts as accepted and is
  additionally reported as `accepted_duplicate_of_existing`.

Morphology classification is telemetry-only and never changes alignment. The
fixture tests prove an instrumented import produces a byte-identical
`iterdump()` to an uninstrumented one.

### Additional decisions made in Phase 7A

1. **Rollback identity.** A rollback is either a legacy conversion
   (`rollback-verification.json` + `READY`) or a previously published candidate
   (`READY` = SHA-256 of `manifest.json`). This lets each successful candidate
   become the next migration's rollback without rewriting it.
2. **Gold set gating.** Prepare accepts only `scope=production`, `approved=true`
   gold sets. `docs/DICTIONARY-GOLD-CASES-v1.draft.json` is a proposal with
   `approved=false`; a real build fails until the Owner reviews and approves it.
   Synthetic fixture gold sets are accepted only through the test-only
   `allow_fixture_gold` parameter (not exposed by the CLI).
3. **Failure evidence.** A failed build leaves its directory without `READY`
   and with `build-failure.json`; cutover refuses any release containing that
   file. Nothing is auto-deleted.
4. **Recovery baseline.** Before stopping the service, cutover records what the
   running backend reports (`/api/dictionary/status` and smoke lookups).
   Automatic recovery must reproduce that observation exactly; this works for
   legacy setups whether or not they had a Sidecar.
5. **First-cutover recovery** removes the `dictionary_current` symlink that
   run created (a symlink owns no data) and restores the original plist
   byte-for-byte, so the backend again uses the untouched flat file and the
   next attempt starts from the same legacy state. Recovery restores the link
   even when `switch_current` fails after `os.replace`. A link that exists while
   the plist does not use it is treated as inconsistent and refused, and
   `install_personal_app_macos.sh` carries versioned paths over only when the
   existing plist already uses `dictionary_current`.
6. **Learning-store drift** blocks acceptance and triggers recovery; because the
   tool never writes learning data, drift is reported as needing investigation
   (exit code 2) with before/after hash snapshots preserved.
7. **LaunchAgent credential guard.** Migration refuses a plist with
   credential-like environment variable names, a non-loopback `--host`, a
   different `Label`, or a symlinked plist. Error messages never echo values.
8. **Source acquisition is out of scope for 7A.** Prepare consumes already
   downloaded artifacts plus a source-evidence JSON
   (`docs/DICTIONARY-SOURCE-EVIDENCE-v1.example.json`) and re-verifies size,
   SHA-256, gzip trailer, and license per edition. A downloader with `.part`
   publication remains future work.
9. **Interrupt safety.** From the LaunchAgent stop until success or recovery,
   SIGINT/SIGTERM/SIGHUP are deferred and reported; any exception, including
   `KeyboardInterrupt`, triggers recovery. Heavy validation (integrity checks,
   hashes) runs before the stop; only stat-identity rechecks run while stopped.
   The migration lock is taken before preflight.
10. **Rollback targets** must be `legacy-*` or a release recorded as served by a
    completed migration, and never a release with `build-failure.json`.
11. **Legacy conversion** uses the SQLite backup API whenever the database is in
    WAL mode (header bytes 18/19) or has a `-wal` file. An interrupted attempt
    is preserved and the retry uses `legacy-<sha16>-r2` (up to `-r9`); the
    service is restarted and health-checked on every path.
12. **Start-up store creation.** Backend start-up creates missing empty learning
    stores and `library_media/`; a store that was missing before and exists
    afterwards is not drift. Any change to an existing store is.

### Cloud evidence (synthetic only)

All migration tests use `tmp_path` stores, gzip fixtures of a few entries, and a
mocked LaunchAgent/backend (`backend/tests/migration_support.py`). They cover
the fault-case table above except for real power loss and real `launchd`
semantics. Cloud results are functional evidence only, not performance or
production evidence.

### Still requires real-Mac validation

- `launchctl print` environment block format and `bootout` timing on the
  Owner's macOS version;
- that the restarted uvicorn process sees the plist environment;
- APFS behaviour of `os.replace` on directory symlinks and directory `fsync`;
- performance gates on the real 2.5 GB dictionary and Sidecar;
- that existing learning stores are not rewritten on start-up (missing ones are
  created by design and tolerated);
- a full rehearsal on a copy before the production cutover.

## Phase 7A follow-up — split quality gates (analysis only)

Status: **analysis tooling; no change to READY, cutover, or the importer.**

The first real candidate failed 7 of 9 gold cases while showing zero incorrect
associations. Those gold cases run through `DictionaryService`, so they mix
two different questions. They are now reported separately:

| Test | What it checks | Input | Module |
|---|---|---|---|
| Dictionary Data Quality | Chinese values stored on a named source sense: forbidden values absent, values within the reviewed set, gaps counted (not failures) | SQLite rows only | `services/dictionary/data_quality.py`, cases in `docs/DICTIONARY-DATA-QUALITY-CASES-v1.draft.json` |
| Context Sense Selection | the original 9 sentence gold cases, unchanged | `DictionaryService.lookup` | `release_quality.evaluate_gold_cases`, `docs/DICTIONARY-GOLD-CASES-v1.draft.json` |

`backend/scripts/compare_dictionary_releases.py` compares the live (legacy)
database with the candidate read-only and, given a *counterfactual* database
built by the old importer (`9b56b8f`, byte-identical import code to the one that
built the legacy dictionary) on the candidate's source snapshot, decomposes the
coverage change into a snapshot effect and an importer effect. It classifies
every entry that lost all Chinese under the importer change as single-sense,
identical-definition tie, broadcast to distinct senses, or single-target
rejection, and re-runs the strict aligner on raw records of lost common words
to record each translation's exact decision.

`prepare_dictionary_release.py` is unchanged: READY still requires zero gold
(context) failures. Whether READY should instead require Data Quality only is
an Owner decision after the comparison report.

## Phase 7B — importer recovery (Owner-approved; no READY, no cutover)

Same-snapshot analysis of the first real candidate showed English Chinese
coverage falling 24.4% entirely from strict alignment (snapshot effect 0), with
58% of lost entries being single-sense entries and many common words losing
correct values to ties between subsenses that repeat their parent gloss.
Three narrowly scoped repairs are implemented in
`services/dictionary/recovery.py` and enabled by default
(`import_kaikki_sources(recovery=True)`; `recovery=False` reproduces 7A):

1. **Single-sense fallback** — only when the source entry has exactly one raw
   sense (with a gloss) *and* exactly one raw translation table: every raw
   translation record, in any language and before any filter, shares one hint.
   Then `weak_single_token_evidence`, `unsupported_morphology`,
   `generic_stopword_or_lemma_only` and `single_sense_mismatch` are recovered;
   a mismatch is still refused for an English non-verb entry whose table reads
   "to ..." (likely misfiled). Several distinct hints (e.g. a "river shore"
   table inside a bank entry whose only sense is financial) block every
   recovery. Over-limit is never recovered.
2. **Equivalent parent/child groups** — senses merge only when they share the
   exact same `glosses[0]` and nesting is explicit (≥1 subsense, ≤1 bare
   parent); identical text without nesting is never merged; groups are
   per-entry, hence never cross POS. A tie whose winners all lie in one group,
   or an unscoped translation on a one-group entry, goes to the representative
   only (the parent), never re-broadcast to children.
3. **Dungan filter** — zh-CN values without a Han character that are Cyrillic
   or tagged Dungan are rejected as `dungan_cyrillic`, counted in
   `build-report.json` (`script_rejected`). Other non-Han values (e.g. "DNA",
   pinyin) are kept: they are outside the approved scope.

Every stored translation now has a `translation_provenance` row (`method`:
`sense_local` or an accepted entry-level outcome; `recovered_from`: the strict
rejection a recovery overrode; `hint`). Manifest v1 telemetry gains accepted
keys `equivalent_group` and `single_sense_fallback`. Two PR #47 tests that
asserted single-sense morphology rejection now run in strict mode, with new
tests proving multi-sense morphology is still never aligned and single-sense
recovery is recorded with provenance.

`prepare_dictionary_release.py --withhold-ready` is an evaluation build: every
gate (context gold, performance, optional `--data-quality-cases`) runs and is
reported, the manifest is `candidate_validated` only if all pass,
`readiness.json` records READY eligibility, the release is sealed, and READY is
never written. `compare_dictionary_releases.py --strict` adds the same-snapshot
old / strict / repaired comparison with recovery counts by method, values
removed by the script filter, and deterministic review samples.
