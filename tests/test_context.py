"""Carrying scope from one question to the next.

"How did city registrations change year on year?" asked straight after "city
wise registrations in Haryana" answered for the whole country -- the follow-up
lost Haryana entirely. These pin when scope is inherited and, just as
importantly, when it is not.
"""

from __future__ import annotations

import json

import pytest

from app.agent.hints import (
    asks_all_categories,
    continues_previous,
    inherit_filters,
    ranked_dimensions,
)
from app.analysis.spec import AnalysisSpec, Filter
from tests.test_orchestrator import (
    INSIGHTS,
    NARRATIVE,
    PLAN,
    ROUTE,
    _analyst,
)

PREVIOUS = [("STATE", "==", "Delhi")]


def _spec(*filters):
    return AnalysisSpec(group_by=["CITY"], metric="TOTAL", filters=list(filters))


# --- when to continue -------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "how did that change year on year?",
        "break it down by maker",
        "what about three wheelers?",
        "show me the same for 2025",
    ],
)
def test_an_explicit_reference_back_continues(question):
    assert continues_previous(question, _spec(), PREVIOUS)


def test_a_question_with_no_scope_of_its_own_continues():
    """The real case: the follow-up named no place, so it meant the last one."""
    assert continues_previous(
        "How did city registrations change from year to year?", _spec(), PREVIOUS
    )


def test_a_question_naming_its_own_scope_starts_fresh():
    """"top makers in Pune" must not silently stay in Haryana."""
    assert not continues_previous(
        "top makers in Pune", _spec(Filter(column="CITY", op="==", value="Pune")), PREVIOUS
    )


def test_nothing_to_continue_from():
    assert not continues_previous("how did that change?", _spec(), [])


# --- what gets inherited ----------------------------------------------------


def test_the_previous_scope_is_carried_over(maker_entry):
    spec = _spec()
    notes = []
    inherit_filters(spec, PREVIOUS, maker_entry.profile, notes)

    assert [(f.column, f.op, f.value) for f in spec.filters] == PREVIOUS
    assert any("carried" in note for note in notes)


def test_a_column_the_new_question_filtered_is_not_overwritten(maker_entry):
    spec = _spec(Filter(column="STATE", op="==", value="Kerala"))
    inherit_filters(spec, PREVIOUS, maker_entry.profile, [])

    states = [f.value for f in spec.filters if f.column == "STATE"]
    assert states == ["Kerala"]


def test_a_column_the_new_table_lacks_is_skipped(maker_entry):
    spec = _spec()
    inherit_filters(spec, [("NOT_A_COLUMN", "==", "x")], maker_entry.profile, [])
    assert spec.filters == []


# --- end to end -------------------------------------------------------------


def test_a_follow_up_is_answered_under_the_previous_scope(catalog):
    """Two turns: the second names no place and must stay in Delhi."""
    from app.agent import Conversation

    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    first = analyst.ask("top two wheeler makers in Delhi")
    assert any(f.column == "STATE" for f in first.result.spec.filters)

    trend = json.loads(PLAN)
    trend["filters"] = []
    trend["group_by"] = ["MAKER"]
    analyst.backend.responses = [json.dumps(trend), INSIGHTS, NARRATIVE]

    second = analyst.ask("how did that change from year to year?")

    carried = [(f.column, f.value) for f in second.result.spec.filters]
    assert ("STATE", "Delhi") in carried
    assert any("carried" in note for note in second.notes)


def test_an_unrelated_question_does_not_inherit(catalog):
    from app.agent import Conversation

    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    analyst.ask("top two wheeler makers in Delhi")

    fresh = json.loads(PLAN)
    fresh["filters"] = []
    analyst.backend.responses = [json.dumps(fresh), INSIGHTS, NARRATIVE]

    second = analyst.ask("top two wheeler makers in Kerala")

    states = [f.value for f in second.result.spec.filters if f.column == "STATE"]
    assert states == ["Kerala"]


# --- asking for everywhere --------------------------------------------------


