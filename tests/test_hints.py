from __future__ import annotations

import pytest

from app.agent.hints import (
    analyse_question,
    apply_hints,
    infer_missing_filters,
    merge_repeated_filters,
    prefer_total_metric,
    widen_value_group,
)
from app.analysis.spec import AnalysisSpec, Filter


def test_category_is_taken_from_the_question(catalog):
    hints = analyse_question("which two wheeler makers grew fastest?", catalog)
    assert hints.category == "Two Wheeler"
    assert hints.breakdown == "MAKER"
    assert hints.routing_is_certain


@pytest.mark.parametrize("phrasing", ["2W makers", "2 wheeler makers", "two-wheeler makers"])
def test_shorthand_categories_are_understood(catalog, phrasing):
    assert analyse_question(phrasing, catalog).category == "Two Wheeler"


def test_ambiguous_category_defers_to_the_model(catalog):
    hints = analyse_question("compare two wheeler and three wheeler makers", catalog)
    assert hints.category is None
    assert not hints.routing_is_certain


def test_breakdown_is_inferred_from_a_value_in_the_column(catalog):
    """'petrol' is a FUEL value, so the question is about the FUEL table."""
    hints = analyse_question("how many petrol two wheelers were registered?", catalog)
    assert hints.breakdown == "FUEL"


def test_breakdown_synonyms(catalog):
    assert analyse_question("top manufacturers of two wheelers", catalog).breakdown == "MAKER"


def test_breakdown_the_category_lacks_is_not_certain(catalog):
    hints = analyse_question("three wheeler emission norms", catalog)
    assert hints.category == "Three Wheeler"
    assert not hints.routing_is_certain  # only Two Wheeler has a NORM table


@pytest.mark.parametrize(
    "question",
    ["which makers grew fastest", "makers with the biggest increase",
     "how did registrations change", "year over year growth"],
)
def test_growth_intent(question):
    assert analyse_question(question).wants_growth


@pytest.mark.parametrize("question", ["share of electric vehicles", "what percentage is petrol"])
def test_share_intent(question):
    assert analyse_question(question).wants_share


def test_bottom_intent():
    assert analyse_question("the 5 lowest makers").wants_bottom
    assert not analyse_question("the top 5 makers").wants_bottom


@pytest.mark.parametrize("question, expected", [("top 5 makers", 5), ("top five makers", 5),
                                                ("bottom 20 RTOs", 20), ("makers", None)])
def test_limit_is_read_from_the_question(question, expected):
    assert analyse_question(question).limit == expected


@pytest.mark.parametrize(
    "question",
    ["top two wheeler makers in Bangalore", "top 2 wheeler makers",
     "best three wheeler makers", "top two-wheeler brands"],
)
def test_a_vehicle_category_after_top_is_not_a_row_count(question):
    """"top two wheeler makers" was read as "top 2" and returned two rows."""
    assert analyse_question(question).limit is None


def test_years_are_collected():
    assert analyse_question("growth from 2024 to 2025").years == ["2024", "2025"]


