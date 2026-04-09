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


def test_admin_reload_prompt_endpoint() -> None:
    with TestClient(app) as client:
        response = client.post("/admin/reload-prompt", json={"prompt": "新的 system prompt"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["active_prompt"] == "新的 system prompt"


def test_admin_session_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/session/not-exists")

    assert response.status_code == 404


def test_admin_persona_routes_removed() -> None:
    with TestClient(app) as client:
        assert client.get("/admin/persona").status_code == 404
        assert client.post("/admin/persona", json={"preset": "virtual_partner"}).status_code == 404
        assert client.get("/admin/persona/presets").status_code == 404
