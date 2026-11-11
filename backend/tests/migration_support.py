"""Synthetic fixtures and a mock macOS lifecycle for DQ-02 migration tests.

Everything here lives under pytest's ``tmp_path``. Nothing touches a real
dictionary, LaunchAgent, or learning store.
"""

import gzip
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from services.dictionary.forms_sidecar import build_forms_index
from services.dictionary.legacy_release import create_legacy_rollback_release
from services.dictionary.provider import SQLiteDictionaryProvider, import_kaikki_sources
from services.dictionary.release_manifest import file_sha256
from services.dictionary.release_prepare import RepositoryState, prepare_release
from services.dictionary.release_store import (
    CURRENT_LINK_NAME,
    GIB,
    RELEASES_DIRNAME,
    resolve_layout,
)
from services.dictionary.runtime_status import runtime_status
from services.dictionary.service import DictionaryService

FIXTURES = Path(__file__).parent / "fixtures"
MIGRATION_FIXTURES = FIXTURES / "migration"
GIT_SHA = "234f0587b0f1321a7b8af636e259ebf2f017d3ad"
REPOSITORY = RepositoryState(
    url="https://github.com/KIHL-11/language-ai-platform.git",
    git_sha=GIT_SHA,
    worktree_clean=True,
    python_version="3.12",
)
NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)
LEARNING_FILES = {
    "study_bank.json": '{"items":[{"id":"card-1","meaning":"旧释义","review_state":{"due":"2026-10-28"}}]}',
    "chunk_bank.json": '{"chunks":[]}',
    "learning_library.json": '{"entries":[{"id":"lib-1"}]}',
    "speaking_sessions.json": '{"sessions":[]}',
}


def tree_state(root: Path) -> dict:
    """Every path under ``root`` with type, mode, size, mtime and content hash."""
    state = {}
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in sorted([*directories, *files]):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                state[relative] = ("symlink", os.readlink(path))
            elif path.is_dir():
                state[relative] = ("dir", path.stat().st_mode)
            else:
                stat_result = path.stat()
                state[relative] = ("file", stat_result.st_mode, stat_result.st_size,
                                   stat_result.st_mtime_ns, file_sha256(path))
    return state


