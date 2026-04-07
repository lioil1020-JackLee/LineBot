from __future__ import annotations

from linebot_app.db.sqlite import get_connection


class PromptRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def ensure_default_prompt(self, content: str) -> None:
        with get_connection(self.db_path) as connection:
            existing = connection.execute(
                "SELECT id FROM prompts WHERE is_active = 1 LIMIT 1"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO prompts (name, content, is_active) VALUES (?, ?, 1)",
                    ("default", content),
                )
                connection.commit()

    def get_active_prompt(self) -> str | None:
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT content FROM prompts WHERE is_active = 1 ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return str(row["content"])

    def set_active_prompt(self, content: str) -> str:
        with get_connection(self.db_path) as connection:
            connection.execute("UPDATE prompts SET is_active = 0")
            existing = connection.execute(
                "SELECT id FROM prompts WHERE name = ?",
                ("default",),
            ).fetchone()
            if existing is None:
                connection.execute(
                    (
                        "INSERT INTO prompts (name, content, is_active, updated_at) "
                        "VALUES (?, ?, 1, CURRENT_TIMESTAMP)"
                    ),
                    ("default", content),
                )
            else:
                connection.execute(
                    """
                    UPDATE prompts
                    SET content = ?, is_active = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                    """,
                    (content, "default"),
                )
            connection.commit()
        return content
