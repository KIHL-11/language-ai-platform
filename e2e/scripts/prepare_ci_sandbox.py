"""Only the disposable hosted runner may call this fixture preparer."""
import json
import os
from pathlib import Path
import psycopg
from services.cloud.migrations import apply
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
apply(ADMIN, target="0009")
create_runtime_role(ADMIN, "e2e_runtime")
with psycopg.connect(ADMIN, autocommit=True) as conn:
    conn.execute("ALTER ROLE e2e_runtime LOGIN PASSWORD 'e2e_runtime'")
grant_runtime(ADMIN, "e2e_runtime")
require_preflight(RUNTIME, minimum_version="0009")
print("Migration 0001–0009 and least-privilege runtime preflight succeeded")

owner_a, owner_b = "synthetic-e2e-a", "synthetic-e2e-b"
with psycopg.connect(ADMIN) as conn:
    conn.execute("INSERT INTO cloud_users (user_id, created_at) VALUES (%s, now()), (%s, now())", (owner_a, owner_b))
library = PostgresOwnerScopedLibraryRepository(RUNTIME, FilesystemMediaObjectStore(os.environ["CLOUD_MEDIA_ROOT"]))
item = library.create_for_owner(owner_a, _lesson())
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
with open(os.environ["GITHUB_ENV"], "a") as handle:
    handle.write("E2E_MEDIA_STUDY_INPUT=" + str(media_input) + "\n")
    handle.write("E2E_ACCOUNT_A_LIBRARY_ITEM_ID=" + item.id + "\n")
print("Seeded two synthetic accounts and four distinct session cookies without logging tokens")
