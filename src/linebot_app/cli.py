from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .config import get_settings
from .db import init_db
from .repositories.knowledge_repository import KnowledgeRepository
from .repositories.llm_log_repository import LLMLogRepository
from .services.health_service import HealthService
from .services.llm_service import (
    LLMService,
    LLMServiceError,
    LMStudioTimeoutError,
    LMStudioUnavailableError,
)
from .services.rag_service import RAGService


def _build_llm_service() -> LLMService:
    settings = get_settings()
    return LLMService(
        base_url=settings.lm_studio_base_url,
        chat_model=settings.lm_studio_chat_model,
        embed_model=settings.lm_studio_embed_model,
        timeout_seconds=settings.lm_studio_timeout_seconds,
        max_tokens=settings.lm_studio_max_tokens,
        temperature=settings.lm_studio_temperature,
        exe_path=settings.lm_studio_exe_path,
    )


def _build_health_service() -> tuple[HealthService, LLMLogRepository]:
    settings = get_settings()
    llm_service = _build_llm_service()
    log_repo = LLMLogRepository(settings.sqlite_path)
    health_service = HealthService(
        llm_service=llm_service,
        llm_log_repository=log_repo,
        sqlite_path=settings.sqlite_path,
        line_configured=settings.line_ready,
    )
    return health_service, log_repo


def init_db_main() -> None:
    settings = get_settings()
    init_db(settings.sqlite_path)
    print(f"Database initialized at {settings.sqlite_path}")


def ingest_knowledge_main() -> None:
    settings = get_settings()
    rag_service = RAGService(
        llm_service=_build_llm_service(),
        knowledge_repository=KnowledgeRepository(settings.sqlite_path),
        knowledge_dir=settings.knowledge_dir,
        chunk_size=settings.rag_chunk_size,
        chunk_overlap=settings.rag_chunk_overlap,
    )
    result = rag_service.reindex_knowledge()
    print(f"Indexed {result['files']} files and {result['chunks']} chunks")


def health_report_main() -> None:
    health_service, log_repo = _build_health_service()
    detail = health_service.detail()
    print("=== Health Detail ===")
    print(f"status: {detail['status']}")
    print(f"line_configured: {detail['line_configured']}")
    print(f"sqlite: {detail['sqlite']}")
    print(f"lm_studio: {detail['lm_studio']}")

    print("\n=== Recent LLM Logs ===")
    for item in log_repo.get_recent(limit=10):
        print(asdict(item))


def export_metrics_report_main() -> None:
    health_service, log_repo = _build_health_service()
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": health_service.metrics(limit=500),
        "recent_logs": [item.__dict__ for item in log_repo.get_recent(limit=20)],
    }

    output_dir = Path("data/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"metrics_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Report exported: {output_path}")


def cleanup_runtime_main() -> None:
    parser = argparse.ArgumentParser(description="Cleanup runtime data")
    parser.add_argument(
        "--llm-log-days",
        type=int,
        default=7,
        help="Retain llm logs for N days",
    )
    args = parser.parse_args()

    settings = get_settings()
    repo = LLMLogRepository(settings.sqlite_path)
    deleted = repo.delete_older_than_days(days=args.llm_log_days)
    print(f"Deleted {deleted} llm log rows older than {args.llm_log_days} days")


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    question: str
    must_include: list[str]
    must_not_include: list[str]


def _load_eval_cases(path: Path) -> list[EvalCase]:
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


def _score_answer(
    answer: str,
    *,
    must_include: list[str],
    must_not_include: list[str],
) -> tuple[int, list[str]]:
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


def _run_eval(*, eval_path: Path, max_tokens: int, temperature: float) -> int:
    llm_service = _build_llm_service()
    llm_service.max_tokens = max_tokens
    llm_service.temperature = temperature
    cases = _load_eval_cases(eval_path)

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

        score, details = _score_answer(
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


def run_eval_main() -> None:
    parser = argparse.ArgumentParser(
        description="Run offline quality evaluation for LineBot responses"
    )
    parser.add_argument(
        "--eval-path",
        default="data/evals/general_qa.jsonl",
        help="Path to eval jsonl file",
    )
    parser.add_argument("--max-tokens", type=int, default=600)
    parser.add_argument("--temperature", type=float, default=0.4)
    args = parser.parse_args()

    raise SystemExit(
        _run_eval(
            eval_path=Path(args.eval_path),
            max_tokens=args.max_tokens,
            temperature=args.temperature,
        )
    )
