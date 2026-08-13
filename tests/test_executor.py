from __future__ import annotations

import pytest

from app.analysis.executor import CHANGE_COLUMN, GROWTH_COLUMN, SHARE_COLUMN, execute
from app.analysis.spec import AnalysisSpec, Filter


def test_group_and_sum(maker_entry):
    result = execute(maker_entry, AnalysisSpec(group_by=["MAKER"], metric="TOTAL", agg="sum"))
    assert result.label_columns == ["MAKER"]
    assert result.value_columns == ["TOTAL"]

    top = result.frame.iloc[0]
    # Hero: 500+550+100+120 = 1270; Bajaj: 300+600+80+160 = 1140
    assert top["MAKER"] == "HERO MOTOCORP LTD"
    assert top["TOTAL"] == 1270


def test_sort_ascending_for_bottom_questions(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(group_by=["MAKER"], metric="TOTAL", agg="sum", sort_desc=False),
    )
    assert result.frame.iloc[0]["MAKER"] == "TVS MOTOR"


def test_filter_matches_exact_value(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(
            filters=[Filter(column="STATE", op="==", value="Delhi")],
            group_by=["MAKER"],
            metric="TOTAL",
            agg="sum",
        ),
    )
    assert result.rows_matched == 6
    assert result.frame["TOTAL"].sum() == 2250


def test_filter_value_is_fuzzily_resolved(maker_entry):
    """The model writes 'Hero Motocorp'; the file says 'HERO MOTOCORP LTD'."""
    result = execute(
        maker_entry,
        AnalysisSpec(
            filters=[Filter(column="MAKER", op="==", value="Hero Motocorp")],
            group_by=["STATE"],
            metric="TOTAL",
            agg="sum",
        ),
    )
    assert not result.is_empty
    assert result.frame["TOTAL"].sum() == 1270
    assert any("HERO MOTOCORP LTD" in note for note in result.notes)


def test_unmatched_filter_value_is_reported_not_silently_dropped(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(
            filters=[Filter(column="STATE", op="==", value="Atlantis")],
            group_by=["MAKER"],
            metric="TOTAL",
            agg="sum",
        ),
    )
    assert any("Atlantis" in note for note in result.notes)


def test_numeric_filter(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(
            filters=[Filter(column="YEAR", op=">=", value="2025")],
            group_by=["MAKER"],
            metric="TOTAL",
            agg="sum",
        ),
    )
    assert result.rows_matched == 6
    assert result.frame["TOTAL"].sum() == 1560


def test_pivot_produces_a_column_per_period(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(group_by=["MAKER"], metric="TOTAL", agg="sum", pivot_on="YEAR"),
    )
    assert result.value_columns == ["2024", "2025"]
    hero = result.frame.set_index("MAKER").loc["HERO MOTOCORP LTD"]
    assert hero["2024"] == 600 and hero["2025"] == 670


def test_growth_ranks_by_change_not_size(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(
            group_by=["MAKER"], metric="TOTAL", agg="sum",
            pivot_on="YEAR", derive="growth_pct",
        ),
    )
    assert GROWTH_COLUMN in result.frame.columns
    # Bajaj 380 -> 760 = +100%, Hero 600 -> 670 = +11.67%, TVS 260 -> 130 = -50%
    assert result.frame.iloc[0]["MAKER"] == "BAJAJ AUTO LTD"
    assert result.frame.iloc[0][GROWTH_COLUMN] == pytest.approx(100.0)
    assert result.frame.iloc[-1][GROWTH_COLUMN] == pytest.approx(-50.0)


def test_growth_adds_the_pivot_when_the_model_forgets_it(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(group_by=["MAKER"], metric="TOTAL", agg="sum", derive="growth_pct"),
    )
    assert GROWTH_COLUMN in result.frame.columns
    assert any("pivoted on 'YEAR'" in note for note in result.notes)


def test_share_sums_to_one_hundred(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(group_by=["MAKER"], metric="TOTAL", agg="sum", derive="share_pct"),
    )
    assert result.frame[SHARE_COLUMN].sum() == pytest.approx(100.0, abs=0.05)


def test_count_ignores_the_metric(maker_entry):
    result = execute(maker_entry, AnalysisSpec(group_by=["STATE"], agg="count"))
    assert result.value_columns == ["records"]
    assert result.frame["records"].tolist() == [6, 6]


def test_no_group_by_returns_one_number(maker_entry):
    result = execute(maker_entry, AnalysisSpec(metric="TOTAL", agg="sum"))
    assert len(result.frame) == 1
    assert result.frame.iloc[0]["TOTAL"] == 2800


def test_limit_is_applied(maker_entry):
    result = execute(
        maker_entry, AnalysisSpec(group_by=["MAKER"], metric="TOTAL", agg="sum", limit=2)
    )
    assert len(result.frame) == 2


def test_sort_by_value_ranks_a_growth_table_by_size(maker_entry):
    """The big movers' growth, not the biggest percentages."""
    by_growth = execute(maker_entry, AnalysisSpec(
        group_by=["MAKER"], metric="TOTAL", pivot_on="YEAR", derive="growth_pct"))
    by_volume = execute(maker_entry, AnalysisSpec(
        group_by=["MAKER"], metric="TOTAL", pivot_on="YEAR", derive="growth_pct",
        sort_by="value"))

    assert GROWTH_COLUMN in by_volume.frame.columns  # growth is still shown
    latest = by_volume.frame["2025"].tolist()
    assert latest == sorted(latest, reverse=True)
    # ...whereas the default ranks by percent.
    growth = by_growth.frame[GROWTH_COLUMN].tolist()
    assert growth == sorted(growth, reverse=True)


def test_sort_by_change_ranks_by_absolute_movement(maker_entry):
    result = execute(maker_entry, AnalysisSpec(
        group_by=["MAKER"], metric="TOTAL", pivot_on="YEAR", derive="growth_pct",
        sort_by="change"))
    changes = result.frame[CHANGE_COLUMN].tolist()
    assert changes == sorted(changes, reverse=True)


def test_an_unknown_sort_key_falls_back_to_auto():
    assert AnalysisSpec(sort_by="sideways").sort_by == "auto"


def test_a_growth_ranking_drawn_as_a_line_still_ranks_by_growth(maker_entry):
    """Line charts sort chronologically, which put a Haryana growth table in
    alphabetical order and made Ambala look like the fastest grower."""
    result = execute(maker_entry, AnalysisSpec(
        group_by=["MAKER"], metric="TOTAL", pivot_on="YEAR",
        derive="growth_pct", chart="line",
    ))
    growth = result.frame[GROWTH_COLUMN].tolist()
    assert growth == sorted(growth, reverse=True)


def test_a_plain_time_series_still_reads_chronologically(maker_entry):
    result = execute(maker_entry, AnalysisSpec(
        group_by=["YEAR"], metric="TOTAL", chart="line"))
    assert result.frame["YEAR"].tolist() == sorted(result.frame["YEAR"].tolist())


def test_unknown_column_is_repaired_not_fatal(maker_entry):
    result = execute(maker_entry, AnalysisSpec(group_by=["maker"], metric="total", agg="sum"))
    assert result.label_columns == ["MAKER"]
    assert not result.is_empty


def test_empty_result_is_reported_cleanly(maker_entry):
    result = execute(
        maker_entry,
        AnalysisSpec(
            filters=[Filter(column="YEAR", op=">", value="2030")],
            group_by=["MAKER"], metric="TOTAL", agg="sum",
        ),
    )
    assert result.is_empty
    assert "No rows matched the filters." in result.notes
