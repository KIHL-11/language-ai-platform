from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as app_module


client = TestClient(app_module.app)


def _frontend_build(tmp_path: Path) -> Path:
    frontend = tmp_path / "dist"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text(
        '<!doctype html><main id="personal-app">Language AI</main>',
        encoding="utf-8",
    )
    (assets / "app.js").write_text("window.personalApp = true", encoding="utf-8")
    (frontend / "favicon.svg").write_text("<svg></svg>", encoding="utf-8")
    return frontend


def test_health_api_is_not_shadowed_by_personal_frontend(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "FRONTEND_DIST", _frontend_build(tmp_path))

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "personal-app" not in response.text


def test_personal_frontend_index_is_served_at_root(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "FRONTEND_DIST", _frontend_build(tmp_path))

    response = client.get("/?stage=mini_dictation")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="personal-app"' in response.text


def test_personal_frontend_asset_is_served_from_dist(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "FRONTEND_DIST", _frontend_build(tmp_path))

    response = client.get("/assets/app.js")

    assert response.status_code == 200
    assert response.text == "window.personalApp = true"


def test_root_public_file_is_served_without_replacing_spa_navigation(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "FRONTEND_DIST", _frontend_build(tmp_path))

    public_file = client.get("/favicon.svg")
    spa_route = client.get("/training?stage=shadowing")

    assert public_file.status_code == 200
    assert public_file.text == "<svg></svg>"
    assert spa_route.status_code == 200
    assert 'id="personal-app"' in spa_route.text


def test_unknown_api_route_is_not_replaced_by_spa_index(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "FRONTEND_DIST", _frontend_build(tmp_path))

    response = client.get("/api/not-a-real-route")

    assert response.status_code == 404
    assert 'id="personal-app"' not in response.text


def test_media_route_remains_reachable_and_is_not_shadowed(
    tmp_path,
    monkeypatch,
):
    frontend = _frontend_build(tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    (media / "sample.mp3").write_bytes(b"personal-media")
    monkeypatch.setattr(app_module, "FRONTEND_DIST", frontend)
    monkeypatch.setattr(app_module, "MEDIA", media)

    response = client.get("/media/sample.mp3")

    assert response.status_code == 200
    assert response.content == b"personal-media"
    assert 'id="personal-app"' not in response.text


def test_missing_frontend_build_returns_clear_development_message(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(app_module, "FRONTEND_DIST", tmp_path / "missing-dist")

    response = client.get("/")

    assert response.status_code == 503
    assert "npm run build" in response.text
    assert "frontend/dist" in response.text


def test_static_file_resolution_rejects_parent_and_symlink_escape(
    tmp_path,
):
    public = tmp_path / "public"
    public.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")
    (public / "escape.txt").symlink_to(outside)

    with pytest.raises(HTTPException) as parent_escape:
        app_module._safe_static_file(public, "../private.txt")
    with pytest.raises(HTTPException) as symlink_escape:
        app_module._safe_static_file(public, "escape.txt")

    assert parent_escape.value.status_code == 404
    assert symlink_escape.value.status_code == 404