def test_apply_hints_restores_a_dropped_growth_intent(maker_entry):
    """The exact failure seen with the real model: 'grew fastest' planned as a plain sum."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", agg="sum")
    notes = []
    hints = analyse_question("which makers grew fastest from 2024 to 2025", None)

    apply_hints(spec, hints, maker_entry.profile, notes)

    assert spec.derive == "growth_pct"
    assert spec.pivot_on == "YEAR"
    assert any("growth" in note for note in notes)


@pytest.mark.parametrize(
    "question, expected",
    [
        # A question wanting one figure must not come back as a breakdown.
        ("total vehicle registrations in Kerala", []),
        ("how many two wheelers in Delhi", []),
        # ...but a total that is explicitly split still gets its grouping.
        ("total registrations by maker", ["MAKER"]),
        ("which maker has the most", ["MAKER"]),
        # Naming a dimension says what to group by, whichever table was read.
        ("city wise registrations in Kerala", ["CITY"]),
        ("state wise registrations", ["STATE"]),
        ("show all vehicle category registrations", ["CATEGORY"]),
        # Naming a *value* is a filter, not a grouping -- nothing to change.
        ("registrations in Delhi", ["RTO"]),
    ],
)
def test_the_grouping_follows_what_the_question_asks_for(maker_entry, question, expected):
    """"total vehicle registrations" came back as a CLASS breakdown, and
    "city wise registrations" grouped by whatever the table was named after."""
    spec = AnalysisSpec(group_by=["RTO"], metric="TOTAL")
    apply_hints(spec, analyse_question(question, None), maker_entry.profile, [])
    assert spec.group_by == expected


def test_a_single_total_drops_any_derivation(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", derive="share_pct")
    apply_hints(spec, analyse_question("total registrations in Delhi", None),
                maker_entry.profile, [])
    assert spec.group_by == [] and spec.derive == "none"


def test_apply_hints_does_not_override_a_deliberate_choice(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", derive="share_pct")
    hints = analyse_question("which makers grew fastest", None)
    apply_hints(spec, hints, maker_entry.profile, [])
    assert spec.derive == "share_pct"


def _frame(entry):
    from app.data.loader import load_dataset

    return load_dataset(entry.path)


def _class_entry(tmp_path):
    """A table holding a value whose name collides with an analysis word."""
    import pandas as pd

    from app.data.catalog import DatasetEntry

    path = tmp_path / "CLASS.parquet"
    pd.DataFrame(
        [
            {"STATE": "Manipur", "YEAR": 2024, "CLASS": "BREAKDOWN VAN", "TOTAL": 12},
            {"STATE": "Manipur", "YEAR": 2024, "CLASS": "AMBULANCE", "TOTAL": 340},
            {"STATE": "Kerala", "YEAR": 2024, "CLASS": "AMBULANCE", "TOTAL": 210},
        ]
    ).to_parquet(path, index=False)
    return DatasetEntry(key="Ambulance/CLASS", path=path,
                        category="Ambulance", breakdown="CLASS")


def test_a_word_asking_for_a_decomposition_is_not_a_value(tmp_path):
    """"the city breakdown for Manipur" matched the CLASS value BREAKDOWN VAN.

    0 of 46,230 rows -- and the app suggests that exact wording as a follow-up.
    """
    entry = _class_entry(tmp_path)
    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL")

    infer_missing_filters("What is the city breakdown for Manipur?",
                          _frame(entry), entry.profile, spec, [])

    assert not any(f.column == "CLASS" for f in spec.filters)
    assert ("STATE", "==", "Manipur") in [(f.column, f.op, f.value) for f in spec.filters]


def test_a_question_really_about_that_vehicle_still_filters(tmp_path):
    """The stopword must not cost the whole-value match."""
    entry = _class_entry(tmp_path)
    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL")

    infer_missing_filters("how many breakdown van registrations in Manipur?",
                          _frame(entry), entry.profile, spec, [])

    assert ("CLASS", "==", "BREAKDOWN VAN") in [
        (f.column, f.op, f.value) for f in spec.filters
    ]


@pytest.mark.parametrize(
    "question",
    [
        "how many two wheelers were registered in Delhi?",
        "top makers of two wheeler in Delhi",
    ],
)
def test_a_category_is_matched_regardless_of_plural(catalog, question):
    """"the most ambulances" left the category unmatched and widened to all ten."""
    assert analyse_question(question, catalog).category == "Two Wheeler"


def test_a_dropped_filter_is_recovered_from_the_question(maker_entry):
    """Real failure: "top 5 RTOs in Kerala" planned with no filter, returned Tamil Nadu."""
    spec = AnalysisSpec(group_by=["RTO"], metric="TOTAL", agg="sum")
    notes = []

    infer_missing_filters(
        "top 5 RTOs in Delhi by registrations", _frame(maker_entry),
        maker_entry.profile, spec, notes,
    )

    assert [(f.column, f.op, f.value) for f in spec.filters] == [("STATE", "==", "Delhi")]
    assert any("Delhi" in note for note in notes)


def test_one_named_value_of_a_grouped_column_is_not_filtered(maker_entry):
    """"share of petrol" wants group_by=[FUEL], not FUEL == PETROL (which is 100%)."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters(
        "share of hero motocorp ltd", _frame(maker_entry), maker_entry.profile, spec, []
    )
    assert spec.filters == []


