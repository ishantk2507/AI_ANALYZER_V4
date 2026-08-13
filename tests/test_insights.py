"""The BI layer: computed findings and follow-up questions."""

from __future__ import annotations

import pandas as pd
import pytest

from app.analysis.executor import CHANGE_COLUMN, GROWTH_COLUMN, AnalysisResult, execute
from app.analysis.insights import (
    build_followups,
    build_insights,
    insight_numbers,
)
from app.analysis.spec import AnalysisSpec


def _result(frame, *, labels, values, spec=None, notes=None, shown=None):
    limited = frame.head(shown) if shown else frame
    return AnalysisResult(
        frame=limited.reset_index(drop=True),
        full_frame=frame.reset_index(drop=True),
        label_columns=labels,
        value_columns=values,
        spec=spec or AnalysisSpec(group_by=labels, metric=values[0] if values else ""),
        dataset_key="Two Wheeler/MAKER",
        notes=list(notes or []),
    )


def _kinds(result):
    return {i.kind for i in build_insights(result)}


# --- the base-effect guard --------------------------------------------------


def test_growth_ranking_excludes_tiny_bases(maker_entry):
    """The original failure: every "fastest growing" winner sold 1-24 units."""
    frame = pd.DataFrame({
        "MAKER": [f"M{i}" for i in range(12)],
        "2024": [1, 2, 3, 5, 8] + [5000, 6000, 7000, 8000, 9000, 10000, 11000],
        "2025": [900, 800, 700, 600, 500] + [7000, 7200, 7400, 7600, 7800, 8000, 8200],
    })
    notes = []
    from app.analysis.executor import _derive

    table, columns = _derive(frame.assign(**{
        GROWTH_COLUMN: 0}).drop(columns=[GROWTH_COLUMN]),
        AnalysisSpec(derive="growth_pct"), ["2024", "2025"], notes)

    assert GROWTH_COLUMN in columns and CHANGE_COLUMN in columns
    # The five 1-8 unit rows are gone; the real ones remain.
    assert len(table) == 7
    assert any("base under" in note for note in notes)


def test_the_guard_stands_down_on_genuinely_small_data():
    """If the floor would gut the answer, the data is small, not long-tailed."""
    from app.analysis.executor import _derive

    frame = pd.DataFrame({"M": ["a", "b", "c"], "2024": [1, 2, 3], "2025": [10, 20, 30]})
    notes = []
    table, _ = _derive(frame, AnalysisSpec(derive="growth_pct"), ["2024", "2025"], notes)

    assert len(table) == 3
    assert not any("base under" in note for note in notes)


def test_absolute_change_is_always_reported_with_growth(maker_entry):
    result = execute(maker_entry, AnalysisSpec(
        group_by=["MAKER"], metric="TOTAL", pivot_on="YEAR", derive="growth_pct"))
    assert CHANGE_COLUMN in result.frame.columns


# --- insights ---------------------------------------------------------------


@pytest.fixture
def growth_frame():
    return pd.DataFrame({
        "MAKER": ["RIVER", "SIMPLE", "GREAVES", "TVS", "HERO"],
        "2024": [2971.0, 1433.0, 35981.0, 3358226.0, 5597476.0],
        "2025": [17016.0, 6629.0, 57640.0, 3924521.0, 5918100.0],
        GROWTH_COLUMN: [472.74, 362.60, 60.20, 16.86, 5.73],
        CHANGE_COLUMN: [14045.0, 5196.0, 21659.0, 566295.0, 320624.0],
    })


def test_market_movement_supplies_the_denominator(growth_frame):
    result = _result(growth_frame, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(group_by=["MAKER"], metric="TOTAL",
                                       pivot_on="YEAR", derive="growth_pct"))
    market = next(i for i in build_insights(result) if i.kind == "market")
    assert "grew" in market.text
    # 8,995,087 -> 9,923,906 is +10.3%
    assert "10.3%" in market.text


def test_the_volume_winner_is_named_when_it_is_not_the_percent_winner(growth_frame):
    """The single most useful growth insight."""
    result = _result(growth_frame, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(group_by=["MAKER"], metric="TOTAL",
                                       pivot_on="YEAR", derive="growth_pct"))
    mover = next(i for i in build_insights(result) if i.kind == "absolute_mover")
    assert "TVS" in mover.text          # added 566,295
    assert "RIVER" in mover.text        # but RIVER leads on percent
    assert "566,295" in mover.text


def test_insights_track_the_actual_sort_order(growth_frame, monkeypatch):
    """Sorted by size, row 0 is the biggest -- not the fastest grower. Calling it
    "tops the growth ranking" was wrong."""
    from app.config import settings

    # Raise the cap so this checks content, not which four made the cut.
    monkeypatch.setattr(settings, "max_insights", 12)
    by_volume = growth_frame.sort_values("2025", ascending=False).reset_index(drop=True)
    result = _result(by_volume, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(group_by=["MAKER"], metric="TOTAL",
                                       pivot_on="YEAR", derive="growth_pct",
                                       sort_by="value"))
    texts = " ".join(i.text for i in build_insights(result))

    # HERO is the biggest; RIVER is the fastest grower.
    assert "tops the growth ranking" not in texts
    assert "fastest grower is RIVER" in texts
    assert any(i.kind in ("share", "dominance") and "HERO" in i.text
               for i in build_insights(result))


