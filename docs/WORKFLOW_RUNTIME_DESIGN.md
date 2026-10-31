# Workflow runtime and AI-created tools

**Status:** Proposed V5 architecture. This document does not describe a feature currently implemented in V4.

## 1. Objective

Allow a local LLM to compose, save, and reuse analytical workflows without allowing it to execute arbitrary code.

The LLM proposes a declarative plan. A compiler validates it. A deterministic runtime executes registered capabilities. Validators inspect outputs. The model may patch a failing step once within a bounded budget, or the system asks the user for clarification.

## 2. Design constraints

- No model-generated Python, shell, unrestricted SQL, or filesystem operations.
- Every capability has a versioned input and output contract.
- Plans are declarative and validated before execution.
- Every step has preconditions, postconditions, and resource limits.
- User-created workflows require preview and explicit approval.
- Repairs are bounded, recorded, and revalidated.
- Reproducibility requires dataset and capability fingerprints.

## 3. Workflow IR

A workflow is a directed acyclic graph (DAG) of typed steps. Each step contains:
- Stable step ID.
- Capability ID and version.
- Dependencies.
- Typed inputs and references to earlier outputs.
- Declared outputs.
- Optional parameters and resource budget.

The compiler rejects unknown capabilities, invalid arguments, missing references, incompatible types, cycles, and unsafe resource requests.

## 4. Capability contract

Every capability declares:
- ID and semantic version.
- Description.
- Input schema.
- Output schema.
- Preconditions.
- Postconditions.
- Determinism.
- Cacheability and cache-key policy.
- Resource limits.
- Validator.
- Supported execution engines.

Example capability families:
- Data: select, filter, sort, aggregate, pivot, join, deduplicate.
- Statistics: describe, quantile, correlation, outlier detection, confidence interval, test, regression.
- Time series: period aggregation, growth, rolling statistics, seasonality, forecast.
- Presentation: chart, table, report, export.

## 5. Execution lifecycle

1. Resolve dataset and semantic metadata.
2. Extract intent slots.
3. Generate constrained workflow IR.
4. Compile and statically validate the graph.
5. Execute ready steps in dependency order.
6. Validate each step output.
7. Cache validated outputs using dependency hashes.
8. On failure, classify the issue.
9. If repairable and budget remains, ask the model for a patch to the failing step only.
10. Recompile, revalidate, and invalidate only dependent downstream steps.
11. Produce verified facts and provenance.
12. Generate answer and claim references.

## 6. Repair policy

The default budget should be one repair attempt per failing step, with an overall request budget. Repair must never:
- Remove a required user filter without permission.
- Change measure, unit, denominator, or period semantics silently.
- Weaken validation.
- Add an unregistered capability.
- Increase resource limits beyond policy.

If the repair changes user intent, the correct outcome is clarification, not execution.

## 7. Reusable tool lifecycle

Draft → Static validation → Test generation → Independent fixture review → Preview → User approval → Versioned registry → Parameterized execution → Usage feedback

The model may propose test fixtures, but expected results must be independently calculated or reviewed. The same model must not certify its own tool.

## 8. Checkpoints and caching

Cache only deterministic, validated steps. Cache keys should include:
- Dataset fingerprint.
- Capability ID and version.
- Normalized inputs.
- Dependency output hashes.
- Semantic model version.
- Engine/runtime version where it affects results.

If a step changes, invalidate its transitive dependents. Reuse unaffected validated upstream outputs.

## 9. Safety and resource budgets

Each capability should have row, memory, time, and output-size limits. Join operations need cardinality checks. Statistical operations need assumptions and minimum sample checks. Forecasting needs enough history and an explicit horizon. File exports must remain within an approved output directory.

## 10. V4 migration strategy

Keep AnalysisSpec as the simple one-operation workflow adapter. Implement the compiler and registry around a small subset first. Do not rewrite all operations simultaneously.

First vertical slice:
- Load/profile.
- Filter.
- Aggregate.
- Derive period change.
- Validate.
- Render table/chart.
- Narrate facts.

Only add a second execution engine after the capability contract is stable.