def test_a_scoped_value_filters_even_when_the_column_is_grouped(maker_entry):
    """"how many three wheelers **in** Pune" ranked every city and ignored Pune."""
    spec = AnalysisSpec(group_by=["CITY"], metric="TOTAL")
    infer_missing_filters(
        "how many two wheelers in Trivandrum", _frame(maker_entry),
        maker_entry.profile, spec, [],
    )
    city = [f for f in spec.filters if f.column == "CITY"]
    assert city and city[0].value == "Trivandrum"


def test_a_ranking_question_does_not_filter_the_column_it_ranks(maker_entry):
    """"which city has the most" must keep every city in play."""
    spec = AnalysisSpec(group_by=["CITY"], metric="TOTAL")
    infer_missing_filters(
        "which city registered the most two wheelers", _frame(maker_entry),
        maker_entry.profile, spec, [],
    )
    assert not [f for f in spec.filters if f.column == "CITY"]


def test_the_subject_of_a_breakdown_is_still_not_filtered(maker_entry):
    """"share of X" names X without scoping it -- filtering makes the answer 100%."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters(
        "share of hero motocorp ltd", _frame(maker_entry), maker_entry.profile, spec, []
    )
    assert not [f for f in spec.filters if f.column == "MAKER"]


def test_several_places_land_on_one_level(maker_entry):
    """"compare Mumbai, Pune and Delhi" became `STATE == Delhi AND CITY in
    (Mumbai, Pune)` -- an intersection that matches nothing."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters(
        "compare Trivandrum and Old Delhi", _frame(maker_entry),
        maker_entry.profile, spec, [],
    )
    places = [f for f in spec.filters if f.column in ("STATE", "CITY", "RTO")]
    assert len(places) == 1, f"places split across columns: {places}"
    assert places[0].op == "in"


def test_places_that_only_exist_as_states_use_the_state_level(maker_entry):
    """Kerala is not a city, so the pair belongs on STATE."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters(
        "compare Delhi and Kerala", _frame(maker_entry), maker_entry.profile, spec, []
    )
    places = [f for f in spec.filters if f.column in ("STATE", "CITY", "RTO")]
    assert len(places) == 1
    assert places[0].column == "STATE"
    assert set(places[0].value.split(", ")) == {"Delhi", "Kerala"}


def test_a_single_place_is_unaffected_by_consolidation(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters(
        "top makers in Trivandrum", _frame(maker_entry), maker_entry.profile, spec, []
    )
    places = [f for f in spec.filters if f.column in ("STATE", "CITY", "RTO")]
    assert len(places) == 1 and places[0].op == "=="


def test_two_named_values_of_a_grouped_column_are_a_comparison(maker_entry):
    """"compare X and Y" groups by the same column it filters -- that is the point."""
    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL")
    infer_missing_filters(
        "compare Delhi and Kerala", _frame(maker_entry), maker_entry.profile, spec, []
    )
    state = [f for f in spec.filters if f.column == "STATE"]
    assert state and state[0].op == "in"
    assert set(state[0].value.split(", ")) == {"Delhi", "Kerala"}


def test_an_existing_filter_is_not_duplicated(maker_entry):
    from app.analysis.spec import Filter

    spec = AnalysisSpec(filters=[Filter(column="STATE", op="==", value="Kerala")],
                        group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters("makers in Delhi", _frame(maker_entry), maker_entry.profile, spec, [])
    assert len(spec.filters) == 1
    assert spec.filters[0].value == "Kerala"


def test_several_named_values_become_an_in_filter(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    notes = []
    infer_missing_filters(
        "compare Delhi and Kerala", _frame(maker_entry), maker_entry.profile, spec, notes
    )
    assert spec.filters[0].op == "in"
    assert set(spec.filters[0].value.split(", ")) == {"Delhi", "Kerala"}


def test_a_value_with_a_code_suffix_is_matched_by_its_name(maker_entry):
    """RTO values carry an office type and a code the user will never type."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    notes = []
    infer_missing_filters(
        "which makers sell most in Trivandrum?", _frame(maker_entry),
        maker_entry.profile, spec, notes,
    )
    placed = [f for f in spec.filters if f.column in ("RTO", "CITY")]
    assert placed and "Trivandrum" in placed[0].value


