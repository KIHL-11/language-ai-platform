"""Only the disposable hosted runner may call this fixture preparer."""
import hashlib
import json
import os
from pathlib import Path
import psycopg
from services.cloud.migrations import apply
from services.cloud.schema import FORCED_RLS_BY_VERSION, approved_migrations
from services.cloud.roles import create_runtime_role, grant_runtime
from services.cloud.preflight import require_preflight
from services.auth.sessions import BrowserSessionRepository, COOKIE_NAME
from services.library.postgres import PostgresOwnerScopedLibraryRepository, FilesystemMediaObjectStore
from test_owner_scoped_study_bank import _lesson, _media

ADMIN = "postgresql://e2e_admin:e2e_admin@127.0.0.1:5432/e2e_disposable"
RUNTIME = "postgresql://e2e_runtime:e2e_runtime@127.0.0.1:5432/e2e_disposable"
ORIGIN = "https://localhost:8443"

if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("E2E_DISPOSABLE_CI") != "yes":
    raise SystemExit("Refusing fixture provisioning outside dedicated hosted CI")
if os.environ.get("CLOUD_ADMIN_DATABASE_URL") != ADMIN or os.environ.get("CLOUD_STORAGE_DATABASE_URL") != RUNTIME:
    raise SystemExit("Refusing unexpected DSN")
if os.environ.get("CLOUD_SESSION_ORIGIN") != ORIGIN or os.environ.get("CLOUD_SESSION_DATABASE_URL") != RUNTIME:
    raise SystemExit("Refusing unexpected Cloud origin/DSN")
with psycopg.connect(ADMIN) as conn:
    assert conn.execute("SELECT current_database(), current_user, current_setting('server_version_num')::int").fetchone()[:2] == ("e2e_disposable", "e2e_admin")
    assert 160000 <= conn.execute("SELECT current_setting('server_version_num')::int").fetchone()[0] < 170000
    assert conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] == 0

print("Verified disposable PostgreSQL 16 database and empty public schema")
# Database profile: the migration target and the application's minimum version
# are the same value, so the app refuses to start on a schema below it.
PROFILE = os.environ.get("E2E_DB_PROFILE", "")
if PROFILE not in {"0009", "0014"} or os.environ.get("CLOUD_STORAGE_MINIMUM_VERSION") != PROFILE:
    raise SystemExit("E2E_DB_PROFILE must be 0009 or 0014 and equal CLOUD_STORAGE_MINIMUM_VERSION")
if PROFILE not in approved_migrations():
    # A 0014 profile on code without 0014 is a configuration error, never a skip.
    raise SystemExit(f"Application under test does not approve migration {PROFILE}")
applied = apply(ADMIN, target=PROFILE)
create_runtime_role(ADMIN, "e2e_runtime")
with psycopg.connect(ADMIN, autocommit=True) as conn:
    conn.execute("ALTER ROLE e2e_runtime LOGIN PASSWORD 'e2e_runtime'")
grant_runtime(ADMIN, "e2e_runtime")
report = require_preflight(RUNTIME, minimum_version=PROFILE)
assert report.versions[-1] == PROFILE, report.versions
forced = {table for version in report.versions for table in FORCED_RLS_BY_VERSION.get(version, ())}
with psycopg.connect(ADMIN) as conn:
    live = {
        row[0]
        for row in conn.execute(
            "SELECT relname FROM pg_class WHERE relkind = 'r' AND relrowsecurity AND relforcerowsecurity"
            " AND relnamespace = 'public'::regnamespace"
        )
    }
assert live == forced, sorted(live ^ forced)
library_rls = {"cloud_library_items", "cloud_library_media_assets", "cloud_media_cleanup_outbox"}
assert (library_rls <= live) == (PROFILE == "0014"), "Library RLS must be on exactly for the 0014 profile"
print(f"Migration 0001–{PROFILE} ({len(applied)} versions), FORCE RLS on {len(live)} tables"
      f" (Library RLS {'on' if PROFILE == '0014' else 'off'}) and least-privilege runtime preflight"
      f" --minimum {PROFILE} succeeded")

owner_a, owner_b = "synthetic-e2e-a", "synthetic-e2e-b"
with psycopg.connect(ADMIN) as conn:
    conn.execute("INSERT INTO cloud_users (user_id, created_at) VALUES (%s, now()), (%s, now())", (owner_a, owner_b))
library = PostgresOwnerScopedLibraryRepository(RUNTIME, FilesystemMediaObjectStore(os.environ["CLOUD_MEDIA_ROOT"]))
item = library.create_for_owner(owner_a, _lesson())
# Synthetic audio for the #97 regression: one object stays referenced, the other
# belongs to an item the browser deletes; its bytes must be queued, not unlinked.
keep_audio = hashlib.sha256(b"e2e-keep").digest() * 256
retire_audio = hashlib.sha256(b"e2e-retire").digest() * 256
library.put_audio_for_owner(owner_a, item.id, filename="keep.mp3", content_type="audio/mpeg", content=keep_audio)
retire_lesson = _lesson().model_copy(update={"source_url": "https://www.youtube.com/watch?v=retire_123"})
retire = library.create_for_owner(owner_a, retire_lesson)
retire = library.put_audio_for_owner(owner_a, retire.id, filename="retire.mp3", content_type="audio/mpeg",
                                     content=retire_audio)
with psycopg.connect(ADMIN) as conn:
    keys = dict(conn.execute(
        "SELECT library_item_id::text, object_key FROM cloud_library_media_assets WHERE owner_user_id = %s",
        (owner_a,),
    ).fetchall())
sessions = BrowserSessionRepository(RUNTIME)
state_dir = Path(os.environ["E2E_STATE_DIR"])
state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
for label, owner in (("read", owner_a), ("b", owner_b), ("logout", owner_a), ("replay", owner_a)):
    token = sessions.issue_for_verified_account(owner)
    state = {"cookies": [{"name": COOKIE_NAME, "value": token, "domain": "localhost", "path": "/", "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Strict"}], "origins": []}
    path = state_dir / (label + ".json")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle)
media_input = state_dir / "media-study-input.json"
with os.fdopen(os.open(media_input, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
    json.dump({"items": [_media(owner_a, item.id).model_dump(mode="json")]}, handle)
outbox_fixture = state_dir / "outbox-fixture.json"
with os.fdopen(os.open(outbox_fixture, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
    json.dump({
        "owner": owner_a,
        "profile": PROFILE,
        "keep": {"item_id": item.id, "object_key": keys[item.id], "sha256": hashlib.sha256(keep_audio).hexdigest()},
        "retire": {"item_id": retire.id, "object_key": keys[retire.id],
                   "sha256": hashlib.sha256(retire_audio).hexdigest()},
    }, handle)
with open(os.environ["GITHUB_ENV"], "a") as handle:
    handle.write("E2E_MEDIA_STUDY_INPUT=" + str(media_input) + "\n")
    handle.write("E2E_ACCOUNT_A_LIBRARY_ITEM_ID=" + item.id + "\n")
    handle.write("E2E_AUDIO_RETIRE_ITEM_ID=" + retire.id + "\n")
    handle.write("E2E_OUTBOX_FIXTURE=" + str(outbox_fixture) + "\n")
print("Seeded two synthetic accounts and four distinct session cookies without logging tokens")
