"""Cloud PostgreSQL backup, verification, restore and drill (operator CLI).

DSNs are read only from environment variables, never from arguments, so they
do not end up in shell history or process listings. Output is JSON and never
includes a DSN, a credential or an owner id.

  CLOUD_PRIVILEGED_DATABASE_URL  superuser/provider admin (create-backup-role only)
  CLOUD_ADMIN_DATABASE_URL       migration owner (check-roles)
  CLOUD_BACKUP_DATABASE_URL      dedicated BYPASSRLS read-only backup role (backup)
  CLOUD_RESTORE_DATABASE_URL     privileged role on a NEW, EMPTY database (restore)
  CLOUD_DRILL_SUPERUSER_DATABASE_URL  disposable local server (drill; also needs
                                 CLOUD_DRILL_DISPOSABLE=yes)
  CLOUD_PG_BIN                   optional directory of pg_dump/pg_restore; their
                                 major version must equal the server's

Usage (from backend/):
  python scripts/cloud_backup.py create-backup-role --role lap_backup
  python scripts/cloud_backup.py check-roles --runtime-role lap_app --backup-role lap_backup
  python scripts/cloud_backup.py backup --media-root /srv/cloud-media --dest /backups --confirm-writes-frozen
  python scripts/cloud_backup.py verify --backup /backups/cloud-backup-<stamp>
  python scripts/cloud_backup.py restore --backup /backups/cloud-backup-<stamp> --target-media-root /srv/restore-media [--live-media-root /srv/cloud-media]
  python scripts/cloud_backup.py drill [--work-dir /tmp/lapdrill]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from services.cloud.backup import (  # noqa: E402
    BackupInvalid,
    BackupRefused,
    create_backup,
    create_backup_role,
    restore_backup,
    role_separation,
    verify_backup,
)
from services.cloud.backup_drill import DrillRefused, run_drill  # noqa: E402

EXIT_REFUSED = 2
EXIT_CONFIG = 3


def _dsn(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(json.dumps({"ok": False, "error": f"{name} is not set"}))
        raise SystemExit(EXIT_CONFIG)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cloud_backup")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create-backup-role").add_argument("--role", required=True)
    check = sub.add_parser("check-roles")
    check.add_argument("--runtime-role", required=True)
    check.add_argument("--backup-role", required=True)
    make = sub.add_parser("backup")
    make.add_argument("--media-root", required=True, type=Path)
    make.add_argument("--dest", required=True, type=Path)
    make.add_argument("--confirm-writes-frozen", action="store_true",
                      help="Required: the application is stopped or in maintenance (no writes)")
    sub.add_parser("verify").add_argument("--backup", required=True, type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--target-media-root", required=True, type=Path)
    restore.add_argument("--live-media-root", type=Path)
    sub.add_parser("drill").add_argument("--work-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "create-backup-role":
            created = create_backup_role(_dsn("CLOUD_PRIVILEGED_DATABASE_URL"), args.role)
            out = {"ok": True, "created": created, "login": False,
                   "next": "set LOGIN and a password interactively with psql \\password"}
        elif args.command == "check-roles":
            out = role_separation(_dsn("CLOUD_ADMIN_DATABASE_URL"), args.runtime_role, args.backup_role)
            print(json.dumps(out, indent=2))
            return 0 if out["ok"] else EXIT_REFUSED
        elif args.command == "backup":
            result = create_backup(_dsn("CLOUD_BACKUP_DATABASE_URL"), args.media_root, args.dest,
                                   confirm_writes_frozen=args.confirm_writes_frozen)
            out = {"ok": True, "backup": str(result.path), "tables": result.tables, "rows": result.rows,
                   "media_objects": result.media_objects, "media_bytes": result.media_bytes,
                   "dump_bytes": result.dump_bytes, "seconds": result.seconds}
        elif args.command == "verify":
            manifest = verify_backup(args.backup)
            out = {"ok": True, "created_at": manifest["created_at"],
                   "ledger": [row["version"] for row in manifest["ledger"]],
                   "tables": len(manifest["tables"]),
                   "rows": sum(entry["rows"] for entry in manifest["tables"].values()),
                   "media": manifest["media"]["classes"]}
        elif args.command == "restore":
            report = restore_backup(args.backup, _dsn("CLOUD_RESTORE_DATABASE_URL"), args.target_media_root,
                                    live_media_root=args.live_media_root)
            out = {"ok": True, "database": report.database, "media_root": str(report.media_root),
                   "tables": report.tables, "rows": report.rows, "media_objects": report.media_objects,
                   "ledger": report.ledger, "timings": report.timings,
                   "next": "run cloud_db.py preflight with the runtime role before starting the app"}
        else:
            out = run_drill(_dsn("CLOUD_DRILL_SUPERUSER_DATABASE_URL"), args.work_dir)
            print(json.dumps(out, indent=2, ensure_ascii=False))
            return 0 if out["ok"] else EXIT_REFUSED
    except (BackupRefused, BackupInvalid, DrillRefused, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return EXIT_REFUSED
    except subprocess.TimeoutExpired:
        print(json.dumps({"ok": False, "error": "a PostgreSQL client tool timed out"}))
        return EXIT_REFUSED
    except psycopg.Error as exc:
        # Driver messages can carry connection details; report the class only.
        print(json.dumps({"ok": False, "error": f"database error ({type(exc).__name__})"}))
        return EXIT_REFUSED
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