def test_a_word_matching_two_columns_is_claimed_by_the_stronger_one(maker_entry):
    """"Delhi" is a STATE value and is also inside "Delhi RTO 1". Filtering both
    would narrow Delhi down to its named RTOs only."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters(
        "top makers in Delhi", _frame(maker_entry), maker_entry.profile, spec, []
    )
    columns = {f.column for f in spec.filters}
    assert "STATE" in columns
    assert "RTO" not in columns


def test_no_filter_or_note_for_a_value_repeating_the_folder(maker_entry):
    """The note "filtered CATEGORY to it" was a lie once the filter was dropped."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    notes = []
    infer_missing_filters(
        "top two wheeler makers in Delhi", _frame(maker_entry), maker_entry.profile,
        spec, notes, redundant_value="Two Wheeler",
    )
    assert not [f for f in spec.filters if f.column == "CATEGORY"]
    assert not any("CATEGORY" in note for note in notes)
    # The genuine filter still lands.
    assert [f.column for f in spec.filters] == ["STATE"]


def test_structural_words_never_match(maker_entry):
    """"RTO" is in every RTO value; matching on it would filter to everything."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters(
        "list the RTO totals", _frame(maker_entry), maker_entry.profile, spec, []
    )
    assert not [f for f in spec.filters if f.column == "RTO"]


def test_no_value_in_the_question_adds_no_filter(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    infer_missing_filters("top makers", _frame(maker_entry), maker_entry.profile, spec, [])
    assert spec.filters == []


def _two_measure_profile():
    from app.data.profile import ColumnProfile, DatasetProfile

    def measure(name):
        return ColumnProfile(name=name, dtype="int64", kind="numeric", n_unique=9, n_missing=0)

    return DatasetProfile(path="x", rows=9, columns=[measure("TOTAL"), measure("2WIC")])


def test_jargon_metric_falls_back_to_total():
    """Real failure: "two wheeler registrations" was planned as sum(2WIC)."""
    spec = AnalysisSpec(group_by=["RTO"], metric="2WIC")
    notes = []
    prefer_total_metric("top RTOs by two wheeler registrations", _two_measure_profile(),
                        spec, notes)
    assert spec.metric == "TOTAL"
    assert any("2WIC" in note for note in notes)


def test_a_metric_the_question_names_is_kept():
    spec = AnalysisSpec(group_by=["RTO"], metric="2WIC")
    prefer_total_metric("top RTOs by 2WIC", _two_measure_profile(), spec, [])
    assert spec.metric == "2WIC"


def test_arbitrary_ascending_sort_is_corrected(maker_entry):
    """"share of each fuel type" came back 0.01% first; a ranking reads big-first."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", sort_desc=False)
    notes = []
    apply_hints(spec, analyse_question("share of each maker", None), maker_entry.profile, notes)
    assert spec.sort_desc is True
    assert any("largest first" in note for note in notes)


