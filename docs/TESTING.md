# Testing

```bash
python -m pytest
python -m pytest --cov=app --cov-report=term-missing
python -m ruff check app tests scripts
```

433 tests, and **none of them need a model loaded** — the agent pipeline is
exercised against a stub backend that returns canned JSON. That is what makes
the suite fast enough to gate a pull request on, and why CI can run it on
Python 3.10–3.12 without a GPU or a 5GB download.

| Suite | Tests | Covers |
|---|---:|---|
| `test_hints.py` | 83 | Stage 0: deterministic question analysis |
| `test_context.py` | 42 | Scope inheritance, widening, conversation carry-over |
| `test_verify.py` | 36 | Numeric verification, denial and leader checks |
| `test_session.py` | 34 | Per-user sessions, chat lifecycle, memory injection |
| `test_insights.py` | 31 | Computed findings and follow-up generation |
| `test_orchestrator.py` | 30 | The full pipeline against a stub backend |
| `test_places.py` | 26 | RTO → city derivation, aliases, token matching |
| `test_spec_and_matching.py` | 26 | The spec language and fuzzy resolution |
| `test_charts.py` | 25 | Matplotlib rendering and column selection |
| `test_store.py` | 25 | SQLite persistence, migrations, isolation |
| `test_executor.py` | 20 | Filters, pivots, growth, share, sorting |
| `test_interactive.py` | 20 | Vega-Lite / Altair chart construction |
| `test_ui.py` | 20 | The Streamlit app via `AppTest` |
| `test_combined.py` | 10 | Cross-category union tables |
| `test_catalog.py` | 5 | Dataset discovery and routing enums |

## The regression table

Many tests are regressions for failures the real model actually produced during
development, each reproduced from the run that exposed it. **This table is the
reason not to delete a guard that looks redundant.**

| Failure | Guard |
|---|---|
| "**two wheeler** makers" routed to Three Wheeler | question overrules routing |
| "grew fastest" planned as a plain total | intent restoration |
| "top 5 RTOs **in Kerala**" planned with no filter, returned Tamil Nadu | filter inference |
| "makers in **Pune**" / "fuel types in **KL1**" ignored the RTO entirely | distinctive-token matching |
| `KL1` resolved to `KANNUR RTO - KL13` (substring, not token) | whole-token matching |
| "Bangalore" matched nothing — the data only says BENGALURU | city aliases |
| "which **city** has the most…" matched RTOs named "… CITY" | query-word stopwords |
| "top makers in Bangalore" returned **two** rows, no count asked for | sane ranking default |
| Two bars rendered as slabs in a chart sized for ten | fixed bar thickness |
| "top **two** wheeler makers" read as "top 2" — returned two rows | count vs. category |
| "compare Mumbai and Bangalore" applied no filter at all | comparison filtering |
| "how many three wheelers **in** Pune" ranked every city, ignoring Pune | scoped-value filtering |
| A 10-row result rendered with no chart at all | chart fallback |
| Internal repair notes narrated as analysis — a row count quoted as a vehicle total | notes withheld from the narrator |
| A second answer crashed with `StreamlitDuplicateElementId` | per-answer widget keys |
| "two wheeler registrations" planned as `sum(2WIC)` | headline-metric fallback |
| RTOs ranked out of the partial `NORM` table | table widening |
| Tables swapped on row count, not coverage — 6.5x rows, 0.11% more data | totals-based comparison |
| A redundant `CATEGORY` filter dropped 13,326 of Pune's 27,617 registrations | folder *is* the filter |
| "...for three wheelers **in** pune" never saw "pune" — a regex ate the preposition | non-consuming lookahead |
| "the **fuel** split" grouped by CITY; "**city** wise" grouped by CLASS | group by the dimension named |
| "**total** vehicle registrations" returned a CLASS breakdown | single-figure intent |
| Cross-category questions were silently scoped to one arbitrary category | union across categories |
| A single-total answer crashed on `label_columns[0]` | scalar follow-ups |
| A follow-up about Haryana answered for the whole country | scope inheritance |
| "cities **with** highest registrations" filtered to 6 CLASS values containing WITH | English stopwords |
| "growth in the **biggest** cities" ranked by percent, answering about the smallest | `sort_by` |
| Insights called row 0 "the percent leader" on a size-ranked table | leaders computed, not assumed |
| "How does UP compare with **other states**?" inherited UP *and* Bahraich, comparing UP with itself | zoom-out detection |
| "vehicles registered in **the country**" inherited Karnataka and reported 3.7M as national | nationwide detection |
| "compare Mumbai, Pune **and Delhi**" became `STATE == Delhi AND CITY in (Mumbai, Pune)` — matches nothing | places consolidated to one level |
| "How did **city** … from **year** to year" grouped by YEAR, not CITY | non-temporal dimensions first |
| A growth ranking drawn as a line sorted alphabetically | rank by growth, not label |
| An RTO named literally "RTA" became a city called "Rta" | unnamed offices are `Unknown` |
| "How does Haryana compare with other states **for Haryana**?" | scope not repeated |
| `4.001373e+06` printed in the CLI — float32 is not a Python `float` | shared cell formatter |
| "970,931 − 46,514 = 924,417", where 46,514 is nowhere in the data | numeric verification |
| "No makers match" printed above five matching rows | contradiction check |
| `UnicodeEncodeError` on `→` killed the CLI *after* a successful analysis | ASCII console output |

## Conventions

- **No network, no model, no GPU.** `tests/conftest.py` provides the stub
  backend and temporary data trees. A test that needs inference is testing the
  wrong thing.
- **Regressions carry their story.** When fixing a model-produced failure, name
  the test after the behaviour and quote the actual bad output in the docstring
  or comment. The table above is generated from that habit.
- **The deterministic half is tested exhaustively; the model half is tested for
  its contract.** Executor, insights, charts and matching are tested on values.
  Routing and planning are tested on what the pipeline does with a given model
  response, not on what the model says.
- `MPLBACKEND=Agg` is set in CI so matplotlib does not look for a display.

## Adding a dataset fixture

`tests/conftest.py` builds catalogs from small in-memory frames written to a
`tmp_path`. Prefer that over checking in Parquet files: the suite should stay
runnable from a fresh clone with no data present, which is also how CI runs it.
