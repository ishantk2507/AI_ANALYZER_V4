"""The city layer: turning RTO office names into something a person would type."""

from __future__ import annotations

import pandas as pd
import pytest

from app.data.places import (
    CITY_COLUMN,
    add_derived_columns,
    canonical_place,
    extract_city,
    states_for_place,
)


@pytest.mark.parametrize(
    "office, city",
    [
        # Code suffix and directional qualifier both stripped.
        ("MUMBAI (WEST) - MH2", "Mumbai"),
        ("MUMBAI (CENTRAL) - MH1", "Mumbai"),
        ("BENGALURU EAST  RTO - KA3", "Bengaluru"),
        ("BENGALURU NORTH  RTO - KA4", "Bengaluru"),
        ("CHENNAI (SOUTH) RTO - TN3", "Chennai"),
        # Office type as suffix, prefix, and after a comma.
        ("TRIVANDRUM RTO - KL1", "Trivandrum"),
        ("Port Blair DTO - AN1", "Port Blair"),
        ("RTA MEDAK - TG35", "Medak"),
        ("SDM UCHANA - HR90", "Uchana"),
        ("RTA, GURGAON - HR55", "Gurgaon"),
        ("GANDERBAL ARTO - JK16", "Ganderbal"),
        # No office type at all.
        ("AHMEDABAD - GJ1", "Ahmedabad"),
        ("PUNE - MH12", "Pune"),
    ],
)
def test_extract_city(office, city):
    assert extract_city(office) == city


def test_offices_of_one_city_collapse_together():
    """The point of the column: four Chennai offices are one Chennai."""
    offices = [
        "CHENNAI (CENTRAL) RTO - TN1", "CHENNAI (EAST) RTO - TN4",
        "CHENNAI (NORTH) RTO - TN5", "CHENNAI (SOUTH) RTO - TN3",
    ]
    assert {extract_city(o) for o in offices} == {"Chennai"}


def test_an_office_with_no_place_is_unknown_not_a_city():
    """One RTO is named literally "RTA". Reporting a city called "Rta" that
    leads Haryana is worse than admitting the place is not recorded."""
    from app.data.places import UNKNOWN_CITY

    for odd in ["RTA", "RTO - KA1", "OFFICE", "- XX9", "SDM", "", None]:
        assert extract_city(odd) == UNKNOWN_CITY

    # A real place alongside the office type still resolves.
    assert extract_city("RTA, GURGAON - HR55") == "Gurgaon"
    assert extract_city("RTA MEDAK - TG35") == "Medak"


@pytest.mark.parametrize(
    "typed, actual",
    [
        ("Bangalore", "bengaluru"),
        ("bombay", "mumbai"),
        ("Madras", "chennai"),
        ("Calcutta", "kolkata"),
        ("Gurugram", "gurgaon"),
        ("Allahabad", "prayagraj"),
    ],
)
def test_common_aliases(typed, actual):
    assert canonical_place(typed) == actual


def test_a_name_the_data_already_uses_is_not_rewritten():
    assert canonical_place("Bengaluru") is None
    assert canonical_place("Pune") is None


def test_add_derived_columns_inserts_city_beside_rto():
    frame = pd.DataFrame({
        "STATE": ["Maharashtra", "Maharashtra"],
        "RTO": ["MUMBAI (WEST) - MH2", "MUMBAI (EAST) - MH3"],
        "TOTAL": [10, 20],
    })
    add_derived_columns(frame)

    assert list(frame.columns) == ["STATE", "RTO", CITY_COLUMN, "TOTAL"]
    assert frame[CITY_COLUMN].tolist() == ["Mumbai", "Mumbai"]


def test_add_derived_columns_is_idempotent_and_skips_tables_without_rto():
    without = pd.DataFrame({"STATE": ["Kerala"], "TOTAL": [1]})
    add_derived_columns(without)
    assert CITY_COLUMN not in without.columns

    twice = pd.DataFrame({"RTO": ["PUNE - MH12"], "TOTAL": [1]})
    add_derived_columns(twice)
    add_derived_columns(twice)
    assert list(twice.columns).count(CITY_COLUMN) == 1


def test_states_for_place_flags_a_name_used_in_several_states():
    """Bilaspur is an office in Chhattisgarh, Haryana and Himachal Pradesh."""
    frame = pd.DataFrame({
        "STATE": ["Chhattisgarh", "Haryana", "Himachal Pradesh", "Kerala"],
        "RTO": ["Bilaspur RTO - CG10", "SDM BILASPUR - HR71",
                "RLA BILASPUR - HP24", "TRIVANDRUM RTO - KL1"],
        "TOTAL": [1, 2, 3, 4],
    })
    add_derived_columns(frame)

    assert states_for_place(frame, "Bilaspur") == [
        "Chhattisgarh", "Haryana", "Himachal Pradesh",
    ]
    assert states_for_place(frame, "Trivandrum") == ["Kerala"]


def test_city_becomes_a_real_dimension(maker_entry):
    """It has to reach the profile, or the model is never offered it."""
    names = [c.name for c in maker_entry.profile.dimensions]
    assert CITY_COLUMN in names
    assert "Trivandrum" in maker_entry.profile.column(CITY_COLUMN).sample_values
