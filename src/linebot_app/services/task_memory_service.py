from __future__ import annotations

import re


class TaskMemoryService:
    _TASK_PATTERNS = (
        r"(?:我要|我想|請幫我|需要)\s*([^，。!?\n]{4,40})",
        r"(?:待辦|todo)\s*[:：]\s*([^\n]{4,60})",
    )

    def parse_command(self, text: str) -> tuple[str, int | None] | None:
        content = text.strip()
        if not content:
            return None

        lowered = content.lower().replace(" ", "")
        if any(token in lowered for token in ("查看待辦", "待辦清單", "listtasks", "tasks")):
            return ("list", None)

        done_match = re.search(r"(?:完成|done)第?(\d+)項", lowered)
        if done_match:
            return ("done", int(done_match.group(1)))

        start_match = re.search(r"(?:開始|進行|doing)第?(\d+)項", lowered)
        if start_match:
            return ("in_progress", int(start_match.group(1)))

        return None

    def extract_tasks(self, text: str) -> list[str]:
        content = text.strip()
        if not content:
            return []

        tasks: list[str] = []
        for pattern in self._TASK_PATTERNS:
            for match in re.finditer(pattern, content, flags=re.IGNORECASE):
                task = match.group(1).strip(" ，。;；")
                if 4 <= len(task) <= 60:
                    tasks.append(task)

        # Deduplicate while preserving order.
        unique: list[str] = []
        seen: set[str] = set()
        for task in tasks:
            key = task.lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(task)

        return unique
