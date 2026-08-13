"""Interactive (Vega-Lite) charts for the web UI.

`.to_dict()` compiles and validates the spec, so these catch a malformed
encoding the same way rendering a PNG catches a bad matplotlib call.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from app.analysis.charts import SERIES_DARK, SERIES_LIGHT
from app.analysis.interactive import build_chart

pytest.importorskip("altair")


@pytest.fixture
def ranking():
    return pd.DataFrame({
        "MAKER": ["HONDA MOTORCYCLE AND SCOOTER INDIA (P) LTD",
                  "TVS MOTOR COMPANY LTD", "HERO MOTOCORP LTD"],
        "TOTAL": [127833.0, 90847.0, 47726.0],
    })


@pytest.fixture
def pivoted():
    return pd.DataFrame({
        "MAKER": ["A", "B", "C"],
        "2024": [600.0, 380.0, 260.0],
        "2025": [670.0, 760.0, 130.0],
    })


def _spec(chart, **kwargs):
    built = build_chart(**kwargs, chart=chart)
    assert built is not None, f"{chart} produced no chart"
    return built.to_dict()


@pytest.mark.parametrize("chart", ["bar", "line", "pie"])
def test_single_series_charts_compile(ranking, chart):
    _spec(chart, frame=ranking, label_columns=["MAKER"], value_columns=["TOTAL"],
          title="Top makers", subtitle="src", value_label="registrations")


def test_grouped_bar_compiles(pivoted):
    spec = _spec("bar", frame=pivoted, label_columns=["MAKER"],
                 value_columns=["2024", "2025"], title="2024 vs 2025")
    assert "yOffset" in json.dumps(spec)  # grouped, not stacked


def test_scatter_needs_two_measures(ranking):
    frame = pd.DataFrame({"M": ["a", "b"], "x": [1.0, 2.0], "y": [3.0, 4.0]})
    _spec("scatter", frame=frame, label_columns=["M"], value_columns=["x", "y"])
    # With one measure it must fall back rather than produce nothing.
    assert build_chart(ranking, chart="scatter", label_columns=["MAKER"],
                       value_columns=["TOTAL"]) is not None


def test_every_chart_carries_a_tooltip(ranking):
    """A PNG cannot have one; in a browser it is the point."""
    for chart in ("bar", "line", "pie"):
        spec = _spec(chart, frame=ranking, label_columns=["MAKER"], value_columns=["TOTAL"])
        assert "tooltip" in json.dumps(spec)


def test_single_series_uses_slot_one_and_no_legend(ranking):
    spec = _spec("bar", frame=ranking, label_columns=["MAKER"], value_columns=["TOTAL"])
    encoded = json.dumps(spec)
    assert SERIES_LIGHT[0] in encoded
    # One series is named by the title; a legend would be noise.
    assert '"field": "series"' not in encoded


def test_multi_series_maps_slots_in_fixed_order(pivoted):
    spec = _spec("bar", frame=pivoted, label_columns=["MAKER"],
                 value_columns=["2024", "2025"])
    encoded = json.dumps(spec)
    assert SERIES_LIGHT[0] in encoded and SERIES_LIGHT[1] in encoded
    assert SERIES_LIGHT[2] not in encoded  # only two series, only two slots


def test_dark_theme_uses_the_dark_steps(ranking):
    spec = _spec("bar", frame=ranking, label_columns=["MAKER"],
                 value_columns=["TOTAL"], theme="dark")
    encoded = json.dumps(spec)
    assert SERIES_DARK[0] in encoded
    assert "#1a1a19" in encoded  # dark surface


def test_pie_folds_the_tail_rather_than_slivering_it():
    frame = pd.DataFrame({"FUEL": [f"F{i}" for i in range(12)],
                          "TOTAL": [float(100 - i * 5) for i in range(12)]})
    spec = _spec("pie", frame=frame, label_columns=["FUEL"], value_columns=["TOTAL"])
    assert "Other" in json.dumps(spec)


def test_percent_columns_get_a_percent_axis_format():
    frame = pd.DataFrame({"FUEL": ["A", "B"], "share_%": [79.0, 21.0]})
    spec = _spec("bar", frame=frame, label_columns=["FUEL"], value_columns=["share_%"])
    assert ",.1f" in json.dumps(spec)


def test_labels_are_not_truncated(ranking):
    """The PNG truncates at 28 chars; here the full name must survive."""
    spec = _spec("bar", frame=ranking, label_columns=["MAKER"], value_columns=["TOTAL"])
    assert "HONDA MOTORCYCLE AND SCOOTER INDIA (P) LTD" in json.dumps(spec)


def test_nothing_to_draw_returns_none(ranking):
    assert build_chart(ranking, chart="none", label_columns=["MAKER"],
                       value_columns=["TOTAL"]) is None
    assert build_chart(pd.DataFrame(), chart="bar", label_columns=[],
                       value_columns=["TOTAL"]) is None
    assert build_chart(ranking, chart="bar", label_columns=["MAKER"],
                       value_columns=[]) is None


def test_bar_thickness_is_fixed_not_a_fraction_of_height(ranking):
    """Two bars in a tall chart rendered as slabs when the size was a band."""
    two = ranking.head(2)
    small = _spec("bar", frame=two, label_columns=["MAKER"], value_columns=["TOTAL"])
    many = pd.DataFrame({"M": [f"M{i}" for i in range(10)],
                         "TOTAL": [float(100 - i) for i in range(10)]})
    large = _spec("bar", frame=many, label_columns=["M"], value_columns=["TOTAL"])

    assert small["mark"]["size"] == large["mark"]["size"] == 20
    assert small["height"] < large["height"]  # height grows with rows, bars do not


def test_grouped_bars_are_thinner_so_a_pair_fits_its_slot(pivoted):
    spec = _spec("bar", frame=pivoted, label_columns=["MAKER"],
                 value_columns=["2024", "2025"])
    assert 9 <= spec["mark"]["size"] < 20


def test_a_pie_folds_its_tail_by_default(ranking):
    """Part-to-whole at a glance means a handful of slices."""
    many = pd.DataFrame({"M": [f"m{i}" for i in range(20)],
                         "TOTAL": [float(100 - i) for i in range(20)]})
    spec = _spec("pie", frame=many, label_columns=["M"], value_columns=["TOTAL"])
    names = spec["encoding"]["color"]["scale"]["domain"]
    assert "Other" in names and len(names) <= 6


def test_a_reader_can_ask_for_every_slice(ranking):
    """"I'd like to see the distribution of all the categories, not just top N"."""
    many = pd.DataFrame({"M": [f"m{i}" for i in range(20)],
                         "TOTAL": [float(100 - i) for i in range(20)]})
    chart = build_chart(many, chart="pie", label_columns=["M"],
                        value_columns=["TOTAL"], max_slices=20)
    names = chart.to_dict()["encoding"]["color"]["scale"]["domain"]
    assert len(names) == 20 and "Other" not in names


def test_the_palette_repeats_rather_than_failing_past_eight_slices():
    many = pd.DataFrame({"M": [f"m{i}" for i in range(20)],
                         "TOTAL": [float(100 - i) for i in range(20)]})
    chart = build_chart(many, chart="pie", label_columns=["M"],
                        value_columns=["TOTAL"], max_slices=20)
    scale = chart.to_dict()["encoding"]["color"]["scale"]
    assert len(scale["range"]) == len(scale["domain"])


def test_an_average_line_can_be_overlaid(ranking):
    plain = build_chart(ranking, chart="bar", label_columns=["MAKER"],
                        value_columns=["TOTAL"]).to_dict()
    with_avg = build_chart(ranking, chart="bar", label_columns=["MAKER"],
                           value_columns=["TOTAL"], average_line=True).to_dict()

    import json

    assert "layer" not in plain
    # Altair nests the rule and its label into a sub-layer, so check the whole
    # spec rather than counting the top level.
    assert '"rule"' in json.dumps(with_avg)
    assert '"avg"' in json.dumps(with_avg)


def test_nan_values_do_not_break_the_spec():
    frame = pd.DataFrame({"M": ["a", "b", "c"], "growth_%": [10.0, float("nan"), -5.0]})
    _spec("bar", frame=frame, label_columns=["M"], value_columns=["growth_%"])
