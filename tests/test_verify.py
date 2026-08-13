from __future__ import annotations

import pandas as pd
import pytest

from app.agent.verify import (
    check_narrative,
    contradicts_result,
    deterministic_summary,
    misattributes_leader,
    unsupported_numbers,
)


@pytest.fixture
def frame():
    return pd.DataFrame(
        {
            "MAKER": ["BAJAJ AUTO LTD", "PIAGGIO VEHICLES PVT LTD", "TVS MOTOR COMPANY LTD"],
            "2024": [46_514, 88_000, 40_000],
            "2025": [970_931, 188_728, 73_851],
            "growth_%": [1987.4, 114.5, 84.6],
        }
    )


def test_the_real_hallucination_is_caught(frame):
    """Verbatim from a real run: the model subtracted a number it invented."""
    narrative = "Bajaj: 970,931 - 46,514 = 924,417 increase."
    assert unsupported_numbers(narrative, frame) == ["924,417"]


def test_numbers_copied_from_the_table_pass(frame):
    narrative = "Bajaj Auto led with 970,931 registrations in 2025, up from 46,514 in 2024."
    assert unsupported_numbers(narrative, frame) == []


def test_percentages_are_checked(frame):
    assert unsupported_numbers("Bajaj grew 1987.4%.", frame) == []
    assert unsupported_numbers("Bajaj grew 42.0%.", frame) == ["42.0%"]


def test_small_integers_are_not_flagged(frame):
    """'top 3' and ordinals are not claims about the data."""
    assert unsupported_numbers("The top 3 makers, ranked 1 to 3.", frame) == []


def test_column_totals_are_allowed(frame):
    total = frame["2025"].sum()  # 1,233,510
    assert unsupported_numbers(f"Together they registered {total:,}.", frame) == []


def test_rounding_slack(frame):
    assert unsupported_numbers("Bajaj reached 970,930.", frame) == []
    assert unsupported_numbers("Bajaj reached 870,931.", frame) == ["870,931"]


def test_empty_inputs_are_safe(frame):
    assert unsupported_numbers("", frame) == []
    assert unsupported_numbers("anything", pd.DataFrame()) == []


def test_a_narrative_denying_a_non_empty_table_is_caught(frame):
    """Verbatim from a real run, printed directly above five matching rows."""
    denial = "No two-wheeler makers in the provided table match the filters for Delhi."
    assert contradicts_result(denial, frame)


@pytest.mark.parametrize(
    "narrative",
    [
        "Top 5 RTOs are not listed in the table.",
        "The data does not contain Kerala.",
        "This cannot be determined from the given data.",
    ],
)
def test_other_denial_phrasings(narrative, frame):
    assert contradicts_result(narrative, frame)


@pytest.mark.parametrize(
    "narrative",
    [
        "Bajaj Auto led with 970,931 registrations.",
        "No maker exceeded 1,000,000 units.",
        "Growth was not uniform across makers.",
    ],
)
def test_legitimate_negatives_are_not_flagged(narrative, frame):
    assert not contradicts_result(narrative, frame)


def test_denial_is_fine_when_the_table_really_is_empty():
    assert not contradicts_result("No rows matched.", pd.DataFrame())


def test_the_real_misattribution_is_caught(frame):
    """Verbatim from a real run: Honda named as top, above a table led by Bajaj."""
    assert misattributes_leader(
        "Piaggio is the top two-wheeler maker in Delhi.", frame, ["MAKER"]
    )


def test_naming_the_actual_leader_passes(frame):
    assert not misattributes_leader(
        "Bajaj Auto is the top maker, with 970,931 registrations.", frame, ["MAKER"]
    )


def test_a_sentence_naming_several_entities_is_left_alone(frame):
    """"X leads here while Y leads there" is ambiguous, not necessarily wrong."""
    assert not misattributes_leader(
        "Bajaj leads overall while Piaggio leads on growth.", frame, ["MAKER"]
    )


def test_no_superlative_means_no_claim_to_check(frame):
    assert not misattributes_leader("Piaggio registered 188,728 units.", frame, ["MAKER"])


