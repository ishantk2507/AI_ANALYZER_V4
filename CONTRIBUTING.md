# Contributing

## Setup

```bash
python -m venv .venv
```

Activate it, then:

```bash
pip install -e ".[ollama,dev]"
cp .env.example .env          # Copy-Item on Windows PowerShell
python -m app.cli doctor
```

`doctor` is the fastest way to find out which of the model, backend, data
directory, or store is not where the app expects it.

## Before opening a pull request

```bash
python -m pytest
python -m ruff check app tests scripts
```

Both are what CI runs, on Python 3.10–3.12. The suite needs no model, no GPU and
no network, so a failure is a real failure.

## The rule that matters most

**Read [docs/DESIGN-DECISIONS.md](docs/DESIGN-DECISIONS.md) before changing
anything in `app/agent/` or `app/analysis/`.**

Most of the code in the hint, routing and verification layers looks like it
could be simplified. Nearly all of it exists because the real model produced a
specific wrong answer, and [docs/TESTING.md](docs/TESTING.md) records which one.
A guard that looks redundant is usually load-bearing — check the regression
table before deleting it.

If you do remove a guard, remove its regression test in the same commit and say
in the message why the failure can no longer occur.

## Adding to the analysis language

`AnalysisSpec` ([app/analysis/spec.py](app/analysis/spec.py)) is the safety
boundary between the model and execution. It is closed on purpose: the model
picks from enums built at runtime from the live catalog, so an invalid column or
aggregation is not a representable output.

Widening it means:

1. Add the field or enum value to `AnalysisSpec`.
2. Extend the generated JSON Schema so the model can actually emit it.
3. Add the branch to [executor.py](app/analysis/executor.py).
4. Test the executor branch on values, and the pipeline on what it does with a
   given model response.

What it does **not** mean is letting the model write expressions, SQL or code.
Nothing in the execution path calls `eval`, `exec`, or a shell, and that is the
property the whole design is built to keep.

## Tests

- Put regressions in the suite that owns the behaviour
  ([docs/TESTING.md](docs/TESTING.md) maps them).
- Quote the actual bad model output in the test's docstring. The regression
  table is written from that habit, and it is the most useful documentation in
  the project.
- Build fixtures from small in-memory frames in `tmp_path`, as
  `tests/conftest.py` does. Do not check in Parquet files — a fresh clone with
  no data must still run the suite.

## Style

Ruff with `E, F, W, I, B` at 100 columns; `E501` and `B008` are off (line length
is the formatter's job, and `B008` fights typer's documented idiom). Match the
surrounding code's comment density — this codebase explains *why*, not *what*,
and that convention is worth keeping.

## Dependencies

`requirements.txt` is the loose set a contributor installs, `pyproject.toml` is
what `pip install -e .` resolves, and `requirements.lock.txt` is the exact
tested set used by CI and production. Change one, change all three, and update
the lock file's header with the Python version and test count it was verified
against.
