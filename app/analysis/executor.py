"""Deterministic execution of an AnalysisSpec.

Nothing here is generated or evaluated at runtime -- the spec selects among
fixed pandas operations. Every repair the executor makes (a fuzzy column match,
a substituted filter value, an auto-added pivot) is recorded in `notes` and
surfaced to the user, so a silently-wrong answer is hard to produce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

import numpy as np
import pandas as pd

from app.analysis.spec import NONE, AnalysisSpec
from app.config import settings
from app.data.catalog import DatasetEntry
from app.data.matching import distinct_values, resolve_column, resolve_many, resolve_value
from app.logging_setup import get_logger

log = get_logger(__name__)

GROWTH_COLUMN = "growth_%"
SHARE_COLUMN = "share_%"
CHANGE_COLUMN = "change"


@dataclass
class AnalysisResult:
    frame: pd.DataFrame
    label_columns: List[str]
    value_columns: List[str]
    spec: AnalysisSpec
    dataset_key: str
    notes: List[str] = field(default_factory=list)
    rows_scanned: int = 0
    rows_matched: int = 0
    value_label: str = ""
    chart_path: Optional[Path] = None
    #: The whole ranking before `limit` was applied. Shares, concentration and
    #: "biggest absolute mover" are meaningless computed from the top 10 alone.
    full_frame: Optional[pd.DataFrame] = None
    insights: List["object"] = field(default_factory=list)
    followups: List["object"] = field(default_factory=list)

    @property
    def group_count(self) -> int:
        return len(self.full_frame) if self.full_frame is not None else len(self.frame)

    @property
    def is_empty(self) -> bool:
        return self.frame.empty

    def to_markdown(self, max_rows: Optional[int] = None) -> str:
        if self.frame.empty:
            return "(no rows matched)"
        limit = max_rows or settings.max_rows_in_prompt
        text = _markdown_table(self.frame.head(limit))
        if len(self.frame) > limit:
            text += f"\n({len(self.frame) - limit} more rows not shown)"
        return text


def _format_cell(value: Any) -> str:
    """Numbers the model can copy verbatim: separators in, float noise out."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "-"
    if isinstance(value, (bool, np.bool_)):
        return str(bool(value))
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"
    return str(value)


