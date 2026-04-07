from __future__ import annotations

from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class MessageRecord:
    id: int
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
                SELECT id, role, content
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

        items = [
            MessageRecord(id=row["id"], role=row["role"], content=row["content"])
            for row in rows
        ]
        items.reverse()
        return items

    def get_messages_after_id(
        self,
        *,
        session_id: int,
        after_id: int,
        limit: int,
    ) -> list[MessageRecord]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, role, content
                FROM messages
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, after_id, limit),
            ).fetchall()

        return [
            MessageRecord(id=row["id"], role=row["role"], content=row["content"])
            for row in rows
        ]

    def get_latest_message_id(self, *, session_id: int) -> int:
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT COALESCE(MAX(id), 0) AS latest_id
                FROM messages
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()

        return int(row["latest_id"]) if row is not None else 0