@pytest.mark.parametrize(
    "question, nationwide",
    [
        ("show the vehicles registered in the country", True),
        ("registrations nationwide", True),
        ("top makers in India", True),
        # "overall" is an ordinary word, not a place: treating it as nationwide
        # would drop Kerala from "overall registrations in Kerala".
        ("overall registrations", False),
        ("overall registrations in Kerala", False),
        ("Maruti Suzuki India in Kerala", False),
        ("city wise registrations in Karnataka", False),
        ("how did that change year on year", False),
        ("top makers in Pune", False),
    ],
)
def test_nationwide_intent(question, nationwide):
    from app.agent.hints import clears_scope

    assert clears_scope(question) is nationwide


def test_asking_for_the_country_does_not_continue_the_previous_scope():
    """It inherited Karnataka and reported 3.7M as the national total."""
    assert not continues_previous(
        "show the vehicles registered in the country", _spec(),
        [("STATE", "==", "Karnataka")],
    )


def test_asking_for_the_country_drops_a_place_the_model_supplied(maker_entry):
    from app.agent.hints import drop_geographic_scope

    spec = AnalysisSpec(group_by=["CLASS"], metric="TOTAL", filters=[
        Filter(column="STATE", op="==", value="Karnataka"),
        Filter(column="CITY", op="==", value="Bengaluru"),
        Filter(column="CATEGORY", op="==", value="Two Wheeler"),
    ])
    notes = []
    drop_geographic_scope(spec, maker_entry.profile, notes)

    # Places go; a category is not a place, so "two wheelers in the country"
    # stays a two-wheeler question.
    assert [f.column for f in spec.filters] == ["CATEGORY"]
    assert any("whole country" in note for note in notes)


def test_a_country_question_reports_the_country_total(catalog):
    from app.agent import Conversation

    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    analyst.ask("top two wheeler makers in Delhi")

    plan = json.loads(PLAN)
    plan["filters"] = []
    plan["group_by"] = ["MAKER"]
    analyst.backend.responses = [json.dumps(plan), INSIGHTS, NARRATIVE]

    answer = analyst.ask("show the vehicles registered in the country")

    assert not [f for f in answer.result.spec.filters if f.column in ("STATE", "CITY")]
    assert not any("carried" in note for note in answer.notes)


# --- going back up a level -------------------------------------------------


@pytest.mark.parametrize(
    "question, target",
    [
        ("How does Uttar Pradesh compare with the other states?", "STATE"),
        ("How does Pune compare with the other cities?", "CITY"),
        ("how does two wheeler compare against all categories", "CATEGORY"),
        ("rank it versus other states", "STATE"),
        # Not a request to zoom out.
        ("city wise registrations in Uttar Pradesh", None),
        ("top makers in Pune", None),
        ("compare Mumbai and Pune", None),
    ],
)
def test_widening_intent(maker_entry, question, target):
    from app.agent.hints import widening_target

    dimensions = [c.name for c in maker_entry.profile.dimensions]
    assert widening_target(question, dimensions) == target


def test_zooming_out_drops_the_scope_that_made_it_impossible(maker_entry):
    """It compared Uttar Pradesh with itself: filtered to UP *and* Bahraich,
    then grouped by STATE, returning one row."""
    from app.agent.hints import widen_scope

    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL", filters=[
        Filter(column="STATE", op="==", value="Delhi"),
        Filter(column="CITY", op="==", value="Old Delhi"),
    ])
    notes = []
    widen_scope(spec, "STATE", maker_entry.profile, notes)

    assert spec.filters == []
    assert spec.group_by == ["STATE"]
    assert any("dropped" in note for note in notes)


def test_broader_scope_survives_zooming_out(maker_entry):
    """Comparing Pune with other cities inside Kerala stays inside Kerala."""
    from app.agent.hints import widen_scope

    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL", filters=[
        Filter(column="STATE", op="==", value="Kerala"),
        Filter(column="CITY", op="==", value="Trivandrum"),
    ])
    widen_scope(spec, "CITY", maker_entry.profile, [])

    assert [(f.column, f.value) for f in spec.filters] == [("STATE", "Kerala")]
    assert spec.group_by == ["CITY"]


