"""The deterministic half of the agent: spec -> pandas -> chart."""

from app.analysis.charts import render_chart
from app.analysis.executor import AnalysisResult, execute
from app.analysis.spec import (
    AnalysisSpec,
    Filter,
    RoutingDecision,
    analysis_schema,
    parse_json,
    routing_schema,
)

__all__ = [
    "AnalysisSpec",
    "Filter",
    "RoutingDecision",
    "analysis_schema",
    "routing_schema",
    "parse_json",
    "AnalysisResult",
    "execute",
    "render_chart",
]
