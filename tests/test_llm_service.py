from __future__ import annotations

import httpx
import pytest

from linebot_app.services.llm_service import (
    LLMService,
    LLMServiceError,
    LMStudioTimeoutError,
    LMStudioUnavailableError,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, object]:
        return self._payload


class _FakeClient:
    def __init__(
        self,
        *,
        response_map: dict[tuple[str, str], _FakeResponse],
        raise_timeout: bool = False,
    ) -> None:
        self.response_map = response_map
        self.raise_timeout = raise_timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def get(self, url: str):
        return self.response_map[("GET", url)]

    def post(self, url: str, json: dict[str, object]):
        if self.raise_timeout:
            raise httpx.TimeoutException("timeout")
        return self.response_map[("POST", url)]


def test_generate_reply_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = LLMService(
        base_url="http://127.0.0.1:1234/v1",
        chat_model="chat-model",
        embed_model="embed-model",
        timeout_seconds=10,
        max_tokens=256,
        temperature=0.2,
    )
    fake = _FakeClient(
        response_map={
            (
                "POST",
                "http://127.0.0.1:1234/v1/chat/completions",
            ): _FakeResponse(
                200,
                {
                    "model": "chat-model",
                    "choices": [{"message": {"content": "你好"}}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    },
                },
            )
        }
    )
    monkeypatch.setattr(httpx, "Client", lambda timeout: fake)

    result = service.generate_reply(
        system_prompt="test",
        conversation=[{"role": "user", "content": "hi"}],
    )

    assert result.text == "你好"
    assert result.total_tokens == 15


def test_generate_reply_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    service = LLMService(
        base_url="http://127.0.0.1:1234/v1",
        chat_model="chat-model",
        embed_model="embed-model",
        timeout_seconds=10,
        max_tokens=256,
        temperature=0.2,
    )
    fake = _FakeClient(response_map={}, raise_timeout=True)
    monkeypatch.setattr(httpx, "Client", lambda timeout: fake)

    with pytest.raises(LMStudioTimeoutError):
        service.generate_reply(
            system_prompt="test",
            conversation=[{"role": "user", "content": "hi"}],
        )


def test_generate_reply_no_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    service = LLMService(
        base_url="http://127.0.0.1:1234/v1",
        chat_model="chat-model",
        embed_model="embed-model",
        timeout_seconds=10,
        max_tokens=256,
        temperature=0.2,
    )
    fake = _FakeClient(
        response_map={
            (
                "POST",
                "http://127.0.0.1:1234/v1/chat/completions",
            ): _FakeResponse(200, {"choices": []})
        }
    )
    monkeypatch.setattr(httpx, "Client", lambda timeout: fake)

    with pytest.raises(LLMServiceError):
        service.generate_reply(
            system_prompt="test",
            conversation=[{"role": "user", "content": "hi"}],
        )


def test_embed_text_success(monkeypatch: pytest.MonkeyPatch) -> None:
    service = LLMService(
        base_url="http://127.0.0.1:1234/v1",
        chat_model="chat-model",
        embed_model="embed-model",
        timeout_seconds=10,
        max_tokens=256,
        temperature=0.2,
    )
    fake = _FakeClient(
        response_map={
            (
                "POST",
                "http://127.0.0.1:1234/v1/embeddings",
            ): _FakeResponse(200, {"data": [{"embedding": [0.1, 0.2, 0.3]}]})
        }
    )
    monkeypatch.setattr(httpx, "Client", lambda timeout: fake)

    vector = service.embed_text("hello")

    assert vector == [0.1, 0.2, 0.3]


def test_embed_text_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    service = LLMService(
        base_url="http://127.0.0.1:1234/v1",
        chat_model="chat-model",
        embed_model="embed-model",
        timeout_seconds=10,
        max_tokens=256,
        temperature=0.2,
    )

    class _RaiseClient(_FakeClient):
        def post(self, url: str, json: dict[str, object]):
            raise httpx.ConnectError("down")

    fake = _RaiseClient(response_map={})
    monkeypatch.setattr(httpx, "Client", lambda timeout: fake)

    with pytest.raises(LMStudioUnavailableError):
        service.embed_text("hello")
