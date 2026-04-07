from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from linebot_app.config import get_settings
from linebot_app.services.llm_service import LLMService, LLMServiceError, LMStudioTimeoutError, LMStudioUnavailableError


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    must_include: list[str]
    must_not_include: list[str]


def load_eval_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            cases.append(
                EvalCase(
                    case_id=str(payload["id"]),
                    question=str(payload["question"]),
                    must_include=[str(item) for item in payload.get("must_include", [])],
                    must_not_include=[str(item) for item in payload.get("must_not_include", [])],
                )
            )
    return cases


def score_answer(answer: str, *, must_include: list[str], must_not_include: list[str]) -> tuple[int, list[str]]:
    text = answer.lower()
    details: list[str] = []
    score = 100

    for keyword in must_include:
        if keyword.lower() not in text:
            score -= 15
            details.append(f"missing:{keyword}")

    for keyword in must_not_include:
        if keyword.lower() in text:
            score -= 20
            details.append(f"forbidden:{keyword}")

    score = max(0, score)
    return score, details


def run_eval(*, eval_path: Path, max_tokens: int, temperature: float) -> int:
    settings = get_settings()
    llm_service = LLMService(
        base_url=settings.lm_studio_base_url,
        chat_model=settings.lm_studio_chat_model,
        embed_model=settings.lm_studio_embed_model,
        timeout_seconds=settings.lm_studio_timeout_seconds,
        max_tokens=max_tokens,
        temperature=temperature,
        exe_path=settings.lm_studio_exe_path,
    )
    cases = load_eval_cases(eval_path)

    if not cases:
        print("No eval cases found.")
        return 1

    print(f"Running {len(cases)} eval cases with model={llm_service.chat_model}")
    passed = 0
    total_score = 0

    for case in cases:
        try:
            reply = llm_service.generate_reply(
                system_prompt=(
                    "你是 LINE 萬事通助理，請以繁體中文回覆。"
                    "回覆要清楚、可執行，避免誇大保證。"
                ),
                conversation=[{"role": "user", "content": case.question}],
            )
            answer = reply.text
        except (LMStudioUnavailableError, LMStudioTimeoutError, LLMServiceError) as exc:
            print(f"[{case.case_id}] ERROR: {exc}")
            continue

        score, details = score_answer(
            answer,
            must_include=case.must_include,
            must_not_include=case.must_not_include,
        )
        total_score += score
        ok = score >= 70
        if ok:
            passed += 1

        print(f"[{case.case_id}] score={score} pass={ok}")
        if details:
            print(f"  details: {', '.join(details)}")

    avg = round(total_score / len(cases), 2)
    pass_rate = round((passed / len(cases)) * 100, 2)
    print("\n=== Eval Summary ===")
    print(f"cases={len(cases)}")
    print(f"passed={passed}")
    print(f"pass_rate={pass_rate}%")
    print(f"avg_score={avg}")

    return 0 if pass_rate >= 80 else 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Run offline quality evaluation for LineBot responses")
    parser.add_argument(
        "--eval-path",
        default="data/evals/general_qa.jsonl",
        help="Path to eval jsonl file",
    )
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument("--temperature", type=float, default=0.4)
    args = parser.parse_args()

    raise SystemExit(
        run_eval(
            eval_path=Path(args.eval_path),
            max_tokens=args.max_tokens,
            temperature=args.temperature,
        )
    )


if __name__ == "__main__":
    main()
