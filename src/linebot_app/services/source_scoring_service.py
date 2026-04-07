from __future__ import annotations


class SourceScoringService:
    def confidence_label(self, score: float) -> str:
        if score >= 0.8:
            return "高"
        if score >= 0.6:
            return "中"
        return "低"
