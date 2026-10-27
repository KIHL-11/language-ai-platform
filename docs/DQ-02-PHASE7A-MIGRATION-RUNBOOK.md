# DQ-02 Phase 7A — Real-Mac Acceptance Runbook

This runbook is for the Owner's Mac. Every step marked **Owner approval** is a
separate decision. Nothing here has been run on a Mac yet; cloud results came
from synthetic fixtures and a mocked LaunchAgent.

Conventions: run commands from the repository root. `PY` is
`backend/.venv/bin/python`. Exit code `0` = done, `1` = refused or rolled back
cleanly, `2` = manual attention required (see "Manual recovery").

The tools never download, never delete, never write learning stores, and never
open the installed `backend/storage/dictionary.sqlite3` for writing.

## 0. Preconditions

1. Time Machine (or equivalent) backup completed today.
2. Record free space: `df -h backend/storage`.
3. Independent copy of learning data (belt and braces; the tools never write it):

   ```bash
   mkdir -p ~/LanguageAI-learning-backup-$(date +%Y%m%d)
   cp -p backend/storage/{study_bank,chunk_bank,learning_library,speaking_sessions}.json \
     ~/LanguageAI-learning-backup-$(date +%Y%m%d)/ 2>/dev/null || true
   ```

## 1. Check out and verify the code

```bash
git fetch origin
git switch codex/dq-02-migration-automation-phase7a
git status --short          # must be empty
git rev-parse HEAD          # record; must match the pushed SHA
backend/.venv/bin/pip install -r backend/requirements.txt
backend/.venv/bin/pip check
(cd backend && .venv/bin/python -m pytest -q \
  tests/test_dictionary_import_telemetry.py tests/test_dictionary_release_prepare.py \
  tests/test_dictionary_cutover.py tests/test_dictionary_runtime_status.py \
  tests/test_dictionary_migration_manifest.py tests/test_personal_app_scripts.py)
(cd backend && .venv/bin/python -m pytest -q)
bash -n scripts/cutover_dictionary_macos.sh
```

Record exact pass/skip counts with Mac model, macOS and Python versions.

## 2. Read-only status

```bash
scripts/cutover_dictionary_macos.sh status
curl -s http://127.0.0.1:8000/api/dictionary/status
```

Expected before migration: `current_build_id: null`, `release_id: "unversioned"`,
`launch_agent_loaded: true`. This is the first real check that the
`launchctl print` environment parser works on this macOS version; confirm
`loaded_dictionary_environment` matches what you expect (normally `{}`).

## 3. Legacy rollback release (stops and restarts the backend once)

```bash
scripts/cutover_dictionary_macos.sh legacy-rollback --dry-run
scripts/cutover_dictionary_macos.sh legacy-rollback
```

Verify:

- output `action: created`, `build_id: legacy-<16 hex>` (or `-r2` if an earlier
  attempt was interrupted and preserved); record it as `LEGACY_ID`;
- `ls -l backend/storage/dictionary_releases/$LEGACY_ID` shows read-only files
  and `READY`;
- `shasum -a 256 backend/storage/dictionary.sqlite3` equals
  `legacy_database.sha256` in `legacy-provenance.json`;
- the app still works and `/api/dictionary/status` still says `unversioned`;
- `backend/storage/dictionary_migrations/<run>/learning-*.json` are identical.

This step needs roughly one extra dictionary + Sidecar of free space (~3 GB).

## 4. Source acquisition — **Owner approval: full Kaikki download**

Not automated in Phase 7A. For each edition:

```bash
curl -fL --retry 3 -o kaikki-en.jsonl.gz.part -D en.headers <ROLLING_URL>
gzip -t kaikki-en.jsonl.gz.part
shasum -a 256 kaikki-en.jsonl.gz.part; stat -f %z kaikki-en.jsonl.gz.part
mv kaikki-en.jsonl.gz.part kaikki-en-<YYYY-MM-DD or unverified>-<size>-<sha12>.jsonl.gz
```

Keep artifacts in a dedicated directory (`SOURCE_ROOT`) on the same disk.
Copy `docs/DICTIONARY-SOURCE-EVIDENCE-v1.example.json`, fill in each record from
the real file and `*.headers` (ETag, Last-Modified, Content-Length, final URL,
retrieval time), set dates to `null`/`unverified` unless proven, and re-check
the license notice per edition (`verified: true` only after reading it). If the
two editions need different license labels, stop: the v1 importer stores one
label, and an Owner schema decision is required.

## 5. Gold set — **Owner approval: gold cases and common-word list**

```bash
cp docs/DICTIONARY-GOLD-CASES-v1.draft.json ~/dq02-gold-v1.json
```

Review every case, adjust expectations, then set `"approved": true`. Prepare
refuses unapproved or fixture gold sets.

## 6. Prepare the candidate — **Owner approval: candidate build + disk use**

```bash
$PY backend/scripts/prepare_dictionary_release.py --dry-run \
  --source-root "$SOURCE_ROOT" --source-evidence ~/dq02-source-evidence.json \
  --gold-set ~/dq02-gold-v1.json --rollback-build-id "$LEGACY_ID" \
  --projected-dictionary-bytes 2700000000 --projected-sidecar-bytes 600000000
```

