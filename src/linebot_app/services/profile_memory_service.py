from __future__ import annotations


class ProfileMemoryService:
    def extract(self, summary: str) -> dict[str, list[str]]:
        lines = [line.strip("- ").strip() for line in summary.splitlines() if line.strip()]
        preferences: list[str] = []
        goals: list[str] = []
        constraints: list[str] = []

        for line in lines:
            lower = line.lower()
            if any(token in lower for token in ("偏好", "喜歡", "prefer", "preference")):
                preferences.append(line)
            elif any(token in lower for token in ("目標", "想要", "goal")):
                goals.append(line)
            elif any(token in lower for token in ("限制", "不能", "避免", "constraint")):
                constraints.append(line)

        return {
            "preferences": preferences,
            "goals": goals,
            "constraints": constraints,
        }