def test_outperformers_counts_who_beat_the_market(growth_frame, monkeypatch):
    """"Is this good?" needs the market rate, not just the entity's own."""
    from app.config import settings

    monkeypatch.setattr(settings, "max_insights", 12)
    result = _result(growth_frame, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(group_by=["MAKER"], metric="TOTAL",
                                       pivot_on="YEAR", derive="growth_pct"))
    insight = next(i for i in build_insights(result) if i.kind == "outperformers")
    assert "grew faster than the market" in insight.text


def test_share_shift_says_whether_the_leader_gained_ground(growth_frame, monkeypatch):
    """Growing is not the same as winning: the market may be growing faster."""
    from app.config import settings

    monkeypatch.setattr(settings, "max_insights", 12)
    result = _result(growth_frame, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(group_by=["MAKER"], metric="TOTAL",
                                       pivot_on="YEAR", derive="growth_pct"))
    insight = next(i for i in build_insights(result) if i.kind == "share_shift")
    assert ("gained share" in insight.text) or ("lost share" in insight.text)
    assert "points" in insight.text


def test_business_insights_need_two_periods(ranking_frame):
    """They are all period-over-period, so a flat ranking has none."""
    result = _result(ranking_frame, labels=["MAKER"], values=["TOTAL"])
    kinds = {i.kind for i in build_insights(result)}
    assert not kinds & {"outperformers", "share_shift", "concentration_shift", "market"}


def test_the_growth_leader_is_computed_not_assumed(growth_frame):
    result = _result(growth_frame, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(group_by=["MAKER"], metric="TOTAL",
                                       pivot_on="YEAR", derive="growth_pct"))
    mover = next(i for i in build_insights(result) if i.kind == "absolute_mover")
    assert "TVS" in mover.text and "fastest grower is RIVER" in mover.text


def test_base_effect_caution_is_raised_from_the_note(growth_frame):
    result = _result(growth_frame, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(derive="growth_pct"),
                     notes=["excluded 296 rows with a 2024 base under 983 - ..."])
    caution = next(i for i in build_insights(result) if i.kind == "base_effect")
    assert caution.severity == "caution"
    # Cautions are ordered first: they change how the answer reads.
    assert build_insights(result)[0].severity == "caution"


@pytest.fixture
def ranking_frame():
    return pd.DataFrame({
        "MAKER": ["HERO", "HONDA", "TVS", "BAJAJ", "SUZUKI", "YAMAHA"],
        "TOTAL": [5918100.0, 5274325.0, 3924521.0, 2000000.0, 1155815.0, 500000.0],
    })


def test_dominance_versus_plain_share(ranking_frame):
    spread = _result(ranking_frame, labels=["MAKER"], values=["TOTAL"])
    assert "share" in _kinds(spread)

    concentrated = _result(
        pd.DataFrame({"M": ["a", "b", "c"], "TOTAL": [900.0, 50.0, 50.0]}),
        labels=["M"], values=["TOTAL"])
    assert "dominance" in _kinds(concentrated)


def test_concentration_reports_the_top_three(ranking_frame):
    result = _result(ranking_frame, labels=["MAKER"], values=["TOTAL"])
    concentration = next(i for i in build_insights(result) if i.kind == "concentration")
    assert "top 3" in concentration.text


def test_a_close_race_is_called_out_rather_than_a_lead():
    close = _result(pd.DataFrame({"M": ["a", "b"], "TOTAL": [102.0, 100.0]}),
                    labels=["M"], values=["TOTAL"])
    assert "close_race" in _kinds(close)

    clear = _result(pd.DataFrame({"M": ["a", "b"], "TOTAL": [500.0, 100.0]}),
                    labels=["M"], values=["TOTAL"])
    assert "gap" in _kinds(clear)


def test_a_gap_between_percentages_is_stated_in_points():
    """"leads by 18.28" reads as units; the difference is percentage points."""
    from app.analysis.executor import SHARE_COLUMN

    frame = pd.DataFrame({"FUEL": ["ELECTRIC", "CNG"], "TOTAL": [1159826.0, 680085.0],
                          SHARE_COLUMN: [44.18, 25.90]})
    result = _result(frame, labels=["FUEL"], values=["TOTAL", SHARE_COLUMN],
                     spec=AnalysisSpec(group_by=["FUEL"], metric="TOTAL",
                                       derive="share_pct"))
    gap = next(i for i in build_insights(result) if i.kind == "gap")
    assert "18.3 percentage points" in gap.text


def test_a_skewed_distribution_is_flagged():
    """A leader many times the median means the average is not representative."""
    skewed = pd.DataFrame({"M": list("abcdef"),
                           "TOTAL": [900000.0, 5000.0, 4000.0, 3000.0, 2000.0, 1000.0]})
    assert "outlier" in _kinds(_result(skewed, labels=["M"], values=["TOTAL"]))

    even = pd.DataFrame({"M": list("abcdef"),
                         "TOTAL": [100.0, 95.0, 90.0, 85.0, 80.0, 75.0]})
    assert "outlier" not in _kinds(_result(even, labels=["M"], values=["TOTAL"]))


