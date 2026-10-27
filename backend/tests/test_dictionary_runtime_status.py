import os
from pathlib import Path

from fastapi.testclient import TestClient

import app as app_module
from services.api import dictionary_api
from services.dictionary.forms_sidecar import build_forms_index
from services.dictionary.provider import SQLiteDictionaryProvider, import_kaikki_sources
from services.dictionary.release_store import resolve_layout, switch_current
from services.dictionary.runtime_status import runtime_status
from services.dictionary.service import DictionaryService

FIXTURES = Path(__file__).parent / "fixtures"


def _release(storage: Path, build_id: str) -> Path:
    release = storage / "dictionary_releases" / build_id
    release.mkdir(parents=True)
    database = release / "dictionary.sqlite3"
    import_kaikki_sources(
        [(FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/en")],
        database,
        content_license="Fixture",
    )
    build_forms_index(database, release / "dictionary.forms.sqlite3")
    return release


def test_status_endpoint_reports_release_through_current_link_without_paths(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    _release(storage, "release-a")
    _release(storage, "release-b")
    layout = resolve_layout(storage)
    switch_current(layout, "release-a")
    provider = SQLiteDictionaryProvider(
        layout.current_database_path(), layout.current_sidecar_path()
    )
    monkeypatch.setattr(dictionary_api, "dictionary_service", DictionaryService(provider))
    client = TestClient(app_module.app)

    response = client.get("/api/dictionary/status")
    assert response.status_code == 200
    assert response.json() == {
        "configured": True,
        "release_id": "release-a",
        "forms_index_status": "available",
    }
    assert str(tmp_path) not in response.text

    switch_current(layout, "release-b")
    assert client.get("/api/dictionary/status").json()["release_id"] == "release-b"


def test_status_for_legacy_flat_and_missing_dictionaries(tmp_path):
    database = tmp_path / "storage" / "dictionary.sqlite3"
    database.parent.mkdir()
    import_kaikki_sources(
        [(FIXTURES / "kaikki_en_fixture.jsonl", "en", "https://kaikki.org/en")],
        database,
        content_license="Fixture",
    )
    assert runtime_status(database, tmp_path / "storage" / "dictionary.forms.sqlite3") == {
        "configured": True,
        "release_id": "unversioned",
        "forms_index_status": "index_unavailable",
    }
    assert runtime_status(tmp_path / "missing.sqlite3", None)["configured"] is False


def test_switch_current_fsyncs_storage_directory_around_replace(tmp_path, monkeypatch):
    storage = tmp_path / "storage"
    storage.mkdir()
    _release(storage, "release-a")
    layout = resolve_layout(storage)
    from services.dictionary import release_store

    synced = []
    real_fsync_directory = release_store.fsync_directory
    monkeypatch.setattr(
        release_store,
        "fsync_directory",
        lambda path: (synced.append(Path(path)), real_fsync_directory(path)),
    )
    switch_current(layout, "release-a")
    assert synced.count(layout.storage) == 2
    assert os.readlink(storage / "dictionary_current") == "dictionary_releases/release-a"
