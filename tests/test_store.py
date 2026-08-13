"""The durable store: chats, turns and user memory.

Every test runs against its own SQLite file (see conftest), so these are fast
and need no model, no data tree and no cleanup.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.store import ChatStore, StoredTurn, get_store, reset_store

# --- users -------------------------------------------------------------------


def test_user_is_created_once_and_reused(store):
    first = store.user_id("ishan")
    assert store.user_id("ishan") == first
    assert store.user_id("someone-else") != first


def test_blank_username_falls_back_to_the_default(store, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "default_user", "local")
    assert store.user_id("   ") == store.user_id("local")


# --- chats -------------------------------------------------------------------


def test_chats_list_newest_first_with_turn_counts(user):
    store, user_id = user
    older = store.create_chat(user_id, "Older")
    newer = store.create_chat(user_id, "Newer")
    store.add_turn(newer, StoredTurn(question="q1"))
    store.add_turn(newer, StoredTurn(question="q2"))

    listed = store.list_chats(user_id)
    assert [c.id for c in listed] == [newer, older]
    assert listed[0].turn_count == 2
    assert listed[1].turn_count == 0


def test_a_chat_is_only_visible_to_its_owner(store):
    mine = store.user_id("me")
    theirs = store.user_id("them")
    chat = store.create_chat(mine, "Private")

    assert store.chat_exists(chat, user_id=mine)
    # The check that stops a guessed id in a URL reading someone else's history.
    assert not store.chat_exists(chat, user_id=theirs)
    assert store.list_chats(theirs) == []


def test_deleting_a_chat_takes_its_turns_with_it(user):
    store, user_id = user
    chat = store.create_chat(user_id, "Doomed")
    store.add_turn(chat, StoredTurn(question="q"))
    assert store.stats()["turns"] == 1

    store.delete_chat(chat)
    # Cascade, which only happens because PRAGMA foreign_keys is on per connection.
    assert store.stats()["turns"] == 0
    assert not store.chat_exists(chat)


def test_renaming_ignores_blank_titles(user):
    store, user_id = user
    chat = store.create_chat(user_id, "Real title")
    store.rename_chat(chat, "   ")
    assert store.chat_title(chat) == "Real title"


# --- turns -------------------------------------------------------------------


def test_turns_round_trip_including_filters(user):
    store, user_id = user
    chat = store.create_chat(user_id)
    store.add_turn(
        chat,
        StoredTurn(
            question="top makers in Kerala",
            answer="HERO leads.",
            dataset_key="Two Wheeler/MAKER",
            plan="rank MAKER by TOTAL",
            filters=[("STATE", "==", "Kerala")],
        ),
    )

    (restored,) = store.load_turns(chat)
    assert restored.question == "top makers in Kerala"
    assert restored.dataset_key == "Two Wheeler/MAKER"
    # Tuples, not lists: the orchestrator's filter inheritance unpacks triples.
    assert restored.filters == [("STATE", "==", "Kerala")]


def test_turns_keep_their_order(user):
    store, user_id = user
    chat = store.create_chat(user_id)
    for question in ("first", "second", "third"):
        store.add_turn(chat, StoredTurn(question=question))

    assert [t.question for t in store.load_turns(chat)] == ["first", "second", "third"]


def test_adding_a_turn_bumps_the_chat_up_the_list(user):
    store, user_id = user
    first = store.create_chat(user_id, "First")
    second = store.create_chat(user_id, "Second")
    assert [c.id for c in store.list_chats(user_id)] == [second, first]

    store.add_turn(first, StoredTurn(question="hello"))
    assert [c.id for c in store.list_chats(user_id)] == [first, second]


def test_trim_turns_keeps_only_the_newest(user):
    store, user_id = user
    chat = store.create_chat(user_id)
    for question in ("a", "b", "c", "d"):
        store.add_turn(chat, StoredTurn(question=question))

    store.trim_turns(chat, keep_last=2)
    assert [t.question for t in store.load_turns(chat)] == ["c", "d"]


def test_trim_to_zero_empties_the_transcript(user):
    store, user_id = user
    chat = store.create_chat(user_id)
    store.add_turn(chat, StoredTurn(question="a"))
    store.trim_turns(chat, keep_last=0)
    assert store.load_turns(chat) == []


def test_a_hand_corrupted_filters_column_does_not_crash_the_load(user):
    store, user_id = user
    chat = store.create_chat(user_id)
    store.add_turn(chat, StoredTurn(question="q", filters=[("STATE", "==", "Kerala")]))

    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE turns SET filters = 'not json'")

    # The answer text is still worth showing even if its scope is unreadable.
    (restored,) = store.load_turns(chat)
    assert restored.question == "q"
    assert restored.filters == []


# --- the rolling summary -----------------------------------------------------


def test_summary_round_trips(user):
    store, user_id = user
    chat = store.create_chat(user_id)
    assert store.load_summary(chat) == ""

    store.save_summary(chat, "Asked about Kerala two-wheelers.")
    assert store.load_summary(chat) == "Asked about Kerala two-wheelers."


# --- user memory -------------------------------------------------------------


def test_memory_is_upserted_not_duplicated(user):
    store, user_id = user
    store.set_memory(user_id, "region", "Karnataka")
    store.set_memory(user_id, "region", "Kerala")

    memories = store.list_memories(user_id)
    assert len(memories) == 1
    assert memories[0].value == "Kerala"


def test_memory_is_per_user(store):
    mine = store.user_id("me")
    theirs = store.user_id("them")
    store.set_memory(mine, "region", "Karnataka")

    assert store.list_memories(theirs) == []


def test_blank_memory_keys_and_values_are_ignored(user):
    store, user_id = user
    store.set_memory(user_id, "", "Karnataka")
    store.set_memory(user_id, "region", "   ")
    assert store.list_memories(user_id) == []


def test_memory_listing_is_capped_newest_first(user):
    store, user_id = user
    for index in range(5):
        store.set_memory(user_id, f"key{index}", f"value{index}")

    limited = store.list_memories(user_id, limit=2)
    assert len(limited) == 2
    # Newest first, so a cap drops the stalest facts rather than the newest ones.
    assert {m.key for m in limited} == {"key3", "key4"}


def test_forgetting(user):
    store, user_id = user
    store.set_memory(user_id, "region", "Karnataka")
    store.set_memory(user_id, "focus", "electric")

    store.delete_memory(user_id, "region")
    assert [m.key for m in store.list_memories(user_id)] == ["focus"]

    assert store.clear_memories(user_id) == 1
    assert store.list_memories(user_id) == []


# --- lifecycle ---------------------------------------------------------------


def test_the_database_is_created_on_construction(tmp_path):
    path = tmp_path / "nested" / "deeper" / "chats.db"
    ChatStore(path)
    assert path.is_file()


def test_reopening_the_same_file_sees_the_same_chats(tmp_path):
    path = tmp_path / "persist.db"
    first = ChatStore(path)
    user_id = first.user_id("ishan")
    first.create_chat(user_id, "Survives a restart")

    # A second process would do exactly this.
    reopened = ChatStore(path)
    assert [c.title for c in reopened.list_chats(reopened.user_id("ishan"))] == [
        "Survives a restart"
    ]


def test_get_store_is_a_singleton_and_honours_the_off_switch(monkeypatch):
    from app.config import settings

    assert get_store() is get_store()

    reset_store()
    monkeypatch.setattr(settings, "persist_chats", False)
    assert get_store() is None


@pytest.mark.parametrize("table", ["users", "chats", "turns", "memories"])
def test_stats_counts_every_table(user, table):
    store, user_id = user
    chat = store.create_chat(user_id)
    store.add_turn(chat, StoredTurn(question="q"))
    store.set_memory(user_id, "region", "Kerala")

    assert store.stats()[table] == 1
