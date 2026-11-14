"""Cloud Phase 1 composition: all-or-nothing wiring that fails closed.

PostgreSQL cases reuse the throwaway database/role fixture of the foundation
tests; synthetic data only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

import app as app_module
from services.auth.browser import configure_browser_sessions
from services.auth.sessions import COOKIE_NAME, BrowserSessionRepository, SessionPolicy, csrf_token
from services.cloud import composition
from services.cloud.composition import (
    STATE_KEYS,
    CloudStorageMisconfigured,
    CloudStorageSettings,
    bootstrap_cloud_storage,
    clear_cloud_storage,
)
from test_cloud_db_foundation import SYNTHETIC_PASSWORD, cloud_db  # noqa: F401  (fixture)
import test_durable_ai_chunk_candidates as ai_tests

ORIGIN = "https://app.localhost"
NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)
OWNER_A = "cloud-p1-compose-a"
OWNER_B = "cloud-p1-compose-b"


class State:
    pass


class FakeApp:
    def __init__(self):
        self.state = State()


@pytest.fixture
def media_root(tmp_path_factory):
    return tmp_path_factory.mktemp("cloud-media")


@pytest.fixture
def clean_app():
    names = (*STATE_KEYS, "cloud_storage_status", "browser_session_repository",
             "browser_session_policy", "identity_verifier")
    previous = {name: getattr(app_module.app.state, name, None) for name in names}
    for name in names:
        if hasattr(app_module.app.state, name):
            delattr(app_module.app.state, name)
    try:
        yield app_module.app
    finally:
        for name, value in previous.items():
            if hasattr(app_module.app.state, name):
                delattr(app_module.app.state, name)
            if value is not None:
                setattr(app_module.app.state, name, value)


# ---------------------------------------------------------------- settings

def test_no_storage_settings_means_not_configured(media_root):
    assert CloudStorageSettings.from_environment({}) is None


@pytest.mark.parametrize("env", [
    {"CLOUD_STORAGE_DATABASE_URL": "postgresql://synthetic"},
    {"CLOUD_MEDIA_ROOT": "/tmp"},
])
def test_partial_settings_are_refused(env):
    with pytest.raises(CloudStorageMisconfigured, match="Both"):
        CloudStorageSettings.from_environment(env)


def test_media_root_must_be_absolute_existing_and_outside_the_checkout(media_root, tmp_path):
    base = {"CLOUD_STORAGE_DATABASE_URL": "postgresql://synthetic"}
    for value, message in (
        ("relative/media", "absolute"),
        (str(tmp_path / "missing"), "existing directory"),
        (str(Path(composition.REPOSITORY_ROOT) / "backend"), "outside the application checkout"),
    ):
        with pytest.raises(CloudStorageMisconfigured, match=message):
            CloudStorageSettings.from_environment({**base, "CLOUD_MEDIA_ROOT": value})
    link = tmp_path / "media-link"
    link.symlink_to(media_root)
    with pytest.raises(CloudStorageMisconfigured, match="symbolic link"):
        CloudStorageSettings.from_environment({**base, "CLOUD_MEDIA_ROOT": str(link)})


@pytest.mark.parametrize("minimum", ["10", "0010", "0013", "abcd"])
def test_minimum_version_must_be_inside_phase1(media_root, minimum):
    with pytest.raises(CloudStorageMisconfigured, match="MINIMUM_VERSION"):
        CloudStorageSettings.from_environment({
            "CLOUD_STORAGE_DATABASE_URL": "postgresql://synthetic", "CLOUD_MEDIA_ROOT": str(media_root),
            "CLOUD_STORAGE_MINIMUM_VERSION": minimum})


def test_settings_repr_never_contains_the_dsn(media_root):
    settings = CloudStorageSettings.from_environment({
        "CLOUD_STORAGE_DATABASE_URL": "postgresql://u:secret-value@h/db", "CLOUD_MEDIA_ROOT": str(media_root)})
    assert "secret-value" not in repr(settings)


# ---------------------------------------------------------------- bootstrap without a database

def test_not_configured_wires_nothing():
    app = FakeApp()
    assert bootstrap_cloud_storage(app, environ={}).state == "not_configured"
    assert not any(hasattr(app.state, key) for key in STATE_KEYS)


def test_misconfiguration_is_refused_remembered_and_retried_later():
    app, now = FakeApp(), [100.0]
    calls = []
    status = bootstrap_cloud_storage(app, environ={"CLOUD_STORAGE_DATABASE_URL": "x"},
                                     clock=lambda: now[0], build=lambda s: calls.append(s))
    assert status.state == "refused" and "Both" in status.reasons[0]
    assert bootstrap_cloud_storage(app, environ={}, clock=lambda: now[0]) is status  # remembered
    now[0] += composition.RETRY_AFTER_SECONDS + 1
    assert bootstrap_cloud_storage(app, environ={}, clock=lambda: now[0]).state == "not_configured"
    assert calls == []


def test_construction_failure_leaves_every_key_absent(media_root):
    app = FakeApp()

    def explode(settings):
        raise RuntimeError("synthetic constructor failure with secret-value")

    status = bootstrap_cloud_storage(app, environ={
        "CLOUD_STORAGE_DATABASE_URL": "postgresql://u:secret-value@h/db", "CLOUD_MEDIA_ROOT": str(media_root)},
        build=explode)
    assert status.state == "refused"
    assert status.reasons == ("repository construction failed (RuntimeError)",)
    assert not any(hasattr(app.state, key) for key in STATE_KEYS)


def test_pre_injected_repositories_are_never_replaced(media_root):
    app = FakeApp()
    sentinel = object()
    app.state.cloud_topic_repository = sentinel
    status = bootstrap_cloud_storage(app, environ={
        "CLOUD_STORAGE_DATABASE_URL": "postgresql://synthetic", "CLOUD_MEDIA_ROOT": str(media_root)},
        build=lambda s: pytest.fail("must not build"))
    assert status.state == "pre_injected"
    assert app.state.cloud_topic_repository is sentinel
    assert not hasattr(app.state, "cloud_library_repository")
    assert not hasattr(app.state, "cloud_storage_status")


# ---------------------------------------------------------------- bootstrap against PostgreSQL

def test_valid_runtime_role_wires_all_four_sharing_one_study_bank(cloud_db, media_root):
    dsn = cloud_db.runtime()
    app = FakeApp()
    status = bootstrap_cloud_storage(app, environ={
        "CLOUD_STORAGE_DATABASE_URL": dsn, "CLOUD_MEDIA_ROOT": str(media_root)})
    assert status.state == "wired", status.reasons
    assert all(getattr(app.state, key) is not None for key in STATE_KEYS)
    assert app.state.cloud_ai_chunk_repository.study_bank is app.state.cloud_study_bank_repository
    assert app.state.cloud_library_repository.media_objects.root == media_root.resolve()
    clear_cloud_storage(app)
    assert not any(hasattr(app.state, key) for key in (*STATE_KEYS, "cloud_storage_status"))


@pytest.mark.parametrize("mutation, reason", [
    ("ALTER ROLE {role} BYPASSRLS", "runtime role has BYPASSRLS"),
    ("GRANT TRUNCATE ON cloud_users TO {role}", "excess privilege TRUNCATE on cloud_users"),
    ("CREATE TABLE cloud_media_generations (id UUID)", "unexpected tables: cloud_media_generations"),
])
def test_preflight_failure_wires_nothing(cloud_db, media_root, mutation, reason):
    dsn = cloud_db.runtime()
    cloud_db.execute(sql.SQL(mutation.replace("{role}", "{}")).format(sql.Identifier(cloud_db.runtime_role))
                     if "{role}" in mutation else mutation)
    app = FakeApp()
    status = bootstrap_cloud_storage(app, environ={
        "CLOUD_STORAGE_DATABASE_URL": dsn, "CLOUD_MEDIA_ROOT": str(media_root)})
    assert status.state == "refused"
    assert reason in status.reasons
    assert not any(hasattr(app.state, key) for key in STATE_KEYS)


def test_superuser_dsn_wires_nothing(cloud_db, media_root):
    import services.cloud.migrations as migrations
    migrations.apply(cloud_db.admin_dsn)
    if not cloud_db.scalar("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
        pytest.skip("TEST_POSTGRES_DSN is not a superuser")
    app = FakeApp()
    status = bootstrap_cloud_storage(app, environ={
        "CLOUD_STORAGE_DATABASE_URL": cloud_db.admin_dsn, "CLOUD_MEDIA_ROOT": str(media_root)})
    assert status.state == "refused"
    assert "runtime role has SUPERUSER" in status.reasons
    assert not any(hasattr(app.state, key) for key in STATE_KEYS)


def test_minimum_version_is_enforced_at_bootstrap(cloud_db, media_root):
    dsn = cloud_db.runtime(versions_target="0006")
    app = FakeApp()
    status = bootstrap_cloud_storage(app, environ={
        "CLOUD_STORAGE_DATABASE_URL": dsn, "CLOUD_MEDIA_ROOT": str(media_root),
        "CLOUD_STORAGE_MINIMUM_VERSION": "0009"})
    assert status.state == "refused"
    assert "schema is at 0006, at least 0009 is required" in status.reasons


# ---------------------------------------------------------------- HTTP slice through the real boundary

def _browser(repo, owner):
    token = repo.issue_for_verified_account(owner)
    client = TestClient(app_module.app, base_url=ORIGIN)
    client.cookies.set(COOKIE_NAME, token, domain="app.localhost", path="/")
    return client, {"Origin": ORIGIN, "X-CSRF-Token": csrf_token(token)}


def _lesson_payload():
    return {"lesson": ai_tests._lesson().model_dump(mode="json")}


def test_http_routes_use_composed_storage_and_isolate_owners(cloud_db, media_root, clean_app, monkeypatch):
    dsn = cloud_db.runtime()
    cloud_db.execute(
        "INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s), (%s, %s)",
        (OWNER_A, NOW, OWNER_B, NOW))
    monkeypatch.setenv("LANGUAGE_AI_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("CLOUD_STORAGE_DATABASE_URL", dsn)
    monkeypatch.setenv("CLOUD_MEDIA_ROOT", str(media_root))
    sessions = BrowserSessionRepository(dsn)
    configure_browser_sessions(clean_app, sessions, SessionPolicy(ORIGIN))

    client_a, headers_a = _browser(sessions, OWNER_A)
    created = client_a.post("/api/library/items", json=_lesson_payload(), headers=headers_a)
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    assert clean_app.state.cloud_storage_status.state == "wired"
    assert [i["id"] for i in client_a.get("/api/library/items").json()["items"]] == [item_id]

    client_b, headers_b = _browser(sessions, OWNER_B)
    assert client_b.get("/api/library/items").json()["items"] == []
    assert client_b.get(f"/api/library/items/{item_id}").status_code == 404
    assert client_b.delete(f"/api/library/items/{item_id}", headers=headers_b).status_code == 404
    assert client_a.get(f"/api/library/items/{item_id}").status_code == 200

    # Simulated restart: new app state, same database; data is still there.
    clear_cloud_storage(clean_app)
    client_a2, _ = _browser(sessions, OWNER_A)
    assert [i["id"] for i in client_a2.get("/api/library/items").json()["items"]] == [item_id]


def test_http_routes_fail_closed_when_preflight_refuses(cloud_db, media_root, clean_app, monkeypatch):
    dsn = cloud_db.runtime()
    cloud_db.execute("INSERT INTO cloud_users (user_id, created_at) VALUES (%s, %s)", (OWNER_A, NOW))
    cloud_db.execute(sql.SQL("ALTER ROLE {} BYPASSRLS").format(sql.Identifier(cloud_db.runtime_role)))
    monkeypatch.setenv("LANGUAGE_AI_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("CLOUD_STORAGE_DATABASE_URL", dsn)
    monkeypatch.setenv("CLOUD_MEDIA_ROOT", str(media_root))
    sessions = BrowserSessionRepository(dsn)
    configure_browser_sessions(clean_app, sessions, SessionPolicy(ORIGIN))
    client, headers = _browser(sessions, OWNER_A)
    listed = client.get("/api/library/items")
    assert listed.status_code == 503
    assert listed.json()["detail"]["code"] == "cloud_tenant_storage_not_configured"
    review = client.get("/api/review/queue", params={"user_id": OWNER_A})
    assert review.status_code == 503
    assert review.json()["detail"]["code"] == "cloud_study_bank_storage_not_configured"
    assert not any(hasattr(clean_app.state, key) for key in STATE_KEYS)


def test_personal_mode_never_bootstraps_cloud_storage(media_root, clean_app, monkeypatch):
    monkeypatch.setenv("LANGUAGE_AI_DEPLOYMENT_MODE", "personal")
    monkeypatch.setenv("CLOUD_STORAGE_DATABASE_URL", "postgresql://synthetic-unreachable")
    monkeypatch.setenv("CLOUD_MEDIA_ROOT", str(media_root))
    client = TestClient(clean_app)
    assert client.get("/api/deployment").json() == {"mode": "personal"}
    assert not hasattr(clean_app.state, "cloud_storage_status")
    assert not any(hasattr(clean_app.state, key) for key in STATE_KEYS)
