"""Streamlit front end.

    streamlit run app/ui.py
    ai-analyzer-ui                     # the same thing, once installed

The chart is never the only way to read a value -- the result table is rendered
beside every answer as the table-view twin.

Chats and standing preferences are persisted (see `app/store.py`), so closing
the tab is not the same as losing the thread. Restored turns render as text:
the question, the answer and the plan are stored, but the result frame is not,
because re-executing a spec is milliseconds of pandas and storing every frame
would not be.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

# Streamlit executes this file directly, so the project root needs to be importable.
_ROOT = str(Path(__file__).resolve().parents[1])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import streamlit as st  # noqa: E402

from app import cache  # noqa: E402
from app.agent import Analyst  # noqa: E402
from app.config import settings  # noqa: E402
from app.data.catalog import get_catalog  # noqa: E402
from app.data.loader import cache_stats, clear_dataset_cache  # noqa: E402
from app.logging_setup import configure_logging  # noqa: E402
from app.session import UserSession  # noqa: E402

st.set_page_config(page_title="AI Analyzer V3", page_icon="📊", layout="wide")
configure_logging()


def _stretch() -> dict:
    """Streamlit 1.59 renamed `use_container_width=True` to `width="stretch"`.

    Returned as kwargs so the app runs on either side of that rename -- the
    project's .venv and the system interpreter are on different versions.
    """
    try:
        major, minor = (int(part) for part in st.__version__.split(".")[:2])
    except (ValueError, AttributeError):  # pragma: no cover - odd version string
        return {"use_container_width": True}
    return {"width": "stretch"} if (major, minor) >= (1, 59) else {"use_container_width": True}


STRETCH = _stretch()


def _app_theme() -> str:
    """Default the charts to whatever theme Streamlit is actually showing.

    A light chart on a dark page reads as a bright rectangle pasted onto the
    app, so the default follows the page rather than a fixed setting.
    """
    try:
        active = getattr(getattr(st.context, "theme", None), "type", None)
        if active in ("light", "dark"):
            return active
    except Exception:  # pragma: no cover - older Streamlit has no st.context
        pass
    base = st.get_option("theme.base")
    return base if base in ("light", "dark") else settings.chart_theme


@st.cache_resource(show_spinner="Loading the model...")
def _backend():
    """One model per process, shared across browser sessions."""
    from app.llm import get_backend

    return get_backend()


# --- session, chats and memory ----------------------------------------------


def _request_headers() -> dict:
    """The inbound request's headers, when this Streamlit can supply them.

    Only consulted when AUTH_HEADER is configured; single-user installs never
    reach the header at all.
    """
    try:
        return dict(st.context.headers or {})
    except Exception:  # pragma: no cover - older Streamlit has no st.context
        return {}


def _messages_from(conversation) -> list:
    """Rebuild a renderable transcript from a restored conversation.

    Stored turns become plain text rather than full answers: what survives a
    reload is the question, the prose and the plan, not the result frame.
    """
    messages = []
    for turn in conversation.turns:
        messages.append({"role": "user", "content": turn.question})
        messages.append({
            "role": "assistant",
            "text": turn.answer,
            "plan": turn.plan,
            "dataset": turn.dataset_key,
        })
    return messages


def _session() -> UserSession:
    """This browser session's user and active chat, created once."""
    session = st.session_state.get("user_session")
    if session is None:
        session = UserSession(headers=_request_headers())
        session.ensure_chat()
        st.session_state.user_session = session
        st.session_state.messages = _messages_from(session.conversation)
    return session


def _switch_to(session: UserSession, chat_id: int) -> None:
    if session.open_chat(chat_id):
        st.session_state.messages = _messages_from(session.conversation)
        st.session_state.pop("pending_question", None)
        st.rerun()


def _analyst(data_dir: str, session: UserSession) -> Analyst:
    catalog = get_catalog(Path(data_dir) if data_dir else None)
    analyst = st.session_state.get("analyst")
    if analyst is None or analyst.catalog.root != catalog.root:
        analyst = Analyst(backend=_backend(), catalog=catalog,
                          conversation=session.conversation)
        st.session_state.analyst = analyst
    # The active chat can change under the analyst between runs, so the
    # conversation is rebound every time rather than only at construction.
    analyst.conversation = session.conversation
    return analyst


