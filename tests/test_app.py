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


def test_admin_session_profile_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/session/not-exists/profile")

    assert response.status_code == 404


def test_admin_session_tasks_not_found() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/session/not-exists/tasks")

    assert response.status_code == 404


def test_admin_metrics_endpoint_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "window" in payload


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


def test_admin_metrics_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert "status_counts" in payload
    assert "latency_ms" in payload


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


def test_admin_persona_endpoints() -> None:
    with TestClient(app) as client:
        get_response = client.get("/admin/persona")
        assert get_response.status_code == 200
        assert get_response.json()["ok"] is True
        assert "active_preset" in get_response.json()

        post_response = client.post(
            "/admin/persona",
            json={"preset": "virtual_partner"},
        )
        assert post_response.status_code == 200
        assert post_response.json()["ok"] is True
        assert "虛擬情人" in post_response.json()["persona_prompt"]


def test_admin_persona_rejects_unknown_preset() -> None:
    with TestClient(app) as client:
        response = client.post("/admin/persona", json={"preset": "unknown_role"})

    assert response.status_code == 400


def test_admin_persona_presets_crud() -> None:
    with TestClient(app) as client:
        create_response = client.post(
            "/admin/persona/presets",
            json={
                "name": "coach",
                "prompt": "你現在扮演健身教練，回覆精簡。",
                "set_active": True,
            },
        )
        assert create_response.status_code == 200
        assert create_response.json()["ok"] is True
        assert create_response.json()["item"]["name"] == "coach"

        list_response = client.get("/admin/persona/presets")
        assert list_response.status_code == 200
        names = [item["name"] for item in list_response.json()["items"]]
        assert "coach" in names

        active_response = client.get("/admin/persona")
        assert active_response.status_code == 200
        assert active_response.json()["active_preset"] == "coach"

        delete_response = client.delete("/admin/persona/presets/coach")
        assert delete_response.status_code == 200
        assert delete_response.json()["ok"] is True


def test_admin_persona_presets_rejects_builtin_delete() -> None:
    with TestClient(app) as client:
        response = client.delete("/admin/persona/presets/default")

    assert response.status_code == 400


def test_admin_persona_export_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/admin/persona/export")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert isinstance(payload["items"], list)
    assert any(item["name"] == "default" for item in payload["items"])


def test_admin_persona_import_endpoint() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/admin/persona/import",
            json={
                "items": [
                    {
                        "name": "travel_guide",
                        "prompt": "你現在扮演旅遊顧問，回覆要清楚且實用。",
                        "is_builtin": False,
                        "is_active": True,
                    }
                ]
            },
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        assert response.json()["imported"] >= 1

        active_response = client.get("/admin/persona")
        assert active_response.status_code == 200
        assert active_response.json()["active_preset"] == "travel_guide"
