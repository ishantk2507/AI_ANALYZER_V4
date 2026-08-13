# Architecture

## Purpose and boundaries

AI Analyzer turns questions into safe, deterministic analysis over local CSV or
Parquet data. It is deliberately not an autonomous code-generation agent.
The language model may choose from a constrained schema, but it cannot execute
Python, access a shell, access the filesystem, or call arbitrary tools.

```text
Question
   │
   ├─ Hints and conversation scope
   ├─ Route: category + breakdown (schema-constrained LLM)
   ├─ Plan: AnalysisSpec JSON (schema-constrained LLM)
   ├─ Execute: filters, aggregation, pivot, derivation, sort (pandas)
   ├─ Findings: deterministic insights and follow-up questions
   ├─ Chart: Matplotlib PNG / Altair interactive chart
   └─ Narrate: grounded LLM prose, then numeric verification
```

If routing, planning, or narration fails, the application returns a useful
error or deterministic summary rather than running unconstrained model output.

## Main modules

| Area | Modules | Responsibility |
| --- | --- | --- |
| Entry points | `app/launcher.py`, `app/cli.py`, `app/ui.py` | Start Streamlit, expose CLI, render the web experience. |
| Configuration | `app/config.py`, `app/logging_setup.py` | Read environment configuration, resolve paths, and configure rotating logs. |
| Data | `app/data/catalog.py`, `loader.py`, `profile.py`, `matching.py`, `places.py` | Discover files, cache/profile data, resolve user labels, and derive location fields. |
| Agent | `app/agent/orchestrator.py`, `hints.py`, `memory.py`, `prompts.py`, `verify.py` | Coordinate the pipeline, maintain conversational context, constrain prompts, and verify prose. |
| Analysis | `app/analysis/spec.py`, `executor.py`, `insights.py`, `charts.py`, `interactive.py` | Define the closed analysis language, execute it, calculate findings, and draw charts. |
| Inference | `app/llm/base.py`, `factory.py`, `ollama_backend.py`, `llamacpp_backend.py` | Provide interchangeable Ollama and llama.cpp adapters. |
| Persistence | `app/store.py`, `app/session.py`, `app/cache.py` | Store user chats/preferences in SQLite and cache model responses. |

## AnalysisSpec

`AnalysisSpec` is the safety boundary between an LLM plan and execution. Its
fields allow only filters, up to two grouping dimensions, an aggregate,
optional pivot, growth/share derivation, sort strategy, row limit, and chart
type. Column names and enum values are generated from the live catalog and
validated again by the executor. There is no `eval`, `exec`, SQL, generated
source code, or arbitrary file access in the execution path.

Growth calculations add both percent change and absolute change. Tiny starting
bases are excluded from growth rankings when enough meaningful rows remain; the
returned notes state when this happens. Numerical claims in LLM prose are
checked against computed result values, with a deterministic fallback summary.

## Data lifecycle

1. The catalog discovers top-level flat files and one-level category folders.
2. Profiles inspect up to 200,000 rows and are cached by file path, mtime, and
   size under `CACHE_DIR/profiles`.
3. The loader reads the selected file, adds supported derived geographic
   columns, optimises dtypes, and keeps a bounded in-memory LRU.
4. The executor filters and aggregates the loaded DataFrame; it does not
   mutate source data.

Parquet is preferred because it preserves types and supports efficient
profiling. When CSV and Parquet have the same stem in a folder, Parquet wins.

## State and concurrency

The UI process holds one model backend and data caches per process. Chats,
turns, and user preferences are persisted in a SQLite database when enabled.
SQLite suits a single Streamlit process and normal desktop use. It is not a
multi-writer, horizontally scaled datastore; deploy one application replica per
store file, or replace `app/store.py` before scaling the web tier.

`AUTH_HEADER` partitions chat data by username. It is only an identity handoff
from an authenticated proxy—not authentication by itself. An untrusted caller
can forge any HTTP header, so the proxy must remove client-supplied versions and
set the final value itself.

## Failure handling

- Empty or missing data: the request stops before model work.
- Missing inference backend/model: the request returns a backend-specific
  remediation message.
- Invalid model JSON/schema: bounded repair attempts, then an error.
- Unknown columns/values: fuzzy resolution where possible, otherwise a note and
  safe omission.
- Invalid narrative numbers: one rewrite attempt, then deterministic output.
- Cache or write failure: analysis continues where possible; caches are
  disposable.

## Design limitations

- An LLM can still choose a semantically imperfect but schema-valid analysis;
  validate important conclusions against the displayed table.
- Input data is trusted local data. The application is not a data sanitisation
  pipeline.
- Models, data, and the SQLite store may contain sensitive information. Apply
  operating-system permissions and backups appropriate to that data.
