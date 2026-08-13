"""The session layer: user identity, chat lifecycle, and memory injection.

These are the behaviours both front ends depend on, tested without either one.
"""

from __future__ import annotations

import pytest

from app.agent.memory import VERBATIM_TURNS, Conversation, Turn
from app.config import settings
from app.session import UNTITLED, UserSession, resolve_username, title_from_question
from app.store import StoredTurn

# --- who the user is ---------------------------------------------------------


def test_single_user_mode_ignores_headers(monkeypatch):
    monkeypatch.setattr(settings, "auth_header", "")
    monkeypatch.setattr(settings, "default_user", "local")

    assert resolve_username({"X-Forwarded-User": "someone"}) == "local"


def test_the_configured_header_names_the_user(monkeypatch):
    monkeypatch.setattr(settings, "auth_header", "X-Forwarded-User")

    assert resolve_username({"X-Forwarded-User": "ishan"}) == "ishan"
    # Header names are case-insensitive, and proxies disagree about casing.
    assert resolve_username({"x-forwarded-user": "ishan"}) == "ishan"


def test_a_missing_or_empty_header_falls_back_rather_than_failing(monkeypatch):
    monkeypatch.setattr(settings, "auth_header", "X-Forwarded-User")
    monkeypatch.setattr(settings, "default_user", "local")

    assert resolve_username({}) == "local"
    assert resolve_username({"X-Forwarded-User": "   "}) == "local"


def test_a_hostile_username_is_stripped_to_something_boring(monkeypatch):
    monkeypatch.setattr(settings, "auth_header", "X-Forwarded-User")

    assert resolve_username({"X-Forwarded-User": "../../etc/passwd"}) == "....etcpasswd"
    assert resolve_username({"X-Forwarded-User": "<script>x</script>"}) == "scriptxscript"


# --- chat titles -------------------------------------------------------------


def test_a_short_question_becomes_the_title_verbatim():
    assert title_from_question("top makers in Kerala") == "top makers in Kerala"


def test_a_long_question_is_cut_at_a_word_boundary():
    title = title_from_question(
        "which two wheeler makers grew fastest between 2024 and 2025 in Karnataka"
    )
    assert title.endswith("...")
    assert len(title) <= 51
    # Cut between words, not mid-word.
    assert not title[:-3].endswith(" ")
    assert " ".join(title[:-3].split()) == title[:-3]


def test_an_empty_question_leaves_the_chat_untitled():
    assert title_from_question("") == UNTITLED
    assert title_from_question("   ") == UNTITLED


def test_a_single_very_long_word_is_still_cut():
    title = title_from_question("x" * 200)
    assert len(title) == 51


# --- the chat lifecycle ------------------------------------------------------


def test_a_new_session_starts_with_no_chat_until_asked(store):
    session = UserSession(username="ishan", store=store)
    assert session.chat_id is None
    assert session.chats() == []


def test_ensure_chat_creates_one_then_reuses_it(store):
    session = UserSession(username="ishan", store=store)
    created = session.ensure_chat()

    assert created is not None
    assert session.ensure_chat() == created
    assert len(session.chats()) == 1


def test_reopening_the_app_lands_back_in_the_most_recent_chat(store):
    first = UserSession(username="ishan", store=store)
    chat_id = first.ensure_chat()
    first.conversation.add(Turn(question="top makers", answer="HERO leads."))

    # A fresh session is what a browser reload produces.
    returning = UserSession(username="ishan", store=store)
    assert returning.ensure_chat() == chat_id
    assert [t.question for t in returning.conversation.turns] == ["top makers"]


def test_new_chat_starts_an_empty_transcript(store):
    session = UserSession(username="ishan", store=store)
    session.ensure_chat()
    session.conversation.add(Turn(question="first question"))

    session.new_chat()
    assert session.conversation.turns == []
    assert len(session.chats()) == 2


