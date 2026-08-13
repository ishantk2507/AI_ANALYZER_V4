"""Narrative verification.

Narration is the one stage where the model writes free text, so constrained
decoding cannot help. Everything it produces is checked against the computed
table afterwards. Three failure modes, all observed from the real 3B model:

1. **Invented numbers** - "970,931 - 46,514 = 924,417", where 46,514 appears
   nowhere in the data.
2. **Denying a non-empty result** - "No two-wheeler makers in the provided table
   match" printed directly above five matching rows.
3. **Misattributing the leader** - "Honda is the top maker in Delhi" above a
   table led by Hero MotoCorp. Every number correct; only the subject wrong.

Each check is deliberately conservative: a false positive costs a rewrite and a
duller answer, so they only fire on unambiguous contradictions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set

import numpy as np
import pandas as pd

from app.logging_setup import get_logger

log = get_logger(__name__)

#: 1,234 · 1234.5 · 12.3% · -4%
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?\s*%?")

#: Below this, a bare integer is almost certainly an ordinal or a count.
MAGNITUDE_FLOOR = 1000.0
#: Relative slack, so "1,270" may legitimately be written as "1.27 thousand".
TOLERANCE = 0.005


def _to_float(text: str) -> float | None:
    cleaned = text.replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def supported_numbers(frame: pd.DataFrame) -> Set[float]:
    """Every number a faithful narrative is allowed to use."""
    allowed: Set[float] = set()

    for column in frame.columns:
        # Column headers are often periods ("2024") and get quoted as such.
        header = _to_float(str(column))
        if header is not None:
            allowed.add(header)

        series = frame[column]
        numeric = pd.to_numeric(series, errors="coerce")
        for value in numeric.dropna().tolist():
            allowed.add(float(value))
            allowed.add(round(float(value), 1))
            allowed.add(float(round(value)))

        if not pd.api.types.is_numeric_dtype(series):
            for value in series.astype(str).tolist():
                for token in _NUMBER.findall(value):
                    parsed = _to_float(token)
                    if parsed is not None:
                        allowed.add(parsed)

    # Totals and row counts are fair game to state.
    numeric_only = frame.select_dtypes(include=[np.number])
    for column in numeric_only.columns:
        allowed.add(float(numeric_only[column].sum()))
    allowed.add(float(len(frame)))
    return allowed


def unsupported_numbers(
    narrative: str, frame: pd.DataFrame, extra: Optional[Set[float]] = None
) -> List[str]:
    """Numbers in the prose that appear neither in the table nor in the findings.

    `extra` carries values computed by the insight layer -- shares, gaps, market
    totals. They are as trustworthy as the table (same pandas, no model), so the
    narrative is allowed to quote them.
    """
    if not narrative or frame is None or frame.empty:
        return []

    allowed = supported_numbers(frame)
    if extra:
        allowed |= set(extra)
    flagged: List[str] = []

    for token in _NUMBER.findall(narrative):
        value = _to_float(token)
        if value is None:
            continue

        is_percentage = "%" in token
        if not is_percentage and abs(value) < MAGNITUDE_FLOOR:
            continue

        if any(_close(value, candidate) for candidate in allowed):
            continue

        cleaned = token.strip()
        if cleaned not in flagged:
            flagged.append(cleaned)

    return flagged


#: Phrases that deny the table can answer at all. Enumerated rather than matched
#: by proximity: "the results show growth was not uniform" is a legitimate
#: sentence that any negation-near-a-noun heuristic would flag.
_DENIAL = re.compile(
    r"\b("
    r"no (?:rows?|data|results?|records?|matches|entries)"
    r"|(?:does|do|did) not (?:contain|include|have|show|list)"
    r"|cannot be (?:determined|answered|computed|found)"
    r"|unable to (?:determine|answer|find)"
    r"|not (?:listed|shown|available|present|included) in the (?:provided |given )?(?:table|data)"
    r"|(?:no|none) (?:of the )?[a-z\- ]{0,30}?(?:in|match(?:es)?) the "
    r"(?:provided |given )?(?:table|data|results?)"
    r")",
    re.IGNORECASE,
)


def contradicts_result(narrative: str, frame: pd.DataFrame) -> bool:
    """True when the prose claims there is nothing to report but there is.

    Seen with the real model: "No two-wheeler makers in the provided table match
    the filters for Delhi" printed directly above five Delhi makers. No invented
    numbers, so the numeric check cannot see it.
    """
    if not narrative or frame is None or frame.empty:
        return False
    return bool(_DENIAL.search(narrative))


#: Claims that the named entity is the answer to the ranking.
_SUPERLATIVE = re.compile(
    r"\b(top|leads?|leading|led|highest|most|largest|biggest|best|fastest|"
    r"lowest|least|smallest|worst|slowest|number one|#1|first)\b",
    re.IGNORECASE,
)

#: Words shared by half the companies in any registry, so useless for telling
#: one row's label from another's.
_LABEL_STOPWORDS = {
    "ltd", "limited", "pvt", "private", "llp", "inc", "corp", "co", "company",
    "companies", "india", "indian", "motor", "motors", "auto", "autos",
    "automobile", "automobiles", "vehicle", "vehicles", "the", "and", "of",
    "importer", "technology", "technologies", "industries", "international",
    "electric", "mobility", "group", "sa", "ag", "gmbh", "rto", "dto", "srto",
}


_PUNCT_TOKENS = re.compile(r"[a-z0-9]+")


def _distinctive_tokens(label: str) -> Set[str]:
    tokens = {t for t in _PUNCT_TOKENS.findall(str(label).lower()) if len(t) >= 3}
    return tokens - _LABEL_STOPWORDS


def _first_sentence(text: str) -> str:
    for chunk in re.split(r"(?<=[.!?])\s+|\n", text.strip()):
        if chunk.strip():
            return chunk.strip()
    return ""


def misattributes_leader(narrative: str, frame: pd.DataFrame, label_columns) -> bool:
    """True when the headline names an entity that is not the top row.

    Seen with the real model: "Honda is the top two-wheeler maker in Delhi"
    written directly above a table led by Hero MotoCorp. Every number in the
    answer was correct, so the numeric check passed it.

    Deliberately conservative -- it only fires when the opening sentence makes a
    superlative claim and mentions exactly one row's entity.
    """
    if not narrative or frame is None or frame.empty or not label_columns:
        return False
    labels = [c for c in label_columns if c in frame.columns]
    if not labels or len(frame) < 2:
        return False

    sentence = _first_sentence(narrative)
    if not sentence or not _SUPERLATIVE.search(sentence):
        return False

    words = set(_PUNCT_TOKENS.findall(sentence.lower()))
    matched = {
        index
        for index in range(len(frame))
        for column in labels
        if _distinctive_tokens(frame.iloc[index][column]) & words
    }
    # Ambiguous sentences ("Honda leads Kerala while Hero leads Delhi") are left
    # alone; only an unambiguous wrong attribution is worth overriding.
    return len(matched) == 1 and 0 not in matched


def _close(value: float, candidate: float) -> bool:
    if value == candidate:
        return True
    scale = max(abs(value), abs(candidate), 1.0)
    return abs(value - candidate) / scale <= TOLERANCE


#: A bare section header echoed from the prompt's own scaffolding. "CAUTION: x"
#: is kept -- it carries the caveat; "CAUTION:" alone is noise.
_BARE_HEADER = re.compile(
    r"^\s*[-•*]?\s*(findings|table|caution|answer|result|summary|note)\s*:?\s*$",
    re.IGNORECASE,
)


def tidy_narrative(text: str) -> str:
    """Drop section headers the model copied out of its instructions.

    The prompt forbids them and the model mostly complies, but a 3B will still
    emit a lone "FINDINGS:" line. Deleting it is more reliable than asking again.
    """
    if not text:
        return text
    kept = [line for line in text.splitlines() if not _BARE_HEADER.match(line)]
    # Collapse the blank line a removed header leaves behind.
    out, previous_blank = [], False
    for line in kept:
        blank = not line.strip()
        if not (blank and previous_blank):
            out.append(line)
        previous_blank = blank
    return "\n".join(out).strip()


@dataclass
class NarrativeIssue:
    """A reason the generated prose contradicts the computed table."""

    kind: str  # "invented_numbers" | "denied_results" | "wrong_leader"
    note: str  # shown to the user if the rewrite also fails
    hint: str  # fed back to the model on the retry

    def __str__(self) -> str:  # pragma: no cover - logging convenience
        return f"{self.kind}: {self.note}"


def check_narrative(
    narrative: str,
    frame: pd.DataFrame,
    label_columns: Sequence[str],
    extra_numbers: Optional[Set[float]] = None,
) -> Optional[NarrativeIssue]:
    """Return the first contradiction found, or None if the prose is faithful."""
    if not narrative or frame is None or frame.empty:
        return None

    if contradicts_result(narrative, frame):
        return NarrativeIssue(
            kind="denied_results",
            note=(
                f"the model reported no results while the table had {len(frame):,}; "
                "wrote the summary directly from the table instead"
            ),
            hint=(
                f"Your previous answer said there were no results, but the table above "
                f"has {len(frame):,} rows. Answer from those rows."
            ),
        )

    flagged = unsupported_numbers(narrative, frame, extra_numbers)
    if flagged:
        return NarrativeIssue(
            kind="invented_numbers",
            note=(
                f"the model invented numbers not present in the results "
                f"({', '.join(flagged)}); wrote the summary directly from the table instead"
            ),
            hint=(
                "Your previous answer used numbers that are NOT in the table: "
                + ", ".join(flagged)
                + ". Rewrite it using only numbers copied from the table above."
            ),
        )

    if misattributes_leader(narrative, frame, label_columns):
        leader = " / ".join(
            str(frame.iloc[0][c]) for c in label_columns if c in frame.columns
        )
        return NarrativeIssue(
            kind="wrong_leader",
            note=(
                f"the model named the wrong leader; the top row is '{leader}'. "
                "Wrote the summary directly from the table instead"
            ),
            hint=(
                f"Your previous answer named the wrong leader. The first row of the "
                f"table is '{leader}' -- that is the answer. Rewrite it."
            ),
        )

    return None


def _headline_column(result, values: List[str]) -> str:
    """The column the table is actually ordered by.

    Taking the last value column said "Nagar Lucknow leads on change (4,341)"
    about a table sorted by size, where Prayagraj led on change by four times as
    much. The headline has to match the ranking.
    """
    spec = getattr(result, "spec", None)
    sort_by = getattr(spec, "sort_by", "auto")
    derive = getattr(spec, "derive", "none")

    raw = [v for v in values if v not in ("growth_%", "share_%", "change")]

    if sort_by == "growth" or (sort_by == "auto" and derive == "growth_pct"):
        return "growth_%" if "growth_%" in values else (raw[-1] if raw else values[-1])
    if sort_by == "change":
        return "change" if "change" in values else (raw[-1] if raw else values[-1])
    if sort_by == "auto" and derive == "share_pct" and "share_%" in values:
        return "share_%"
    return raw[-1] if raw else values[-1]


def deterministic_summary(result, entry) -> str:
    """A factual answer written without the model.

    Used when narration keeps inventing numbers. Duller than the model's prose,
    but every figure is read straight off the table.
    """
    frame = result.frame
    if frame.empty:
        return "No rows matched that request."

    labels = result.label_columns
    values = [c for c in result.value_columns if c in frame.columns]
    if not values:
        return f"{len(frame):,} rows returned from {entry.key}."

    headline = _headline_column(result, values)
    top = frame.iloc[0]
    if labels:
        subject = " / ".join(str(top[c]) for c in labels)
    else:
        # A single total has no dimension to name, so the scope is the subject:
        # "Tamil Nadu: TOTAL 4,001,373", not "MAKER: TOTAL 4,001,373".
        scope = [str(f.value) for f in getattr(result.spec, "filters", []) if f.value]
        subject = ", ".join(scope) if scope else "Total"

    # One row is an answer, not a ranking: "leads ... out of 1 rows" is nonsense.
    if len(frame) == 1:
        values = ", ".join(f"{column} {_fmt(top[column])}" for column in values)
        return f"**{subject}**: {values} (from `{entry.key}`)."

    # The table is sorted the way the question asked, so "leads" is wrong when
    # the question asked for the smallest values.
    verb = "leads on" if getattr(result.spec, "sort_desc", True) else "is lowest on"
    lines = [
        f"**{subject}** {verb} {headline} "
        f"({_fmt(top[headline])}), out of {len(frame):,} rows from `{entry.key}`."
    ]

    for _, record in frame.head(3).iterrows():
        name = " / ".join(str(record[c]) for c in labels) if labels else headline
        pieces = [f"{column}: {_fmt(record[column])}" for column in values]
        lines.append(f"- {name}: " + ", ".join(pieces))

    return "\n".join(lines)


def _fmt(value) -> str:
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        if np.isnan(value):
            return "n/a"
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return str(value)
