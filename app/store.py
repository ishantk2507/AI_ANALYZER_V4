"""Durable storage for users, chats and user memory.

Everything the agent needs to answer a question is derived from the data tree;
everything in *here* is what makes it feel like the same assistant tomorrow --
which chats exist, what was asked in them, and the handful of standing facts a
user does not want to retype ("I mostly care about Karnataka").

SQLite rather than JSON files, for one reason: on a shared server several
Streamlit sessions write at once, and a directory of JSON files has no story for
that. WAL mode lets readers proceed during a write, which is the whole access
pattern here -- one writer, several readers.

A connection is opened per operation rather than pooled. Streamlit runs each
session's script in its own thread and re-runs it on every interaction, so a
shared connection would need `check_same_thread=False` plus a lock; opening one
costs microseconds against a local file and sidesteps the question.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Bumped when the schema changes; `_migrate` walks from whatever is on disk.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_by_user ON chats(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY,
    chat_id     INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL DEFAULT '',
    dataset_key TEXT NOT NULL DEFAULT '',
    plan        TEXT NOT NULL DEFAULT '',
    filters     TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_by_chat ON turns(chat_id, position);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(user_id, key)
);
"""


def _now() -> str:
    """A sortable UTC timestamp.

    Microseconds, and always six digits of them: these strings are compared
    lexicographically by `ORDER BY`, so the width has to be fixed. At second
    resolution several chats created in the same second tied, and the sidebar
    then listed them in whatever order SQLite happened to return.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class ChatSummary:
    """A chat as the sidebar needs it -- without loading its turns."""

    id: int
    title: str
    turn_count: int
    updated_at: str

    @property
    def updated_display(self) -> str:
        """`updated_at` as something readable, falling back to the raw string."""
        try:
            stamp = datetime.fromisoformat(self.updated_at)
        except ValueError:  # pragma: no cover - only if the row was hand-edited
            return self.updated_at
        return stamp.astimezone().strftime("%d %b, %H:%M")


@dataclass(frozen=True)
class Memory:
    """One standing fact about a user."""

    key: str
    value: str
    updated_at: str = ""


@dataclass
class StoredTurn:
    """A persisted turn, in the shape `agent.memory.Turn` is built from."""

    question: str
    answer: str = ""
    dataset_key: str = ""
    plan: str = ""
    filters: List[Tuple[str, str, str]] = field(default_factory=list)


class ChatStore:
    """The SQLite-backed store. Cheap to construct; safe to share across threads."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else settings.store_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Guards schema creation only. Ordinary reads and writes rely on SQLite's
        # own locking, which is what WAL mode is configured for below.
        self._init_lock = threading.Lock()
        self._ready = False
        self._ensure_schema()

    # -- connection ---------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        # Off by default in SQLite, and the cascade deletes below depend on it.
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:  # commits on success, rolls back on exception
                yield connection
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        with self._init_lock:
            if self._ready:
                return
            with self._connect() as connection:
                # WAL is a property of the database file, not the connection, so
                # this only has to succeed once. It fails on network filesystems,
                # where the default journal still works -- just with less
                # concurrency.
                try:
                    connection.execute("PRAGMA journal_mode = WAL")
                except sqlite3.DatabaseError:  # pragma: no cover - exotic filesystems
                    log.warning("Could not enable WAL on %s; using the default journal",
                                self.path)
                connection.executescript(_SCHEMA)
                self._migrate(connection)
            self._ready = True

    def _migrate(self, connection: sqlite3.Connection) -> None:
        """Bring an older database up to `SCHEMA_VERSION`.

        `user_version` starts at 0 on a database SQLite has just created, which
        is indistinguishable from one written before versioning existed -- and
        both want exactly the same thing, since `_SCHEMA` is `IF NOT EXISTS`
        throughout. So version 0 simply becomes version 1.
        """
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current == SCHEMA_VERSION:
            return
        if current > SCHEMA_VERSION:
            log.warning(
                "%s was written by a newer version of this app (schema %d > %d); "
                "continuing, but expect missing columns", self.path, current, SCHEMA_VERSION
            )
            return
        # Future migrations chain from here: `if current < 2: ...`
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        log.info("Store schema at version %d (%s)", SCHEMA_VERSION, self.path)

    # -- users --------------------------------------------------------------

    def user_id(self, username: str) -> int:
        """The id for `username`, creating the user on first sight."""
        name = (username or settings.default_user).strip() or settings.default_user
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ?", (name,)
            ).fetchone()
            if row is not None:
                return int(row["id"])
            cursor = connection.execute(
                "INSERT INTO users (username, created_at) VALUES (?, ?)", (name, _now())
            )
            log.info("Created user '%s'", name)
            return int(cursor.lastrowid)

    # -- chats --------------------------------------------------------------

    def create_chat(self, user_id: int, title: str = "New chat") -> int:
        stamp = _now()
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO chats (user_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, title.strip() or "New chat", stamp, stamp),
            )
            return int(cursor.lastrowid)

    def list_chats(self, user_id: int, limit: Optional[int] = None) -> List[ChatSummary]:
        """Newest first. Turn counts come from one grouped query, not N+1."""
        capped = limit if limit is not None else settings.max_chats_listed
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chats.id, chats.title, chats.updated_at,
                       COUNT(turns.id) AS turn_count
                  FROM chats
                  LEFT JOIN turns ON turns.chat_id = chats.id
                 WHERE chats.user_id = ?
                 GROUP BY chats.id
                 ORDER BY chats.updated_at DESC, chats.id DESC
                 LIMIT ?
                """,
                (user_id, capped),
            ).fetchall()
        return [
            ChatSummary(
                id=int(row["id"]),
                title=row["title"],
                turn_count=int(row["turn_count"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def rename_chat(self, chat_id: int, title: str) -> None:
        cleaned = title.strip()
        if not cleaned:
            return
        with self._connect() as connection:
            connection.execute(
                "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
                (cleaned[:120], _now(), chat_id),
            )

    def delete_chat(self, chat_id: int) -> None:
        """Removes the chat and, by cascade, its turns."""
        with self._connect() as connection:
            connection.execute("DELETE FROM chats WHERE id = ?", (chat_id,))

    def chat_title(self, chat_id: int) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT title FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
        return row["title"] if row else ""

    def chat_exists(self, chat_id: int, user_id: Optional[int] = None) -> bool:
        """Whether the chat is there -- and, when asked, whether it is this user's.

        The `user_id` check is what stops a stale or guessed chat id in a URL
        from reading someone else's history on a shared server.
        """
        query = "SELECT 1 FROM chats WHERE id = ?"
        params: tuple = (chat_id,)
        if user_id is not None:
            query += " AND user_id = ?"
            params += (user_id,)
        with self._connect() as connection:
            return connection.execute(query, params).fetchone() is not None

    # -- turns --------------------------------------------------------------

    def add_turn(self, chat_id: int, turn: StoredTurn) -> None:
        """Append a turn and bump the chat's `updated_at`, in one transaction."""
        stamp = _now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS next FROM turns WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            connection.execute(
                "INSERT INTO turns (chat_id, position, question, answer, dataset_key, "
                "plan, filters, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_id,
                    int(row["next"]),
                    turn.question,
                    turn.answer,
                    turn.dataset_key,
                    turn.plan,
                    json.dumps([list(item) for item in turn.filters]),
                    stamp,
                ),
            )
            connection.execute(
                "UPDATE chats SET updated_at = ? WHERE id = ?", (stamp, chat_id)
            )

    def load_turns(self, chat_id: int) -> List[StoredTurn]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT question, answer, dataset_key, plan, filters FROM turns "
                "WHERE chat_id = ? ORDER BY position",
                (chat_id,),
            ).fetchall()

        turns: List[StoredTurn] = []
        for row in rows:
            try:
                raw = json.loads(row["filters"])
                filters = [tuple(item) for item in raw]
            except (ValueError, TypeError):  # pragma: no cover - hand-edited row
                filters = []
            turns.append(
                StoredTurn(
                    question=row["question"],
                    answer=row["answer"],
                    dataset_key=row["dataset_key"],
                    plan=row["plan"],
                    filters=filters,
                )
            )
        return turns

    # -- the rolling summary ------------------------------------------------

    def save_summary(self, chat_id: int, summary: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE chats SET summary = ? WHERE id = ?", (summary, chat_id)
            )

    def load_summary(self, chat_id: int) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT summary FROM chats WHERE id = ?", (chat_id,)
            ).fetchone()
        return row["summary"] if row else ""

    def trim_turns(self, chat_id: int, keep_last: int) -> None:
        """Drop all but the last `keep_last` turns.

        Mirrors what `Conversation.maybe_summarise` does in memory: once older
        turns have been folded into the summary, keeping them on disk would mean
        a resumed chat replays history the summary already covers.
        """
        if keep_last < 0:
            return
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM turns WHERE chat_id = ? AND id NOT IN ("
                "  SELECT id FROM turns WHERE chat_id = ? ORDER BY position DESC LIMIT ?"
                ")",
                (chat_id, chat_id, keep_last),
            )

    # -- user memory --------------------------------------------------------

    def set_memory(self, user_id: int, key: str, value: str) -> None:
        """Add or replace one standing fact."""
        cleaned_key = key.strip()[:60]
        cleaned_value = value.strip()[:400]
        if not cleaned_key or not cleaned_value:
            return
        stamp = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO memories (user_id, key, value, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (user_id, cleaned_key, cleaned_value, stamp, stamp),
            )

    def list_memories(self, user_id: int, limit: Optional[int] = None) -> List[Memory]:
        """Most recently updated first, so the cap drops the stalest facts."""
        capped = limit if limit is not None else settings.max_user_memories
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT key, value, updated_at FROM memories WHERE user_id = ? "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (user_id, capped),
            ).fetchall()
        return [
            Memory(key=row["key"], value=row["value"], updated_at=row["updated_at"])
            for row in rows
        ]

    def delete_memory(self, user_id: int, key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM memories WHERE user_id = ? AND key = ?", (user_id, key)
            )

    def clear_memories(self, user_id: int) -> int:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM memories WHERE user_id = ?", (user_id,))
            return cursor.rowcount

    # -- housekeeping -------------------------------------------------------

    def stats(self) -> Dict[str, int]:
        with self._connect() as connection:
            counts = {
                name: int(
                    connection.execute(f"SELECT COUNT(*) AS n FROM {name}").fetchone()["n"]
                )
                for name in ("users", "chats", "turns", "memories")
            }
        counts["bytes"] = self.path.stat().st_size if self.path.is_file() else 0
        return counts


_store: Optional[ChatStore] = None
_store_lock = threading.Lock()


def get_store() -> Optional[ChatStore]:
    """The process-wide store, or None when persistence is switched off.

    Returning None rather than a no-op store is deliberate: callers have to say
    what they do without persistence, and every one of them can already run
    without it -- that is the single-shot `ask` path.
    """
    global _store
    if not settings.persist_chats:
        return None
    with _store_lock:
        if _store is None:
            _store = ChatStore()
            log.info("Chat store ready at %s", _store.path)
        return _store


def reset_store() -> None:
    """Drop the cached store. Used by tests, which point at a temp file."""
    global _store
    with _store_lock:
        _store = None
