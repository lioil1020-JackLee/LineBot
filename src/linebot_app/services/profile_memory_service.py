from __future__ import annotations


class ProfileMemoryService:
    def extract(self, summary: str) -> dict[str, list[str]]:
        lines = [line.strip("- ").strip() for line in summary.splitlines() if line.strip()]
        preferences: list[str] = []
        goals: list[str] = []
        constraints: list[str] = []

        for line in lines:
            lower = line.lower()
            if "偏好" in line or "prefer" in lower:
                preferences.append(line)
            elif "目標" in line or "goal" in lower or "想" in line:
                goals.append(line)
            elif "限制" in line or "避免" in line or "不能" in line:
                constraints.append(line)

        return {
            "preferences": preferences,
            "goals": goals,
            "constraints": constraints,
        }
