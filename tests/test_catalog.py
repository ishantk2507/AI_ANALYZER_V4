from __future__ import annotations


def test_discovers_categories_and_tables(catalog):
    assert catalog.categories == ["Three Wheeler", "Two Wheeler"]
    assert set(catalog.breakdowns) == {"FUEL", "MAKER", "NORM"}
    assert len(catalog) == 5  # 2 categories x 2 tables, plus one NORM


def test_resolve_repairs_a_missing_table(catalog):
    entry, notes = catalog.resolve("Three Wheeler", "NORM")
    assert entry.category == "Three Wheeler"
    assert entry.breakdown in {"FUEL", "MAKER"}
    assert any("NORM" in note for note in notes)


def test_resolve_is_case_insensitive(catalog):
    entry, _ = catalog.resolve("two wheeler", "maker")
    assert entry.key == "Two Wheeler/MAKER"


def test_routing_block_lists_gaps(catalog):
    rendered = catalog.render_routing()
    assert "Two Wheeler" in rendered
    assert "MAKER" in rendered
    assert "ONLY SOME TABLES" in rendered


def test_profile_classifies_columns(maker_entry):
    profile = maker_entry.profile
    kinds = {c.name: c.kind for c in profile.columns}
    assert kinds["TOTAL"] == "numeric"
    assert kinds["YEAR"] == "temporal"  # an int column, but a period not a measure
    assert kinds["MAKER"] == "categorical"
    assert "HERO MOTOCORP LTD" in profile.column("MAKER").sample_values
