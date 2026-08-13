"""Streamlit UI smoke tests.

`AppTest` executes app/ui.py the way a browser session would, so a NameError or
a bad widget call surfaces here rather than as a red screen in the browser.
The backend is stubbed, so no model is loaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.test_orchestrator import INSIGHTS, PINNED, PLAN, StubBackend  # noqa: E402

UI = str(Path(__file__).resolve().parents[1] / "app" / "ui.py")


def _control(harness, kind: str, label: str):
    """Pick a widget by its label -- position also matches the sidebar's."""
    for element in harness.get(kind):
        if str(element.label) == label:
            return element
    raise AssertionError(f"no {kind} labelled {label!r}")


NARRATIVE = "Hero MotoCorp leads Delhi with 1,050 registrations."


@pytest.fixture(autouse=True)
def _clear_streamlit_caches():
    """`@st.cache_resource` keeps one backend per *process*, which is right for
    the app and wrong for tests -- without this, run two leaks into run three."""
    import streamlit as st

    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


@pytest.fixture
def app(data_root, monkeypatch):
    """Run the UI against the test data with a canned backend."""
    from app.config import settings
    from app.llm import factory

    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(factory, "_backend", StubBackend([PLAN, INSIGHTS, NARRATIVE]))

    harness = AppTest.from_file(UI, default_timeout=60)
    harness.run()
    return harness


def test_ui_renders_without_exceptions(app):
    assert not app.exception, [str(e) for e in app.exception]
    assert "Ask your data" in [h.value for h in app.title]


def test_sidebar_reports_the_catalog_and_backend(app):
    text = " ".join(str(e.value) for e in app.sidebar.markdown) + " ".join(
        str(e.value) for e in app.sidebar.caption
    )
    assert "5 tables" in text
    assert any("stub" in str(e.value) for e in app.sidebar.success)


def test_cpu_backend_warns_about_the_gpu(data_root, monkeypatch):
    from app.config import settings
    from app.llm import factory
    from app.llm.base import BackendInfo

    class CpuBackend(StubBackend):
        def info(self):
            return BackendInfo(name="llama_cpp", model="ministral.gguf", gpu=False)

    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(factory, "_backend", CpuBackend([]))

    harness = AppTest.from_file(UI, default_timeout=60)
    harness.run()

    assert not harness.exception
    assert any("CPU" in str(w.value) for w in harness.sidebar.warning)


def test_asking_a_question_renders_an_answer(app):
    app.chat_input[0].set_value(PINNED).run()

    assert not app.exception, [str(e) for e in app.exception]
    assert any("Hero MotoCorp" in str(e.value) for e in app.markdown)
    assert len(app.dataframe) == 1  # the table-view twin is always rendered


def test_a_second_answer_does_not_collide_with_the_first(data_root, monkeypatch):
    """Streamlit keys widgets by type + parameters, so two answers both offering
    "Download CSV" raise StreamlitDuplicateElementId unless each carries a key."""
    from app.config import settings
    from app.llm import factory

    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(
        factory, "_backend",
        StubBackend([PLAN, INSIGHTS, NARRATIVE, PLAN, INSIGHTS, NARRATIVE]),
    )

    harness = AppTest.from_file(UI, default_timeout=90)
    harness.run()

    harness.chat_input[0].set_value(PINNED).run()
    assert not harness.exception, [str(e) for e in harness.exception]

    harness.chat_input[0].set_value("top two wheeler makers in Kerala").run()
    assert not harness.exception, [str(e) for e in harness.exception]

    # Both answers rendered, each with its own table and download buttons.
    assert len(harness.dataframe) == 2
    # `.get()` rather than `.download_button`: the named accessor only exists in
    # newer Streamlit, and this suite runs against two versions.
    assert len(harness.get("download_button")) == 4  # PNG + CSV per answer