def _render_chat_list(session: UserSession) -> None:
    """The sidebar's chat switcher."""
    if not session.persistent:
        st.caption("Persistence is off — this chat ends with the process.")
        return

    if st.button("➕  New chat", key="new_chat", **STRETCH):
        session.new_chat()
        st.session_state.messages = []
        st.rerun()

    chats = session.chats()
    if not chats:
        return

    for entry in chats:
        active = entry.id == session.chat_id
        # A plain bullet marks the open chat. Deliberately not a padding space
        # on the others: an alignment character is invisible to a sighted reader
        # and noise to a screen reader.
        if st.button(
            f"• {entry.title}" if active else entry.title,
            key=f"chat_{entry.id}",
            help=f"{entry.turn_count} turn(s) · {entry.updated_display}",
            disabled=active,
            **STRETCH,
        ):
            _switch_to(session, entry.id)

    with st.expander("Manage this chat"):
        if session.chat_id is None:
            st.caption("No chat is open.")
            return

        renamed = st.text_input("Title", value=session.store.chat_title(session.chat_id),
                                key="rename_input")
        if st.button("Rename", key="rename_chat", **STRETCH):
            session.rename_chat(renamed)
            st.rerun()

        # Two deliberate steps: one stray click should not take a chat with it.
        confirmed = st.checkbox("Yes, delete this chat", key="confirm_delete")
        if st.button("Delete chat", key="delete_chat", disabled=not confirmed, **STRETCH):
            session.delete_chat(session.chat_id)
            session.ensure_chat()
            st.session_state.messages = _messages_from(session.conversation)
            st.rerun()


def _render_memory(session: UserSession) -> None:
    """The sidebar's standing-preferences editor.

    These reach routing and planning, where a wrong one silently changes an
    answer -- so they are shown in full, and every one has a remove button
    beside it rather than being buried behind a settings page.
    """
    if not session.persistent:
        return

    remembered = session.memories()
    with st.expander(f"Remembered ({len(remembered)})", expanded=False):
        st.caption("Applied to every question, in every chat.")

        for item in remembered:
            row = st.columns([5, 1])
            row[0].markdown(f"**{item.key}** — {item.value}")
            if row[1].button("✕", key=f"forget_{item.key}", help=f"Forget '{item.key}'"):
                session.forget(item.key)
                st.rerun()

        if len(remembered) >= settings.max_user_memories:
            st.caption(f"At the {settings.max_user_memories}-preference limit; "
                       "remove one to add another.")

        key = st.text_input("Name", key="memory_key", placeholder="region")
        value = st.text_input("Value", key="memory_value", placeholder="Karnataka")
        if st.button("Remember", key="add_memory", **STRETCH):
            if key.strip() and value.strip():
                session.remember(key, value)
                st.rerun()
            else:
                st.warning("Both a name and a value are needed.")


# --- answers -----------------------------------------------------------------


def _interactive_chart(answer, result, average: bool = False):
    """Build the Vega-Lite twin of the PNG the agent already rendered."""
    from app.analysis.charts import select_chart_columns
    from app.analysis.interactive import build_chart

    spec = result.spec
    columns, label = select_chart_columns(result.value_columns, spec.derive, spec.chart)
    return build_chart(
        result.frame,
        chart=spec.chart,
        label_columns=result.label_columns,
        value_columns=columns,
        title=spec.title or answer.question[:80],
        subtitle=f"{result.dataset_key} · {result.value_label}",
        value_label=label or result.value_label,
        theme=st.session_state.get("chart_theme", "light"),
        average_line=average,
        # A reader who asked for every row means it, even in a pie.
        max_slices=len(result.frame) if spec.limit >= settings.max_result_rows else None,
    )


