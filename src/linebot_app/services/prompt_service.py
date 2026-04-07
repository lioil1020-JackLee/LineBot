from __future__ import annotations

from linebot_app.repositories.prompt_repository import PromptRepository


class PromptService:
    def __init__(self, *, prompt_repository: PromptRepository, default_prompt: str) -> None:
        self.prompt_repository = prompt_repository
        self.prompt_repository.ensure_default_prompt(default_prompt.strip())

    def get_active_prompt(self) -> str:
        active = self.prompt_repository.get_active_prompt()
        return (active or "").strip()

    def reload(self, prompt: str | None = None) -> str:
        if prompt:
            return self.prompt_repository.set_active_prompt(prompt.strip())
        return self.get_active_prompt()