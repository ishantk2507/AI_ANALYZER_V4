# Design decisions

Why the system is shaped the way it is. [Architecture](ARCHITECTURE.md)
describes *what* the pipeline does; this describes *why*, and what happened when
it was built differently.

Most of the guards documented here exist because the real model produced a
specific wrong answer. Removing one without reading the reason it was added will
reintroduce a failure that is already written down. The matching regression
tests are catalogued in [Testing](TESTING.md).

---

## The pipeline is fixed, not agentic

```text
                    ┌──────────────────────────────────────────┐
  "which two        │  0. HINTS    string matching, no model   │
   wheeler makers   │  1. ROUTE    model → {category, table}   │  ← constrained
   grew fastest     │  2. PLAN     model → AnalysisSpec (JSON) │  ← constrained
   from 2024   ───► │  3. EXECUTE  deterministic pandas        │
   to 2025?"        │  4. INSIGHTS computed findings + next Qs │
                    │  5. CHART    matplotlib / Vega-Lite      │
                    │  6. NARRATE  model → prose → verified    │
                    └──────────────────────────────────────────┘
                        ↓          ↓         ↓          ↓
                   narrative   findings    chart   result table
                                              + follow-up questions
```

Stages 1 and 2 are the only places the model makes a decision, and both are
**JSON-schema-constrained**: the schema is built at runtime from the real
catalog and the real column names, so an invalid table, column, aggregation or
chart type is *not a representable output*. Stages 3 and 4 are ordinary Python —
nothing is generated, `exec`'d, or sandboxed, because there is no generated code
to sandbox.

Stage 0 and the verification pass in stage 6 exist because constrained decoding
stops the model producing an *invalid* answer but not a *wrong* one. Both were
added after watching the real model fail:

- Asked "which **two wheeler** makers grew fastest", it routed to *Three Wheeler*
  and planned a plain total with no growth at all.
- It then wrote "970,931 − 46,514 = 924,417 increase", where 46,514 appears
  nowhere in the data.

Stage 0 recovers both facts from the question with plain string matching — which
is instant, free, and cannot hallucinate — and uses them to pin the model's
choices. Stage 6 checks every figure in the prose against the computed table.

## Why not CrewAI, LangChain, or a multi-agent ReAct loop

The obvious alternative is to give a model a set of tools and let it loop. That
was rejected for reasons specific to running a **3B quantised model on consumer
hardware**, not out of preference:

- **Free-form tool calling is the failure mode small models are worst at.** An
  open loop asks the model to emit a valid tool name, valid arguments, and a
  valid stopping decision, every turn. A 3B gets each of those wrong often
  enough that the loop rarely terminates usefully. Constraining the *one* JSON
  object it must produce removes that entire class of failure: with a schema
  built from the live catalog, naming a table that does not exist is not
  something the sampler can emit.
- **A loop multiplies the cost of the slowest component.** Every ReAct turn is a
  full generation. At 50 tok/s a five-turn loop is most of a minute before any
  pandas runs. The fixed pipeline calls the model three times (route, plan,
  narrate) plus one interpretation, and two of those are ~100-token structured
  outputs.
- **Failure has nowhere to go in a loop.** When a stage here fails, the fallback
  is written down and deterministic: routing falls back to the previous table,
  planning to a default breakdown, narration to a table-derived summary. An
  agent that has looped six times has no equivalent "known-good" state.
- **The framework would be the dependency doing the least work.** What this
  project needs from an orchestration layer is a typed spec, a validator, and a
  retry — roughly [`spec.py`](../app/analysis/spec.py) and 40 lines of
  `orchestrator.py`. The rest of a framework is abstraction over provider APIs
  this app does not call.

The tradeoff is real and worth stating: the pipeline can only answer questions
that fit `AnalysisSpec`. It cannot decide to fetch a second table, or invent a
new kind of analysis mid-run. That is the price of never being wrong about which
column it is reading. See *Known limitations* below.

## Insights are computed, not written

An early version answered "which makers grew fastest?" like this:

| MAKER | 2024 | 2025 | growth_% |
|---|---|---|---|
| KINETIC MOTOR COMPANY | **5** | 1,660 | 33,100% |
| BU4 AUTO PVT LTD | **1** | 68 | 6,700% |

Every winner had a 2024 base of 1–24 units. That is rounding noise sorted
descending, not growth — and the narrative dutifully reported it as the answer.

Two things fix that, and neither involves asking the model to try harder.

