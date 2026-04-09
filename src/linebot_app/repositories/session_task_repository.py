from __future__ import annotations

from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class SessionTaskRecord:
    id: int
    session_id: int
    task_text: str
    status: str


class SessionTaskRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def get_by_session(
        self,
        *,
        session_id: int,
        status: str | None = None,
    ) -> list[SessionTaskRecord]:
        with get_connection(self.db_path) as connection:
            if status is None:
                rows = connection.execute(
                    """
                    SELECT id, session_id, task_text, status
                    FROM session_tasks
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT 100
                    """,
                    (session_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, session_id, task_text, status
                    FROM session_tasks
                    WHERE session_id = ? AND status = ?
                    ORDER BY id DESC
                    LIMIT 100
                    """,
                    (session_id, status),
                ).fetchall()

        return [
            SessionTaskRecord(
                id=row["id"],
                session_id=row["session_id"],
                task_text=row["task_text"],
                status=row["status"],
            )
            for row in rows
        ]

    def add_task(self, *, session_id: int, task_text: str) -> None:
        with get_connection(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO session_tasks (session_id, task_text, status)
                VALUES (?, ?, 'open')
                """,
                (session_id, task_text.strip()),
            )
            connection.commit()

    def update_status(self, *, task_id: int, status: str) -> bool:
        with get_connection(self.db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE session_tasks
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, task_id),
            )
            connection.commit()
        return cursor.rowcount > 0
