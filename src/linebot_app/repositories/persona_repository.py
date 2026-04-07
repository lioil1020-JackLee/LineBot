from __future__ import annotations

from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class PersonaPresetRecord:
    name: str
    prompt: str
    is_builtin: bool
    is_active: bool
    created_at: str
    updated_at: str


class PersonaRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def ensure_builtin_presets(self, presets: dict[str, str]) -> None:
        with get_connection(self.db_path) as connection:
            for name, prompt in presets.items():
                row = connection.execute(
                    "SELECT id FROM persona_presets WHERE name = ?",
                    (name,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        (
                            "INSERT INTO persona_presets "
                            "(name, prompt, is_builtin, is_active, updated_at) "
                            "VALUES (?, ?, 1, 0, CURRENT_TIMESTAMP)"
                        ),
                        (name, prompt),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE persona_presets
                        SET prompt = ?, is_builtin = 1, updated_at = CURRENT_TIMESTAMP
                        WHERE name = ?
                        """,
                        (prompt, name),
                    )

            active = connection.execute(
                "SELECT name FROM persona_presets WHERE is_active = 1 LIMIT 1"
            ).fetchone()
            if active is None and "default" in presets:
                connection.execute("UPDATE persona_presets SET is_active = 0")
                connection.execute(
                    (
                        "UPDATE persona_presets "
                        "SET is_active = 1, updated_at = CURRENT_TIMESTAMP "
                        "WHERE name = ?"
                    ),
                    ("default",),
                )
            connection.commit()

    def list_presets(self) -> list[PersonaPresetRecord]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT name, prompt, is_builtin, is_active, created_at, updated_at
                FROM persona_presets
                ORDER BY is_builtin DESC, name ASC
                """
            ).fetchall()
        return [
            PersonaPresetRecord(
                name=str(row["name"]),
                prompt=str(row["prompt"]),
                is_builtin=bool(row["is_builtin"]),
                is_active=bool(row["is_active"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]

    def get_active(self) -> PersonaPresetRecord | None:
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT name, prompt, is_builtin, is_active, created_at, updated_at
                FROM persona_presets
                WHERE is_active = 1
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return None
        return PersonaPresetRecord(
            name=str(row["name"]),
            prompt=str(row["prompt"]),
            is_builtin=bool(row["is_builtin"]),
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def set_active(self, name: str) -> PersonaPresetRecord | None:
        target = name.strip().lower()
        if not target:
            return None
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT name FROM persona_presets WHERE name = ?",
                (target,),
            ).fetchone()
            if row is None:
                return None
            connection.execute("UPDATE persona_presets SET is_active = 0")
            connection.execute(
                (
                    "UPDATE persona_presets "
                    "SET is_active = 1, updated_at = CURRENT_TIMESTAMP "
                    "WHERE name = ?"
                ),
                (target,),
            )
            connection.commit()
        return self.get_active()

    def upsert_custom(self, *, name: str, prompt: str, set_active: bool) -> PersonaPresetRecord:
        normalized = name.strip().lower()
        if not normalized:
            normalized = "custom"
        with get_connection(self.db_path) as connection:
            existing = connection.execute(
                "SELECT id FROM persona_presets WHERE name = ?",
                (normalized,),
            ).fetchone()
            if set_active:
                connection.execute("UPDATE persona_presets SET is_active = 0")

            if existing is None:
                connection.execute(
                    (
                        "INSERT INTO persona_presets "
                        "(name, prompt, is_builtin, is_active, updated_at) "
                        "VALUES (?, ?, 0, ?, CURRENT_TIMESTAMP)"
                    ),
                    (normalized, prompt.strip(), 1 if set_active else 0),
                )
            else:
                connection.execute(
                    """
                    UPDATE persona_presets
                    SET prompt = ?, is_builtin = 0, is_active = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE name = ?
                    """,
                    (prompt.strip(), 1 if set_active else 0, normalized),
                )
            connection.commit()

        record = self.get_active() if set_active else None
        if record is not None and record.name == normalized:
            return record

        with get_connection(self.db_path) as connection:
            row = connection.execute(
                """
                SELECT name, prompt, is_builtin, is_active, created_at, updated_at
                FROM persona_presets
                WHERE name = ?
                LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        return PersonaPresetRecord(
            name=str(row["name"]),
            prompt=str(row["prompt"]),
            is_builtin=bool(row["is_builtin"]),
            is_active=bool(row["is_active"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def delete_custom(self, name: str) -> bool:
        target = name.strip().lower()
        if not target:
            return False
        with get_connection(self.db_path) as connection:
            row = connection.execute(
                "SELECT is_builtin, is_active FROM persona_presets WHERE name = ?",
                (target,),
            ).fetchone()
            if row is None or bool(row["is_builtin"]):
                return False

            was_active = bool(row["is_active"])
            connection.execute("DELETE FROM persona_presets WHERE name = ?", (target,))
            if was_active:
                connection.execute("UPDATE persona_presets SET is_active = 0")
                connection.execute(
                    (
                        "UPDATE persona_presets "
                        "SET is_active = 1, updated_at = CURRENT_TIMESTAMP "
                        "WHERE name = ?"
                    ),
                    ("default",),
                )
            connection.commit()
            return True
