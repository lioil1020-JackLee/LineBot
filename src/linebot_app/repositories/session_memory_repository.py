from __future__ import annotations

from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class SessionMemoryRecord:
    session_id: int
    summary: str
    last_message_id: int


class SessionMemoryRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def get(self, session_id: int) -> SessionMemoryRecord | None:
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT session_id, summary, last_message_id
                FROM session_memories
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            return None

        return SessionMemoryRecord(
            session_id=row["session_id"],
            summary=row["summary"],
            last_message_id=row["last_message_id"],
        )

    def upsert(self, *, session_id: int, summary: str, last_message_id: int) -> None:
        with get_connection(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO session_memories (session_id, summary, last_message_id, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    last_message_id = excluded.last_message_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (session_id, summary, last_message_id),
            )
            connection.commit()
