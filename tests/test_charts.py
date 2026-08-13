from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.charts import (
    MAX_SERIES,
    SERIES_DARK,
    SERIES_LIGHT,
    compact_number,
    format_percent,
    render_chart,
    select_chart_columns,
)


@pytest.fixture
def ranking():
    return pd.DataFrame(
        {
            "MAKER": ["HERO MOTOCORP LTD", "BAJAJ AUTO LTD", "TVS MOTOR", "ATHER", "OLA"],
            "TOTAL": [1270.0, 1140.0, 390.0, 220.0, 90.0],
        }
    )


@pytest.fixture
def pivoted():
    return pd.DataFrame(
        {
            "MAKER": ["HERO MOTOCORP LTD", "BAJAJ AUTO LTD", "TVS MOTOR"],
            "2024": [600.0, 380.0, 260.0],
            "2025": [670.0, 760.0, 130.0],
        }
    )


@pytest.mark.parametrize("chart", ["bar", "barh", "line", "pie", "scatter"])
def test_every_chart_type_renders(ranking, chart, tmp_path):
    path = render_chart(
        ranking, chart=chart, label_columns=["MAKER"], value_columns=["TOTAL"],
        title="Top makers", out_path=tmp_path / f"{chart}.png",
    )
    assert path is not None and path.is_file() and path.stat().st_size > 1000


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_both_themes_render(pivoted, theme, tmp_path):
    path = render_chart(
        pivoted, chart="bar", label_columns=["MAKER"], value_columns=["2024", "2025"],
        title="2024 vs 2025", theme=theme, out_path=tmp_path / f"{theme}.png",
    )
    assert path is not None and path.is_file()


def test_a_single_number_becomes_a_stat_tile_not_a_one_bar_chart(tmp_path):
    frame = pd.DataFrame({"TOTAL": [2800.0]})
    path = render_chart(
        frame, chart="bar", label_columns=[], value_columns=["TOTAL"],
        title="Total registrations", out_path=tmp_path / "stat.png",
    )
    assert path is not None and path.is_file()


def test_chart_none_renders_nothing(ranking, tmp_path):
    assert render_chart(ranking, chart="none", label_columns=["MAKER"],
                        value_columns=["TOTAL"], out_path=tmp_path / "x.png") is None


def test_empty_frame_renders_nothing(tmp_path):
    assert render_chart(pd.DataFrame(), chart="bar", label_columns=[],
                        value_columns=["TOTAL"], out_path=tmp_path / "x.png") is None


def test_many_series_do_not_cycle_the_palette(tmp_path):
    """Past eight series the palette is exhausted -- extras stay out of the chart."""
    frame = pd.DataFrame({"label": ["a", "b"], **{f"s{i}": [i, i + 1] for i in range(12)}})
    path = render_chart(
        frame, chart="bar", label_columns=["label"],
        value_columns=[f"s{i}" for i in range(12)],
        title="many", out_path=tmp_path / "many.png",
    )
    assert path is not None and path.is_file()


def test_palette_slots_are_distinct_and_ordered():
    assert len(SERIES_LIGHT) == len(SERIES_DARK) == MAX_SERIES
    assert len(set(SERIES_LIGHT)) == MAX_SERIES
    assert len(set(SERIES_DARK)) == MAX_SERIES
    assert SERIES_LIGHT[0] == "#2a78d6"  # slot 1 is always blue


@pytest.mark.parametrize(
    "value, expected",
    [(950, "950"), (1500, "1.5K"), (1_234_567, "1.23M"), (2_500_000_000, "2.5B"), (0, "0")],
)
def test_compact_number(value, expected):
    assert compact_number(value) == expected


def test_growth_is_not_plotted_beside_raw_counts():
    """33,100% next to 5 units on one axis flattens every other bar."""
    columns, label = select_chart_columns(["2024", "2025", "growth_%"], "growth_pct", "bar")
    assert columns == ["growth_%"]
    assert label == "growth %"


def test_share_replaces_the_count_except_on_a_pie():
    assert select_chart_columns(["TOTAL", "share_%"], "share_pct", "bar")[0] == ["share_%"]
    # A pie encodes share as angle already, so plot the real quantity.
    assert select_chart_columns(["TOTAL", "share_%"], "share_pct", "pie")[0] == ["TOTAL"]


def test_plain_results_keep_every_column():
    assert select_chart_columns(["2024", "2025"], "none", "bar")[0] == ["2024", "2025"]


@pytest.mark.parametrize(
    "value, expected", [(33100.0, "33,100%"), (114.5, "114%"), (-50.0, "-50%"), (4.25, "4.2%")]
)
def test_format_percent(value, expected):
    assert format_percent(value) == expected


def test_negative_growth_renders(tmp_path):
    frame = pd.DataFrame({"MAKER": ["A", "B", "C"], "growth_%": [120.0, -5.0, -60.0]})
    path = render_chart(
        frame, chart="bar", label_columns=["MAKER"], value_columns=["growth_%"],
        title="Growth", value_label="growth %", out_path=tmp_path / "neg.png",
    )
    assert path is not None and path.is_file()