def test_coverage_says_how_much_the_shown_rows_represent(ranking_frame):
    result = _result(ranking_frame, labels=["MAKER"], values=["TOTAL"], shown=2)
    coverage = next(i for i in build_insights(result) if i.kind == "coverage")
    assert "not displayed" in coverage.text


def test_insights_are_capped_and_never_raise():
    from app.config import settings

    result = _result(pd.DataFrame({"M": ["a"] * 8, "TOTAL": [float(9 - i) for i in range(8)]}),
                     labels=["M"], values=["TOTAL"], shown=3)
    assert len(build_insights(result)) <= settings.max_insights


def test_no_insights_from_an_empty_or_unlabelled_result():
    assert build_insights(_result(pd.DataFrame(), labels=[], values=[])) == []
    assert build_insights(_result(pd.DataFrame({"TOTAL": [1.0]}), labels=[],
                                  values=["TOTAL"])) == []


def test_insight_numbers_are_offered_to_the_verifier(growth_frame):
    """Otherwise quoting a computed share is treated as a hallucination."""
    result = _result(growth_frame, labels=["MAKER"],
                     values=["2024", "2025", GROWTH_COLUMN, CHANGE_COLUMN],
                     spec=AnalysisSpec(group_by=["MAKER"], metric="TOTAL",
                                       pivot_on="YEAR", derive="growth_pct"))
    insights = build_insights(result)
    numbers = insight_numbers(insights)
    assert numbers

    from app.agent.verify import unsupported_numbers

    market = next(i for i in insights if i.kind == "market")
    total_2025 = 9923906.0
    assert unsupported_numbers(f"The market reached {total_2025:,.0f}.",
                               result.frame, numbers) == []
    # ...but an invented number is still caught.
    assert unsupported_numbers("The market reached 8,123,456.",
                               result.frame, numbers) == ["8,123,456"]
    assert market.values


# --- follow-ups -------------------------------------------------------------


def test_a_growth_answer_suggests_the_volume_view(maker_entry):
    result = execute(maker_entry, AnalysisSpec(
        group_by=["MAKER"], metric="TOTAL", pivot_on="YEAR", derive="growth_pct"))
    questions = " ".join(f.question for f in build_followups(result, maker_entry))
    assert "absolute" in questions.lower()


def test_a_plain_ranking_suggests_a_trend_and_a_share(maker_entry):
    result = execute(maker_entry, AnalysisSpec(group_by=["MAKER"], metric="TOTAL"))
    questions = " ".join(f.question for f in build_followups(result, maker_entry)).lower()
    assert "year" in questions or "share" in questions


def test_a_filtered_answer_suggests_widening_it(maker_entry):
    from app.analysis.spec import Filter

    result = execute(maker_entry, AnalysisSpec(
        filters=[Filter(column="STATE", op="==", value="Delhi")],
        group_by=["MAKER"], metric="TOTAL"))
    questions = " ".join(f.question for f in build_followups(result, maker_entry))
    assert "Delhi" in questions and "compare" in questions.lower()


@pytest.mark.parametrize(
    "word, plural",
    [("CITY", "cities"), ("STATE", "states"), ("CLASS", "classes"), ("RTO", "rtos")],
)
def test_place_names_are_pluralised(word, plural):
    """"the other citys" turned up in a real follow-up."""
    from app.analysis.insights import _plural

    assert _plural(word) == plural


def test_a_city_filter_reads_as_english(maker_entry):
    from app.analysis.spec import Filter

    result = execute(maker_entry, AnalysisSpec(
        filters=[Filter(column="CITY", op="==", value="Trivandrum")],
        group_by=["MAKER"], metric="TOTAL"))
    questions = " ".join(f.question for f in build_followups(result, maker_entry))
    assert "other cities" in questions
    assert "citys" not in questions


def test_a_suggestion_does_not_repeat_a_place_it_already_names(maker_entry):
    """"How does Haryana compare with the other states **for Haryana**?"."""
    from app.analysis.spec import Filter

    result = execute(maker_entry, AnalysisSpec(
        filters=[Filter(column="STATE", op="==", value="Delhi")],
        group_by=["MAKER"], metric="TOTAL"))

    for item in build_followups(result, maker_entry):
        assert item.question.lower().count("delhi") <= 1


def test_followups_are_unique_and_capped(maker_entry):
    from app.config import settings

    result = execute(maker_entry, AnalysisSpec(group_by=["MAKER"], metric="TOTAL"))
    followups = build_followups(result, maker_entry)
    assert len(followups) <= settings.max_followups
    assert len({f.question for f in followups}) == len(followups)


def test_no_followups_for_an_empty_result(maker_entry):
    from app.analysis.spec import Filter

    result = execute(maker_entry, AnalysisSpec(
        filters=[Filter(column="YEAR", op=">", value="2099")],
        group_by=["MAKER"], metric="TOTAL"))
    assert build_followups(result, maker_entry) == []
