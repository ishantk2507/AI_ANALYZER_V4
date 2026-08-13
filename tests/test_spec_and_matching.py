from __future__ import annotations

import pytest

from app.analysis.spec import AnalysisSpec, analysis_schema, parse_json, routing_schema
from app.data.matching import resolve_column, resolve_value


def test_parse_json_handles_a_code_fence():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_handles_leading_prose():
    assert parse_json('Sure! Here is the plan:\n{"a": {"b": 2}}\nHope that helps.') == {
        "a": {"b": 2}
    }


def test_parse_json_handles_braces_inside_strings():
    assert parse_json('{"title": "a } brace"}') == {"title": "a } brace"}


def test_parse_json_rejects_garbage():
    with pytest.raises(ValueError):
        parse_json("no json here at all")


def test_spec_clamps_a_silly_limit():
    assert AnalysisSpec(limit=100000).limit <= 500
    assert AnalysisSpec(limit=0).limit == 1


def test_spec_falls_back_on_unknown_enums():
    spec = AnalysisSpec(agg="frobnicate", chart="hologram", derive="magic")
    assert (spec.agg, spec.chart, spec.derive) == ("sum", "bar", "none")


def test_spec_dedupes_and_caps_group_by():
    spec = AnalysisSpec(group_by=["A", "A", "B", "C"])
    assert spec.group_by == ["A", "B"]


def test_routing_schema_closes_the_world():
    schema = routing_schema(["Two Wheeler"], ["MAKER", "FUEL"])
    assert schema["properties"]["category"]["enum"] == ["Two Wheeler"]
    assert schema["properties"]["breakdown"]["enum"] == ["MAKER", "FUEL"]


def test_analysis_schema_restricts_columns_to_real_ones():
    schema = analysis_schema(["STATE", "TOTAL"], ["STATE"], ["TOTAL"])
    assert schema["properties"]["metric"]["enum"] == ["TOTAL"]
    assert schema["properties"]["group_by"]["items"]["enum"] == ["STATE"]
    assert schema["properties"]["pivot_on"]["enum"] == ["none", "STATE"]
    assert schema["properties"]["filters"]["items"]["properties"]["column"]["enum"] == [
        "STATE",
        "TOTAL",
    ]


@pytest.mark.parametrize(
    "written, expected",
    [
        ("Delhi", "Delhi"),
        ("delhi", "Delhi"),
        ("DELHI", "Delhi"),
        ("Hero Motocorp", "HERO MOTOCORP LTD"),
        ("hero motocorp ltd", "HERO MOTOCORP LTD"),
        ("Electric", "ELECTRIC(BOV)"),
    ],
)
def test_resolve_value_maps_onto_real_values(written, expected):
    options = ["Delhi", "Kerala", "HERO MOTOCORP LTD", "BAJAJ AUTO LTD", "ELECTRIC(BOV)"]
    assert resolve_value(written, options) == expected


def test_resolve_value_gives_up_rather_than_guessing():
    assert resolve_value("Atlantis", ["Delhi", "Kerala"]) is None


def test_resolve_value_prefers_the_shortest_containment():
    options = ["DELHI", "NEW DELHI DTO", "DELHI CANTT"]
    assert resolve_value("delhi", options) == "DELHI"


RTOS = [
    "TRIVANDRUM RTO - KL1", "KANNUR RTO - KL13", "KOLLAM RTO - KL2",
    "PUNE - MH12", "PIMPRI CHINCHWAD - MH14", "MUMBAI (CENTRAL) - MH01",
]


@pytest.mark.parametrize(
    "written, expected",
    [
        # A code is a whole token, never a prefix of a longer one: KL1 != KL13.
        ("KL1", "TRIVANDRUM RTO - KL1"),
        ("KL13", "KANNUR RTO - KL13"),
        ("MH12", "PUNE - MH12"),
        ("kl2", "KOLLAM RTO - KL2"),
        # City names still work.
        ("Trivandrum", "TRIVANDRUM RTO - KL1"),
        ("Pune", "PUNE - MH12"),
        ("pimpri chinchwad", "PIMPRI CHINCHWAD - MH14"),
    ],
)
def test_rto_codes_and_names_resolve(written, expected):
    assert resolve_value(written, RTOS) == expected


def test_an_unknown_rto_is_not_guessed():
    assert resolve_value("Andheri", RTOS) is None


def test_resolve_column_is_case_insensitive():
    assert resolve_column("total", ["STATE", "TOTAL"]) == "TOTAL"
    assert resolve_column("nonsense", ["STATE", "TOTAL"]) is None
