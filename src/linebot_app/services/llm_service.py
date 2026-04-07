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
        """嘗試啟動 LM Studio，若已執行或無 exe_path 則返回 True。

        Args:
            max_wait_seconds: 最大等待秒數

        Returns:
            True 若成功連接或啟動，False 若失敗
        """
        if not self.exe_path:
            return self.is_available()

        exe_path = Path(self.exe_path)
        if not exe_path.exists():
            print(f"⚠️  LM Studio exe 不存在: {self.exe_path}")
            return self.is_available()

        # 檢查是否已可連接
        if self.is_available():
            print("✓ LM Studio 已在執行")
            return True

        # 嘗試啟動
        print(f"🚀 啟動 LM Studio: {exe_path}")
        try:
            self._lm_studio_process = subprocess.Popen(
                [str(exe_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_CONSOLE if hasattr(subprocess, "CREATE_NEW_CONSOLE") else 0,
            )
        except Exception as e:
            print(f"❌ 啟動 LM Studio 失敗: {e}")
            return False

        # 等待啟動完成
        start_time = time.time()
        while time.time() - start_time < max_wait_seconds:
            if self.is_available():
                print(f"✓ LM Studio 啟動成功 ({time.time() - start_time:.1f}s)")
                return True
            time.sleep(1)

        print(f"❌ LM Studio 在 {max_wait_seconds}s 內未啟動")
        return False

    def generate_reply(self, *, system_prompt: str, conversation: list[dict[str, str]]) -> LLMReply:
        messages = [{"role": "system", "content": system_prompt}, *conversation]
        payload = {
            "model": self.chat_model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        started = perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.base_url}/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LMStudioTimeoutError("LM Studio request timeout") from exc
        except httpx.HTTPError as exc:
            raise LMStudioUnavailableError("LM Studio is unavailable") from exc

        latency_ms = int((perf_counter() - started) * 1000)

        if response.status_code >= 500:
            raise LLMServiceError(f"LM Studio server error: {response.status_code}")
        if response.status_code >= 400:
            raise LLMServiceError(f"LM Studio request failed: {response.status_code}")

        data = response.json()
        choices = data.get("choices", [])
        if not choices:
            raise LLMServiceError("LM Studio returned no choices")

        message = choices[0].get("message", {})
        text = (message.get("content") or "").strip()
        if not text:
            raise LLMServiceError("LM Studio returned empty content")

        usage = data.get("usage", {})
        return LLMReply(
            text=text,
            model_name=data.get("model") or self.chat_model,
            latency_ms=latency_ms,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
        )

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