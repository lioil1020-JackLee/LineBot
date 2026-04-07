from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalLLMReply:
    text: str
    model_name: str


class ExternalLLMService:
    """OpenAI-compatible 外部模型備援服務（例如 OpenRouter）。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_candidates: list[str],
        timeout_seconds: int,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip()
        self.model_candidates = [m.strip() for m in model_candidates if m.strip()]
        self.timeout_seconds = timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.model_candidates)

    def generate_reply(
        self,
        *,
        system_prompt: str,
        conversation: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> ExternalLLMReply | None:
        if not self.enabled:
            return None

        messages = [{"role": "system", "content": system_prompt}, *conversation]
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for model in self.model_candidates:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                if response.status_code >= 400:
                    logger.warning(
                        "external_llm model=%s request failed status=%s",
                        model,
                        response.status_code,
                    )
                    continue
                data = response.json()
                choices = data.get("choices", [])
                if not choices:
                    continue
                text = (choices[0].get("message", {}).get("content") or "").strip()
                if not text:
                    continue
                return ExternalLLMReply(text=text, model_name=data.get("model") or model)
            except Exception:
                logger.exception("external_llm model=%s call failed", model)
                continue

        return None
