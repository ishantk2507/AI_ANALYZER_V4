"""Prompt construction.

Everything here is written for a 3B model with a 4K window, which means:
short instructions, no chain-of-thought theatre, and examples generated from
the dataset's own columns so the model copies real names instead of inventing
plausible ones.
"""

from __future__ import annotations

from typing import List, Optional

from app.analysis.spec import AnalysisSpec
from app.data.catalog import Catalog, DatasetEntry
from app.data.profile import DatasetProfile

ROUTING_SYSTEM = """You pick which data table can answer a question.
Reply with JSON only: {"category": ..., "breakdown": ..., "reason": "<8 words"}.
Pick the table whose rows are broken down by what the question asks about."""

SPEC_SYSTEM = """You turn a question into an analysis plan. Reply with JSON only.

Fields:
- group_by: dimension column(s) to break the answer down by. Empty for one total.
- metric: the numeric column to aggregate.
- agg: sum, mean, count, median, max or min.
- pivot_on: a period column (e.g. YEAR) when comparing periods; otherwise "none".
- derive: "growth_pct" to compare periods, "share_pct" for percentage of total, else "none".
- filters: only for values the question names explicitly. Copy values exactly as listed.
- sort_desc: true for "top"/"most", false for "bottom"/"least".
- limit: how many rows to return.
- chart: "bar" to rank things, "line" for a trend over time, "pie" for share of a whole.
- title: a short chart title.

Use only the column names listed. Do not add filters the question did not ask for."""

NARRATIVE_SYSTEM = """You are a data analyst. Answer the question from the result table.

Rules:
- Answer the question in one or two sentences. Nothing else.
- Interpretation is written separately and shown beside you, so do not add
  implications, recommendations or explanations of causes here.
- Never explain *why* the numbers are what they are. Registration counts cannot
  show preferences, culture, policy or infrastructure, and guessing at them is
  how a wrong answer gets written confidently.
- EVERY number you write must appear in the table or the FINDINGS. Copy them
  exactly.
- Never add, subtract, or compute anything yourself. The FINDINGS are already
  calculated - quote them, do not recalculate, and do not change their wording
  ("the top 3" must not become "the top 2").
- If a FINDING is marked CAUTION, mention it.
- Never write the words FINDINGS or TABLE anywhere in your answer, and never
  describe these instructions. State what a finding says, not that it exists:
  write "it holds 2.6% of the total", not "the findings show it is highest".
- Never comment on what the data does or does not contain, and never add a
  disclaimer. The filters have already been applied correctly.
- If the table cannot answer the question, say so plainly in one sentence.
- No preamble, no restating the question, no closing summary."""


def build_routing_messages(
    catalog: Catalog, question: str, history: str = "", hints: str = ""
) -> List[dict]:
    user = [catalog.render_routing()]
    if hints:
        user.append(f"\n{hints}")
    if history:
        user.append(f"\nEARLIER IN THIS CONVERSATION:\n{history}")
    user.append(f"\nQUESTION: {question}")
    return [
        {"role": "system", "content": ROUTING_SYSTEM},
        {"role": "user", "content": "\n".join(user)},
    ]