Check `disk_budget.required_free_bytes` against `free_bytes`. Then run the same
command without `--dry-run`. It does not stop the backend. Record `build_id` as
`CANDIDATE_ID`. Inspect in `backend/storage/dictionary_releases/$CANDIDATE_ID/`:

- `quality-report.json`: `gold_case_failures == 0`, `incorrect_associations == 0`,
  each gold case's lemma/definition/Chinese, `common_word_coverage`,
  `structural_comparison` (expect coverage changes; snapshot attribution will
  say "confounded" because the old raw snapshot is unavailable),
  `performance` (cold < 1 s, hot < 0.5 s on real data);
- `build-report.json`: per-edition telemetry, `peak_rss_bytes`, elapsed time;
- independent re-check:

  ```bash
  $PY backend/scripts/validate_dictionary_release.py \
    --release backend/storage/dictionary_releases/$CANDIDATE_ID \
    --releases-root backend/storage/dictionary_releases --source-root "$SOURCE_ROOT"
  ```

If a gold case fails, the release has `build-failure.json` and no `READY`; it is
never selectable. Revise and re-run (a new build ID is allocated).

## 7. Cutover — **Owner approval: production cutover**

```bash
scripts/cutover_dictionary_macos.sh cutover --build-id "$CANDIDATE_ID" \
  --source-root "$SOURCE_ROOT" --dry-run
scripts/cutover_dictionary_macos.sh cutover --build-id "$CANDIDATE_ID" \
  --source-root "$SOURCE_ROOT" --confirm "CUTOVER $CANDIDATE_ID"
```

The tool stops the LaunchAgent, hashes learning stores, switches
`dictionary_current`, writes a new plist (old one saved as
`dictionary_migrations/<run>/launch-agent.before.plist`, mode 0600), restarts,
compares `launchctl print` environment, smoke-tests both languages through the
live backend, and re-hashes learning stores. Any failure triggers automatic
rollback.

Verify afterwards:

```bash
scripts/cutover_dictionary_macos.sh status        # current_build_id == CANDIDATE_ID
curl -s http://127.0.0.1:8000/api/dictionary/status
cat backend/storage/dictionary_migrations/*-cutover-$CANDIDATE_ID/result.json
```

Then in the app: look up `get` in "I get what you mean.", `bank` in a river
sentence, `komme`, `Entwicklung`, with inflections shown. Open Study Bank /
Daily Review and confirm existing cards, due dates and historical Chinese
meanings are unchanged.

Pressing Ctrl-C while the service is stopped does not abort the run: the
signal is deferred until the cutover finishes or rolls back, then reported.

## 8. Rollback rehearsal (recommended once)

```bash
scripts/cutover_dictionary_macos.sh rollback --build-id "$LEGACY_ID" --dry-run
scripts/cutover_dictionary_macos.sh rollback --build-id "$LEGACY_ID" --confirm "ROLLBACK $LEGACY_ID"
scripts/cutover_dictionary_macos.sh cutover --build-id "$CANDIDATE_ID" \
  --source-root "$SOURCE_ROOT" --confirm "CUTOVER $CANDIDATE_ID"
```

After rolling back to `LEGACY_ID` the backend serves the byte-identical copy of
the old dictionary through `dictionary_current` (status shows `LEGACY_ID`).

Also run `scripts/install_personal_app_macos.sh` once after cutover and confirm
with `status` that the reinstalled plist still points through
`dictionary_current`.

## Manual recovery (exit code 2)

1. Read `backend/storage/dictionary_migrations/<latest>/result.json` and
   `events`.
2. If learning-store drift was reported, do not retry. Compare
   `learning-before.json` with `learning-after*.json` (hashes only) and restore
   from the step-0 copy only after understanding the cause.
3. To return to the original flat configuration by hand:

   ```bash
   launchctl bootout gui/$(id -u)/com.language-ai-platform || true
   cp backend/storage/dictionary_migrations/<run>/launch-agent.before.plist \
     ~/Library/LaunchAgents/com.language-ai-platform.plist
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.language-ai-platform.plist
   launchctl kickstart -k gui/$(id -u)/com.language-ai-platform
   curl -s http://127.0.0.1:8000/api/dictionary/status   # release_id: unversioned
   ```

   The flat `backend/storage/dictionary.sqlite3` is never modified, so this is
   always available. If you return to the flat configuration by hand after a
   first cutover, also remove the link so later runs see legacy mode again
   (it is only a symlink; it owns no data): `rm backend/storage/dictionary_current`.
   Never remove anything inside `dictionary_releases/`.

## Never

- point `import_kaikki_dictionary.py --database` at `backend/storage/dictionary.sqlite3`;
- edit or delete files inside `dictionary_releases/*` (they are sealed read-only);
- delete `legacy-*` or the previous release during the retention period;
- run the Study Bank historical-meaning audit as part of this migration.
