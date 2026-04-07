from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import SCHEMA_STATEMENTS


def _ensure_parent_dir(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def get_connection(db_path: str) -> sqlite3.Connection:
    _ensure_parent_dir(db_path)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def init_db(db_path: str) -> None:
    with get_connection(db_path) as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        connection.commit()


def check_db(db_path: str) -> bool:
    try:
        with get_connection(db_path) as connection:
            connection.execute("SELECT 1")
        return True
    except sqlite3.Error:
        return False