from unittest.mock import patch

from fastapi.testclient import TestClient

from linebot_app.app import app


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_health_detail_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health/detail")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"ok", "degraded"}
    assert "sqlite" in payload
    assert "lm_studio" in payload


def test_webhook_requires_configuration() -> None:
    with patch("linebot_app.app.settings") as mock_settings:
        mock_settings.line_ready = False
        with TestClient(app) as client:
            response = client.post("/webhook", json={"events": []})

        assert response.status_code == 503


def test_removed_admin_routes_return_404() -> None:
    with TestClient(app) as client:
        assert client.post("/admin/reload-prompt", json={"prompt": "x"}).status_code == 404
        assert client.get("/admin/session/not-exists").status_code == 404
        assert client.get("/admin/metrics").status_code == 404
        assert client.get("/admin/model").status_code == 404
        assert client.get("/admin/knowledge/status").status_code == 404