def _render_followups(answer, index: int) -> None:
    """Draw the follow-up buttons for the newest answer.

    Rendered outside `_render_answer` and after the transcript, because the
    history loop cannot know whether a newer answer is about to be appended
    below it -- doing it inline made every turn's buttons stack up.
    """
    if not answer or not answer.ok or not answer.followups:
        return

    st.caption("Ask next")
    for position, (column, item) in enumerate(
        zip(st.columns(len(answer.followups)), answer.followups, strict=True)
    ):
        with column:
            if st.button(item.question, key=f"followup_{index}_{position}",
                         help=item.reason, **STRETCH):
                st.session_state.pending_question = item.question
                st.rerun()


#: Offered as filter controls, in the order they read: broad to narrow.
FILTERABLE = ["CATEGORY", "STATE", "CITY", "YEAR", "FUEL", "CLASS", "MAKER", "NORM"]
#: Above this many options a dropdown is unusable; those columns stay text-only.
MAX_FILTER_OPTIONS = 400


def _view_controls(answer, index: int):
    """Let the reader re-slice and re-shape the answer without the model.

    Re-running a spec is milliseconds of pandas, so every control here just
    edits the spec and executes again -- no inference, no new question. The
    result replaces the answer's everywhere below, so the chart, the table, the
    findings and the downloads can never disagree with each other.
    """
    from app.analysis.executor import execute
    from app.analysis.spec import CHART_TYPES, Filter

    entry, spec = answer.entry, answer.result.spec
    if entry is None or spec is None:
        return None

    frame = entry.load()
    adjusted = spec.model_copy(deep=True)
    touched = False

    with st.expander("Filter and shape these results", expanded=False):
        # --- which rows, and how many -------------------------------------
        shape = st.columns(4)
        with shape[0]:
            view = st.radio("Show", ["Top", "Bottom", "All"], horizontal=True,
                            key=f"view_{index}")
        with shape[1]:
            rows = st.number_input("Rows", min_value=1,
                                   max_value=settings.max_result_rows,
                                   value=min(max(spec.limit, 1), settings.max_result_rows),
                                   step=5, key=f"rows_{index}",
                                   disabled=view == "All")
        with shape[2]:
            chart = st.selectbox("Chart", ["Auto"] + CHART_TYPES, key=f"chart_{index}")
        with shape[3]:
            average = st.checkbox("Average line", key=f"avg_{index}",
                                  help="Draw the mean across the rows shown")

        if view == "All":
            adjusted.limit = settings.max_result_rows
            touched = True
        elif view == "Bottom":
            adjusted.sort_desc, adjusted.limit = False, int(rows)
            touched = True
        elif int(rows) != spec.limit:
            adjusted.limit = int(rows)
            touched = True

        if chart != "Auto" and chart != spec.chart:
            adjusted.chart = chart
            touched = True

        # --- which slice of the data --------------------------------------
        columns = [
            column for column in FILTERABLE
            if column in frame.columns
            and column not in spec.group_by
            and column != spec.pivot_on
        ]
        chosen, controls = {}, st.columns(min(max(len(columns), 1), 4))
        for position, column in enumerate(columns):
            options = sorted(frame[column].dropna().astype(str).unique().tolist())
            if len(options) > MAX_FILTER_OPTIONS:
                continue
            current = next(
                (f.value for f in spec.filters if f.column == column and f.op == "=="), ""
            )
            with controls[position % len(controls)]:
                picked = st.multiselect(
                    column.title(), options,
                    default=[v for v in current.split(", ") if v in options],
                    key=f"filter_{index}_{column}", placeholder="All",
                )
            if picked:
                chosen[column] = picked

        if chosen:
            # Replace only the columns the reader touched; the plan stands.
            adjusted.filters = [f for f in adjusted.filters if f.column not in chosen]
            for column, values in chosen.items():
                adjusted.filters.append(
                    Filter(column=column, op="==" if len(values) == 1 else "in",
                           value=", ".join(values))
                )
            touched = True

        # --- pick individual rows out of the result -----------------------
        picked_rows = []
        if answer.result.label_columns:
            label = answer.result.label_columns[0]
            full = answer.result.full_frame
            if full is not None and label in full.columns:
                picked_rows = st.multiselect(
                    f"Only these {label.lower()} values",
                    sorted(full[label].astype(str).unique().tolist()),
                    key=f"rows_pick_{index}", placeholder="All",
                )
        if picked_rows:
            adjusted.filters = [f for f in adjusted.filters
                                if f.column != answer.result.label_columns[0]]
            adjusted.filters.append(Filter(
                column=answer.result.label_columns[0],
                op="==" if len(picked_rows) == 1 else "in",
                value=", ".join(picked_rows),
            ))
            adjusted.limit = settings.max_result_rows
            touched = True

    if not touched:
        return None
    return execute(entry, adjusted), average