def test_only_the_newest_answer_offers_followups(data_root, monkeypatch):
    from app.config import settings
    from app.llm import factory

    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(
        factory, "_backend",
        StubBackend([PLAN, INSIGHTS, NARRATIVE, PLAN, INSIGHTS, NARRATIVE]),
    )

    harness = AppTest.from_file(UI, default_timeout=90)
    harness.run()
    harness.chat_input[0].set_value(PINNED).run()
    first = len(harness.get("button"))

    harness.chat_input[0].set_value("top two wheeler makers in Kerala").run()
    assert not harness.exception
    # Sidebar buttons persist; follow-ups do not stack up across turns.
    assert len(harness.get("button")) == first


def test_the_answer_offers_view_controls(app):
    """A reader can re-slice and re-shape the result without the model."""
    app.chat_input[0].set_value(PINNED).run()

    assert not app.exception, [str(e) for e in app.exception]

    labels = [str(e.label) for e in app.get("multiselect")]
    assert labels, "no filter controls rendered"
    # Grouped and pivoted columns are the axes, so they are not offered as filters.
    assert "Maker" not in labels
    # ...but individual rows of the result can be picked out.
    assert any("maker" in label.lower() for label in labels)

    assert "Show" in [str(e.label) for e in app.get("radio")]
    assert "Rows" in [str(e.label) for e in app.get("number_input")]
    assert "Chart" in [str(e.label) for e in app.get("selectbox")]
    assert "Average line" in [str(e.label) for e in app.get("checkbox")]


def test_showing_all_rows_lifts_the_limit(app):
    """"I'd like to see all the categories, not just top N"."""
    app.chat_input[0].set_value(PINNED).run()
    before = len(app.dataframe[0].value)

    _control(app, "radio", "Show").set_value("All").run()

    assert not app.exception, [str(e) for e in app.exception]
    assert len(app.dataframe[0].value) >= before


def test_bottom_n_flips_the_ranking(app):
    app.chat_input[0].set_value(PINNED).run()
    top = app.dataframe[0].value.iloc[0]["MAKER"]

    _control(app, "radio", "Show").set_value("Bottom").run()

    assert not app.exception, [str(e) for e in app.exception]
    assert app.dataframe[0].value.iloc[0]["MAKER"] != top


def test_applying_a_filter_reslices_the_result(app):
    app.chat_input[0].set_value(PINNED).run()
    before = len(app.dataframe[0].value)

    controls = [e for e in app.get("multiselect") if str(e.label) == "Year"]
    if not controls:
        pytest.skip("no YEAR control in this fixture")
    controls[0].select("2024").run()

    assert not app.exception, [str(e) for e in app.exception]
    # Same shape, recomputed on a slice -- and no model call was needed.
    assert len(app.dataframe[0].value) <= before


# --- chats and standing preferences ------------------------------------------


def _button(harness, label_starts_with: str):
    for element in harness.get("button"):
        if str(element.label).strip().startswith(label_starts_with):
            return element
    raise AssertionError(
        f"no button starting {label_starts_with!r}; saw "
        f"{[str(e.label) for e in harness.get('button')]}"
    )


def _chat_button(harness, chat_id: int):
    """Find a chat's button by key rather than label -- the label is the chat's
    title, which these tests deliberately let the app choose."""
    for element in harness.get("button"):
        if str(getattr(element, "key", "")) == f"chat_{chat_id}":
            return element
    raise AssertionError(f"no button for chat {chat_id}")


def test_a_chat_is_opened_on_first_load(app):
    session = app.session_state["user_session"]
    assert session.chat_id is not None
    assert len(session.chats()) == 1


def test_asking_a_question_persists_it_and_titles_the_chat(app):
    app.chat_input[0].set_value(PINNED).run()
    assert not app.exception, [str(e) for e in app.exception]

    session = app.session_state["user_session"]
    (chat,) = session.chats()
    assert chat.turn_count == 1
    # Titled from the question rather than left as "New chat".
    assert chat.title.startswith(PINNED[:20])


