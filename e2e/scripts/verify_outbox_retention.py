"""#97 regression, database side: after the browser deleted a Library item with
audio, its object must be queued in cloud_media_cleanup_outbox (pending, no
attempt) and its bytes must still be on disk; the other item's audio must stay
referenced and intact. Runs only in the disposable hosted sandbox.

Writes sanitized evidence <E2E_EVIDENCE_DIR>/upgrade/outbox-retention.json:
fixed labels and counts only, no ids, keys, digests or owner names.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

import psycopg

ADMIN = "postgresql://e2e_admin:e2e_admin@127.0.0.1:5432/e2e_disposable"

if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("E2E_DISPOSABLE_CI") != "yes":
    raise SystemExit("Refusing outbox verification outside dedicated hosted CI")
if os.environ.get("CLOUD_ADMIN_DATABASE_URL") != ADMIN:
    raise SystemExit("Refusing unexpected DSN")

fixture = json.loads(Path(os.environ["E2E_OUTBOX_FIXTURE"]).read_text(encoding="utf-8"))
media_root = Path(os.environ["CLOUD_MEDIA_ROOT"])
profile = os.environ["E2E_DB_PROFILE"]
records = []
failures = []


def check(path, code, ok, created=None):
    record = {"path": path, "method": "CHECK", "status": 200 if ok else 500, "code": code}
    if created is not None:
        record["created"] = created
    records.append(record)
    if not ok:
        failures.append(code)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


with psycopg.connect(ADMIN) as conn:
    top = conn.execute("SELECT max(version) FROM cloud_schema_migrations").fetchone()[0]
    check("/db/cloud_schema_migrations", f"ledger-top-{top}", top == profile)
    retire, keep = fixture["retire"], fixture["keep"]
    rows = conn.execute(
        "SELECT owner_user_id, library_item_id::text, status, attempt_count FROM cloud_media_cleanup_outbox"
        " WHERE object_key = %s",
        (retire["object_key"],),
    ).fetchall()
    check("/db/cloud_media_cleanup_outbox", "retired-object-queued", len(rows) == 1, created=len(rows))
    if rows:
        owner, item_id, status, attempts = rows[0]
        check("/db/cloud_media_cleanup_outbox", f"retired-status-{status}", status == "pending")
        check("/db/cloud_media_cleanup_outbox", "retired-attempts-zero", attempts == 0)
        check("/db/cloud_media_cleanup_outbox", "retired-owner-bound",
              owner == fixture["owner"] and item_id == retire["item_id"])
    retired_assets = conn.execute(
        "SELECT count(*) FROM cloud_library_media_assets WHERE object_key = %s", (retire["object_key"],)
    ).fetchone()[0]
    check("/db/cloud_library_media_assets", "retired-metadata-removed", retired_assets == 0, created=retired_assets)
    retired_items = conn.execute(
        "SELECT count(*) FROM cloud_library_items WHERE item_id = %s", (retire["item_id"],)
    ).fetchone()[0]
    check("/db/cloud_library_items", "retired-item-removed", retired_items == 0, created=retired_items)
    kept = conn.execute(
        "SELECT sha256 FROM cloud_library_media_assets WHERE object_key = %s AND library_item_id = %s",
        (keep["object_key"], keep["item_id"]),
    ).fetchall()
    check("/db/cloud_library_media_assets", "kept-audio-referenced", kept == [(keep["sha256"],)])
    queued_keep = conn.execute(
        "SELECT count(*) FROM cloud_media_cleanup_outbox WHERE object_key = %s", (keep["object_key"],)
    ).fetchone()[0]
    check("/db/cloud_media_cleanup_outbox", "kept-audio-not-queued", queued_keep == 0, created=queued_keep)

check("/media/retired-object", "retired-bytes-retained", sha256(media_root / retire["object_key"]) == retire["sha256"])
check("/media/kept-object", "kept-bytes-intact", sha256(media_root / keep["object_key"]) == keep["sha256"])

directory = Path(os.environ["E2E_EVIDENCE_DIR"]) / "upgrade"
directory.mkdir(mode=0o700, parents=True, exist_ok=True)
path = directory / "outbox-retention.json"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as handle:
    json.dump({"evidence": "outbox-retention", "records": records}, handle, indent=2)
    handle.write("\n")
for record in records:
    print(("PASS " if record["status"] == 200 else "FAIL ") + record["code"])
if failures:
    sys.exit(f"Outbox retention check failed: {', '.join(failures)}")
print(f"Outbox retention OK ({profile}): {len(records)} checks")
