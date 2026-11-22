"""Cloud Phase 1 database procedure (operator CLI).

DSNs are read only from environment variables, never from arguments, so they
do not end up in shell history or process listings. Output is JSON and never
includes a DSN or credential.

  CLOUD_ADMIN_DATABASE_URL    migration/owner connection (plan, apply, roles)
  CLOUD_RUNTIME_DATABASE_URL  application connection (preflight)

Usage (from backend/):
  python scripts/cloud_db.py plan [--target 0009]
  python scripts/cloud_db.py apply [--target 0009]
  python scripts/cloud_db.py create-runtime-role --role lap_app
  python scripts/cloud_db.py grant-runtime --role lap_app
  python scripts/cloud_db.py preflight [--minimum 0009]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.cloud.migrations import MigrationRefused, apply, plan  # noqa: E402
from services.cloud.preflight import run_preflight  # noqa: E402
from services.cloud.roles import RoleRefused, create_runtime_role, grant_runtime  # noqa: E402

EXIT_REFUSED = 2
EXIT_CONFIG = 3


def _dsn(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        print(json.dumps({"ok": False, "error": f"{name} is not set"}))
        raise SystemExit(EXIT_CONFIG)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cloud_db")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--target")
    for name in ("create-runtime-role", "grant-runtime"):
        sub.add_parser(name).add_argument("--role", required=True)
    sub.add_parser("preflight").add_argument("--minimum", default="0006")
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = plan(_dsn("CLOUD_ADMIN_DATABASE_URL"), target=args.target)
            out = {"ok": True, "target": result.target, "applied": result.applied,
                   "pending": result.pending_versions}
        elif args.command == "apply":
            done = apply(_dsn("CLOUD_ADMIN_DATABASE_URL"), target=args.target)
            out = {"ok": True, "applied_now": done}
        elif args.command == "create-runtime-role":
            created = create_runtime_role(_dsn("CLOUD_ADMIN_DATABASE_URL"), args.role)
            out = {"ok": True, "created": created, "login": False,
                   "next": "set LOGIN and a password interactively with psql \\password"}
        elif args.command == "grant-runtime":
            out = {"ok": True, "granted": grant_runtime(_dsn("CLOUD_ADMIN_DATABASE_URL"), args.role)}
        else:
            report = run_preflight(_dsn("CLOUD_RUNTIME_DATABASE_URL"), minimum_version=args.minimum)
            out = {"ok": report.ok, "role": report.role, "versions": report.versions,
                   "server_version_num": report.server_version_num, "failures": report.failures}
            print(json.dumps(out, indent=2))
            return 0 if report.ok else EXIT_REFUSED
    except (MigrationRefused, RoleRefused, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return EXIT_REFUSED
    except psycopg.Error as exc:
        # Driver messages can carry connection details; report the class only.
        print(json.dumps({"ok": False, "error": f"database error ({type(exc).__name__})"}))
        return EXIT_REFUSED
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
