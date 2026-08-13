"""Cross-category questions.

Registrations live in one folder per vehicle category, so "total vehicle
registrations in Tamil Nadu" cannot be answered from any single one. These pin
the union: how it is built, when it is used, and that it does not double count.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.executor import execute
from app.analysis.spec import AnalysisSpec, Filter
from app.data.catalog import ALL_CATEGORIES, CombinedEntry


def test_combined_spans_every_category(catalog):
    combined = catalog.combined("MAKER")

    assert isinstance(combined, CombinedEntry)
    assert combined.spans_all_categories
    assert combined.category == ALL_CATEGORIES
    assert {s.category for s in combined.sources} == set(catalog.categories)
    assert len(combined.paths) == len(catalog.categories)


def test_combined_keeps_only_shared_columns(catalog):
    """Measure columns differ per category (2WIC vs 3WN vs HMV); TOTAL does not."""
    combined = catalog.combined("MAKER")
    frame = combined.load()

    for source in combined.sources:
        assert set(frame.columns) <= set(source.load().columns)
    assert {"STATE", "YEAR", "CATEGORY", "RTO", "CITY", "MAKER", "TOTAL"} <= set(frame.columns)


def test_combined_does_not_double_count(catalog):
    """Exactly one breakdown per category, or every registration lands twice."""
    combined = catalog.combined("MAKER")
    frame = combined.load()

    expected = sum(
        pd.to_numeric(source.load()["TOTAL"], errors="coerce").sum()
        for source in combined.sources
    )
    assert pd.to_numeric(frame["TOTAL"], errors="coerce").sum() == expected
    assert len(frame) == sum(len(s.load()) for s in combined.sources)


def test_category_becomes_a_groupable_dimension(catalog):
    """The whole point of "show all vehicle category registrations"."""
    combined = catalog.combined("MAKER")
    profile = combined.profile

    category = profile.column("CATEGORY")
    assert category is not None and category.kind == "categorical"
    assert set(category.sample_values) == set(catalog.categories)


def test_combined_profile_totals_match_the_sources(catalog):
    combined = catalog.combined("MAKER")
    assert combined.profile.rows == sum(s.profile.rows for s in combined.sources)
    assert combined.profile.column("TOTAL").total == pytest.approx(
        sum(s.profile.column("TOTAL").total for s in combined.sources)
    )


def test_no_combined_entry_when_there_is_nothing_to_combine(catalog):
    # Only Two Wheeler has a NORM table in this fixture.
    assert catalog.combined("NORM") is None
    assert catalog.combined("NOT_A_TABLE") is None


# --- the questions the user actually asks -----------------------------------


def test_all_vehicle_category_registrations(catalog):
    """"show all vehicle category registrations in Delhi"."""
    combined = catalog.combined("MAKER")
    result = execute(combined, AnalysisSpec(
        filters=[Filter(column="STATE", op="==", value="Delhi")],
        group_by=["CATEGORY"], metric="TOTAL", agg="sum",
    ))

    assert set(result.frame["CATEGORY"]) == set(catalog.categories)
    assert not result.is_empty


def test_total_registrations_across_categories(catalog):
    """"total vehicle registrations in Delhi" -- one number, every category."""
    combined = catalog.combined("MAKER")
    result = execute(combined, AnalysisSpec(
        filters=[Filter(column="STATE", op="==", value="Delhi")],
        group_by=[], metric="TOTAL", agg="sum",
    ))

    frame = combined.load()
    expected = pd.to_numeric(
        frame[frame["STATE"].astype(str) == "Delhi"]["TOTAL"], errors="coerce"
    ).sum()
    assert len(result.frame) == 1
    assert result.frame.iloc[0]["TOTAL"] == expected


def test_city_wise_registrations_across_categories(catalog):
    """"city wise registrations" -- CITY is derived, CATEGORY is spanned."""
    combined = catalog.combined("MAKER")
    result = execute(combined, AnalysisSpec(group_by=["CITY"], metric="TOTAL", agg="sum"))

    assert result.label_columns == ["CITY"]
    assert not result.is_empty


def test_two_categories_can_be_compared(catalog):
    """The limitation this replaces: comparison used to need one table."""
    combined = catalog.combined("MAKER")
    result = execute(combined, AnalysisSpec(
        filters=[Filter(column="CATEGORY", op="in",
                        value="Two Wheeler, Three Wheeler")],
        group_by=["CATEGORY"], metric="TOTAL", agg="sum",
    ))

    assert set(result.frame["CATEGORY"]) == {"Two Wheeler", "Three Wheeler"}