**A base guard.** A growth ranking now ignores rows starting below
`max(growth_min_base, 0.005% of the total base)`, and says how many it dropped.
The same question now returns River Mobility (2,971 → 17,016, +473%), Simple
Energy, Pur Energy — real companies with real bases. Absolute `change` is always
reported beside the percentage.

**A computed findings layer.** The narrator prompt forbids arithmetic, because a
3B model that does maths invents numbers. So interpretation cannot come from the
model — it is calculated in pandas first
([insights.py](../app/analysis/insights.py)) and handed over as verified facts
to narrate:

- **market movement** — the denominator: "the total grew 6.8%; judge movers against that"
- **absolute vs relative** — "TVS added the most volume (566,295) despite growing only 16.9%"
- **share / dominance** — where the leader sits in the whole
- **concentration** — what the top 3 hold
- **gap or close race** — is the lead decisive?
- **outlier** — leader many times the median, so the average misleads
- **coverage** — what fraction of the total the displayed rows represent
- **base-effect caution** — raised whenever the guard fired

And, for period-over-period questions, the ones a business actually asks:

- **outperformers** — "8 of 13 grew faster than the market's 7.4%"
- **growth contribution** — "97.7% of all the growth came from just 3: PETROL(E20),
  PURE EV, DIESEL" — growth is rarely evenly spread
- **decliners** — "4 of 13 shrank, giving up 4,844,242 between them; PETROL fell most"
- **share shift** — "PETROL lost share: 74.3% of the total in 2024 to 55.1% in 2025
  (-19.1 points)" — growing is not the same as winning
- **rank shift** — the lead changing hands, which a table of totals hides
- **concentration shift** — "the market is consolidating: the top 3 held 71.4%
  in 2024 and 72.0% in 2025"
- **electrification** — "electric rose from 7.7% of registrations to 8.5%
  (+0.8 points)". Domain-aware on purpose, and restricted to the columns that
  describe the *vehicle*: a maker called "OLA ELECTRIC" is a company, not a
  powertrain, and matching it reported electric *falling* when one firm lost share.

**The model writes the interpretation.** A separate stage takes the computed
findings and the table and asks the model what they *mean* — an observation
("M-CYCLE/SCOOTER is 70.7% of registrations") is not an insight until something
follows from it. The facts stay computed, so the numbers are still verified
against the same allow-list; only the meaning is generated. If the
interpretation quotes a figure that is not in the findings it is rewritten once,
then dropped in favour of the findings alone.

The narrative and the interpretation have deliberately separate jobs: the
narrative answers the question and is forbidden from explaining *why* the
numbers are what they are, because registration counts cannot show preference,
policy or culture, and a 3B model guessing at them writes confident nonsense.

Those numbers are added to the verifier's allow-list, so the model may quote them
but still cannot invent its own. The result, from a real run:

> The two fastest-growing two-wheeler makers are RIVER MOBILITY (472.74%) and
> SIMPLEENERGY (362.60%).
> - Both outpaced the overall industry growth of **6.8%** by a massive margin.
> - …despite being tiny fractions (**0.1%** each) of the total market.
> - CAUTION: their explosive growth is due to small base volumes.

## How it stays honest

Narration is the one stage where the model writes free text, so constrained
decoding cannot help. Everything it produces is checked against the computed
table afterwards ([verify.py](../app/agent/verify.py)). Three failure modes, all
observed from the real 3B model:

1. **Invented numbers** — "970,931 − 46,514 = 924,417", where 46,514 appears
   nowhere in the data. Every number in the prose is matched against an
   allow-list built from the table: its cells, its column headers (periods like
   "2024" get quoted as numbers), column totals, the row count, and the
   insight layer's computed figures. A 0.5% tolerance lets "1,270" be written as
   "1.27 thousand"; bare integers below 1,000 are skipped because they are
   almost always ordinals or counts.
2. **Denying a non-empty result** — "No two-wheeler makers in the provided table
   match" printed directly above five matching rows. Matched against an
   enumerated list of denial phrases rather than a negation heuristic, because
   "growth was not uniform" is a legitimate sentence.
3. **Misattributing the leader** — "Honda is the top maker in Delhi" above a
   table led by Hero MotoCorp. Every number correct; only the subject wrong.
   Fires only when the opening sentence makes a superlative claim and names
   exactly one row's entity, and that row is not the first.

Each check is deliberately conservative: a false positive costs a rewrite and a
duller answer, so they only fire on unambiguous contradictions. One rewrite is
allowed, told exactly what was wrong. If that also fails, the answer ships a
table-derived summary — a duller true answer beats a fluent false one.