def test_a_reload_restores_the_transcript_as_text(app, data_root, monkeypatch):
    """A new browser session is a new AppTest against the same store."""
    from app.config import settings
    from app.llm import factory

    app.chat_input[0].set_value(PINNED).run()
    assert not app.exception

    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(factory, "_backend", StubBackend([]))
    reloaded = AppTest.from_file(UI, default_timeout=60)
    reloaded.run()

    assert not reloaded.exception, [str(e) for e in reloaded.exception]
    text = " ".join(str(e.value) for e in reloaded.markdown)
    assert PINNED in text
    assert "Hero MotoCorp" in text
    # No model was needed to redraw it.
    assert reloaded.session_state["user_session"].chat_id is not None


def test_new_chat_clears_the_transcript_without_deleting_the_old_one(app):
    app.chat_input[0].set_value(PINNED).run()
    assert not app.exception

    _button(app, "➕").click().run()
    assert not app.exception, [str(e) for e in app.exception]

    assert app.session_state["messages"] == []
    # The first chat is still there, alongside the new one.
    assert len(app.session_state["user_session"].chats()) == 2


def test_switching_back_to_an_earlier_chat_restores_its_transcript(app):
    app.chat_input[0].set_value(PINNED).run()
    first_chat = app.session_state["user_session"].chat_id

    _button(app, "➕").click().run()
    assert app.session_state["messages"] == []

    _chat_button(app, first_chat).click().run()
    assert not app.exception, [str(e) for e in app.exception]

    session = app.session_state["user_session"]
    assert session.chat_id == first_chat
    assert any(PINNED in str(e.value) for e in app.markdown)


def test_a_preference_added_in_the_sidebar_reaches_the_prompt(app):
    _control(app, "text_input", "Name").set_value("region").run()
    _control(app, "text_input", "Value").set_value("Karnataka").run()
    _button(app, "Remember").click().run()

    assert not app.exception, [str(e) for e in app.exception]

    session = app.session_state["user_session"]
    assert [(m.key, m.value) for m in session.memories()] == [("region", "Karnataka")]
    assert "region: Karnataka" in session.conversation.context_block()


def test_a_preference_can_be_removed_again(app):
    session = app.session_state["user_session"]
    session.remember("region", "Karnataka")
    app.run()

    _button(app, "✕").click().run()

    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state["user_session"].memories() == []


def test_reset_clears_the_transcript_but_keeps_the_preferences(app):
    session = app.session_state["user_session"]
    session.remember("region", "Karnataka")
    app.chat_input[0].set_value(PINNED).run()

    _button(app, "Reset conversation").click().run()

    assert not app.exception, [str(e) for e in app.exception]
    assert app.session_state["messages"] == []
    # The preference is the user's, not the chat's.
    assert len(app.session_state["user_session"].memories()) == 1


def test_the_ui_runs_with_persistence_switched_off(data_root, monkeypatch):
    from app.config import settings
    from app.llm import factory

    monkeypatch.setattr(settings, "data_dir", data_root)
    monkeypatch.setattr(settings, "persist_chats", False)
    monkeypatch.setattr(factory, "_backend", StubBackend([PLAN, INSIGHTS, NARRATIVE]))

    harness = AppTest.from_file(UI, default_timeout=60)
    harness.run()
    assert not harness.exception, [str(e) for e in harness.exception]

    harness.chat_input[0].set_value(PINNED).run()
    assert not harness.exception, [str(e) for e in harness.exception]
    assert any("Hero MotoCorp" in str(e.value) for e in harness.markdown)


def test_missing_data_directory_is_reported(monkeypatch, tmp_path):
    from app.config import settings
    from app.llm import factory

    monkeypatch.setattr(settings, "data_dir", tmp_path / "nothing-here")
    monkeypatch.setattr(factory, "_backend", StubBackend([]))

    harness = AppTest.from_file(UI, default_timeout=60)
    harness.run()

    assert not harness.exception
    assert any("No .parquet or .csv" in str(e.value) for e in harness.sidebar.error)
