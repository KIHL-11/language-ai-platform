"""Static description of the Cloud Phase 1 schema.

Everything here is derived from the migration files 0001-0009 and from the SQL
the runtime repositories issue. It is data, not a migration: changing it never
changes a database. Preflight compares a live database against it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations" / "postgresql"
MANIFEST_NAME = "PHASE1-MANIFEST.sha256"
LEDGER_TABLE = "cloud_schema_migrations"

# Versions that may exist in the tree but must never be applied by Phase 1.
FIRST_FORBIDDEN_VERSION = 10

_MANIFEST_LINE = re.compile(r"^([0-9a-f]{64})  ((\d{4})_[a-z0-9_]+\.up\.sql)$")

# Tables each version creates. Preflight requires the live public schema to
# hold exactly the union for the applied versions plus the ledger, so objects
# from 0010+ (or anything hand-made) are detected.
TABLES_BY_VERSION: dict[str, tuple[str, ...]] = {
    "0001": (
        "cloud_users",
        "cloud_library_items",
        "cloud_library_media_assets",
        "cloud_media_cleanup_outbox",
    ),
    "0002": ("cloud_study_items", "cloud_study_media_provenance"),
    "0003": (
        "cloud_ai_chunk_batches",
        "cloud_ai_chunk_candidates",
        "cloud_ai_chunk_evidence",
        "cloud_ai_chunk_generation_requests",
        "cloud_ai_chunk_decisions",
        "cloud_ai_chunk_decision_keys",
    ),
    "0004": ("cloud_topic_sessions", "cloud_topic_targets", "cloud_topic_requests"),
    "0005": ("cloud_browser_sessions",),
    "0006": (),
    "0007": ("cloud_external_identities", "cloud_email_login_challenges"),
    "0008": ("cloud_oauth_states",),
    "0009": (),
}

# Tables that the migrations put under ENABLE + FORCE row level security.
FORCED_RLS_BY_VERSION: dict[str, tuple[str, ...]] = {
    "0002": TABLES_BY_VERSION["0002"],
    "0003": TABLES_BY_VERSION["0003"],
    "0004": TABLES_BY_VERSION["0004"],
}

# Columns and constraints added by versions that create no table; preflight
# checks them so a half-applied or hand-edited schema is refused.
COLUMNS_BY_VERSION: dict[str, tuple[tuple[str, str], ...]] = {
    "0008": (
        ("cloud_email_login_challenges", "purpose"),
        ("cloud_email_login_challenges", "owner_user_id"),
        ("cloud_email_login_challenges", "code_format"),
        ("cloud_browser_sessions", "reauthenticated_at"),
    ),
    "0009": (("cloud_email_login_challenges", "replaces_subject"),),
}
CONSTRAINTS_BY_VERSION: dict[str, tuple[tuple[str, str], ...]] = {
    "0006": (("cloud_topic_sessions", "cloud_topic_json_identity_required"),),
}

TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")

# Least-privilege grants for the runtime role, derived from the SQL issued by
# backend/services (library, study_bank, ai_chunks, topics, auth). UPDATE is
# also needed where rows are locked with SELECT ... FOR UPDATE or upserted with
# ON CONFLICT DO UPDATE; SELECT is needed wherever WHERE/RETURNING read columns.
# Maintenance purges (DELETE on auth tables) belong to a separate job role and
# are intentionally absent. TRUNCATE, REFERENCES and TRIGGER are never granted.
RUNTIME_GRANTS: dict[str, frozenset[str]] = {
    "cloud_users": frozenset({"SELECT", "INSERT"}),
    "cloud_library_items": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "cloud_library_media_assets": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "cloud_media_cleanup_outbox": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "cloud_study_items": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "cloud_study_media_provenance": frozenset({"INSERT"}),
    "cloud_ai_chunk_batches": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "cloud_ai_chunk_candidates": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "cloud_ai_chunk_evidence": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "cloud_ai_chunk_generation_requests": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "cloud_ai_chunk_decisions": frozenset({"SELECT", "INSERT", "DELETE"}),
    "cloud_ai_chunk_decision_keys": frozenset({"SELECT", "INSERT", "DELETE"}),
    "cloud_topic_sessions": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "cloud_topic_targets": frozenset({"INSERT"}),
    "cloud_topic_requests": frozenset({"SELECT", "INSERT"}),
    "cloud_browser_sessions": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "cloud_external_identities": frozenset({"SELECT", "INSERT", "UPDATE", "DELETE"}),
    "cloud_email_login_challenges": frozenset({"SELECT", "INSERT", "UPDATE"}),
    "cloud_oauth_states": frozenset({"SELECT", "INSERT", "UPDATE"}),
    LEDGER_TABLE: frozenset({"SELECT"}),
}

SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class SchemaDefinitionError(ValueError):
    """The migration set on disk does not match the pinned manifest."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: str
    filename: str
    sha256: str
    path: Path

    def exists(self) -> bool:
        return self.path.is_file()

    def body(self) -> str:
        """Return the verified statements without the file's own BEGIN/COMMIT.

        The runner wraps the body and its ledger row in a single transaction, so
        the files' own framing is removed only after the digest check passes and
        only when it is exactly one leading BEGIN; and one trailing COMMIT;.
        """
        raw = self.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise SchemaDefinitionError(f"{self.filename} does not match the pinned digest")
        text = raw.decode("utf-8")
        lines = text.splitlines()
        meaningful = [index for index, line in enumerate(lines) if line.strip()]
        if not meaningful:
            raise SchemaDefinitionError(f"{self.filename} is empty")
        first, last = meaningful[0], meaningful[-1]
        if lines[first].strip() != "BEGIN;" or lines[last].strip() != "COMMIT;":
            raise SchemaDefinitionError(f"{self.filename} is not framed by BEGIN; ... COMMIT;")
        inner = lines[first + 1:last]
        for line in inner:
            if re.match(r"^\s*(BEGIN|COMMIT|ROLLBACK|END)\s*;", line, re.I):
                raise SchemaDefinitionError(f"{self.filename} contains nested transaction control")
        return "\n".join(inner) + "\n"


def load_manifest(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    path = directory / MANIFEST_NAME
    migrations: list[Migration] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise SchemaDefinitionError(f"Malformed manifest line: {line!r}")
        digest, filename, version = match.groups()
        if int(version) >= FIRST_FORBIDDEN_VERSION:
            raise SchemaDefinitionError(f"Version {version} is outside the Phase 1 set")
        if version not in TABLES_BY_VERSION:
            raise SchemaDefinitionError(f"Version {version} has no schema description")
        migrations.append(Migration(version, filename, digest, directory / filename))
    versions = [migration.version for migration in migrations]
    expected = [f"{index:04d}" for index in range(1, len(versions) + 1)]
    if versions != expected:
        raise SchemaDefinitionError("Manifest versions must be contiguous from 0001")
    return migrations


def expected_tables(versions: list[str]) -> set[str]:
    tables = {LEDGER_TABLE}
    for version in versions:
        tables.update(TABLES_BY_VERSION[version])
    return tables


def forced_rls_tables(versions: list[str]) -> set[str]:
    return {table for version in versions for table in FORCED_RLS_BY_VERSION.get(version, ())}


def validate_identifier(name: str) -> str:
    if not isinstance(name, str) or not SAFE_IDENTIFIER.fullmatch(name):
        raise ValueError("Role names must be lowercase identifiers")
    if name.startswith("pg_") or name in {"postgres", "public"}:
        raise ValueError("Reserved role name")
    return name
