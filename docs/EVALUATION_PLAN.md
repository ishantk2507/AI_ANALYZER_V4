# Evaluation and benchmark plan

**Purpose:** Measure whether AI Analyzer understands the question, executes the intended calculation, and communicates only supported claims.

## 1. Evaluation layers

| Layer | Question | Method |
|---|---|---|
| Intent | Did the system understand the task? | Slot-level expected intent |
| Planning | Does the plan represent the request? | Plan contract and reference plan |
| Scope | Were all requested filters and periods applied? | Exact filter assertions |
| Execution | Are numerical results correct? | Known-answer comparison |
| Semantics | Are measure, unit, denominator, and aggregation valid? | Semantic metadata checks |
| Claims | Does each statement follow from computed facts? | Fact references and claim checks |
| Reliability | Does the system fail safely? | Ambiguity and unsupported cases |
| Performance | How much time and inference does it use? | Stage and token telemetry |
| Generalization | Does it work outside VAHAN? | Held-out datasets and domains |

## 2. Initial 50-case suite

Suggested composition:
- 8 simple aggregations and rankings.
- 8 filters, multi-value filters, and location/value resolution.
- 8 time comparisons and growth/share calculations.
- 6 multi-dimension groupings and pivots.
- 5 joins and multi-table questions (once supported).
- 5 data-quality and missing-value questions.
- 4 ambiguous requests that should clarify.
- 3 unsupported operations that should abstain.
- 3 follow-up questions testing context inheritance and scope widening.

This is a starting distribution, not a claim that these capabilities already exist. Mark unsupported cases as expected abstentions until implemented.

## 3. Dataset coverage

Use small synthetic datasets with known answers first. Add at least three unrelated dataset families:
- Sales/transactions.
- Inventory/operations.
- Public or synthetic demographic/education data.

Keep VAHAN as a domain regression suite. Do not use only one domain to claim general-purpose performance.

For every fixture, document:
- Dataset ID and version.
- Row count and column schema.
- Units and semantic roles.
- Known data-quality conditions.
- Expected output and tolerance.
- License/source or synthetic-generation method.

Never commit private or sensitive data.

## 4. Case format

Each case should include:
- case_id
- dataset_id
- question
- expected_status
- expected_metric
- expected_aggregation
- expected_group_by
- required_filters
- expected_periods
- expected_result
- numeric_tolerance
- expected_caveats
- forbidden_claims
- tags

Reference outputs should be independently calculated or manually reviewed. The model must not generate the answer key it is evaluated against.

## 5. Metrics

### Correctness
- Intent slot accuracy.
- Required-filter precision and recall.
- Metric and aggregation accuracy.
- Grouping and period accuracy.
- Numerical exact-match rate or tolerance-based accuracy.
- Claim support rate.
- Unsupported-success rate.

### Reliability
- Correct clarification rate.
- Correct unsupported/failed status rate.
- Repair success rate.
- Regression rate across releases.

### Performance
- End-to-end p50 and p95 latency.
- Per-stage p50 and p95 latency.
- Prompt and completion tokens.
- Model calls per successful answer.
- Cache hit rate.
- Peak memory and GPU/CPU offload information where available.

Report denominators and sample sizes for every percentage. Separate deterministic tests from model-dependent evaluations.

## 6. Metamorphic tests

Use invariants where their assumptions hold:
- Row-order permutation should not alter order-independent aggregates.
- Partitioned additive aggregation should reconcile with whole-dataset aggregation.
- Shares over exhaustive, non-overlapping groups should sum to approximately 100%.
- Adding rows outside a filter should not change the filtered result.
- Filtering cannot increase source-row count.
- A declared one-to-one join should not multiply fact rows.
- Equivalent date representations should resolve to the same periods.

Document exceptions: non-additive measures, overlapping groups, null handling, and many-to-many joins require different expectations.

## 7. Evaluation protocol

For each candidate change:
1. Pin code commit, model digest, backend, quantization, context, and settings.
2. Run deterministic unit and integration tests.
3. Run the full fixed evaluation suite.
4. Repeat model-dependent cases enough times to observe variance.
5. Compare quality, latency, and tokens against the baseline.
6. Inspect all new failures manually.
7. Accept only if the change improves its target metric without unacceptable regressions.

## 8. Release gates

- No known-answer numerical regressions in P0 cases.
- No required filter silently omitted.
- Unsupported or ambiguous requests do not appear as completed.
- Claim support meets the release threshold defined by the owner.
- p95 latency does not materially regress without an explicit trade-off.
- All benchmark results include commit and model metadata.

## 9. Reporting

Produce machine-readable JSONL for individual cases and a concise Markdown/CSV summary. Preserve failed cases as regression candidates. Do not delete difficult cases to improve a score.