def test_switching_chats_restores_the_right_transcript(store):
    session = UserSession(username="ishan", store=store)
    first = session.ensure_chat()
    session.conversation.add(Turn(question="about Kerala"))

    second = session.new_chat()
    session.conversation.add(Turn(question="about Delhi"))

    assert session.open_chat(first)
    assert [t.question for t in session.conversation.turns] == ["about Kerala"]

    assert session.open_chat(second)
    assert [t.question for t in session.conversation.turns] == ["about Delhi"]


def test_a_chat_belonging_to_someone_else_cannot_be_opened(store):
    theirs = UserSession(username="them", store=store)
    their_chat = theirs.ensure_chat()

    mine = UserSession(username="me", store=store)
    # The stale-URL / guessed-id case.
    assert mine.open_chat(their_chat) is False
    assert mine.chat_id is None


def test_opening_a_deleted_chat_reports_failure_rather_than_crashing(store):
    session = UserSession(username="ishan", store=store)
    chat_id = session.ensure_chat()
    session.delete_chat(chat_id)

    assert session.open_chat(chat_id) is False


def test_deleting_the_active_chat_leaves_none_open(store):
    session = UserSession(username="ishan", store=store)
    chat_id = session.ensure_chat()
    session.conversation.add(Turn(question="q"))

    session.delete_chat(chat_id)
    assert session.chat_id is None
    assert session.conversation.turns == []


def test_the_first_question_titles_the_chat(store):
    session = UserSession(username="ishan", store=store)
    chat_id = session.ensure_chat()
    assert store.chat_title(chat_id) == UNTITLED

    session.note_question("which makers grew fastest?")
    assert store.chat_title(chat_id) == "which makers grew fastest?"


def test_later_questions_do_not_retitle_the_chat(store):
    session = UserSession(username="ishan", store=store)
    chat_id = session.ensure_chat()
    session.note_question("the first question")
    session.note_question("a completely different second question")

    assert store.chat_title(chat_id) == "the first question"


def test_a_manual_rename_survives_the_next_question(store):
    session = UserSession(username="ishan", store=store)
    chat_id = session.ensure_chat()
    session.rename_chat("Kerala research")
    session.note_question("some question")

    assert store.chat_title(chat_id) == "Kerala research"


# --- user memory -------------------------------------------------------------


def test_memories_reach_the_prompt_context(store):
    session = UserSession(username="ishan", store=store)
    session.ensure_chat()
    session.remember("region", "Karnataka")

    context = session.conversation.context_block()
    assert "Standing preferences" in context
    assert "region: Karnataka" in context


def test_a_memory_takes_effect_without_reopening_the_chat(store):
    session = UserSession(username="ishan", store=store)
    session.ensure_chat()
    assert "Karnataka" not in session.conversation.context_block()

    session.remember("region", "Karnataka")
    assert "Karnataka" in session.conversation.context_block()

    session.forget("region")
    assert "Karnataka" not in session.conversation.context_block()


def test_memories_follow_the_user_into_a_new_chat(store):
    session = UserSession(username="ishan", store=store)
    session.ensure_chat()
    session.remember("region", "Karnataka")

    session.new_chat()
    assert "Karnataka" in session.conversation.context_block()


def test_memories_survive_a_restart(store):
    first = UserSession(username="ishan", store=store)
    first.ensure_chat()
    first.remember("focus", "electric vehicles")

    returning = UserSession(username="ishan", store=store)
    returning.ensure_chat()
    assert "electric vehicles" in returning.conversation.context_block()


def test_clearing_the_transcript_keeps_the_standing_preferences(store):
    session = UserSession(username="ishan", store=store)
    session.ensure_chat()
    session.remember("region", "Karnataka")
    session.conversation.add(Turn(question="q"))

    session.conversation.clear()
    assert session.conversation.turns == []
    # The preferences are the user's, not the chat's.
    assert "Karnataka" in session.conversation.context_block()


def test_forget_all_reports_what_it_removed(store):
    session = UserSession(username="ishan", store=store)
    session.ensure_chat()
    session.remember("a", "1")
    session.remember("b", "2")

    assert session.forget_all() == 2
    assert session.memories() == []


