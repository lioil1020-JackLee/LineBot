from __future__ import annotations

import json
from dataclasses import dataclass

from linebot_app.db.sqlite import get_connection


@dataclass(frozen=True)
class KnowledgeChunkRecord:
    id: int
    source_path: str
    chunk_index: int
    content: str
    embedding: list[float]


class KnowledgeRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def replace_chunks_for_source(
        self,
        *,
        source_path: str,
        chunks: list[tuple[int, str, list[float]]],
    ) -> None:
        with get_connection(self.db_path) as connection:
            connection.execute(
                "DELETE FROM knowledge_chunks WHERE source_path = ?",
                (source_path,),
            )
            for chunk_index, content, embedding in chunks:
                connection.execute(
                    """
                    INSERT INTO knowledge_chunks (source_path, chunk_index, content, embedding_json)
                    VALUES (?, ?, ?, ?)
                    """,
                    (source_path, chunk_index, content, json.dumps(embedding)),
                )
            connection.commit()

    def get_all_chunks(self) -> list[KnowledgeChunkRecord]:
        with get_connection(self.db_path) as connection:
            rows = connection.execute(
                "SELECT id, source_path, chunk_index, content, embedding_json FROM knowledge_chunks"
            ).fetchall()
        return [
            KnowledgeChunkRecord(
                id=row["id"],
                source_path=row["source_path"],
                chunk_index=row["chunk_index"],
                content=row["content"],
                embedding=json.loads(row["embedding_json"]),
            )
            for row in rows
        ]

    def count_chunks(self) -> int:
        with get_connection(self.db_path) as connection:
            row = connection.execute("SELECT COUNT(*) AS c FROM knowledge_chunks").fetchone()
        return int(row["c"])
