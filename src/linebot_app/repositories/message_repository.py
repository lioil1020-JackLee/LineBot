from __future__ import annotations

from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class MessageRecord:
    role: str
    content: str


class MessageRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def add_message(
        self,
        *,
        session_id: int,
        role: str,
        content: str,
        source: str = "line",
        token_count: int | None = None,
    ) -> None:
        with get_connection(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO messages (session_id, role, content, token_count, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, role, content, token_count, source),
            )
            connection.commit()

    def get_recent_messages(self, *, session_id: int, limit: int) -> list[MessageRecord]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT role, content
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

        items = [MessageRecord(role=row["role"], content=row["content"]) for row in rows]
        items.reverse()
        return items