def test_a_comparison_follow_up_beats_inheritance(catalog):
    """The suggestion the system itself offers must not inherit the scope it is
    asking to leave."""
    from app.agent import Conversation

    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    analyst.ask("top two wheeler makers in Delhi")

    plan = json.loads(PLAN)
    plan["filters"] = []
    plan["group_by"] = ["STATE"]
    analyst.backend.responses = [json.dumps(plan), INSIGHTS, NARRATIVE]

    answer = analyst.ask("How does Delhi compare with the other states?")

    assert not [f for f in answer.result.spec.filters if f.column == "STATE"]
    assert answer.result.spec.group_by == ["STATE"]
    assert len(answer.result.frame) > 1, "a comparison needs more than one row"


def test_zooming_out_clears_the_scope_for_what_follows(catalog):
    """After comparing all states the conversation is national; the next
    question must not silently drop back into Uttar Pradesh."""
    from app.agent import Conversation
    from app.agent.memory import Turn

    conversation = Conversation()
    conversation.add(Turn(question="city wise in Delhi",
                          filters=[("STATE", "==", "Delhi")]))
    assert conversation.last_filters == [("STATE", "==", "Delhi")]

    conversation.add(Turn(question="compare with other states", filters=[]))
    assert conversation.last_filters == []


def test_a_suggested_follow_up_carries_its_scope(catalog):
    """The suggestion text must stand alone, not just rely on inheritance."""
    from app.analysis.executor import execute
    from app.analysis.insights import build_followups

    entry = catalog.get("Two Wheeler", "MAKER")
    result = execute(entry, AnalysisSpec(
        filters=[Filter(column="STATE", op="==", value="Delhi")],
        group_by=["MAKER"], metric="TOTAL",
    ))
    questions = " ".join(f.question for f in build_followups(result, entry))
    assert "Delhi" in questions


# --- carrying the vehicle category ------------------------------------------


def test_the_previous_category_is_inherited(catalog):
    """"what is the city breakdown for Manipur?" asked after an ambulance
    question widened to all ten categories: the branch that decides this
    returns before the model runs, and the model is the only stage that sees
    the transcript."""
    from app.agent import Conversation

    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    first = analyst.ask("top two wheeler makers in Delhi")
    assert first.entry.key == "Two Wheeler/MAKER"

    analyst.backend.responses = [ROUTE, PLAN, INSIGHTS, NARRATIVE]
    second = analyst.ask("which makers led in Kerala?")

    assert second.entry.key == "Two Wheeler/MAKER"
    assert any("stayed on 'Two Wheeler'" in note for note in second.notes)


@pytest.mark.parametrize(
    "question",
    [
        "total vehicle registrations in Kerala",
        "how does that compare across all vehicle categories?",
    ],
)
def test_a_fleet_wide_question_does_not_inherit_the_category(catalog, question):
    """The category counterpart of "in the country": a fresh, fleet-wide ask."""
    from app.agent import Conversation

    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    analyst.ask("top two wheeler makers in Delhi")

    analyst.backend.responses = [ROUTE, PLAN, INSIGHTS, NARRATIVE]
    second = analyst.ask(question)

    assert second.entry.spans_all_categories


def test_widening_the_geography_keeps_the_category(catalog):
    """"nationwide" is a different axis -- it drops Delhi, not Two Wheeler."""
    assert not asks_all_categories("how many were registered nationwide?")


# --- a filter must not fight the ranking it was asked for -------------------


def test_a_dimension_the_question_ranks_by_does_not_inherit_its_filter():
    """"which states registered the most tractors?" answered for Manipur alone."""
    spec = AnalysisSpec(group_by=["STATE"], metric="TOTAL")
    assert ranked_dimensions("which states registered the most tractors?", spec) == {"STATE"}


def test_a_question_pointing_back_keeps_the_scope_it_ranks_by():
    """"how did they change?" after "compare Mumbai and Pune" is about those two."""
    spec = AnalysisSpec(group_by=["CITY"], metric="TOTAL")
    assert ranked_dimensions("how did they change year on year?", spec) == set()
