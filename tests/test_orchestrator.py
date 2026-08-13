"""End-to-end agent tests with a stub backend.

These pin the wiring and the failure handling without needing a 2GB model
loaded, so they run in milliseconds and stay deterministic.
"""

from __future__ import annotations

import json
from typing import List

import pytest

from app.agent import Analyst, Conversation
from app.llm.base import BackendInfo, BackendUnavailable, LLMBackend


class StubBackend(LLMBackend):
    """Replays canned responses and records what it was asked."""

    def __init__(self, responses: List[str]):
        self.responses = list(responses)
        self.calls: List[dict] = []

    def chat(self, messages, *, schema=None, temperature=0.0, max_tokens=512,
             stop=None, model=None):
        self.calls.append({"messages": messages, "schema": schema, "model": model})
        if not self.responses:
            return "no more canned responses"
        return self.responses.pop(0)

    def info(self):
        return BackendInfo(name="stub", model="stub", gpu=False)


ROUTE = json.dumps({"category": "Two Wheeler", "breakdown": "MAKER", "reason": "makers"})
PLAN = json.dumps(
    {
        "filters": [{"column": "STATE", "op": "==", "value": "Delhi"}],
        "group_by": ["MAKER"],
        "metric": "TOTAL",
        "agg": "sum",
        "pivot_on": "none",
        "derive": "none",
        "sort_desc": True,
        "limit": 5,
        "chart": "bar",
        "title": "Top makers in Delhi",
    }
)
# Delhi totals: Hero 500+550=1050, Bajaj 300+600=900, TVS 200+100=300.
NARRATIVE = "Hero MotoCorp leads Delhi with 1,050 registrations."

#: The interpretation stage runs between planning and narration, so it consumes
#: a response of its own. Quotes only a number that is really in the table.
INSIGHTS = "- Hero MotoCorp's 1,050 puts it clearly ahead in Delhi."

#: Names both the category and the breakdown, so routing is deterministic and
#: the model is never asked -- hence no ROUTE in these tests' canned responses.
PINNED = "top two wheeler makers in Delhi"
#: Names the category but not the breakdown, so the model still routes.
ROUTED = "top two wheeler RTOs"


def _analyst(catalog, responses, **kwargs):
    return Analyst(backend=StubBackend(responses), catalog=catalog,
                   use_cache=False, **kwargs)


def test_full_pipeline(catalog):
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE])
    answer = analyst.ask(PINNED)

    assert answer.ok
    assert answer.entry.key == "Two Wheeler/MAKER"
    assert answer.narrative == NARRATIVE
    assert answer.chart_path is not None and answer.chart_path.is_file()
    assert answer.result.frame.iloc[0]["MAKER"] == "HERO MOTOCORP LTD"
    assert answer.result.frame.iloc[0]["TOTAL"] == 1050
    assert set(answer.timings) == {"route", "plan", "execute", "interpret",
                                   "chart", "narrate"}


def test_schemas_are_built_from_the_real_catalog(catalog):
    # ROUTED, not PINNED: a pinned question skips the routing call entirely.
    analyst = _analyst(catalog, [ROUTE, PLAN, INSIGHTS, NARRATIVE])
    analyst.ask(ROUTED)

    routing_schema = analyst.backend.calls[0]["schema"]
    assert routing_schema["properties"]["category"]["enum"] == catalog.categories

    plan_schema = analyst.backend.calls[1]["schema"]
    assert plan_schema["properties"]["metric"]["enum"] == ["TOTAL"]
    assert "MAKER" in plan_schema["properties"]["group_by"]["items"]["enum"]


def test_plan_is_retried_then_repaired(catalog):
    analyst = _analyst(catalog, ["not json", "still not json", "nope", INSIGHTS, NARRATIVE])

    answer = analyst.ask(PINNED)
    assert answer.ok
    assert any("could not build a plan" in note for note in answer.notes)
    assert not answer.result.is_empty  # the fallback plan still produced an answer


def test_routing_falls_back_to_the_previous_table(catalog):
    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    analyst.ask(PINNED)

    analyst.backend.responses = ["garbage", "garbage", "garbage", PLAN, INSIGHTS, NARRATIVE]
    answer = analyst.ask("and in Kerala?")
    assert answer.entry.key == "Two Wheeler/MAKER"
    assert any("reused the previous one" in note for note in answer.notes)


