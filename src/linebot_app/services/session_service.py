from __future__ import annotations

from linebot_app.repositories.message_repository import MessageRecord, MessageRepository
from linebot_app.repositories.session_repository import SessionRecord, SessionRepository


class SessionService:
    def __init__(
        self,
        session_repository: SessionRepository,
        message_repository: MessageRepository,
        max_turns: int,
    ) -> None:
        self.session_repository = session_repository
        self.message_repository = message_repository
        self.max_turns = max_turns

    def get_or_create_session(self, line_user_id: str) -> SessionRecord:
        session = self.session_repository.get_by_line_user_id(line_user_id)
        if session is not None:
            return session
        return self.session_repository.create(line_user_id)

    def get_recent_context(self, session_id: int) -> list[MessageRecord]:
        return self.message_repository.get_recent_messages(
            session_id=session_id,
            limit=self.max_turns * 2,
        )

    def mark_activity(self, session_id: int) -> None:
        self.session_repository.touch(session_id)