def write_launch_agent(path: Path, *, environment: dict | None = None, host: str = "127.0.0.1") -> bytes:
    payload = {
        "Label": "com.language-ai-platform",
        "ProgramArguments": ["/example/.venv/bin/python", "-m", "uvicorn", "app:app",
                             "--host", host, "--port", "8000"],
        "WorkingDirectory": "/example/backend",
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1", **(environment or {})},
    }
    data = plistlib.dumps(payload, sort_keys=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


class MigrationEnvironment:
    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.storage = tmp_path / "backend" / "storage"
        self.storage.mkdir(parents=True)
        self.sources = tmp_path / "sources"
        self.sources.mkdir()
        self.plist = tmp_path / "LaunchAgents" / "com.language-ai-platform.plist"
        self.legacy = self.storage / "dictionary.sqlite3"
        import_kaikki_sources(
            [
                (FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/dictionary/legacy"),
                (FIXTURES / "kaikki_de_fixture.jsonl", "de", "https://kaikki.org/dewiktionary/legacy"),
            ],
            self.legacy,
            content_license="Legacy fixture license",
        )
        build_forms_index(self.legacy, self.storage / "dictionary.forms.sqlite3")
        (self.storage / "dictionary.forms.sqlite3.lock").unlink()
        for name, content in LEARNING_FILES.items():
            (self.storage / name).write_text(content, encoding="utf-8")
        media = self.storage / "library_media" / "lib-1"
        media.mkdir(parents=True)
        (media / "audio.mp3").write_bytes(b"ID3-synthetic-audio")
        self.original_plist = write_launch_agent(self.plist)
        self.artifacts = {}
        for language in ("en", "de"):
            artifact = self.sources / f"kaikki-{language}-fixture.jsonl.gz"
            artifact.write_bytes(gzip.compress(
                (MIGRATION_FIXTURES / f"kaikki_migration_{language}.jsonl").read_bytes(), mtime=0,
            ))
            self.artifacts[language] = artifact
        self.evidence = tmp_path / "source-evidence.json"
        self.write_evidence()
        self.gold = MIGRATION_FIXTURES / "gold_fixture.json"

    @property
    def layout(self):
        return resolve_layout(self.storage, create=True)

    def source_record(self, language: str, **overrides) -> dict:
        artifact = self.artifacts[language]
        record = {
            "edition": f"{language} synthetic edition",
            "entry_language": language,
            "acquisition_url": f"https://kaikki.org/{language}/fixture.jsonl.gz",
            "resolved_url": f"https://kaikki.org/{language}/fixture.jsonl.gz",
            "rolling_url": True,
            "snapshot_identity": {"sha256": file_sha256(artifact), "size_bytes": artifact.stat().st_size},
            "dates": {
                "dump_date": None, "dump_date_status": "unverified",
                "extraction_date": None, "extraction_date_status": "unverified",
                "retrieved_at": "2026-10-27T10:00:00Z",
            },
            "http_evidence": {"etag": None, "last_modified": None, "content_length": artifact.stat().st_size},
            "license": {
                "label": "CC BY-SA 4.0 and GFDL (synthetic fixture)",
                "notice_url": "https://kaikki.org/",
                "attribution": "Synthetic fixture derived from no real source",
                "verified": True,
                "verified_at": "2026-10-27T10:00:00Z",
            },
            "artifact_name": artifact.name,
        }
        record.update(overrides)
        return record

    def write_evidence(self, sources: list[dict] | None = None) -> None:
        self.evidence.write_text(json.dumps({
            "evidence_version": 1,
            "sources": sources or [self.source_record("en"), self.source_record("de")],
        }), encoding="utf-8")

    def legacy_release(self) -> str:
        return create_legacy_rollback_release(self.layout, self.legacy, now=NOW)["build_id"]

    def prepare(self, rollback_build_id: str, **overrides):
        arguments = {
            "layout": self.layout,
            "source_root": self.sources,
            "source_evidence": self.evidence,
            "gold_set": self.gold,
            "rollback_build_id": rollback_build_id,
            "repository": REPOSITORY,
            "now": NOW,
            "allow_fixture_gold": True,
            "minimum_headroom_bytes": 0,
        }
        arguments.update(overrides)
        return prepare_release(**arguments)

    def learning_hashes(self) -> dict:
        return {name: file_sha256(self.storage / name) for name in LEARNING_FILES}


class FakeBackend:
    """Emulates the uvicorn process the LaunchAgent starts.

    ``start`` reads the dictionary variables from the plist (as launchd would
    export them) and builds a fresh DictionaryService, so caches die with the
    process exactly like a real restart.
    """

    def __init__(self, default_database: Path):
        self.default_database = default_database
        self.service: DictionaryService | None = None
        self.environment: dict[str, str] = {}
        self.fail_lookup = False

    def boot(self, environment: dict[str, str]) -> None:
        self.environment = dict(environment)
        database = Path(environment.get("DICTIONARY_DB_PATH") or self.default_database)
        sidecar = environment.get("DICTIONARY_FORMS_INDEX_PATH")
        self.service = DictionaryService(SQLiteDictionaryProvider(database, Path(sidecar) if sidecar else None))

    def shutdown(self) -> None:
        self.service = None

    def healthy(self) -> bool:
        return self.service is not None

    def get_json(self, path: str) -> dict:
        assert path == "/api/dictionary/status" and self.service is not None
        provider = self.service.provider
        return runtime_status(provider.path, provider.forms_index_path)

    def post_json(self, path: str, payload: dict) -> dict:
        assert path == "/api/dictionary/lookup" and self.service is not None
        if self.fail_lookup:
            return {"status": "not_found"}
        return self.service.lookup(
            payload["language"], payload["word"], payload["sentence"],
            include_inflections=payload["include_inflections"],
        ).model_dump()


class MockLaunchAgent:
    """Records lifecycle calls; can inject launchd-level failures."""

    def __init__(self, backend: FakeBackend, *, loaded: bool = True, plist: Path | None = None):
        self.backend = backend
        self.loaded = False
        self.calls: list[str] = []
        self.fail_start_times = 0
        self.ignore_plist_environment = False
        self.refuse_stop = False
        if loaded and plist is not None:
            self._boot(plist)

    def _boot(self, plist: Path) -> None:
        environment = plistlib.loads(plist.read_bytes()).get("EnvironmentVariables", {})
        if self.ignore_plist_environment:
            environment = {key: value for key, value in environment.items() if not key.startswith("DICTIONARY_")}
        self.backend.boot(environment)
        self.loaded = True

    def is_loaded(self) -> bool:
        return self.loaded

    def stop(self) -> None:
        self.calls.append("stop")
        if self.refuse_stop:
            return
        self.loaded = False
        self.backend.shutdown()

    def start(self, plist_path: Path) -> None:
        self.calls.append("start")
        if self.fail_start_times:
            self.fail_start_times -= 1
            return
        self._boot(plist_path)

    def loaded_environment(self) -> dict[str, str]:
        assert self.loaded
        return dict(self.backend.environment)


def lifecycle(env: MigrationEnvironment, *, loaded: bool = True):
    backend = FakeBackend(env.legacy)
    controller = MockLaunchAgent(backend, loaded=loaded, plist=env.plist)
    return controller, backend


def current_target(env: MigrationEnvironment) -> str | None:
    link = env.storage / CURRENT_LINK_NAME
    return os.readlink(link) if link.is_symlink() else None


def release_dir(env: MigrationEnvironment, build_id: str) -> Path:
    return env.storage / RELEASES_DIRNAME / build_id


def make_writable(path: Path) -> None:
    path.chmod(0o755)
    for child in path.iterdir():
        child.chmod(0o644)


def run_script(script: Path, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *arguments], capture_output=True,
                          text=True, check=False, cwd=cwd)


__all__ = [name for name in dir() if not name.startswith("_")] + ["GIB", "hashlib", "shutil"]