def test_conversation_remembers_turns(catalog):
    conversation = Conversation()
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE], conversation=conversation)
    analyst.ask(PINNED)

    assert len(conversation.turns) == 1
    assert conversation.last_dataset == "Two Wheeler/MAKER"
    assert PINNED in conversation.context_block()


def test_empty_result_skips_narration_and_explains_itself(catalog):
    plan = json.loads(PLAN)
    plan["filters"] = [{"column": "YEAR", "op": ">", "value": "2099"}]
    # Names neither category nor breakdown, so the model routes and the answer
    # spans every category -- still empty, which is the point.
    analyst = _analyst(catalog, [ROUTE, json.dumps(plan), INSIGHTS, NARRATIVE])

    answer = analyst.ask("registrations after 2099")
    assert answer.ok
    assert answer.result.is_empty
    assert "No rows matched" in answer.narrative
    assert "narrate" not in answer.timings  # the model was not asked to invent one


def test_invented_numbers_fall_back_to_a_table_derived_summary(catalog):
    """If the model keeps inventing figures, ship the boring true answer instead."""
    liar = "Hero registered 999,999,999 units, up from 888,888,888."
    analyst = _analyst(catalog, [PLAN, INSIGHTS, liar, liar])

    answer = analyst.ask(PINNED)

    assert answer.ok
    assert "999,999,999" not in answer.narrative
    assert "HERO MOTOCORP LTD" in answer.narrative
    assert any("invented numbers" in note for note in answer.notes)


def test_narration_is_retried_before_giving_up(catalog):
    liar = "Hero registered 999,999,999 units."
    honest = "Hero MotoCorp leads Delhi with 1,050 registrations."
    analyst = _analyst(catalog, [PLAN, INSIGHTS, liar, honest])

    answer = analyst.ask(PINNED)
    assert answer.narrative == honest
    assert not any("invented numbers" in note for note in answer.notes)


def test_the_interpretation_can_use_a_different_model(catalog, monkeypatch):
    """Writing the business reading is the one job a 3B does badly, so it can be
    sent to a larger model while the constrained stages stay on the small one."""
    from app.config import settings

    monkeypatch.setattr(settings, "insight_model", "big-model")
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE])
    analyst.ask(PINNED)

    used = [call["model"] for call in analyst.backend.calls]
    assert used == [None, "big-model", None], used


def test_no_override_leaves_every_stage_on_the_main_model(catalog):
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE])
    analyst.ask(PINNED)
    assert all(call["model"] is None for call in analyst.backend.calls)


def test_denying_a_non_empty_table_falls_back_to_the_summary(catalog):
    denial = "No makers in the provided table match the filters."
    analyst = _analyst(catalog, [PLAN, INSIGHTS, denial, denial])

    answer = analyst.ask(PINNED)
    assert "HERO MOTOCORP LTD" in answer.narrative
    assert any("reported no results" in note for note in answer.notes)


def test_naming_the_wrong_leader_falls_back_to_the_summary(catalog):
    """Delhi is led by Hero (1,050); claiming TVS is wrong even though 300 is real."""
    wrong = "TVS Motor is the top maker in Delhi, with 300 registrations."
    analyst = _analyst(catalog, [PLAN, INSIGHTS, wrong, wrong])

    answer = analyst.ask(PINNED)
    assert "HERO MOTOCORP LTD" in answer.narrative
    assert any("wrong leader" in note for note in answer.notes)


def test_the_rewrite_is_told_which_leader_is_right(catalog):
    wrong = "TVS Motor is the top maker in Delhi."
    honest = "Hero MotoCorp leads Delhi with 1,050 registrations."
    analyst = _analyst(catalog, [PLAN, INSIGHTS, wrong, honest])

    answer = analyst.ask(PINNED)
    assert answer.narrative == honest
    retry_prompt = analyst.backend.calls[-1]["messages"][-1]["content"]
    assert "HERO MOTOCORP LTD" in retry_prompt


def test_a_partial_table_is_widened_when_its_breakdown_is_unused(catalog):
    """NORM holds materially fewer registrations, so ranking out of it undercounts."""
    plan = json.loads(PLAN)
    plan["group_by"] = ["RTO"]
    plan["filters"] = []
    route = json.dumps({"category": "Two Wheeler", "breakdown": "NORM", "reason": "x"})
    analyst = _analyst(catalog, [route, json.dumps(plan), INSIGHTS, NARRATIVE])

    answer = analyst.ask(ROUTED)

    assert answer.entry.breakdown in {"MAKER", "FUEL"}
    assert any("fewer TOTAL" in note for note in answer.notes)
    # The reported plan must name the table actually read, not the routed one.
    assert "NORM" not in answer.plan
    assert answer.entry.key in answer.plan