def _markdown_table(frame: pd.DataFrame) -> str:
    """A pipe table without pulling in `tabulate`."""
    headers = [str(c) for c in frame.columns]
    rows = [[_format_cell(v) for v in record] for record in frame.itertuples(index=False)]

    widths = [len(h) for h in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells):
        return "| " + " | ".join(c.ljust(widths[i]) for i, c in enumerate(cells)) + " |"

    out = [line(headers), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out += [line(row) for row in rows]
    return "\n".join(out)


def execute(entry: DatasetEntry, spec: AnalysisSpec) -> AnalysisResult:
    frame = entry.load()
    columns = [str(c) for c in frame.columns]
    notes: List[str] = []

    metric = _resolve_metric(spec, columns, frame, notes)
    group_by = _resolve_group_by(spec, columns, notes)
    pivot_on = _resolve_pivot(spec, columns, entry, group_by, notes)

    if pivot_on and pivot_on in group_by:
        group_by = [c for c in group_by if c != pivot_on]

    filtered = _apply_filters(frame, spec, columns, notes)
    rows_matched = len(filtered)

    if filtered.empty:
        return AnalysisResult(
            frame=pd.DataFrame(),
            label_columns=[],
            value_columns=[],
            spec=spec,
            dataset_key=entry.key,
            notes=notes + ["No rows matched the filters."],
            rows_scanned=len(frame),
            rows_matched=0,
        )

    value_name = "records" if spec.agg == "count" else metric
    result, label_columns, value_columns = _aggregate(
        filtered, spec, group_by, pivot_on, metric, value_name, notes
    )

    result, value_columns = _derive(result, spec, value_columns, notes)
    ranked = _sort(result, spec, label_columns, value_columns)
    result = ranked.head(spec.limit)

    return AnalysisResult(
        frame=result.reset_index(drop=True),
        full_frame=ranked.reset_index(drop=True),
        label_columns=label_columns,
        value_columns=value_columns,
        spec=spec,
        dataset_key=entry.key,
        notes=notes,
        rows_scanned=len(frame),
        rows_matched=rows_matched,
        value_label=_value_label(spec, value_name),
    )


# --- resolution -------------------------------------------------------------


def _resolve_metric(spec, columns, frame, notes) -> str:
    numeric = [c for c in columns if pd.api.types.is_numeric_dtype(frame[c])]

    if spec.metric and spec.metric in columns:
        return spec.metric

    if spec.metric:
        fixed = resolve_column(spec.metric, numeric or columns)
        if fixed:
            notes.append(f"metric '{spec.metric}' resolved to '{fixed}'")
            return fixed

    fallback = "TOTAL" if "TOTAL" in numeric else (numeric[0] if numeric else columns[0])
    if spec.agg != "count":
        notes.append(f"no usable metric given; used '{fallback}'")
    return fallback


def _resolve_group_by(spec, columns, notes) -> List[str]:
    resolved: List[str] = []
    for name in spec.group_by:
        if name in columns:
            resolved.append(name)
            continue
        fixed = resolve_column(name, columns)
        if fixed:
            notes.append(f"group column '{name}' resolved to '{fixed}'")
            resolved.append(fixed)
        else:
            notes.append(f"ignored unknown group column '{name}'")
    return resolved


def _resolve_pivot(spec, columns, entry, group_by, notes) -> Optional[str]:
    if spec.pivot_on and spec.pivot_on != NONE:
        if spec.pivot_on in columns:
            return spec.pivot_on
        fixed = resolve_column(spec.pivot_on, columns)
        if fixed:
            notes.append(f"pivot column '{spec.pivot_on}' resolved to '{fixed}'")
            return fixed
        notes.append(f"ignored unknown pivot column '{spec.pivot_on}'")
        return None

    # Growth needs two periods side by side. If the model asked for growth but
    # forgot the pivot, supply the dataset's temporal column.
    if spec.derive == "growth_pct":
        temporal = [c.name for c in entry.profile.columns if c.kind == "temporal"]
        candidate = next((c for c in temporal if c in columns and c not in group_by), None)
        if candidate:
            notes.append(f"growth needs periods side by side; pivoted on '{candidate}'")
            return candidate
    return None


def _apply_filters(frame: pd.DataFrame, spec, columns, notes) -> pd.DataFrame:
    if not spec.filters:
        return frame

    mask = pd.Series(True, index=frame.index)

    for item in spec.filters:
        column = item.column if item.column in columns else resolve_column(item.column, columns)
        if column is None:
            notes.append(f"ignored filter on unknown column '{item.column}'")
            continue

        series = frame[column]
        raw = str(item.value).strip()
        if raw == "":
            continue

        try:
            if item.op in (">", ">=", "<", "<="):
                condition = _numeric_filter(series, item.op, raw, notes, column)
            elif item.op == "contains":
                condition = series.astype(str).str.contains(raw, case=False, na=False)
            elif item.op == "in":
                condition = _membership_filter(series, raw, notes, column)
            else:  # == and !=
                condition = _equality_filter(series, item.op, raw, notes, column)
        except Exception as exc:  # pragma: no cover - defensive
            notes.append(f"filter on '{column}' failed ({exc}); ignored")
            continue

        if condition is None:
            continue
        mask &= condition

    filtered = frame[mask]
    if len(filtered) != len(frame):
        log.debug("Filters kept %d of %d rows", len(filtered), len(frame))
    return filtered


def _numeric_filter(series, op, raw, notes, column):
    numeric = pd.to_numeric(series, errors="coerce")
    try:
        threshold = float(raw)
    except ValueError:
        notes.append(f"filter '{column} {op} {raw}' is not numeric; ignored")
        return None
    return {
        ">": numeric > threshold,
        ">=": numeric >= threshold,
        "<": numeric < threshold,
        "<=": numeric <= threshold,
    }[op]


def _membership_filter(series, raw, notes, column):
    wanted = [part.strip() for part in raw.split(",") if part.strip()]
    if pd.api.types.is_numeric_dtype(series):
        numbers = [float(v) for v in wanted if _is_number(v)]
        return pd.to_numeric(series, errors="coerce").isin(numbers)

    options = distinct_values(series)
    resolved, missing = resolve_many(wanted, options)
    if missing:
        notes.append(f"no match in '{column}' for: {', '.join(missing)}")
    if not resolved:
        return None
    return series.astype(str).isin(resolved)


def _equality_filter(series, op, raw, notes, column):
    if pd.api.types.is_numeric_dtype(series) and _is_number(raw):
        numeric = pd.to_numeric(series, errors="coerce")
        condition = numeric == float(raw)
        return ~condition if op == "!=" else condition

    options = distinct_values(series)
    match = resolve_value(raw, options)
    if match is None:
        notes.append(f"'{raw}' not found in '{column}'; filter ignored")
        return None
    if match != raw:
        notes.append(f"matched '{raw}' to '{match}' in '{column}'")

    condition = series.astype(str) == match
    return ~condition if op == "!=" else condition


def _is_number(text: str) -> bool:
    try:
        float(text)
        return True
    except (TypeError, ValueError):
        return False


# --- aggregation ------------------------------------------------------------


def _aggregate(filtered, spec, group_by, pivot_on, metric, value_name, notes):
    agg = spec.agg

    # No dimensions at all: a single headline number.
    if not group_by and not pivot_on:
        value = _scalar(filtered, metric, agg)
        return pd.DataFrame({value_name: [value]}), [], [value_name]

    if pivot_on and not group_by:
        group_by = [pivot_on]
        pivot_on = None
        notes.append("nothing to pivot against; grouped by the period instead")

    if pivot_on:
        table = pd.pivot_table(
            filtered,
            index=group_by,
            columns=pivot_on,
            values=metric,
            aggfunc="size" if agg == "count" else agg,
            observed=True,
            fill_value=0,
        )
        table.columns = [str(c) for c in table.columns]
        value_columns = list(table.columns)
        table = table.reset_index()
        table.columns = [str(c) for c in table.columns]
        return table, [str(c) for c in group_by], value_columns

    # `observed=True` matters: category dtypes otherwise emit every unused
    # combination and blow the frame up.
    grouped = filtered.groupby(group_by, observed=True, dropna=False)
    series = grouped.size() if agg == "count" else grouped[metric].agg(agg)
    table = series.rename(value_name).reset_index()
    table.columns = [str(c) for c in table.columns]
    return table, [str(c) for c in group_by], [value_name]


def _scalar(filtered, metric, agg) -> Any:
    if agg == "count":
        return len(filtered)
    series = pd.to_numeric(filtered[metric], errors="coerce")
    return getattr(series, agg)()


def _derive(table, spec, value_columns, notes):
    if spec.derive == "growth_pct":
        if len(value_columns) < 2:
            notes.append("growth needs at least two periods; showing raw values")
            return table, value_columns

        ordered = _order_periods(value_columns)
        first, last = ordered[0], ordered[-1]
        base = pd.to_numeric(table[first], errors="coerce")
        head = pd.to_numeric(table[last], errors="coerce")

        growth = np.where(base > 0, (head - base) / base * 100.0, np.nan)
        table[GROWTH_COLUMN] = np.round(growth, 2)
        # Percent alone is not a finding: 5 -> 1,660 units is +33,100% and
        # immaterial, while +17% on 3.4M units is the actual story.
        table[CHANGE_COLUMN] = head - base
        notes.append(f"growth computed from {first} to {last}")

        table = _drop_base_effects(table, base, first, notes)
        return table, value_columns + [GROWTH_COLUMN, CHANGE_COLUMN]

    if spec.derive == "share_pct":
        if len(value_columns) != 1:
            notes.append("share needs a single value column; showing raw values")
            return table, value_columns
        column = value_columns[0]
        total = pd.to_numeric(table[column], errors="coerce").sum()
        if not total:
            notes.append("share is undefined when the total is zero")
            return table, value_columns
        table[SHARE_COLUMN] = np.round(
            pd.to_numeric(table[column], errors="coerce") / total * 100.0, 2
        )
        return table, value_columns + [SHARE_COLUMN]

    return table, value_columns


def _drop_base_effects(table, base, base_column: str, notes: List[str]):
    """Keep tiny-denominator rows out of a growth ranking.

    Sorting by percent change puts whoever sold one unit last year at the top.
    Every winner of "which makers grew fastest" had a 2024 base of 1-24 units,
    which is noise, not growth. Rows below the floor are excluded from the
    ranking and the exclusion is reported -- never silently dropped.
    """
    total = float(base.sum())
    if not total or table.empty:
        return table

    floor = max(settings.growth_min_base, total * settings.growth_min_base_share)
    keep = base >= floor

    # If the floor would gut the answer, the data is simply small: leave it be.
    if int(keep.sum()) < settings.growth_min_rows:
        return table

    dropped = int((~keep).sum())
    if dropped:
        notes.append(
            f"excluded {dropped} rows with a {base_column} base under "
            f"{floor:,.0f} - a large percent change on a tiny base is not growth"
        )
    return table[keep]


def _order_periods(value_columns: List[str]) -> List[str]:
    """Sort period columns numerically when possible ("2024" before "2025")."""
    if all(_is_number(c) for c in value_columns):
        return sorted(value_columns, key=float)
    return sorted(value_columns)


def _sort(table, spec, label_columns, value_columns):
    """Order the whole ranking. `limit` is applied by the caller, so the full
    ordering survives for share and concentration maths."""
    if table.empty:
        return table

    # A time series reads chronologically, not by magnitude -- but only when it
    # *is* one. A growth ranking drawn as a line still ranks by growth; sorting
    # it by label put Ambala on top of a Haryana growth table alphabetically.
    if spec.chart == "line" and label_columns and spec.derive == NONE:
        return table.sort_values(label_columns[0])

    def latest_value() -> Optional[str]:
        raw = [c for c in value_columns
               if c in table.columns and c not in (GROWTH_COLUMN, SHARE_COLUMN, CHANGE_COLUMN)]
        ordered = _order_periods(raw)
        return ordered[-1] if ordered else None

    if spec.sort_by == "value":
        # "growth in the cities with the highest registrations": rank by size,
        # still showing the growth columns.
        sort_column = latest_value()
    elif spec.sort_by == "growth":
        sort_column = GROWTH_COLUMN if GROWTH_COLUMN in table.columns else latest_value()
    elif spec.sort_by == "change":
        sort_column = CHANGE_COLUMN if CHANGE_COLUMN in table.columns else latest_value()
    elif spec.sort_by == "label" and label_columns:
        return table.sort_values(label_columns[0], ascending=not spec.sort_desc)
    elif spec.derive == "growth_pct" and GROWTH_COLUMN in table.columns:
        sort_column = GROWTH_COLUMN
    elif spec.derive == "share_pct" and SHARE_COLUMN in table.columns:
        sort_column = SHARE_COLUMN
    else:
        sort_column = latest_value()

    if sort_column and sort_column in table.columns:
        table = table.sort_values(sort_column, ascending=not spec.sort_desc, na_position="last")

    return table


def _value_label(spec, value_name: str) -> str:
    if spec.agg == "count":
        return "record count"
    return f"{spec.agg} of {value_name}"
