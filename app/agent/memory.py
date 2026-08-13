"""Conversation memory.

Two kinds of memory meet here.

**Within a chat**, a 4K window will not hold a long transcript plus a schema plus
a result table, so history is compressed rather than truncated: the last few
turns stay verbatim (follow-ups like "and for three-wheelers?" depend on them)
and anything older collapses into a rolling summary.

**Across chats**, a handful of standing facts about the user -- "I mostly care
about Karnataka" -- ride along in every prompt. Those are explicit and few by
design: they are injected into routing and planning, where a wrong one silently
changes an answer, so they are only ever written when a user asks for them.

Persistence is optional and write-through. With no store attached this class
behaves exactly as it always did, in memory, which is what the single-shot `ask`
path and every test use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Tuple

from app.logging_setup import get_logger

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime, fine for typing
    from app.store import ChatStore

log = get_logger(__name__)

#: Turns kept verbatim; older ones fold into the summary.
VERBATIM_TURNS = 2
#: Summarisation kicks in once the transcript is longer than this.
SUMMARISE_AFTER = 4


@dataclass
class Turn:
    question: str
    answer: str = ""
    dataset_key: str = ""
    plan: str = ""
    #: The scope this turn was answered under, as (column, op, value) triples, so
    #: a follow-up can inherit it: "how did it change year on year?" asked after
    #: "city wise registrations in Haryana" means *in Haryana*.
    filters: List[tuple] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"Q: {self.question}"]
        if self.dataset_key:
            lines.append(f"   table: {self.dataset_key}")
        if self.plan:
            lines.append(f"   plan: {self.plan}")
        if self.answer:
            lines.append(f"A: {_first_sentences(self.answer, 2)}")
        return "\n".join(lines)


@dataclass
class Conversation:
    turns: List[Turn] = field(default_factory=list)
    summary: str = ""
    #: Standing facts about the user, as (key, value). Rendered ahead of the
    #: transcript so they read as background rather than as the last thing said.
    memories: List[Tuple[str, str]] = field(default_factory=list)

    #: Write-through persistence. Both must be set for anything to be saved;
    #: neither is required for the conversation to work.
    store: Optional["ChatStore"] = None
    chat_id: Optional[int] = None

    # -- persistence --------------------------------------------------------

    @property
    def persistent(self) -> bool:
        return self.store is not None and self.chat_id is not None

    def bind(self, store: "ChatStore", chat_id: int) -> "Conversation":
        """Attach a store *without* replaying what is already in it.

        Used when a conversation was just loaded from that same chat, so its
        turns are the rows on disk. `load` does this for you.
        """
        self.store, self.chat_id = store, chat_id
        return self

    @classmethod
    def load(cls, store: "ChatStore", chat_id: int,
             memories: Optional[List[Tuple[str, str]]] = None) -> "Conversation":
        """Rebuild a conversation from its stored turns and summary."""
        conversation = cls(
            turns=[
                Turn(
                    question=stored.question,
                    answer=stored.answer,
                    dataset_key=stored.dataset_key,
                    plan=stored.plan,
                    filters=list(stored.filters),
                )
                for stored in store.load_turns(chat_id)
            ],
            summary=store.load_summary(chat_id),
            memories=list(memories or []),
        )
        return conversation.bind(store, chat_id)

    # -- the transcript -----------------------------------------------------

    def add(self, turn: Turn) -> None:
        self.turns.append(turn)
        if not self.persistent:
            return
        from app.store import StoredTurn

        try:
            self.store.add_turn(
                self.chat_id,
                StoredTurn(
                    question=turn.question,
                    answer=turn.answer,
                    dataset_key=turn.dataset_key,
                    plan=turn.plan,
                    filters=[tuple(item) for item in turn.filters],
                ),
            )
        except Exception as exc:  # pragma: no cover - a full disk, a locked file
            # An answer that was computed correctly should still reach the reader
            # when only the bookkeeping failed.
            log.warning("Could not persist a turn (%s); continuing in memory", exc)

    @property
    def last_dataset(self) -> Optional[str]:
        for turn in reversed(self.turns):
            if turn.dataset_key:
                return turn.dataset_key
        return None

    @property
    def last_filters(self) -> List[tuple]:
        """The scope of the immediately preceding answer.

        Deliberately *not* "the most recent turn that had a scope": after "how
        does UP compare with the other states?" the conversation is national,
        and reaching further back would silently resurrect Uttar Pradesh.
        """
        return self.turns[-1].filters if self.turns else []

    def context_block(self) -> str:
        """What gets injected into routing and planning prompts."""
        parts = []
        if self.memories:
            facts = "; ".join(f"{key}: {value}" for key, value in self.memories)
            parts.append(f"Standing preferences: {facts}")
        if self.summary:
            parts.append(f"Summary so far: {self.summary}")
        for turn in self.turns[-VERBATIM_TURNS:]:
            parts.append(turn.render())
        return "\n".join(parts).strip()

    def maybe_summarise(self, backend) -> None:
        """Fold older turns into `summary`. Falls back to a cheap join."""
        older = self.turns[:-VERBATIM_TURNS]
        if len(self.turns) <= SUMMARISE_AFTER or not older:
            return

        transcript = "\n".join(turn.render() for turn in older)
        if self.summary:
            transcript = f"Previous summary: {self.summary}\n{transcript}"

        try:
            from app.agent.prompts import build_summary_messages

            self.summary = backend.chat(
                build_summary_messages(transcript),
                temperature=0.2,
                max_tokens=160,
            ).strip()
        except Exception as exc:
            log.warning("Summarisation failed (%s); using a plain digest", exc)
            self.summary = "; ".join(turn.question for turn in older[-4:])

        # Older turns are now represented by the summary.
        self.turns = self.turns[-VERBATIM_TURNS:]

        if self.persistent:
            try:
                self.store.save_summary(self.chat_id, self.summary)
                # Kept in step with the in-memory trim above: leaving the folded
                # turns on disk would make a resumed chat replay history the
                # summary already covers.
                self.store.trim_turns(self.chat_id, VERBATIM_TURNS)
            except Exception as exc:  # pragma: no cover
                log.warning("Could not persist the summary (%s)", exc)

    def clear(self) -> None:
        """Empty the transcript. Standing preferences are the user's, not the
        chat's, so they survive -- clearing those is a separate, explicit act."""
        self.turns.clear()
        self.summary = ""
        if self.persistent:
            try:
                self.store.trim_turns(self.chat_id, 0)
                self.store.save_summary(self.chat_id, "")
            except Exception as exc:  # pragma: no cover
                log.warning("Could not clear the stored transcript (%s)", exc)


def _first_sentences(text: str, count: int) -> str:
    cleaned = " ".join(text.split())
    pieces = cleaned.replace("! ", ". ").replace("? ", ". ").split(". ")
    joined = ". ".join(pieces[:count]).strip()
    if joined and not joined.endswith("."):
        joined += "."
    return joined[:400]
