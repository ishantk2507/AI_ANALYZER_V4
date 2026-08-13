from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    """Keep test runs out of the real cache/output/store directories."""
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "output_dir", tmp_path / "out")
    monkeypatch.setattr(settings, "log_dir", tmp_path / "logs")
    # Per-test database, so no test can see another's chats -- and so a test run
    # never touches the developer's real history.
    monkeypatch.setattr(settings, "store_path", tmp_path / "var" / "analyzer.db")
    settings.ensure_dirs()

    from app.data.loader import clear_dataset_cache
    from app.store import reset_store

    clear_dataset_cache()
    reset_store()
    yield
    reset_store()


@pytest.fixture
def store():
    """A ChatStore on this test's isolated database."""
    from app.store import ChatStore

    return ChatStore()


@pytest.fixture
def user(store):
    """(store, user_id) for the tests that do not care how the user was made."""
    return store, store.user_id("tester")


#: Office names shaped like the real ones -- a place, an office type and a code --
#: so the derived CITY column and code matching are exercised realistically.
_RTOS = {"Delhi": "OLD DELHI (MALL ROAD) - DL1", "Kerala": "TRIVANDRUM RTO - KL1"}


def _rows():
    """A miniature stand-in for the Vahan registration tables."""
    records = []
    data = {
        ("Delhi", 2024): {"HERO MOTOCORP LTD": 500, "BAJAJ AUTO LTD": 300, "TVS MOTOR": 200},
        ("Delhi", 2025): {"HERO MOTOCORP LTD": 550, "BAJAJ AUTO LTD": 600, "TVS MOTOR": 100},
        ("Kerala", 2024): {"HERO MOTOCORP LTD": 100, "BAJAJ AUTO LTD": 80, "TVS MOTOR": 60},
        ("Kerala", 2025): {"HERO MOTOCORP LTD": 120, "BAJAJ AUTO LTD": 160, "TVS MOTOR": 30},
    }
    for (state, year), makers in data.items():
        for maker, total in makers.items():
            records.append(
                {
                    "STATE": state,
                    "YEAR": year,
                    "CATEGORY": "Two Wheeler",
                    "RTO": _RTOS[state],
                    "MAKER": maker,
                    "TOTAL": total,
                }
            )
    return records


@pytest.fixture
def data_root(tmp_path) -> Path:
    root = tmp_path / "data"
    frame = pd.DataFrame(_rows())

    for category in ("Two Wheeler", "Three Wheeler"):
        directory = root / category
        directory.mkdir(parents=True)
        scaled = frame.copy()
        scaled["CATEGORY"] = category
        if category == "Three Wheeler":
            scaled["TOTAL"] = scaled["TOTAL"] // 10
        scaled.to_parquet(directory / "MAKER.parquet", index=False)

        fuel = scaled.rename(columns={"MAKER": "FUEL"}).copy()
        fuel["FUEL"] = fuel["FUEL"].map(
            {"HERO MOTOCORP LTD": "PETROL", "BAJAJ AUTO LTD": "ELECTRIC(BOV)", "TVS MOTOR": "CNG"}
        )
        fuel.to_parquet(directory / "FUEL.parquet", index=False)

    # Only one category gets a NORM table, so the "missing table" path is covered.
    # It is also deliberately a *partial* extract, like the real Vahan NORM data:
    # fewer rows than MAKER, so ranking out of it would undercount.
    (root / "Two Wheeler" / "NORM.parquet").write_bytes(_norm_bytes(frame))
    return root


def _norm_bytes(frame: pd.DataFrame) -> bytes:
    import io

    norm = frame.rename(columns={"MAKER": "NORM"}).head(4).copy()
    norm["NORM"] = "BHARAT STAGE VI"
    buffer = io.BytesIO()
    norm.to_parquet(buffer, index=False)
    return buffer.getvalue()


@pytest.fixture
def catalog(data_root):
    from app.data.catalog import get_catalog

    return get_catalog(data_root, refresh=True)


@pytest.fixture
def maker_entry(catalog):
    return catalog.get("Two Wheeler", "MAKER")
