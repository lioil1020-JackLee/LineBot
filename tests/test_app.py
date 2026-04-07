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
        response = client.post("/admin/reload-prompt", json={"prompt": "請用繁體中文回覆"})

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["active_prompt"] == "請用繁體中文回覆"


def test_admin_session_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/session/not-exists")

    assert response.status_code == 404


def test_admin_knowledge_status_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/knowledge/status")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "chunks" in response.json()


def test_admin_llm_logs_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/llm-logs")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "items" in response.json()


def test_admin_model_endpoints() -> None:
    with TestClient(app) as client:
        get_response = client.get("/admin/model")
        assert get_response.status_code == 200
        assert get_response.json()["ok"] is True

        post_response = client.post(
            "/admin/model",
            json={"chat_model": "qwen3-coder-30b-a3b-instruct"},
        )
        assert post_response.status_code == 200
        assert post_response.json()["ok"] is True
        assert post_response.json()["chat_model"] == "qwen3-coder-30b-a3b-instruct"