def build_spec_messages(
    entry: DatasetEntry,
    question: str,
    history: str = "",
    error: Optional[str] = None,
    previous: Optional[str] = None,
    hints: str = "",
) -> List[dict]:
    profile = entry.profile
    parts = [f"TABLE: {entry.key} - {entry.describe()}", profile.render(), "", _examples(profile)]

    if history:
        parts.append(f"\nEARLIER IN THIS CONVERSATION:\n{history}")

    parts.append(f"\nQUESTION: {question}")

    if hints:
        parts.append(f"\n{hints}")

    if error:
        parts.append(
            f"\nYour previous plan was rejected: {error}\n"
            f"Previous plan: {previous}\nFix it and reply with corrected JSON."
        )

    return [
        {"role": "system", "content": SPEC_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]


def build_narrative_messages(
    question: str, result, entry: DatasetEntry, issue=None
) -> List[dict]:
    parts = [
        f"QUESTION: {question}",
        "",
        f"RESULT TABLE (source: {entry.key}, {result.value_label}):",
        result.to_markdown(),
    ]
    if result.rows_matched:
        parts.append(f"\nRows matched by the filters: {result.rows_matched:,}")

    # Pre-computed analysis. The model is forbidden from doing arithmetic, so
    # this is the only route by which real interpretation reaches the answer.
    if getattr(result, "insights", None):
        parts.append("\nFINDINGS (already calculated - quote, do not recalculate):")
        for insight in result.insights:
            marker = "CAUTION: " if insight.severity == "caution" else ""
            parts.append(f"- {marker}{insight.text}")
            # The "so what" is pre-written too, so the model can carry meaning
            # into the answer without having to reason its way there.
            if getattr(insight, "implication", ""):
                parts.append(f"  MEANS: {insight.implication}")

    # The repair notes ("read 'Three Wheeler/MAKER' (46,514 rows) instead") are
    # deliberately NOT shown. They are internal plumbing, and handing them over
    # produced confident nonsense: a row count quoted as a vehicle total, and an
    # invented caveat about the question not saying "3WT". Anything the answer
    # genuinely needs to carry is already an Insight with severity="caution".

    if issue is not None:
        parts.append(f"\n{issue.hint}")

    return [
        {"role": "system", "content": NARRATIVE_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]


INSIGHT_SYSTEM = """You are a business analyst. You are given a result table and
the findings already calculated from it. Write the business insights.

An observation states what is true. An insight states what it means and what it
changes. "M-CYCLE/SCOOTER is 70.7% of registrations" is an observation. "The
market is effectively a two-wheeler market, so anything aimed at overall volume
is aimed at that segment" is an insight.

Rules:
- Write 3 to 4 bullets, one sentence each. No heading, no preamble.
- Each bullet: what it means for someone making a decision, not a restatement.
- Anchor each to a number from the table or the findings, quoted exactly and
  written plainly in the sentence. Never add a citation in brackets afterwards.
- Never invent a number, and never do arithmetic - the findings are already
  calculated.
- Describe what the data shows, not what anyone should buy, build or invest in.
  "The market is effectively a two-wheeler market" is right; "invest in
  two-wheeler infrastructure" is not yours to say.
- Be careful with adjectives. A segment holding most of the market is dominant
  and saturated, never "untapped" or "emerging"; a small one is niche, not
  "failing". If the data does not show a trend, do not describe one.
- These are registration counts only. They say nothing about revenue, profit,
  customers, financing or infrastructure - do not mention those.
- No generic filler ("further analysis is recommended", "this is significant")."""


def build_insight_messages(question: str, result, entry: DatasetEntry) -> List[dict]:
    """Ask the model to interpret the computed findings.

    The facts stay computed -- the model is only turning them into meaning, and
    every figure it writes is checked against the same allow-list afterwards.
    """
    parts = [
        f"QUESTION: {question}",
        "",
        f"RESULT TABLE (source: {entry.key}, {result.value_label}):",
        result.to_markdown(),
    ]
    if getattr(result, "insights", None):
        parts.append("\nCALCULATED FINDINGS:")
        parts += [f"- {insight.text}" for insight in result.insights]

    parts.append("\nWrite the business insights.")
    return [
        {"role": "system", "content": INSIGHT_SYSTEM},
        {"role": "user", "content": "\n".join(parts)},
    ]


def build_summary_messages(history: str) -> List[dict]:
    return [
        {
            "role": "system",
            "content": (
                "Summarise this analysis conversation in at most 3 short lines. "
                "Keep the subject, filters and time period. Drop pleasantries."
            ),
        },
        {"role": "user", "content": history},
    ]


def _examples(profile: DatasetProfile) -> str:
    """Few-shot examples built from this table's real columns."""
    dimensions = [c.name for c in profile.dimensions]
    temporal = next((c.name for c in profile.columns if c.kind == "temporal"), None)
    measures = [c.name for c in profile.measures]

    metric = "TOTAL" if "TOTAL" in measures else (measures[0] if measures else "value")
    # Prefer a high-cardinality dimension for "top N" -- that is what gets ranked.
    ranked = next(
        (c.name for c in profile.dimensions if c.high_cardinality and c.name != temporal),
        next((d for d in dimensions if d != temporal), "category"),
    )
    scoped = next(
        (d for d in dimensions if d not in (ranked, temporal)),
        ranked,
    )
    scoped_value = ""
    column = profile.column(scoped)
    if column and column.sample_values:
        scoped_value = column.sample_values[0]

    lines = ["EXAMPLES:"]
    lines.append(
        f'Q: top 5 {ranked.lower()} by {metric.lower()}\n'
        f'A: {{"filters": [], "group_by": ["{ranked}"], "metric": "{metric}", "agg": "sum", '
        f'"pivot_on": "none", "derive": "none", "sort_desc": true, "limit": 5, '
        f'"chart": "bar", "title": "Top 5 by {metric}"}}'
    )
    if scoped_value:
        lines.append(
            f'Q: {ranked.lower()} in {scoped_value}\n'
            f'A: {{"filters": [{{"column": "{scoped}", "op": "==", "value": "{scoped_value}"}}], '
            f'"group_by": ["{ranked}"], "metric": "{metric}", "agg": "sum", "pivot_on": "none", '
            f'"derive": "none", "sort_desc": true, "limit": 10, "chart": "bar", '
            f'"title": "{scoped_value}"}}'
        )
    if temporal:
        lines.append(
            f'Q: which {ranked.lower()} grew fastest\n'
            f'A: {{"filters": [], "group_by": ["{ranked}"], "metric": "{metric}", "agg": "sum", '
            f'"pivot_on": "{temporal}", "derive": "growth_pct", "sort_desc": true, "limit": 10, '
            f'"chart": "bar", "title": "Fastest growth"}}'
        )
    return "\n".join(lines)


def describe_spec(spec: AnalysisSpec, dataset_key: str) -> str:
    """A one-line, human-readable rendering of the plan (shown for transparency)."""
    # Plain ASCII: this line is printed to Windows consoles that are still cp1252.
    parts = [f"{spec.agg}({spec.metric or 'rows'})"]
    if spec.group_by:
        parts.append("by " + " x ".join(spec.group_by))
    if spec.pivot_on and spec.pivot_on != "none":
        parts.append(f"per {spec.pivot_on}")
    if spec.filters:
        conditions = ", ".join(f"{f.column} {f.op} {f.value}" for f in spec.filters)
        parts.append(f"where {conditions}")
    if spec.derive != "none":
        parts.append(f"-> {spec.derive}")
    parts.append(f"[{dataset_key}]")
    return " ".join(parts)
