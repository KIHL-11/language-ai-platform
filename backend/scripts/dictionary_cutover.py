#!/usr/bin/env python3
"""Controlled dictionary release lifecycle for the macOS LaunchAgent.

Subcommands:
  status            read-only: current release, releases, LaunchAgent paths
  legacy-rollback   stop backend, create/verify legacy-<sha> rollback release, restart
  cutover           switch to a prepared release; automatic rollback on failure
  rollback          switch back to an earlier validated release

Every mutating subcommand is read-only by default. ``--execute`` is required
before mutation; cutover/rollback also require an exact Owner confirmation phrase.
This tool never downloads, imports, or deletes data.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.dictionary.cutover import (  # noqa: E402
    CutoverError,
    run_cutover,
    run_legacy_conversion,
    run_rollback,
)
from services.dictionary.launch_agent import (  # noqa: E402
    LABEL,
    LaunchAgentError,
    LaunchctlController,
    LocalBackendClient,
    plist_dictionary_environment,
    read_plist,
)
from services.dictionary.legacy_release import LegacyConversionError  # noqa: E402
from services.dictionary.release_manifest import ManifestValidationError  # noqa: E402
from services.dictionary.release_store import (  # noqa: E402
    LEGACY_DICTIONARY_NAME,
    ReleaseStoreError,
    current_build_id,
    resolve_layout,
)


def _dry_run(arguments: argparse.Namespace) -> bool:
    """Status and every command without --execute are read-only."""
    return arguments.command == "status" or not bool(
        getattr(arguments, "execute", False)
    )


def _status(layout, plist_path: Path, controller) -> dict:
    releases = []
    if layout.releases.is_dir():
        for path in sorted(layout.releases.iterdir()):
            if path.is_dir() and not path.is_symlink():
                releases.append({
                    "build_id": path.name,
                    "ready": (path / "READY").is_file(),
                    "failed": (path / "build-failure.json").exists(),
                    "ready_withheld": (path / "readiness.json").exists() and not (path / "READY").exists(),
                })
    plist_environment = None
    if plist_path.is_file():
        try:
            plist_environment = plist_dictionary_environment(read_plist(plist_path))
        except (LaunchAgentError, ValueError):
            plist_environment = "unreadable"
    loaded = controller.is_loaded()
    return {
        "current_build_id": current_build_id(layout),
        "releases": releases,
        "launch_agent_loaded": loaded,
        "plist_dictionary_environment": plist_environment,
        "loaded_dictionary_environment": (
            {key: value for key, value in controller.loaded_environment().items() if key.startswith("DICTIONARY_")}
            if loaded else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--storage-root", type=Path, default=BACKEND / "storage")
    parser.add_argument("--plist", type=Path,
                        default=Path(os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    legacy = commands.add_parser("legacy-rollback")
    legacy.add_argument("--legacy-database", type=Path)
    legacy_mode = legacy.add_mutually_exclusive_group()
    legacy_mode.add_argument("--execute", action="store_true")
    legacy_mode.add_argument("--dry-run", action="store_true")
    cutover = commands.add_parser("cutover")
    cutover.add_argument("--build-id", required=True)
    cutover.add_argument("--source-root", type=Path, required=True)
    cutover.add_argument("--legacy-database", type=Path)
    cutover.add_argument("--confirm")
    cutover_mode = cutover.add_mutually_exclusive_group()
    cutover_mode.add_argument("--execute", action="store_true")
    cutover_mode.add_argument("--dry-run", action="store_true")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--build-id", required=True)
    rollback.add_argument("--confirm")
    rollback_mode = rollback.add_mutually_exclusive_group()
    rollback_mode.add_argument("--execute", action="store_true")
    rollback_mode.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    controller = LaunchctlController()
    client = LocalBackendClient()
    try:
        dry_run = _dry_run(arguments)
        layout = resolve_layout(arguments.storage_root, create=False)
        legacy_database = getattr(arguments, "legacy_database", None) or layout.storage / LEGACY_DICTIONARY_NAME
        if arguments.command == "status":
            result = _status(layout, arguments.plist, controller)
        elif arguments.command == "legacy-rollback":
            result = run_legacy_conversion(
                layout=layout, legacy_database=legacy_database, controller=controller,
                client=client, plist_path=arguments.plist, dry_run=dry_run,
            )
        elif arguments.command == "cutover":
            result = run_cutover(
                layout=layout, source_root=arguments.source_root, build_id=arguments.build_id,
                confirmation=arguments.confirm, controller=controller, client=client,
                plist_path=arguments.plist, legacy_database=legacy_database, dry_run=dry_run,
            )
        else:
            result = run_rollback(
                layout=layout, build_id=arguments.build_id, confirmation=arguments.confirm,
                controller=controller, client=client, plist_path=arguments.plist, dry_run=dry_run,
            )
    except CutoverError as exc:
        print(f"Dictionary {arguments.command} did not complete: {exc}", file=sys.stderr)
        return 2 if exc.recovered is False else 1
    except KeyboardInterrupt:
        print(
            f"Dictionary {arguments.command} was interrupted; run 'status' before retrying",
            file=sys.stderr,
        )
        return 2
    except (LegacyConversionError, ManifestValidationError, ReleaseStoreError,
            LaunchAgentError, OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(f"Dictionary {arguments.command} did not complete: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
