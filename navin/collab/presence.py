# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""In-memory presence roster keyed by chat_id for hybrid collab rooms."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class PresenceMember:
    """One connected collaborator in a chat room."""

    member_id: str
    display_name: str
    role: str  # admin | member | viewer | host
    connection_id: str = ""

    def payload(self) -> dict[str, str]:
        return {
            "member_id": self.member_id,
            "display_name": self.display_name,
            "role": self.role,
        }


@dataclass
class PresenceTracker:
    """Track connected members per chat_id (connection -> member)."""

    # chat_id -> connection_id -> PresenceMember
    _by_chat: dict[str, dict[str, PresenceMember]] = field(default_factory=dict)
    # connection_id -> (chat_id, member)
    _by_conn: dict[str, tuple[str, PresenceMember]] = field(default_factory=dict)

    def join(
        self,
        chat_id: str,
        *,
        connection_id: str,
        member_id: str,
        display_name: str,
        role: str,
    ) -> PresenceMember:
        """Register *connection_id* on *chat_id*; replaces prior chat for that conn."""
        self.leave_connection(connection_id)
        member = PresenceMember(
            member_id=member_id,
            display_name=display_name.strip() or member_id,
            role=role or "member",
            connection_id=connection_id,
        )
        self._by_chat.setdefault(chat_id, {})[connection_id] = member
        self._by_conn[connection_id] = (chat_id, member)
        return member

    def leave_connection(self, connection_id: str) -> tuple[str, PresenceMember] | None:
        """Remove a connection from whatever chat it was in."""
        entry = self._by_conn.pop(connection_id, None)
        if entry is None:
            return None
        chat_id, member = entry
        room = self._by_chat.get(chat_id)
        if room is not None:
            room.pop(connection_id, None)
            if not room:
                self._by_chat.pop(chat_id, None)
        return chat_id, member

    def members(self, chat_id: str) -> list[PresenceMember]:
        room = self._by_chat.get(chat_id) or {}
        return list(room.values())

    def sync_payload(self, chat_id: str) -> dict[str, Any]:
        return {
            "chat_id": chat_id,
            "members": [m.payload() for m in self.members(chat_id)],
        }

    def clear(self) -> None:
        self._by_chat.clear()
        self._by_conn.clear()


def member_as_dict(member: PresenceMember) -> dict[str, str]:
    data = asdict(member)
    data.pop("connection_id", None)
    return data