# --- running without persistence ---------------------------------------------


def test_everything_still_works_with_persistence_off(monkeypatch):
    monkeypatch.setattr(settings, "persist_chats", False)
    session = UserSession(username="ishan")

    assert session.persistent is False
    assert session.ensure_chat() is None
    assert session.chats() == []
    assert session.memories() == []

    # The conversation is a plain in-memory one, and still works.
    session.conversation.add(Turn(question="q", answer="a"))
    assert len(session.conversation.turns) == 1

    # These are no-ops rather than errors.
    session.remember("region", "Karnataka")
    session.note_question("q")
    session.delete_chat(1)
    assert session.forget_all() == 0


# --- write-through persistence on Conversation itself ------------------------


def test_a_bare_conversation_persists_nothing(store):
    conversation = Conversation()
    assert conversation.persistent is False
    conversation.add(Turn(question="q"))
    assert store.stats()["turns"] == 0


def test_turns_are_written_through_as_they_are_added(user):
    store, user_id = user
    chat_id = store.create_chat(user_id)
    conversation = Conversation().bind(store, chat_id)

    conversation.add(Turn(question="q1", answer="a1", filters=[("STATE", "==", "Kerala")]))
    stored = store.load_turns(chat_id)
    assert [t.question for t in stored] == ["q1"]
    assert stored[0].filters == [("STATE", "==", "Kerala")]


def test_a_failing_store_does_not_lose_the_answer(user, monkeypatch):
    store, user_id = user
    chat_id = store.create_chat(user_id)
    conversation = Conversation().bind(store, chat_id)

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "add_turn", explode)

    # The analysis already succeeded; only the bookkeeping failed.
    conversation.add(Turn(question="q", answer="a real answer"))
    assert conversation.turns[-1].answer == "a real answer"


def test_summarising_folds_the_transcript_on_disk_too(user):
    store, user_id = user
    chat_id = store.create_chat(user_id)
    conversation = Conversation().bind(store, chat_id)

    for index in range(6):
        conversation.add(Turn(question=f"question {index}", answer=f"answer {index}"))

    class StubBackend:
        def chat(self, *_args, **_kwargs):
            return "Asked six things about registrations."

    conversation.maybe_summarise(StubBackend())

    assert store.load_summary(chat_id) == "Asked six things about registrations."
    # Turns the summary now covers are not replayed on top of it.
    assert len(store.load_turns(chat_id)) == VERBATIM_TURNS


def test_a_summarised_chat_reloads_with_its_summary(user):
    store, user_id = user
    chat_id = store.create_chat(user_id)
    store.save_summary(chat_id, "Earlier: Kerala two-wheelers.")
    store.add_turn(chat_id, StoredTurn(question="and Delhi?"))

    reloaded = Conversation.load(store, chat_id)
    context = reloaded.context_block()
    assert "Earlier: Kerala two-wheelers." in context
    assert "and Delhi?" in context


def test_clearing_a_persistent_conversation_clears_the_stored_one(user):
    store, user_id = user
    chat_id = store.create_chat(user_id)
    conversation = Conversation().bind(store, chat_id)
    conversation.add(Turn(question="q"))
    conversation.summary = "something"

    conversation.clear()
    assert store.load_turns(chat_id) == []
    assert store.load_summary(chat_id) == ""


@pytest.mark.parametrize("attribute", ["last_dataset", "last_filters"])
def test_reloaded_conversations_still_answer_follow_ups(user, attribute):
    """The scope-inheritance inputs have to survive a reload, or a follow-up
    asked after a restart silently widens to the whole country."""
    store, user_id = user
    chat_id = store.create_chat(user_id)
    conversation = Conversation().bind(store, chat_id)
    conversation.add(
        Turn(question="q", dataset_key="Two Wheeler/MAKER",
             filters=[("STATE", "==", "Kerala")])
    )

    reloaded = Conversation.load(store, chat_id)
    assert getattr(reloaded, attribute) == getattr(conversation, attribute)
