from __future__ import annotations

from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class SessionRecord:
    id: int
    line_user_id: str
    status: str


class SessionRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def get_by_line_user_id(self, line_user_id: str) -> SessionRecord | None:
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT id, line_user_id, status FROM sessions WHERE line_user_id = ?",
                (line_user_id,),
            ).fetchone()

        if row is None:
            return None
        return SessionRecord(id=row["id"], line_user_id=row["line_user_id"], status=row["status"])

    def create(self, line_user_id: str) -> SessionRecord:
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                "INSERT INTO sessions (line_user_id, status) VALUES (?, 'active')",
                (line_user_id,),
            )
            connection.commit()

        return SessionRecord(id=cursor.lastrowid, line_user_id=line_user_id, status="active")

    def touch(self, session_id: int) -> None:
        with get_connection(self.db_path) as connection:
            connection.execute(
                """
                UPDATE sessions
                SET updated_at = CURRENT_TIMESTAMP,
                    last_message_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (session_id,),
            )
            connection.commit()