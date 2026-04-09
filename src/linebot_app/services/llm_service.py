from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import httpx


class LMStudioUnavailableError(Exception):
    pass


class LMStudioTimeoutError(Exception):
    pass


class LLMServiceError(Exception):
    pass


def _truncate_error_text(text: str, limit: int = 240) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


@dataclass(frozen=True)
class LLMReply:
    text: str
    model_name: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


class LLMService:
    def __init__(
        self,
        *,
        base_url: str,
        chat_model: str,
        embed_model: str,
        timeout_seconds: int,
        max_tokens: int,
        temperature: float,
        exe_path: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.exe_path = exe_path
        self._lm_studio_process: subprocess.Popen | None = None

    def get_models(self) -> dict[str, str]:
        return {
            "chat_model": self.chat_model,
            "embed_model": self.embed_model,
        }

    def set_models(
        self,
        *,
        chat_model: str | None = None,
        embed_model: str | None = None,
    ) -> dict[str, str]:
        if chat_model:
            self.chat_model = chat_model.strip()
        if embed_model:
            self.embed_model = embed_model.strip()
        return self.get_models()

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as client:
                response = client.get(f"{self.base_url}/models")
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def try_start_lm_studio(self, max_wait_seconds: int = 30) -> bool:
        """Try to launch LM Studio when an executable path is configured."""
        if not self.exe_path:
            return self.is_available()

        exe_path = Path(self.exe_path)
        if not exe_path.exists():
            print(f"LM Studio executable not found: {self.exe_path}")
            return self.is_available()

        if self.is_available():
            print("LM Studio is already running.")
            return True

        print(f"Starting LM Studio: {exe_path}")
        try:
            self._lm_studio_process = subprocess.Popen(
                [str(exe_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(
                    subprocess.CREATE_NEW_CONSOLE
                    if hasattr(subprocess, "CREATE_NEW_CONSOLE")
                    else 0
                ),
            )
        except Exception as exc:
            print(f"Failed to start LM Studio: {exc}")
            return False

        start_time = time.time()
        while time.time() - start_time < max_wait_seconds:
            if self.is_available():
                elapsed = time.time() - start_time
                print(f"LM Studio became available after {elapsed:.1f}s")
                return True
            time.sleep(1)

        print(f"LM Studio did not become available within {max_wait_seconds}s")
        return False

    def generate_reply(
        self,
        *,
        system_prompt: str,
        conversation: list[dict[str, str]],
        timeout_seconds: int | None = None,
        max_tokens: int | None = None,
    ) -> LLMReply:
        messages = [{"role": "system", "content": system_prompt}, *conversation]
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }

        effective_timeout = timeout_seconds or self.timeout_seconds
        started = perf_counter()
        try:
            with httpx.Client(timeout=effective_timeout) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LMStudioTimeoutError(
                f"LM Studio request timeout after {effective_timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise LMStudioUnavailableError("LM Studio is unavailable") from exc

        latency_ms = int((perf_counter() - started) * 1000)

        if response.status_code >= 500:
            detail = _truncate_error_text(getattr(response, "text", ""))
            raise LLMServiceError(
                "LM Studio server error: "
                f"{response.status_code}; model={self.chat_model}; body={detail}"
            )
        if response.status_code >= 400:
            detail = _truncate_error_text(getattr(response, "text", ""))
            raise LLMServiceError(
                "LM Studio request failed: "
                f"{response.status_code}; model={self.chat_model}; body={detail}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMServiceError("LM Studio returned invalid JSON") from exc
        choices = data.get("choices", [])
        if not choices:
            raise LLMServiceError(
                f"LM Studio returned no choices; model={data.get('model') or self.chat_model}"
            )

        message = choices[0].get("message", {})
        text = (message.get("content") or "").strip()
        if not text:
            retry_text = self._retry_finalize_answer(
                conversation=conversation,
                timeout_seconds=effective_timeout,
                max_tokens=max_tokens or self.max_tokens,
            )
            if retry_text:
                text = retry_text
            else:
                raise LLMServiceError(
                    "LM Studio returned empty content; "
                    f"model={data.get('model') or self.chat_model}"
                )

        usage = data.get("usage", {})
        return LLMReply(
            text=text,
            model_name=data.get("model") or self.chat_model,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

    def _retry_finalize_answer(
        self,
        *,
        conversation: list[dict[str, str]],
        timeout_seconds: int,
        max_tokens: int,
    ) -> str | None:
        """Handle occasional empty content responses by forcing a concise final answer once."""
        recovery_messages = [
            {
                "role": "system",
                "content": (
                    "You must now produce the final answer directly. "
                    "Do not emit tool calls, tags, or extra metadata."
                ),
            },
            *conversation,
        ]
        payload = {
            "model": self.chat_model,
            "messages": recovery_messages,
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload)
        except httpx.HTTPError:
            return None

        if response.status_code >= 400:
            return None

        try:
            data = response.json()
        except ValueError:
            return None

        choices = data.get("choices", [])
        if not choices:
            return None

        message = choices[0].get("message", {})
        text = (message.get("content") or "").strip()
        return text or None

    def embed_text(self, text: str) -> list[float]:
        payload = {
            "model": self.embed_model,
            "input": text,
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.base_url}/embeddings", json=payload)
        except httpx.TimeoutException as exc:
            raise LMStudioTimeoutError("LM Studio embedding timeout") from exc
        except httpx.HTTPError as exc:
            raise LMStudioUnavailableError("LM Studio is unavailable") from exc

        if response.status_code >= 500:
            raise LLMServiceError(f"LM Studio embedding server error: {response.status_code}")
        if response.status_code >= 400:
            raise LLMServiceError(f"LM Studio embedding request failed: {response.status_code}")

        data = response.json()
        items = data.get("data", [])
        if not items:
            raise LLMServiceError("LM Studio embedding returned no data")
        embedding = items[0].get("embedding")
        if not embedding:
            raise LLMServiceError("LM Studio embedding is empty")
        return [float(value) for value in embedding]