def test_a_category_filter_repeating_the_folder_is_dropped(catalog):
    """Reading Three Wheeler/CLASS *is* the three-wheeler filter. Re-applying it
    dropped 13,326 of Pune's 27,617 registrations, because that file also holds
    rows labelled 'Construction Equipment Vehicle'."""
    from app.agent.orchestrator import _drop_redundant_category_filter
    from app.analysis.spec import AnalysisSpec, Filter

    entry = catalog.get("Two Wheeler", "MAKER")
    spec = AnalysisSpec(
        filters=[Filter(column="CATEGORY", op="==", value="Two Wheeler"),
                 Filter(column="STATE", op="==", value="Delhi")],
        group_by=["MAKER"], metric="TOTAL",
    )
    _drop_redundant_category_filter(entry, spec)

    assert [f.column for f in spec.filters] == ["STATE"]


def test_a_filter_naming_a_different_category_is_kept(catalog):
    from app.agent.orchestrator import _drop_redundant_category_filter
    from app.analysis.spec import AnalysisSpec, Filter

    entry = catalog.get("Two Wheeler", "MAKER")
    spec = AnalysisSpec(
        filters=[Filter(column="CATEGORY", op="==", value="Three Wheeler")],
        group_by=["MAKER"], metric="TOTAL",
    )
    _drop_redundant_category_filter(entry, spec)
    assert len(spec.filters) == 1


def test_the_same_question_agrees_across_sibling_tables(catalog):
    """The whole point: which breakdown routing picks must not change the answer."""
    from app.agent.orchestrator import _drop_redundant_category_filter
    from app.analysis.executor import execute
    from app.analysis.spec import AnalysisSpec, Filter

    totals = set()
    for breakdown in catalog.breakdowns_for("Two Wheeler"):
        # NORM is the deliberately-partial extract in this fixture, so it is
        # expected to disagree -- that is what the widening logic exists for.
        if breakdown == "NORM":
            continue
        entry = catalog.get("Two Wheeler", breakdown)
        spec = AnalysisSpec(
            filters=[Filter(column="CATEGORY", op="==", value="Two Wheeler"),
                     Filter(column="STATE", op="==", value="Delhi")],
            group_by=["STATE"], metric="TOTAL",
        )
        _drop_redundant_category_filter(entry, spec)
        result = execute(entry, spec)
        if not result.is_empty:
            totals.add(int(result.frame["TOTAL"].iloc[0]))

    assert len(totals) == 1, f"tables disagree on the same question: {totals}"


def test_an_unused_breakdown_falls_back_to_the_base_table(catalog, monkeypatch):
    """Which breakdown routing lands on must not change the answer, so anything
    not *about* its breakdown is read from the canonical table."""
    from app.agent.orchestrator import Analyst
    from app.analysis.spec import AnalysisSpec
    from app.config import settings

    # The fixture has no CLASS table; FUEL stands in as the configured base.
    monkeypatch.setattr(settings, "base_breakdown", "FUEL")
    analyst = Analyst(backend=StubBackend([]), catalog=catalog, use_cache=False)

    routed = catalog.get("Two Wheeler", "MAKER")
    spec = AnalysisSpec(group_by=["RTO"], metric="TOTAL")
    assert analyst._widen_table(routed, spec, []).breakdown == "FUEL"


def test_a_question_about_the_breakdown_keeps_its_table(catalog, monkeypatch):
    from app.agent.orchestrator import Analyst
    from app.analysis.spec import AnalysisSpec
    from app.config import settings

    monkeypatch.setattr(settings, "base_breakdown", "FUEL")
    analyst = Analyst(backend=StubBackend([]), catalog=catalog, use_cache=False)

    routed = catalog.get("Two Wheeler", "MAKER")
    spec = AnalysisSpec(group_by=["MAKER"], metric="TOTAL")
    assert analyst._widen_table(routed, spec, []).breakdown == "MAKER"


def test_a_materially_short_base_table_is_not_preferred(catalog, monkeypatch):
    """NORM is the partial extract in this fixture; preferring it would undercount."""
    from app.agent.orchestrator import Analyst
    from app.analysis.spec import AnalysisSpec
    from app.config import settings

    monkeypatch.setattr(settings, "base_breakdown", "NORM")
    analyst = Analyst(backend=StubBackend([]), catalog=catalog, use_cache=False)

    routed = catalog.get("Two Wheeler", "MAKER")
    spec = AnalysisSpec(group_by=["RTO"], metric="TOTAL")
    assert analyst._widen_table(routed, spec, []).breakdown != "NORM"


