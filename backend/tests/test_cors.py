import pytest
from fastapi.testclient import TestClient

import app as app_module


client = TestClient(app_module.app)


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "http://127.0.0.1:5173"],
)
def test_chunk_memory_practice_patch_preflight(origin):
    response = client.options(
        "/api/chunks/memory/example-chunk/practice",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed_methods = {
        method.strip()
        for method in response.headers["access-control-allow-methods"].split(",")
    }
    assert "PATCH" in allowed_methods


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "http://127.0.0.1:5173"],
)
def test_library_delete_preflight(origin):
    response = client.options(
        "/api/library/items/example-item",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "DELETE",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed_methods = {
        method.strip()
        for method in response.headers["access-control-allow-methods"].split(",")
    }
    assert "DELETE" in allowed_methods
