from __future__ import annotations

from linebot_app.services.research_planner_service import ResearchPlannerService


class _StubLLM:
    def __init__(self, reply_text: str) -> None:
        self._reply_text = reply_text
        self.timeout_seconds = 10
        self.max_tokens = 300

    def generate_reply(self, *, system_prompt: str, conversation: list[dict[str, str]], **kwargs):
        class _Reply:
            def __init__(self, text: str) -> None:
                self.text = text

        return _Reply(self._reply_text)


def test_planner_uses_llm_json_when_available() -> None:
    llm = _StubLLM(
        """
        {
          "route": "search_then_answer",
          "needs_external_info": true,
          "needs_knowledge_base": true,
          "freshness": "today",
          "search_queries": ["CPBL 今日賽程", "中職 今天 賽程"],
          "forbid_unverified_claims": true,
          "answer_style": "balanced"
        }
        """
    )
    planner = ResearchPlannerService(llm_service=llm)
    plan = planner.plan(question="今天有什麼棒球賽？", context=None)
    assert plan.needs_external_info is True
    assert plan.route == "search_then_answer"
    assert plan.search_queries


def test_planner_falls_back_to_heuristic_on_bad_json() -> None:
    llm = _StubLLM("not json")
    planner = ResearchPlannerService(llm_service=llm)
    plan = planner.plan(question="最新匯率多少？", context=None)
    assert plan.needs_external_info is True
    assert plan.route == "search_then_answer"

