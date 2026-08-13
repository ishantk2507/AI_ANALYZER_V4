"""One user, one chat, one analyst.

The glue between the store and the two front ends. It exists so `ui.py` and
`cli.py` do not each grow their own answer to "which chat is this, whose is it,
and what does the agent already know about them" -- and so that logic is
testable without a browser or a terminal.

Deliberately framework-free: no Streamlit, no typer. The caller passes in
request headers if it has them.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from app.agent.memory import Conversation
from app.config import settings
from app.logging_setup import get_logger
from app.store import ChatStore, ChatSummary, Memory, get_store

log = get_logger(__name__)

#: A new chat is called this until its first question retitles it.
UNTITLED = "New chat"


def resolve_username(headers: Optional[Dict[str, str]] = None) -> str:
    """Who this session belongs to.

    With `auth_header` unset the app is single-user and everyone is
    `default_user`. With it set, the named header is trusted -- which is only
    safe behind a proxy that strips it from inbound requests and sets it from a
    real authentication result. See docs/DEPLOYMENT.md.
    """
    header = settings.auth_header.strip()
    if not header or not headers:
        return settings.default_user

    # Header names are case-insensitive; the mapping we are handed may not be.
    wanted = header.lower()
    for name, value in headers.items():
        if name.lower() == wanted:
            cleaned = (value or "").strip()
            if cleaned:
                # A username reaches a filesystem path in no case here, but it is
                # displayed and stored, so keep it boring.
                return re.sub(r"[^\w.@ -]", "", cleaned)[:80] or settings.default_user
    log.debug("auth_header '%s' is configured but absent from the request", header)
    return settings.default_user


def title_from_question(question: str) -> str:
    """A chat title derived from its first question.

    Cut at a word boundary rather than mid-word, because "What are the top mak"
    reads as a bug. No model involved: titling is not worth an inference, and a
    deterministic title is one the user can predict.
    """
    cleaned = " ".join((question or "").split())
    if not cleaned:
        return UNTITLED
    if len(cleaned) <= 48:
        return cleaned
    clipped = cleaned[:48]
    spaced = clipped.rsplit(" ", 1)[0]
    return (spaced if len(spaced) >= 24 else clipped) + "..."


class UserSession:
    """A user's active chat, with the conversation wired to persistence.

    Construct one per front-end session. It is cheap: the store is process-wide
    and the conversation is only loaded when a chat is opened.
    """

    def __init__(
        self,
        username: Optional[str] = None,
        store: Optional[ChatStore] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.store = store if store is not None else get_store()
        self.username = username or resolve_username(headers)
        self.user_id: Optional[int] = (
            self.store.user_id(self.username) if self.store else None
        )
        self.chat_id: Optional[int] = None
        self.conversation = Conversation(memories=self._memory_pairs())

    # -- persistence state --------------------------------------------------

    @property
    def persistent(self) -> bool:
        return self.store is not None and self.user_id is not None

    def _memory_pairs(self) -> List[Tuple[str, str]]:
        if not (self.store and self.user_id is not None):
            return []
        return [(item.key, item.value) for item in self.store.list_memories(self.user_id)]

    # -- chats --------------------------------------------------------------

    def chats(self) -> List[ChatSummary]:
        if not self.persistent:
            return []
        return self.store.list_chats(self.user_id)

    def new_chat(self, title: str = UNTITLED) -> Optional[int]:
        """Start a fresh chat and make it active. Returns its id, or None when
        persistence is off -- in which case the conversation is simply reset."""
        if not self.persistent:
            self.conversation = Conversation(memories=self._memory_pairs())
            self.chat_id = None
            return None

        self.chat_id = self.store.create_chat(self.user_id, title)
        self.conversation = Conversation(memories=self._memory_pairs())
        self.conversation.bind(self.store, self.chat_id)
        return self.chat_id

    def open_chat(self, chat_id: int) -> bool:
        """Make an existing chat active, replaying its turns into memory.

        Returns False when the chat is gone or belongs to someone else, which is
        the case a stale browser tab or a hand-edited URL produces.
        """
        if not self.persistent:
            return False
        if not self.store.chat_exists(chat_id, user_id=self.user_id):
            log.info("Chat %s is not available to '%s'", chat_id, self.username)
            return False

        self.chat_id = chat_id
        self.conversation = Conversation.load(
            self.store, chat_id, memories=self._memory_pairs()
        )
        return True

    def ensure_chat(self) -> Optional[int]:
        """The active chat, opening the most recent or starting one if need be.

        This is what a front end calls before the first question: reopening the
        app should land you back where you left off, not in a blank chat with
        yesterday's still in the sidebar.
        """
        if self.chat_id is not None:
            return self.chat_id
        if not self.persistent:
            return None

        existing = self.store.list_chats(self.user_id, limit=1)
        if existing and self.open_chat(existing[0].id):
            return self.chat_id
        return self.new_chat()

    def rename_chat(self, title: str, chat_id: Optional[int] = None) -> None:
        target = chat_id if chat_id is not None else self.chat_id
        if self.persistent and target is not None:
            self.store.rename_chat(target, title)

    def delete_chat(self, chat_id: int) -> None:
        """Delete a chat. Deleting the active one leaves no chat open, so the
        caller decides what to show next rather than being handed a surprise."""
        if not self.persistent:
            return
        self.store.delete_chat(chat_id)
        if chat_id == self.chat_id:
            self.chat_id = None
            self.conversation = Conversation(memories=self._memory_pairs())

    def note_question(self, question: str) -> None:
        """Retitle an untitled chat from its first question.

        Called before the answer exists, so a question that goes on to fail
        still names the chat it happened in.
        """
        if not (self.persistent and self.chat_id is not None):
            return
        if self.store.chat_title(self.chat_id) != UNTITLED:
            return
        self.store.rename_chat(self.chat_id, title_from_question(question))

    # -- user memory --------------------------------------------------------

    def memories(self) -> List[Memory]:
        if not self.persistent:
            return []
        return self.store.list_memories(self.user_id)

    def remember(self, key: str, value: str) -> None:
        """Add or replace a standing fact, and make it live immediately.

        The conversation holds its own copy so `context_block` does not hit the
        database on every prompt, so it is refreshed here rather than left to
        go stale until the next chat switch.
        """
        if not self.persistent:
            return
        self.store.set_memory(self.user_id, key, value)
        self.conversation.memories = self._memory_pairs()

    def forget(self, key: str) -> None:
        if not self.persistent:
            return
        self.store.delete_memory(self.user_id, key)
        self.conversation.memories = self._memory_pairs()

    def forget_all(self) -> int:
        if not self.persistent:
            return 0
        removed = self.store.clear_memories(self.user_id)
        self.conversation.memories = []
        return removed