def test_an_explicitly_ascending_question_is_left_alone(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", sort_desc=False)
    notes = []
    apply_hints(spec, analyse_question("the lowest makers", None), maker_entry.profile, notes)
    assert spec.sort_desc is False
    assert not any("largest first" in note for note in notes)


@pytest.mark.parametrize(
    "question, by_volume",
    [
        ("what about the growth in the cities with highest registrations", True),
        ("growth in the biggest cities", True),
        ("top 10 makers by registrations", True),
        # The superlative is about growth itself, so rank by growth.
        ("which city grew the fastest", False),
        ("which maker grew the most", False),
        ("fastest growing makers", False),
    ],
)
def test_a_superlative_about_size_ranks_by_size(maker_entry, question, by_volume):
    """"growth in the cities with highest registrations" ranked by percent, so it
    answered about the smallest cities."""
    hints = analyse_question(question, None)
    assert hints.wants_volume_order is by_volume

    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", derive="growth_pct")
    apply_hints(spec, hints, maker_entry.profile, [])
    assert spec.sort_by == ("value" if by_volume else "auto")


def test_a_deliberate_sort_key_is_not_overridden(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", derive="growth_pct",
                        sort_by="change")
    apply_hints(spec, analyse_question("growth in the biggest cities", None),
                maker_entry.profile, [])
    assert spec.sort_by == "change"


@pytest.mark.parametrize(
    "question",
    ["what about the growth in the cities with highest registrations",
     "which cities had the most growth", "show me total registrations"],
)
def test_ordinary_english_never_becomes_a_filter(maker_entry, question):
    """"cities **with** highest registrations" matched six CLASS values containing
    WITH and cut the answer to 228 of 46,230 rows."""
    spec = AnalysisSpec(group_by=["CITY"], metric="TOTAL")
    infer_missing_filters(question, _frame(maker_entry), maker_entry.profile, spec, [])
    assert spec.filters == []


def test_a_stingy_unrequested_limit_is_widened(maker_entry):
    """"top makers in Bangalore" came back with two rows and no count was asked for."""
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", limit=2)
    notes = []
    apply_hints(spec, analyse_question("top makers in Bangalore", None),
                maker_entry.profile, notes)
    assert spec.limit == 10
    assert any("no row count" in note for note in notes)


def test_a_requested_small_limit_is_respected(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", limit=10)
    apply_hints(spec, analyse_question("top 2 makers", None), maker_entry.profile, [])
    assert spec.limit == 2


def test_a_single_total_is_not_widened(maker_entry):
    """No group_by means one number, not a ranking that needs ten rows."""
    spec = AnalysisSpec(group_by=[], metric="TOTAL", limit=1)
    apply_hints(spec, analyse_question("total registrations", None), maker_entry.profile, [])
    assert spec.limit == 1


def test_apply_hints_flips_sort_and_limit(maker_entry):
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", sort_desc=True, limit=10)
    notes = []
    apply_hints(spec, analyse_question("bottom 3 makers", None), maker_entry.profile, notes)
    assert spec.sort_desc is False
    assert spec.limit == 3
    assert len(notes) == 2


# --- a value word is not the name of its column -----------------------------


def _fuel_entry(tmp_path):
    """FUEL as the real data spells it: two electric values, plus a hybrid."""
    import pandas as pd

    from app.data.catalog import DatasetEntry

    path = tmp_path / "FUEL.parquet"
    pd.DataFrame(
        [
            {"STATE": "Uttar Pradesh", "YEAR": 2024, "FUEL": "ELECTRIC(BOV)", "TOTAL": 500},
            {"STATE": "Uttar Pradesh", "YEAR": 2024, "FUEL": "PURE EV", "TOTAL": 120},
            {"STATE": "Uttar Pradesh", "YEAR": 2024, "FUEL": "STRONG HYBRID EV", "TOTAL": 40},
            {"STATE": "Bihar", "YEAR": 2024, "FUEL": "PETROL", "TOTAL": 300},
        ]
    ).to_parquet(path, index=False)
    return DatasetEntry(key="Three Wheeler/FUEL", path=path,
                        category="Three Wheeler", breakdown="FUEL")


def test_a_value_word_pins_the_table_but_not_the_grouping(catalog):
    """"ev" was listed as a synonym for the FUEL column, so "highest three
    wheeler ev registration in which state" grouped by FUEL and answered with a
    single electric row, ignoring the state it was asked for."""
    hints = analyse_question("highest three wheeler ev registration in which state", catalog)
    assert hints.breakdown == "FUEL"       # still reads from the FUEL table
    assert not hints.breakdown_named       # but that is not what to group by

    spec = AnalysisSpec(group_by=["FUEL"], metric="TOTAL")
    apply_hints(spec, hints, catalog.get("Three Wheeler", "FUEL").profile, [])
    assert spec.group_by == ["STATE"]


def test_the_column_word_still_decides_the_grouping(catalog):
    hints = analyse_question("the fuel split for three wheelers", catalog)
    assert hints.breakdown == "FUEL"
    assert hints.breakdown_named


def test_ev_covers_every_electric_spelling(tmp_path):
    """`FUEL == ELECTRIC(BOV)` dropped PURE EV -- 22% of electric three-wheelers."""
    entry = _fuel_entry(tmp_path)
    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL",
                        filters=[Filter(column="FUEL", op="==", value="ELECTRIC(BOV)")])
    notes = []

    widen_value_group("highest three wheeler ev registration in which state",
                      _frame(entry), entry.profile, spec, notes)

    assert [(f.op, f.value) for f in spec.filters] == [("in", "ELECTRIC(BOV), PURE EV")]
    assert "HYBRID" not in " ".join(notes)


def test_a_hybrid_is_not_an_ev(tmp_path):
    entry = _fuel_entry(tmp_path)
    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL")
    widen_value_group("electric three wheelers", _frame(entry), entry.profile, spec, [])
    assert "STRONG HYBRID EV" not in " ".join(str(f.value) for f in spec.filters)


def test_grouping_by_fuel_is_not_narrowed_to_the_evs(tmp_path):
    """"the ev and petrol split" wants them side by side, not the EVs alone."""
    entry = _fuel_entry(tmp_path)
    spec = AnalysisSpec(group_by=["FUEL"], metric="TOTAL")
    widen_value_group("the ev and petrol split", _frame(entry), entry.profile, spec, [])
    assert spec.filters == []


def test_a_fuel_filter_the_model_meant_is_left_alone(tmp_path):
    entry = _fuel_entry(tmp_path)
    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL",
                        filters=[Filter(column="FUEL", op="==", value="PETROL")])
    widen_value_group("electric three wheelers", _frame(entry), entry.profile, spec, [])
    assert [f.value for f in spec.filters] == ["PETROL"]


