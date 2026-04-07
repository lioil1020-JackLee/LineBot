from __future__ import annotations

from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class LLMLogRecord:
    request_id: str
    session_id: int | None
    model_name: str | None
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    status: str
    error_message: str | None
    created_at: str


class LLMLogRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def add_log(
        self,
        *,
        request_id: str,
        session_id: int | None,
        model_name: str | None,
        latency_ms: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with get_connection(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO llm_logs (
                    request_id,
                    session_id,
                    model_name,
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    status,
                    error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    session_id,
                    model_name,
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    status,
                    error_message,
                ),
            )
            connection.commit()

    def get_recent(self, *, limit: int = 10) -> list[LLMLogRecord]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT request_id, session_id, model_name, latency_ms,
                       prompt_tokens, completion_tokens, total_tokens,
                       status, error_message, created_at
                FROM llm_logs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            LLMLogRecord(
                request_id=row["request_id"],
                session_id=row["session_id"],
                model_name=row["model_name"],
                latency_ms=row["latency_ms"],
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                total_tokens=row["total_tokens"],
                status=row["status"],
                error_message=row["error_message"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def delete_older_than_days(self, *, days: int) -> int:
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                "DELETE FROM llm_logs WHERE created_at < datetime('now', ?)",
                (f"-{days} day",),
            )
            connection.commit()
        return cursor.rowcount
