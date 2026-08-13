"""Business-intelligence layer: the "so what" behind a result table.

A number with no denominator is not an insight. "Bengaluru: 661,855" says
nothing until you know it is 3.1% of the national total, 2.5x Mumbai, and that
the market it sits in grew 6.9%.

Everything here is computed in pandas, never by the model. That is deliberate:
the narrator is forbidden from doing arithmetic (a 3B model invents numbers the
moment it tries), so the only way to get real analysis into the answer is to
compute it first and hand over verified facts. The model's job stays what it is
good at -- turning facts into a sentence.

Follow-ups are generated the same way: from the spec and the result, as
questions this system can actually answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Set

import numpy as np
import pandas as pd

from app.analysis.executor import CHANGE_COLUMN, GROWTH_COLUMN, SHARE_COLUMN, AnalysisResult
from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)

#: A leader is only "dominant" past this share of the total.
DOMINANCE_SHARE = 40.0
#: Top-N used for the concentration reading.
CONCENTRATION_N = 3
#: A gap under this is a close race, not a lead.
NOTABLE_GAP_PCT = 15.0
#: An outlier sits this many times above the median.
OUTLIER_MULTIPLE = 5.0


@dataclass
class Insight:
    kind: str
    text: str
    severity: str = "info"  # "info" | "caution"
    #: Every number this insight introduces, so the narrative verifier can
    #: accept the model quoting them.
    values: List[float] = field(default_factory=list)


@dataclass
class FollowUp:
    question: str
    reason: str = ""


def _fmt(value: float) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def _pct(value: float) -> str:
    return f"{value:,.1f}%"


def _plural(word: str) -> str:
    """Enough English to avoid "the other citys" in a follow-up."""
    lowered = word.lower()
    if lowered.endswith("y") and not lowered.endswith(("ay", "ey", "iy", "oy", "uy")):
        return lowered[:-1] + "ies"
    if lowered.endswith(("s", "x", "z", "ch", "sh")):
        return lowered + "es"
    return lowered + "s"


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce")


def _label_of(frame: pd.DataFrame, index: int, label_columns: Sequence[str]) -> str:
    columns = [c for c in label_columns if c in frame.columns]
    if not columns:
        return "the result"
    return " / ".join(str(frame.iloc[index][c]) for c in columns)


def _headline_column(result: AnalysisResult) -> Optional[str]:
    """The column the ranking is actually about."""
    frame = result.full_frame if result.full_frame is not None else result.frame
    if frame is None or frame.empty:
        return None

    # Must follow the ranking, not just the calculation: on a size-ranked growth
    # table the leader's edge is in units, and quoting it as "838.4 percentage
    # points" of growth rate reads as nonsense.
    sort_by = getattr(result.spec, "sort_by", "auto")
    if sort_by == "auto":
        if result.spec.derive == "growth_pct" and GROWTH_COLUMN in frame.columns:
            return GROWTH_COLUMN
        if result.spec.derive == "share_pct" and SHARE_COLUMN in frame.columns:
            return SHARE_COLUMN
    elif sort_by == "growth" and GROWTH_COLUMN in frame.columns:
        return GROWTH_COLUMN
    elif sort_by == "change" and CHANGE_COLUMN in frame.columns:
        return CHANGE_COLUMN

    candidates = [
        c for c in result.value_columns
        if c in frame.columns and c not in (GROWTH_COLUMN, SHARE_COLUMN, CHANGE_COLUMN)
    ]
    return candidates[-1] if candidates else None


def _volume_column(result: AnalysisResult) -> Optional[str]:
    """The column holding actual quantities, even when ranking by percent."""
    frame = result.full_frame if result.full_frame is not None else result.frame
    if frame is None or frame.empty:
        return None
    candidates = [
        c for c in result.value_columns
        if c in frame.columns and c not in (GROWTH_COLUMN, SHARE_COLUMN, CHANGE_COLUMN)
    ]
    return candidates[-1] if candidates else None


def build_insights(result: AnalysisResult) -> List[Insight]:
    """Everything worth saying about a result that the table alone does not."""
    frame = result.full_frame if result.full_frame is not None else result.frame
    if frame is None or frame.empty or not result.label_columns:
        return []

    insights: List[Insight] = []
    try:
        insights += _base_effect_caution(result)
        insights += _market_movement(result, frame)
        insights += _absolute_vs_relative(result, frame)
        insights += _outperformers(result, frame)
        insights += _growth_contribution(result, frame)
        insights += _decliners(result, frame)
        insights += _rank_shift(result, frame)
        insights += _electrification(result, frame)
        insights += _share_shift(result, frame)
        insights += _concentration_shift(result, frame)
        insights += _share_and_concentration(result, frame)
        insights += _leader_gap(result, frame)
        insights += _outlier(result, frame)
        insights += _coverage(result, frame)
    except Exception:  # pragma: no cover - insights must never break an answer
        log.exception("Insight computation failed")

    # Cautions first: a caveat that changes how the answer reads outranks colour.
    insights.sort(key=lambda i: 0 if i.severity == "caution" else 1)
    return insights[: settings.max_insights]


def _base_effect_caution(result: AnalysisResult) -> List[Insight]:
    for note in result.notes:
        if "base under" in note:
            return [Insight(
                kind="base_effect",
                severity="caution",
                text="Rows with a negligible starting value were kept out of this "
                     "ranking; a large percent change on a tiny base is not growth.",
            )]
    return []


def _market_movement(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """The denominator: how the whole set moved, not just the winner."""
    if result.spec.derive != "growth_pct":
        return []
    periods = [c for c in result.value_columns
               if c in frame.columns and c not in (GROWTH_COLUMN, SHARE_COLUMN, CHANGE_COLUMN)]
    if len(periods) < 2:
        return []

    first, last = periods[0], periods[-1]
    start, end = float(_numeric(frame, first).sum()), float(_numeric(frame, last).sum())
    if not start:
        return []

    change = (end - start) / start * 100.0
    direction = "grew" if change >= 0 else "shrank"
    return [Insight(
        kind="market",
        text=f"Across everything shown, the total {direction} from {_fmt(start)} in "
             f"{first} to {_fmt(end)} in {last} ({_pct(change)}). Judge individual "
             f"movers against that.",
        values=[start, end, change, abs(change)],
    )]


def _absolute_vs_relative(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """The single most useful growth insight: percent winner != volume winner."""
    if result.spec.derive != "growth_pct" or CHANGE_COLUMN not in frame.columns:
        return []
    if len(frame) < 2:
        return []

    change = _numeric(frame, CHANGE_COLUMN)
    if change.isna().all():
        return []

    position = int(np.nanargmax(change.to_numpy(dtype=float)))
    mover = _label_of(frame, position, result.label_columns)
    added = float(change.iloc[position])
    growth = (
        float(_numeric(frame, GROWTH_COLUMN).iloc[position])
        if GROWTH_COLUMN in frame.columns else None
    )

    # The fastest grower, computed -- not row 0. With `sort_by="value"` the top
    # row is the biggest city, and calling it "the percent leader" was wrong.
    if GROWTH_COLUMN in frame.columns:
        rates = _numeric(frame, GROWTH_COLUMN).to_numpy(dtype=float)
        if np.isnan(rates).all():
            return []
        leader = _label_of(frame, int(np.nanargmax(rates)), result.label_columns)
    else:
        leader = _label_of(frame, 0, result.label_columns)

    if mover == leader:
        return []

    text = (f"{mover} added the most actual volume ({_fmt(added)})"
            + (f", despite growing only {_pct(growth)}" if growth is not None else "")
            + f" - the fastest grower is {leader}.")
    values = [added] + ([growth] if growth is not None else [])
    return [Insight(
        kind="absolute_mover", text=text, values=values,
    )]


def _periods(result: AnalysisResult, frame: pd.DataFrame) -> Optional[tuple]:
    """The first and last period columns of a growth table."""
    if result.spec.derive != "growth_pct":
        return None
    periods = [c for c in result.value_columns
               if c in frame.columns and c not in (GROWTH_COLUMN, SHARE_COLUMN, CHANGE_COLUMN)]
    return (periods[0], periods[-1]) if len(periods) >= 2 else None


def _outperformers(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """How many beat the market -- the question behind "is this good?"."""
    periods = _periods(result, frame)
    if periods is None or GROWTH_COLUMN not in frame.columns or len(frame) < 3:
        return []

    first, last = periods
    start, end = float(_numeric(frame, first).sum()), float(_numeric(frame, last).sum())
    if not start:
        return []

    market = (end - start) / start * 100.0
    rates = _numeric(frame, GROWTH_COLUMN).dropna()
    if rates.empty:
        return []

    beat = int((rates > market).sum())
    return [Insight(
        kind="outperformers",
        text=f"{beat} of {len(rates)} grew faster than the market's {_pct(market)}; "
             f"the other {len(rates) - beat} lost ground relative to it.",
        values=[float(beat), float(len(rates)), float(len(rates) - beat), market],
    )]


def _growth_contribution(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """Who actually drove the increase -- growth is rarely evenly spread."""
    if _periods(result, frame) is None or CHANGE_COLUMN not in frame.columns:
        return []
    if len(frame) <= CONCENTRATION_N:
        return []

    change = _numeric(frame, CHANGE_COLUMN)
    gained = change[change > 0]
    if gained.empty or gained.sum() <= 0:
        return []

    top = gained.nlargest(CONCENTRATION_N)
    share = float(top.sum()) / float(gained.sum()) * 100.0
    names = ", ".join(
        _label_of(frame, position, result.label_columns)
        for position in [frame.index.get_loc(i) for i in top.index]
    )
    return [Insight(
        kind="growth_contribution",
        text=f"{_pct(share)} of all the growth came from just {len(top)}: {names}.",
        values=[share, float(len(top))],
    )]


def _decliners(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """What is shrinking, which a ranking sorted by size hides at the bottom."""
    if _periods(result, frame) is None or CHANGE_COLUMN not in frame.columns:
        return []

    change = _numeric(frame, CHANGE_COLUMN).dropna()
    lost = change[change < 0]
    if lost.empty:
        return []

    worst_index = int(lost.idxmin())
    worst = _label_of(frame, frame.index.get_loc(worst_index), result.label_columns)
    total_lost = abs(float(lost.sum()))
    return [Insight(
        kind="decliners",
        severity="caution",
        text=f"{len(lost)} of {len(change)} shrank, giving up {_fmt(total_lost)} "
             f"between them; {worst} fell most ({_fmt(abs(float(lost.min())))}).",
        values=[float(len(lost)), float(len(change)), total_lost,
                abs(float(lost.min()))],
    )]


def _rank_shift(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """Someone overtaking someone else is the story a table of totals hides."""
    periods = _periods(result, frame)
    if periods is None or len(frame) < 2:
        return []

    first, last = periods
    before = _numeric(frame, first)
    after = _numeric(frame, last)
    if before.isna().all() or after.isna().all():
        return []

    was_leader = _label_of(frame, int(np.nanargmax(before.to_numpy(dtype=float))),
                           result.label_columns)
    now_leader = _label_of(frame, int(np.nanargmax(after.to_numpy(dtype=float))),
                           result.label_columns)
    if was_leader == now_leader:
        return []

    return [Insight(
        kind="rank_shift",
        text=f"The lead changed hands: {was_leader} led in {first}, "
             f"{now_leader} leads in {last}.",
    )]


#: Values that mean "electric" across the fuel and class vocabularies.
#: Non-capturing, or pandas warns that `str.contains` is discarding the groups.
_ELECTRIC = re.compile(r"(?:\belectric|e-?rickshaw|pure ev|\bev\b|bov)", re.IGNORECASE)

#: Only dimensions describing the *vehicle* carry a fuel story. A maker called
#: "OLA ELECTRIC" is a company, not a powertrain -- matching it reported
#: "electric fell from 2.4% to 1.3%" when that was one firm losing share.
_POWERTRAIN_DIMENSIONS = {"FUEL", "CLASS"}


def _electrification(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """The transition story, when the breakdown happens to carry it.

    Domain-aware on purpose: for registration data "how fast is electric
    growing" is the question, and it is invisible in a generic share table.
    """
    periods = _periods(result, frame)
    if periods is None or not result.label_columns:
        return []

    column = result.label_columns[0]
    if column not in frame.columns or column.upper() not in _POWERTRAIN_DIMENSIONS:
        return []

    electric = frame[frame[column].astype(str).str.contains(_ELECTRIC, na=False)]
    if electric.empty or len(electric) == len(frame):
        return []

    first, last = periods
    start_total = float(_numeric(frame, first).sum())
    end_total = float(_numeric(frame, last).sum())
    if not start_total or not end_total:
        return []

    before = float(_numeric(electric, first).sum()) / start_total * 100.0
    after = float(_numeric(electric, last).sum()) / end_total * 100.0
    if abs(after - before) < 0.1:
        return []

    direction = "rose" if after > before else "fell"
    return [Insight(
        kind="electrification",
        text=f"Electric {direction} from {_pct(before)} of registrations in {first} "
             f"to {_pct(after)} in {last} ({after - before:+.1f} points).",
        values=[before, after, abs(after - before), round(abs(after - before), 1)],
    )]


def _share_shift(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """Whether the leader is actually gaining ground, or just growing with it."""
    periods = _periods(result, frame)
    if periods is None or len(frame) < 2:
        return []

    first, last = periods
    start_total = float(_numeric(frame, first).sum())
    end_total = float(_numeric(frame, last).sum())
    if not start_total or not end_total:
        return []

    leader = _label_of(frame, 0, result.label_columns)
    before = float(_numeric(frame, first).iloc[0]) / start_total * 100.0
    after = float(_numeric(frame, last).iloc[0]) / end_total * 100.0
    move = after - before
    if abs(move) < 0.1:
        return []

    direction = "gained" if move > 0 else "lost"
    return [Insight(
        kind="share_shift",
        text=f"{leader} {direction} share: {_pct(before)} of the total in {first} "
             f"to {_pct(after)} in {last} ({move:+.1f} points).",
        values=[before, after, abs(move), round(abs(move), 1)],
    )]


def _concentration_shift(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """Is the market consolidating or opening up?"""
    periods = _periods(result, frame)
    if periods is None or len(frame) <= CONCENTRATION_N:
        return []

    first, last = periods
    start_total = float(_numeric(frame, first).sum())
    end_total = float(_numeric(frame, last).sum())
    if not start_total or not end_total:
        return []

    # Ranked on the closing period, so "the top 3" means the same three in both.
    top = frame.nlargest(CONCENTRATION_N, last)
    before = float(_numeric(top, first).sum()) / start_total * 100.0
    after = float(_numeric(top, last).sum()) / end_total * 100.0
    move = after - before
    if abs(move) < 0.5:
        return []

    direction = "consolidating" if move > 0 else "opening up"
    return [Insight(
        kind="concentration_shift",
        text=f"The market is {direction}: the top {CONCENTRATION_N} held "
             f"{_pct(before)} in {first} and {_pct(after)} in {last} "
             f"({move:+.1f} points).",
        values=[before, after, abs(move), round(abs(move), 1)],
    )]


def _share_and_concentration(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    column = _volume_column(result)
    if column is None or len(frame) < 2:
        return []

    values = _numeric(frame, column)
    total = float(values.sum())
    if total <= 0:
        return []

    insights: List[Insight] = []
    top = float(values.iloc[0])
    leader = _label_of(frame, 0, result.label_columns)
    share = top / total * 100.0

    # Only when the table really is percent-ranked. Sorted by size, row 0 is the
    # biggest, not the fastest, and "tops the growth ranking" was a lie.
    if result.spec.derive == "growth_pct" and result.spec.sort_by in ("auto", "growth"):
        # Ranked by percent, so the leader is the fastest grower -- whether it is
        # big enough to matter is the useful thing to say about it.
        insights.append(Insight(
            kind="share",
            text=f"{leader} tops the growth ranking but is only {_pct(share)} of the "
                 f"{_fmt(total)} total, so the change is fast rather than large.",
            values=[share, total, top],
        ))
    elif share >= DOMINANCE_SHARE:
        insights.append(Insight(
            kind="dominance",
            text=f"{leader} alone accounts for {_pct(share)} of the {_fmt(total)} total.",
            values=[share, total, top],
        ))
    else:
        insights.append(Insight(
            kind="share",
            text=f"{leader} is {_pct(share)} of the {_fmt(total)} total across "
                 f"{len(frame):,} {'group' if len(frame) == 1 else 'groups'}.",
            values=[share, total, top, float(len(frame))],
        ))

    if len(frame) > CONCENTRATION_N:
        top_n = float(values.head(CONCENTRATION_N).sum()) / total * 100.0
        insights.append(Insight(
            kind="concentration",
            text=f"The top {CONCENTRATION_N} hold {_pct(top_n)} of the total; "
                 f"the remaining {len(frame) - CONCENTRATION_N:,} share the rest.",
            values=[top_n, float(len(frame) - CONCENTRATION_N)],
        ))
    return insights


def _leader_gap(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    column = _headline_column(result)
    if column is None or len(frame) < 2:
        return []

    values = _numeric(frame, column)
    first, second = float(values.iloc[0]), float(values.iloc[1])
    if np.isnan(first) or np.isnan(second) or second == 0:
        return []

    gap = abs(first - second)
    gap_pct = gap / abs(second) * 100.0
    leader = _label_of(frame, 0, result.label_columns)
    runner = _label_of(frame, 1, result.label_columns)

    # A difference between two percentages is measured in points, not in a
    # bare number: "leads by 18.28" reads as units and means nothing.
    if str(column).endswith("_%"):
        return [Insight(
            kind="gap",
            text=f"{leader} leads {runner} by {gap:,.1f} percentage points.",
            values=[gap, round(gap, 1)],
        )]

    if gap_pct < NOTABLE_GAP_PCT:
        return [Insight(
            kind="close_race",
            text=f"{leader} and {runner} are close - only {_pct(gap_pct)} apart.",
            values=[gap_pct],
        )]
    return [Insight(
        kind="gap",
        text=f"{leader} leads {runner} by {_fmt(gap)} ({_pct(gap_pct)}).",
        values=[gap, gap_pct],
    )]


def _outlier(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    column = _volume_column(result)
    if column is None or len(frame) < 5:
        return []

    values = _numeric(frame, column).dropna()
    median = float(values.median())
    top = float(values.iloc[0])
    if median <= 0 or top < median * OUTLIER_MULTIPLE:
        return []

    leader = _label_of(frame, 0, result.label_columns)
    multiple = top / median
    return [Insight(
        kind="outlier",
        text=f"{leader} is {multiple:,.1f}x the median of {_fmt(median)}, "
             f"so the distribution is heavily skewed.",
        values=[median, multiple],
    )]


def _coverage(result: AnalysisResult, frame: pd.DataFrame) -> List[Insight]:
    """Whether the rows shown actually represent the whole."""
    column = _volume_column(result)
    shown = len(result.frame)
    if column is None or shown >= len(frame):
        return []

    values = _numeric(frame, column)
    total = float(values.sum())
    if total <= 0:
        return []

    covered = float(values.head(shown).sum()) / total * 100.0
    return [Insight(
        kind="coverage",
        text=f"The {shown} rows shown cover {_pct(covered)} of the total; "
             f"{len(frame) - shown:,} more are not displayed.",
        values=[covered, float(len(frame) - shown)],
    )]


def insight_numbers(insights: Sequence[Insight]) -> Set[float]:
    """Values the narrative may quote because they were computed, not invented."""
    allowed: Set[float] = set()
    for insight in insights:
        for value in insight.values:
            if isinstance(value, (int, float)) and not np.isnan(float(value)):
                allowed.add(float(value))
                allowed.add(round(float(value), 1))
                # Both roundings: writing 88.5% as "88%" is a fair paraphrase,
                # and rejecting it cost a whole interpretation.
                allowed.add(float(round(value)))
                allowed.add(float(int(value)))
    return allowed


# --- follow-up questions ----------------------------------------------------


def build_followups(result: AnalysisResult, entry, catalog=None) -> List[FollowUp]:
    """Questions this system can actually answer, phrased for a person to click."""
    frame = result.frame
    if frame is None or frame.empty:
        return []

    spec = result.spec
    profile = entry.profile
    followups: List[FollowUp] = []
    grouped = set(spec.group_by)
    filtered = {f.column for f in spec.filters}

    top_label = _label_of(frame, 0, result.label_columns) if result.label_columns else ""

    # Carry the whole scope into the wording, so a suggestion stands on its own:
    # "How did city registrations change year on year?" lost Haryana entirely.
    category = next((f.value for f in spec.filters if f.column.upper() == "CATEGORY"), "")
    places = [f.value for f in spec.filters
              if f.column.upper() in ("CITY", "STATE") and f.op == "=="]
    pieces = ([category.lower()] if category else []) + places
    scope = f" for {' in '.join(pieces)}" if pieces else ""

    def scoped(text: str) -> str:
        """Append the scope unless the sentence already names it."""
        lowered = text.lower()
        if all(piece.lower() in lowered for piece in pieces):
            return text
        return text[:-1] + scope + text[-1:] if text.endswith("?") else text + scope

    temporal = next((c.name for c in profile.columns if c.kind == "temporal"), None)
    #: None for a single-figure answer, which has no dimension to talk about.
    dimension = result.label_columns[0].lower() if result.label_columns else None

    # A single total invites the breakdowns it is hiding.
    if dimension is None:
        for column in profile.dimensions:
            if column.name == temporal or column.name in {f.column for f in spec.filters}:
                continue
            followups.append(FollowUp(
                question=scoped(f"Break that down by {column.name.lower()}."),
                reason=f"splits the total across {column.name}",
            ))
        if temporal:
            followups.append(FollowUp(
                question=scoped("How did that total change from year to year?"),
                reason="adds the time dimension this answer has none of",
            ))
        return _unique(followups)[: settings.max_followups]

    # A ranking with no time dimension begs the trend question.
    if spec.derive == "none" and spec.pivot_on in ("none", None) and temporal:
        followups.append(FollowUp(
            question=scoped(f"How did {dimension} registrations change from year to year?"),
            reason="adds the time dimension this answer has none of",
        ))

    # A percent ranking hides who actually moved the numbers.
    if spec.derive == "growth_pct":
        followups.append(FollowUp(
            question=scoped(f"Which {dimension} added the most registrations in absolute numbers?"),
            reason="percent growth favours small bases; volume shows real impact",
        ))

    # Drill from the leader into another dimension.
    other = next(
        (c.name for c in profile.dimensions
         if c.name not in grouped and c.name not in filtered
         and c.name != temporal and c.name.upper() not in ("CATEGORY", "RTO")),
        None,
    )
    if other and top_label:
        followups.append(FollowUp(
            question=scoped(f"What is the {other.lower()} breakdown for {top_label}?"),
            reason=f"drills into the leader by {other}",
        ))

    # Widen a narrow filter back out. Offered before the generic suggestions:
    # a result scoped to one place is meaningless without its peers.
    place = next((f for f in spec.filters if f.column.upper() in ("CITY", "STATE")), None)
    if place:
        followups.append(FollowUp(
            question=f"How does {place.value} compare with the other {_plural(place.column)}?",
            reason="puts the filtered result in context",
        ))

    # Turn a count into a proportion.
    if spec.derive == "none" and spec.group_by:
        followups.append(FollowUp(
            question=scoped(f"What share of the total does each {dimension} hold?"),
            reason="converts volumes into a mix",
        ))

    return _unique(followups)[: settings.max_followups]


def _unique(followups: List[FollowUp]) -> List[FollowUp]:
    seen, unique = set(), []
    for item in followups:
        if item.question not in seen:
            seen.add(item.question)
            unique.append(item)
    return unique