def test_a_column_filtered_over_and_over_becomes_one_membership():
    """The model wrote three `FUEL in <one value>` filters; ANDed, a row has one
    fuel, so "electric two wheeler registrations by state" returned nothing."""
    spec = AnalysisSpec(
        group_by=["STATE"], metric="TOTAL",
        filters=[
            Filter(column="FUEL", op="in", value="ELECTRIC(BOV)"),
            Filter(column="FUEL", op="in", value="PURE EV"),
            Filter(column="FUEL", op="==", value="STRONG HYBRID EV"),
        ],
    )
    notes = []

    merge_repeated_filters(spec, notes)

    assert [(f.column, f.op, f.value) for f in spec.filters] == [
        ("FUEL", "in", "ELECTRIC(BOV), PURE EV, STRONG HYBRID EV")
    ]
    assert any("cannot all hold at once" in note for note in notes)


def test_repeated_exclusions_are_left_alone():
    """"not petrol and not diesel" really does mean both at once."""
    spec = AnalysisSpec(
        group_by=["STATE"], metric="TOTAL",
        filters=[Filter(column="FUEL", op="!=", value="PETROL"),
                 Filter(column="FUEL", op="!=", value="DIESEL")],
    )
    merge_repeated_filters(spec, [])
    assert len(spec.filters) == 2


def test_filters_on_different_columns_are_untouched():
    spec = AnalysisSpec(
        group_by=["MAKER"], metric="TOTAL",
        filters=[Filter(column="STATE", op="==", value="Delhi"),
                 Filter(column="YEAR", op="==", value="2024")],
    )
    merge_repeated_filters(spec, [])
    assert len(spec.filters) == 2


def test_a_hybrid_swept_in_with_the_evs_is_dropped(tmp_path):
    """The model reached for "electric" and included STRONG HYBRID EV."""
    entry = _fuel_entry(tmp_path)
    spec = AnalysisSpec(
        group_by=["STATE"], metric="TOTAL",
        filters=[Filter(column="FUEL", op="in",
                        value="ELECTRIC(BOV), PURE EV, STRONG HYBRID EV")],
    )

    widen_value_group("electric two wheeler registrations by state",
                      _frame(entry), entry.profile, spec, [])

    assert [f.value for f in spec.filters] == ["ELECTRIC(BOV), PURE EV"]