def test_corporate_boilerplate_does_not_match_a_row(frame):
    """Every row ends in LTD; matching on it would flag everything."""
    assert not misattributes_leader(
        "The top company limited its growth.", frame, ["MAKER"]
    )


def test_check_narrative_reports_each_failure_kind(frame):
    def kind(text):
        issue = check_narrative(text, frame, ["MAKER"])
        return issue.kind if issue else None

    assert kind("Bajaj Auto led with 970,931 registrations.") is None
    assert kind("970,931 - 46,514 = 924,417 increase.") == "invented_numbers"
    assert kind("No makers in the provided table match.") == "denied_results"
    assert kind("Piaggio is the top maker.") == "wrong_leader"


def test_check_narrative_hint_names_the_real_leader(frame):
    issue = check_narrative("Piaggio is the top maker.", frame, ["MAKER"])
    assert "BAJAJ AUTO LTD" in issue.hint
    assert "BAJAJ AUTO LTD" in issue.note


def test_console_facing_strings_are_ascii(frame, maker_entry):
    """A cp1252 Windows console cannot print these, and it crashes rather than
    degrade -- after the analysis has already succeeded."""
    from app.agent.prompts import describe_spec
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec, Filter
    from app.llm.base import BackendInfo

    spec = AnalysisSpec(
        filters=[Filter(column="STATE", op="==", value="Delhi")],
        group_by=["MAKER", "STATE"], metric="TOTAL", pivot_on="YEAR", derive="growth_pct",
    )
    result = AnalysisResult(
        frame=frame, label_columns=["MAKER"], value_columns=["2025"],
        spec=spec, dataset_key=maker_entry.key,
    )

    for text in (
        describe_spec(spec, maker_entry.key),
        deterministic_summary(result, maker_entry),
        BackendInfo(name="llama_cpp", model="model.gguf", gpu=False).describe(),
        result.to_markdown(),
    ):
        text.encode("cp1252")  # raises UnicodeEncodeError if it would crash the CLI


def test_deterministic_summary_only_uses_real_numbers(frame, maker_entry):
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec

    result = AnalysisResult(
        frame=frame,
        label_columns=["MAKER"],
        value_columns=["2024", "2025", "growth_%"],
        spec=AnalysisSpec(),
        dataset_key=maker_entry.key,
    )
    summary = deterministic_summary(result, maker_entry)

    assert "BAJAJ AUTO LTD" in summary
    assert unsupported_numbers(summary, frame) == []


def test_internal_repair_notes_are_never_shown_to_the_narrator(maker_entry):
    """Handing them over produced a row count quoted as a vehicle total, and an
    invented caveat about the question not saying "3WT"."""
    from app.agent.prompts import build_narrative_messages
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec

    clean = pd.DataFrame({"CITY": ["Pune"], "TOTAL": [27617]})
    result = AnalysisResult(
        frame=clean, label_columns=["CITY"], value_columns=["TOTAL"],
        spec=AnalysisSpec(), dataset_key=maker_entry.key,
        notes=["'Three Wheeler/CLASS' has 7,141 rows and its CLASS column is unused; "
               "read 'Three Wheeler/MAKER' (46,514 rows) instead",
               "question did not name '3WT'; used 'TOTAL' instead"],
    )
    prompt = build_narrative_messages("how many three wheelers in pune", result,
                                      maker_entry)[-1]["content"]

    assert "27,617" in prompt          # the answer is there
    assert "46,514" not in prompt      # the plumbing is not
    assert "7,141" not in prompt
    assert "3WT" not in prompt
    assert "CAVEAT" not in prompt.upper()


def test_a_single_row_summary_is_a_statement_not_a_ranking(maker_entry):
    """"Pune leads on TOTAL, out of 1 rows" is not English."""
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec

    one = pd.DataFrame({"CITY": ["Pune"], "TOTAL": [27617]})
    result = AnalysisResult(frame=one, label_columns=["CITY"], value_columns=["TOTAL"],
                            spec=AnalysisSpec(), dataset_key=maker_entry.key)
    summary = deterministic_summary(result, maker_entry)

    assert "Pune" in summary and "27,617" in summary
    assert "1 rows" not in summary
    assert "leads" not in summary