def _render_insights(answer) -> None:
    """The model's reading first, the computed findings beneath it as evidence.

    An observation is not an insight, so the interpretation leads. The findings
    stay visible because they are what it was derived from -- and what the
    reader can check it against.
    """
    if getattr(answer, "interpretation", ""):
        st.markdown("#### What this means")
        st.markdown(answer.interpretation)

    if not answer.insights:
        return
    with st.expander("Findings these are based on", expanded=not answer.interpretation):
        for insight in answer.insights:
            box = st.warning if insight.severity == "caution" else st.info
            box(insight.text, icon="⚠️" if insight.severity == "caution" else "💡")


def _render_restored(message: dict) -> None:
    """Render an answer that came back from the store rather than the agent."""
    st.markdown(message.get("text") or "_(no answer was recorded)_")
    plan = message.get("plan")
    if plan:
        with st.expander("How this was computed"):
            st.code(plan, language="text")
            st.caption(f"From an earlier session · {message.get('dataset', '')}".strip(" ·"))


def _render_answer(answer, index: int = 0) -> None:
    """Render one answer.

    `index` is this answer's position in the transcript and exists solely to key
    the widgets: Streamlit derives an element id from type + parameters, so two
    answers both offering "Download CSV" collide unless each carries its own key.
    """
    if not answer.ok:
        st.error(answer.error)
        return

    if answer.narrative:
        st.markdown(answer.narrative)

    _render_insights(answer)

    result = answer.result
    if result is not None and not result.is_empty:
        # A reader-applied view replaces the result everywhere below, so the
        # chart, table, findings and downloads never disagree.
        average = False
        adjusted = _view_controls(answer, index)
        if adjusted is not None:
            sliced, average = adjusted
            if not sliced.is_empty:
                from app.analysis.insights import build_insights

                result = sliced
                result.insights = build_insights(result)
                # The interpretation was written for the unsliced answer, so it
                # no longer describes what is on screen.
                answer = replace(answer, insights=result.insights, interpretation="")

        chart_tab, table_tab = st.tabs(["Chart", f"Table ({len(result.frame):,} rows)"])

        with chart_tab:
            chart = _interactive_chart(answer, result, average)  # reflects the view above
            if chart is not None:
                # Hover for exact values; labels are not truncated here.
                st.altair_chart(chart, **STRETCH)
            elif answer.chart_path and Path(answer.chart_path).is_file():
                st.image(str(answer.chart_path), **STRETCH)
            else:
                st.caption("No chart for this result.")

            if answer.chart_path and Path(answer.chart_path).is_file():
                st.download_button(
                    "Download PNG",
                    data=Path(answer.chart_path).read_bytes(),
                    file_name=Path(answer.chart_path).name,
                    mime="image/png",
                    key=f"png_{index}",
                )

        with table_tab:
            # The table-view twin: every value readable without the chart.
            st.dataframe(result.frame, hide_index=True, key=f"table_{index}", **STRETCH)
            st.download_button(
                "Download CSV",
                data=result.frame.to_csv(index=False).encode("utf-8"),
                file_name="result.csv",
                mime="text/csv",
                key=f"csv_{index}",
            )

    with st.expander("How this was computed"):
        st.code(answer.plan or "(no plan)", language="text")
        if result is not None:
            st.caption(
                f"{result.rows_matched:,} of {result.rows_scanned:,} rows matched · "
                f"{len(result.frame):,} rows returned"
            )
        if answer.notes:
            for note in answer.notes:
                st.warning(note, icon="⚠️")
        if answer.timings:
            st.caption(
                f"{answer.elapsed:.1f}s total · "
                + " · ".join(f"{k} {v:.1f}s" for k, v in answer.timings.items())
            )


