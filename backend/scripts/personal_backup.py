"""Personal Desktop learning-data backup (operator CLI).

Stop the app first: the backup refuses while 127.0.0.1:<port> is listening.

Usage (from backend/):
  python scripts/personal_backup.py backup --dest /path/outside/the/repo [--storage DIR] [--port 8000]
  python scripts/personal_backup.py verify --backup /path/personal-backup-<stamp>
  python scripts/personal_backup.py restore --backup /path/personal-backup-<stamp> --target /new/empty/dir

restore never writes into the live storage directory; it materializes the
backup into a new directory and checks it loads with the same records. Moving
restored data into place is a separate, manual step done with the app stopped.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.backup.personal import (  # noqa: E402
    DEFAULT_PORT,
    BackupInvalid,
    BackupRefused,
    create_backup,
    default_storage_dir,
    restore_backup,
    verify_backup,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="personal_backup")
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--dest", required=True, type=Path)
    backup.add_argument("--storage", type=Path, default=default_storage_dir())
    backup.add_argument("--port", type=int, default=DEFAULT_PORT)
    sub.add_parser("verify").add_argument("--backup", required=True, type=Path)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--target", required=True, type=Path)
    restore.add_argument("--live-storage", type=Path, default=default_storage_dir())
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            result = create_backup(args.storage, args.dest, port=args.port)
            out = {"ok": True, "backup": str(result.path), "files": result.files, "bytes": result.bytes,
                   "datasets": {k: v.get("count") for k, v in result.summary.items() if isinstance(v, dict)}}
        elif args.command == "verify":
            metadata = verify_backup(args.backup)
            out = {"ok": True, "created_at": metadata["created_at"], "datasets": sorted(metadata["summary"])}
        else:
            report = restore_backup(args.backup, args.target, live_storage=args.live_storage)
            out = {"ok": True, "target": report["target"], "datasets": report["datasets"],
                   "matches_backup": report["matches_backup"]}
    except (BackupRefused, BackupInvalid) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