def test_tables_covering_the_same_data_are_not_swapped(catalog):
    """MAKER has 6.5x the rows of CLASS but 0.11% more registrations. Swapping on
    row count changed nothing and emitted an alarming note."""
    maker = catalog.get("Two Wheeler", "MAKER")
    fuel = catalog.get("Two Wheeler", "FUEL")
    # Same underlying registrations, different granularity.
    assert fuel.profile.rows == maker.profile.rows
    assert fuel.profile.column("TOTAL").total == maker.profile.column("TOTAL").total

    plan = json.loads(PLAN)
    plan["group_by"] = ["RTO"]
    plan["filters"] = []
    route = json.dumps({"category": "Two Wheeler", "breakdown": "FUEL", "reason": "x"})
    analyst = _analyst(catalog, [route, json.dumps(plan), INSIGHTS, NARRATIVE])

    answer = analyst.ask(ROUTED)

    assert answer.entry.key == "Two Wheeler/FUEL"  # stayed put
    assert not any("fewer" in note for note in answer.notes)


def test_widening_is_skipped_when_the_profile_is_only_a_sample(catalog, monkeypatch):
    """A partial total cannot be compared against another table's."""
    from app.agent.orchestrator import _metric_total

    entry = catalog.get("Two Wheeler", "MAKER")
    monkeypatch.setattr(entry.profile, "sampled", True)
    assert _metric_total(entry, "TOTAL") is None


def test_the_table_is_kept_when_its_breakdown_is_the_subject(catalog):
    route = json.dumps({"category": "Two Wheeler", "breakdown": "FUEL", "reason": "x"})
    plan = json.loads(PLAN)
    plan["group_by"] = ["FUEL"]
    plan["filters"] = []
    analyst = _analyst(catalog, [route, json.dumps(plan), INSIGHTS, NARRATIVE])

    answer = analyst.ask("two wheeler registrations by fuel")
    assert answer.entry.key == "Two Wheeler/FUEL"


def test_routing_skips_the_model_when_the_question_names_the_table(catalog):
    analyst = _analyst(catalog, [PLAN, INSIGHTS, NARRATIVE])  # no routing response supplied

    answer = analyst.ask("top two wheeler makers in Delhi")

    assert answer.ok
    assert answer.entry.key == "Two Wheeler/MAKER"
    assert len(analyst.backend.calls) == 3  # plan + interpret + narrate
    assert answer.timings["route"] < 0.05


def test_growth_intent_survives_a_plan_that_dropped_it(catalog):
    """The end-to-end version of the real-model failure."""
    plain = json.loads(PLAN)
    plain["filters"] = []
    plain["derive"] = "none"
    plain["pivot_on"] = "none"
    analyst = _analyst(catalog, [json.dumps(plain), NARRATIVE])

    answer = analyst.ask("which two wheeler makers grew fastest from 2024 to 2025?")

    assert answer.result.spec.derive == "growth_pct"
    assert "growth_%" in answer.result.frame.columns
    assert answer.result.frame.iloc[0]["MAKER"] == "BAJAJ AUTO LTD"  # +100%, not the biggest


def test_missing_backend_is_reported_not_raised(catalog):
    class Dead(LLMBackend):
        def chat(self, *a, **k):
            raise BackendUnavailable("no model file found")

        def info(self):
            raise BackendUnavailable("no model file found")

    analyst = Analyst(backend=Dead(), catalog=catalog, use_cache=False)
    answer = analyst.ask("anything")
    assert not answer.ok
    assert "no model file found" in answer.error


def test_empty_catalog_is_reported(catalog, tmp_path):
    from app.data.catalog import Catalog

    analyst = Analyst(backend=StubBackend([]), catalog=Catalog(tmp_path, {}), use_cache=False)
    answer = analyst.ask("anything")
    assert not answer.ok
    assert "No datasets found" in answer.error


@pytest.mark.parametrize("chart", ["bar", "line", "pie"])
def test_chart_type_from_the_plan_is_honoured(catalog, chart):
    plan = json.loads(PLAN)
    plan["chart"] = chart
    plan["filters"] = []
    analyst = _analyst(catalog, [json.dumps(plan), INSIGHTS, NARRATIVE])

    answer = analyst.ask(PINNED)
    assert answer.ok and answer.chart_path is not None