def _render_message(message: dict, index: int) -> None:
    """Dispatch on where the message came from: the agent, or the store."""
    if message["role"] == "user":
        st.markdown(message["content"])
    elif "answer" in message:
        _render_answer(message["answer"], index=index)
    else:
        _render_restored(message)


# --- sidebar ----------------------------------------------------------------

session = _session()

with st.sidebar:
    st.header("AI Analyzer V3")
    if settings.auth_header:
        st.caption(f"Signed in as **{session.username}**")

    _render_chat_list(session)
    st.divider()

    default_dir = str(settings.resolved_data_dir())
    data_dir = st.text_input("Data directory", value=default_dir)

    try:
        catalog = get_catalog(Path(data_dir) if data_dir else None)
        if catalog.is_empty():
            st.error(f"No .parquet or .csv files under {catalog.root}")
        else:
            st.caption(
                f"{len(catalog)} tables · {len(catalog.categories)} categories"
            )
            with st.expander("Tables"):
                for key in sorted(catalog.entries):
                    st.write(f"`{key}`")
    except Exception as exc:
        st.error(f"Could not read the data directory: {exc}")

    _render_memory(session)

    st.divider()

    try:
        info = _backend().info()
        st.success(info.describe())
        if not info.gpu and info.name == "llama_cpp":
            st.warning(
                "Running on CPU. Start Ollama, or install the CUDA wheel, "
                "to use the GPU.",
                icon="🐌",
            )
    except Exception as exc:
        st.error(f"No inference backend: {exc}")

    theme = st.radio("Chart theme", ["light", "dark"],
                     index=0 if _app_theme() == "light" else 1,
                     horizontal=True)
    st.session_state.chart_theme = theme

    st.divider()

    stats = cache_stats()
    st.caption(f"Datasets in memory: {stats['datasets']} ({stats['bytes'] / 1e6:.0f} MB)")

    if st.button("Reset conversation", **STRETCH):
        session.conversation.clear()
        st.session_state.messages = []
        st.rerun()

    if st.button("Clear caches", **STRETCH):
        removed = cache.clear()["removed"]
        clear_dataset_cache()
        st.toast(f"Cleared {removed} cached responses.")


# --- main -------------------------------------------------------------------

st.title("Ask your data")

if "messages" not in st.session_state:
    st.session_state.messages = []

for position, message in enumerate(st.session_state.messages):
    with st.chat_message(message["role"]):
        _render_message(message, position)

question = st.chat_input("e.g. which two-wheeler makers grew fastest from 2024 to 2025?")

# A follow-up button queues its question for the next run.
if not question and st.session_state.get("pending_question"):
    question = st.session_state.pop("pending_question")

if question:
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # Titles an untitled chat before the answer exists, so a question that goes
    # on to fail still names the chat it happened in.
    session.note_question(question)

    with st.chat_message("assistant"):
        status = st.status("Thinking...", expanded=False)
        try:
            analyst = _analyst(data_dir, session)
            answer = analyst.ask(
                question,
                theme=theme,
                on_progress=lambda stage: status.update(label=f"{stage}..."),
            )
            status.update(label=f"Done in {answer.elapsed:.1f}s", state="complete")
        except Exception as exc:  # pragma: no cover - UI guard
            status.update(label="Failed", state="error")
            st.error(str(exc))
            answer = None

        if answer is not None:
            # The index this answer is about to occupy, so its widget keys match
            # the ones the history loop will use on the next run.
            _render_answer(answer, index=len(st.session_state.messages))
            st.session_state.messages.append({"role": "assistant", "answer": answer})

# Exactly one set of follow-ups, for whatever answer is now last.
_last = next(
    (m for m in reversed(st.session_state.messages)
     if m["role"] == "assistant" and "answer" in m),
    None,
)
if _last is not None:
    _render_followups(_last["answer"], index=len(st.session_state.messages) - 1)