def test_echoed_section_headers_are_stripped():
    """The model keeps emitting a bare "FINDINGS:" line despite being told not to."""
    from app.agent.verify import tidy_narrative

    text = "The categories are:\n\nFINDINGS:\n\n- It holds 78.8% of the total."
    assert tidy_narrative(text) == "The categories are:\n\n- It holds 78.8% of the total."


def test_a_caution_with_content_survives():
    """Only bare headers go; "CAUTION: <the caveat>" is the point."""
    from app.agent.verify import tidy_narrative

    text = "Bajaj led.\n- CAUTION: the base was tiny."
    assert tidy_narrative(text) == text


def test_tidy_narrative_handles_empty_input():
    from app.agent.verify import tidy_narrative

    assert tidy_narrative("") == ""
    assert tidy_narrative("FINDINGS:") == ""


def test_a_single_total_is_named_by_its_scope_not_the_table(maker_entry):
    """"MAKER: TOTAL 4,001,373" -- the breakdown is not the subject of a total."""
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec, Filter

    result = AnalysisResult(
        frame=pd.DataFrame({"TOTAL": [4001373]}),
        label_columns=[], value_columns=["TOTAL"],
        spec=AnalysisSpec(filters=[Filter(column="STATE", op="==", value="Tamil Nadu")]),
        dataset_key=maker_entry.key,
    )
    summary = deterministic_summary(result, maker_entry)

    # The subject is the scope; the table name only appears as the source.
    assert summary.startswith("**Tamil Nadu**")
    assert "4,001,373" in summary


def test_a_scalar_with_no_filters_still_reads_sensibly(maker_entry):
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec

    result = AnalysisResult(
        frame=pd.DataFrame({"TOTAL": [55808972]}),
        label_columns=[], value_columns=["TOTAL"],
        spec=AnalysisSpec(), dataset_key=maker_entry.key,
    )
    assert "Total" in deterministic_summary(result, maker_entry)


def test_downcast_floats_are_formatted_not_printed_in_scientific_notation():
    """dtype downcasting yields numpy float32, which is not a Python float, so an
    isinstance check missed it and the CLI printed 4.001373e+06."""
    import numpy as np

    from app.analysis.executor import _format_cell

    assert _format_cell(np.float32(4001373.0)) == "4,001,373"
    assert _format_cell(np.float64(1234.5)) == "1,234.50"
    assert _format_cell(np.int32(1234)) == "1,234"


@pytest.mark.parametrize(
    "sort_by, derive, expected",
    [
        ("value", "growth_pct", "2025"),      # ranked by size
        ("auto", "growth_pct", "growth_%"),   # ranked by percent
        ("change", "growth_pct", "change"),   # ranked by absolute movement
    ],
)
def test_the_summary_headline_matches_the_ranking(frame, maker_entry,
                                                  sort_by, derive, expected):
    """"Nagar Lucknow leads on change" about a table sorted by size, where a
    different city led on change by four times as much."""
    from app.agent.verify import _headline_column
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec

    result = AnalysisResult(
        frame=frame, label_columns=["MAKER"],
        value_columns=["2024", "2025", "growth_%", "change"],
        spec=AnalysisSpec(sort_by=sort_by, derive=derive), dataset_key=maker_entry.key,
    )
    assert _headline_column(result, result.value_columns) == expected


def test_summary_wording_follows_the_sort_direction(frame, maker_entry):
    from app.analysis.executor import AnalysisResult
    from app.analysis.spec import AnalysisSpec

    def summarise(sort_desc):
        result = AnalysisResult(
            frame=frame, label_columns=["MAKER"], value_columns=["2025"],
            spec=AnalysisSpec(sort_desc=sort_desc), dataset_key=maker_entry.key,
        )
        return deterministic_summary(result, maker_entry)

    assert "leads on" in summarise(True)
    assert "is lowest on" in summarise(False)  # "leads" is wrong for a bottom-5
