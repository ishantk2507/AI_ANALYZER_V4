"""The analysis spec: a small, closed language the model writes instead of code.

Why a spec and not generated Python: a 3B model writes broken pandas often
enough to be unusable, and running its code needs a sandbox. A spec is validated
before anything executes, needs no sandbox because nothing is ever `exec`'d, and
-- crucially -- the JSON Schema handed to the decoder is built from the *actual*
column names, so an invalid column is not a representable output.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator

from app.config import settings

FILTER_OPS = ["==", "!=", ">", ">=", "<", "<=", "in", "contains"]
AGGREGATIONS = ["sum", "mean", "count", "median", "max", "min"]
DERIVATIONS = ["none", "growth_pct", "share_pct"]
#: What to rank by. "auto" means the derived column when there is one, else the
#: metric -- which makes a growth question always rank by percent, so "growth in
#: the biggest cities" could not be asked. "value" ranks by size and still shows
#: the growth columns.
SORT_KEYS = ["auto", "value", "growth", "change", "label"]
CHART_TYPES = ["bar", "barh", "line", "pie", "scatter", "none"]

NONE = "none"


class Filter(BaseModel):
    column: str
    op: str = "=="
    value: str = ""

    @field_validator("op")
    @classmethod
    def _known_op(cls, value: str) -> str:
        return value if value in FILTER_OPS else "=="


class AnalysisSpec(BaseModel):
    """What to compute. Deliberately closed-world -- no free-form expressions."""

    filters: List[Filter] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    metric: str = ""
    agg: str = "sum"
    #: Turn one dimension into columns, e.g. YEAR -> a column per year.
    pivot_on: str = NONE
    derive: str = NONE
    sort_by: str = "auto"
    sort_desc: bool = True
    limit: int = 10
    chart: str = "bar"
    title: str = ""

    @field_validator("agg")
    @classmethod
    def _known_agg(cls, value: str) -> str:
        return value if value in AGGREGATIONS else "sum"

    @field_validator("derive")
    @classmethod
    def _known_derive(cls, value: str) -> str:
        return value if value in DERIVATIONS else NONE

    @field_validator("sort_by")
    @classmethod
    def _known_sort_key(cls, value: str) -> str:
        return value if value in SORT_KEYS else "auto"

    @field_validator("chart")
    @classmethod
    def _known_chart(cls, value: str) -> str:
        return value if value in CHART_TYPES else "bar"

    @field_validator("limit")
    @classmethod
    def _sane_limit(cls, value: int) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 10
        return max(1, min(value, settings.max_result_rows))

    @field_validator("group_by")
    @classmethod
    def _cap_group_by(cls, value: List[str]) -> List[str]:
        # Two dimensions is already a lot to read in a chart.
        seen: List[str] = []
        for item in value:
            if item and item != NONE and item not in seen:
                seen.append(item)
        return seen[:2]


class RoutingDecision(BaseModel):
    category: str
    breakdown: str
    reason: str = ""


# --- JSON Schemas -----------------------------------------------------------
# Kept to the subset llama.cpp's GBNF converter and Ollama's format both handle:
# object / properties / required / enum / array+items / string / integer / boolean.
# No nulls, no oneOf, no $ref -- "none" is used as an explicit sentinel instead.


def routing_schema(categories: List[str], breakdowns: List[str]) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": list(categories)},
            "breakdown": {"type": "string", "enum": list(breakdowns)},
            "reason": {"type": "string"},
        },
        "required": ["category", "breakdown"],
    }


def analysis_schema(
    all_columns: List[str],
    dimension_columns: List[str],
    measure_columns: List[str],
) -> Dict[str, Any]:
    pivot_options = [NONE] + list(dimension_columns)
    metric_options = list(measure_columns) or list(all_columns)

    return {
        "type": "object",
        "properties": {
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column": {"type": "string", "enum": list(all_columns)},
                        "op": {"type": "string", "enum": FILTER_OPS},
                        "value": {"type": "string"},
                    },
                    "required": ["column", "op", "value"],
                },
            },
            "group_by": {
                "type": "array",
                "items": {"type": "string", "enum": list(dimension_columns)},
            },
            "metric": {"type": "string", "enum": metric_options},
            "agg": {"type": "string", "enum": AGGREGATIONS},
            "pivot_on": {"type": "string", "enum": pivot_options},
            "derive": {"type": "string", "enum": DERIVATIONS},
            "sort_by": {"type": "string", "enum": SORT_KEYS},
            "sort_desc": {"type": "boolean"},
            "limit": {"type": "integer"},
            "chart": {"type": "string", "enum": CHART_TYPES},
            "title": {"type": "string"},
        },
        "required": ["group_by", "metric", "agg", "chart"],
    }


# --- Lenient parsing --------------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def parse_json(text: str) -> Dict[str, Any]:
    """Parse model output that *should* be JSON.

    Constrained decoding makes this trivial most of the time, but the fallback
    paths (an unconstrained backend, a truncated generation) still need to cope
    with code fences and leading prose.
    """
    if text is None:
        raise ValueError("empty model response")

    cleaned = _FENCE.sub("", text.strip())
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = _extract_object(cleaned)

    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    return payload


def _extract_object(text: str) -> Any:
    """Pull the first balanced {...} out of surrounding noise."""
    start = text.find("{")
    if start == -1:
        raise ValueError(f"no JSON object in model response: {text[:200]!r}")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : index + 1])
    raise ValueError(f"unbalanced JSON in model response: {text[:200]!r}")