## Choosing a model

Generation throughput on a 6GB RTX 3060 at `num_ctx 4096`, with
`MODEL_N_GPU_LAYERS=-1` forcing full offload
([scripts/bench_model.py](../scripts/bench_model.py)):

| Ollama model | Quant | Throughput | GPU | VRAM |
|---|---|---|---|---|
| `ai-analyzer-ministral` | 3B Q4_K_M | 86.0 tok/s | 100% | 2.3GB |
| `ai-analyzer-ministral-8b-iq4` | 8B IQ4_XS | 50.3 tok/s | 100% | 5.4GB |
| `ai-analyzer-ministral-8b` | 8B Q4_K_M | 43.8 tok/s | 100% | 5.9GB |

An 8B **does** fit in 6GB, contrary to what Ollama's own layer split suggests.
Left to its own estimate Ollama keeps 21–28% of the weights on the CPU and runs
at 14–21 tok/s, so forcing the offload is worth roughly 3×. The cost of forcing
it: a model that genuinely does not fit now fails to load instead of spilling to
CPU. Name an explicit layer count to get the graceful split back.

**IQ4_XS is the recommended pick.** It is both faster than Q4_K_M (less weight
to read per token) and leaves 567MB of VRAM spare against Q4_K_M's 103MB — the
margin that survives a browser opening.

The 3B is fast but writes the worst interpretations; it produced "the skew
toward scooters, far exceeding even motorcycles", which is a category error.
`INSIGHT_MODEL` exists to send only the interpretation stage to a larger model,
but it is worth setting **only when both models fit in VRAM at once**. On 6GB
they do not, so Ollama reloads the 8B on every question and the swap costs more
than it saves — measured at 41s, no better than running the 8B for everything.

## Memory behaviour

A 4K context window will not hold a long chat plus a schema plus a result table,
so history is compressed rather than truncated
([memory.py](../app/agent/memory.py)): the last two turns stay verbatim, because
follow-ups like "and for three-wheelers?" depend on them, and anything older
collapses into a rolling model-written summary. If summarisation fails, it falls
back to a plain join of recent questions.

Scope inheritance is the subtle part. A follow-up inherits the previous answer's
filters only when it introduces nothing of its own at that level, and
`last_filters` deliberately reads *only* the immediately preceding turn: after
"how does UP compare with the other states?" the conversation is national, and
reaching further back would silently resurrect Uttar Pradesh. Questions that
widen on purpose — "across all vehicle categories", "in the country" — clear the
inherited scope instead of adding to it.

Standing user preferences are a separate, durable layer
([store.py](../app/store.py)): facts the user asked to be remembered across
chats, capped by `MAX_USER_MEMORIES` because they compete with the schema and
the result table for the same 4K window.

## Charts

Every answer renders twice: a matplotlib PNG (for the CLI, and as the
downloadable artefact) and a Vega-Lite chart via Altair in the UI, which gives
hover tooltips and untruncated labels. **The chart is never the only way to read
a value** — the result table is rendered beside every answer as the table-view
twin, and both are driven by the same frame, so they cannot disagree.

Reader-applied view controls (filter, top/bottom, row count, chart type) re-run
the *spec* through pandas rather than asking the model again. Re-running a spec
is milliseconds, and because the adjusted result replaces the answer's
everywhere below it, the chart, table, findings and downloads stay consistent.
The interpretation is dropped when a view is adjusted, because it was written
about the unsliced answer.

"No chart" is treated as the model being unhelpful rather than a decision:
nothing in a question asks for a bare table, so a `none` chart type on a
non-empty result is upgraded to a bar chart and a note is recorded.

## Known limitations

- **One table per question.** No joins across breakdowns or categories, so
  "compare two-wheelers and three-wheelers" is answered from whichever table
  routing picks. Cross-category comparison is the obvious next feature.
- **The spec language is closed by design.** No arbitrary expressions, no
  windowing, no custom ratios beyond growth and share. Widening it means adding
  an enum value and a branch in the executor, not letting the model write code.
- **Growth compares first period to last**, not year-over-year across more than
  two periods.
- **An LLM can still choose a semantically imperfect but schema-valid analysis.**
  Validate important conclusions against the displayed table.
- **SQLite is single-writer.** Fine for one Streamlit process; see
  [Deployment](DEPLOYMENT.md) before scaling the web tier.
