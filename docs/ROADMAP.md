# Product and engineering roadmap

**Owner:** Solo developer  
**Status:** Proposed sequencing; dates are intentionally omitted until effort and baseline are measured.

## Guiding rule

Protect the deterministic core. Generalize it incrementally. Do not build a multi-agent system or unrestricted code generation as a shortcut to feature breadth.

## Phase 0 — Establish a trustworthy baseline (P0)

**Outcome:** A clean checkout is reproducible and the current behaviour is measurable.

- [ ] Verify default branch and repository tree.
- [ ] Confirm every imported application module is tracked.
- [ ] Run setup, CLI doctor, lint, and tests from a clean environment.
- [ ] Record current test count and coverage.
- [ ] Record model name, quantization, context size, backend, and hardware.
- [ ] Save a fixed benchmark dataset and question set.
- [ ] Record baseline p50/p95 latency and token usage where available.

**Exit criteria:** Clean-clone setup succeeds; baseline failures and environment limitations are documented.

## Phase 1 — Correctness hardening (P0)

**Outcome:** The system never silently changes the requested analytical scope.

- [ ] Make unresolved filters blocking or clarification-required.
- [ ] Reject unsafe partial value matches unless explicitly requested.
- [ ] Replace silent schema/spec coercions with validation errors.
- [ ] Remove arbitrary metric fallback.
- [ ] Validate grouping, pivot, and derivation preconditions.
- [ ] Introduce explicit answer statuses and typed diagnostics.
- [ ] Add claim-to-fact references to narrative verification.
- [ ] Add regression tests for each fixed failure.

**Exit criteria:** Required-filter and calculation tests pass; unresolved ambiguity cannot produce a normal successful answer.

## Phase 2 — Evaluation and observability (P0)

**Outcome:** Every run can be explained and compared against a baseline.

- [ ] Generate a run ID for every request.
- [ ] Emit structured stage events and stable failure codes.
- [ ] Capture prompt/output token counts and backend timings where supported.
- [ ] Fingerprint datasets and record plan/config/prompt versions.
- [ ] Build a 50-case initial evaluation suite.
- [ ] Report intent fidelity, filter integrity, numerical correctness, claim support, latency, and token cost.
- [ ] Add a regression gate for known-answer tests.

**Exit criteria:** A failed benchmark case can be traced to a stage and reason; a before/after report is generated.

## Phase 3 — Latency and context optimization (P1)

**Outcome:** Fewer model calls and less irrelevant context without reducing answer quality.

- [ ] Benchmark a combined answer/insight generation against current separate calls.
- [ ] Use structured conversation state for analytical scope.
- [ ] Keep only relevant schema, sample values, and recent linguistic context.
- [ ] Include all output-affecting parameters in cache keys.
- [ ] Add bounded cache eviction and hit/miss telemetry.
- [ ] Benchmark Ollama KV cache settings and context sizes.
- [ ] Benchmark DuckDB on larger Parquet workloads.

**Exit criteria:** Quality does not regress and p50/p95 latency or tokens per successful answer improve measurably.

## Phase 4 — General-purpose data understanding (P1)

**Outcome:** The core no longer assumes VAHAN-specific semantics.

- [ ] Introduce semantic roles, units, aggregation rules, identifiers, and time semantics.
- [ ] Separate generic logic from VAHAN-specific hints and insights.
- [ ] Add data-quality profiling and schema-drift reports.
- [ ] Add safe distinct-count and derived-metric operations.
- [ ] Add joins with cardinality and duplication checks.
- [ ] Add CSV/Excel/Parquet onboarding and documented format limits.

**Exit criteria:** The same core benchmark passes on at least three unrelated dataset families.

## Phase 5 — Typed workflow runtime (P2)

**Outcome:** A user request can compose several approved operations.

- [ ] Define a versioned workflow IR.
- [ ] Define capability input/output contracts and preconditions.
- [ ] Compile plans into a validated DAG.
- [ ] Add step-level checkpoints and dependency-aware invalidation.
- [ ] Add per-step postconditions and resource limits.
- [ ] Support bounded, targeted repair or explicit clarification.

**Exit criteria:** Multi-step workflows are deterministic, inspectable, reproducible, and tested without an LLM.

## Phase 6 — User-created reusable tools (P2)

**Outcome:** Users can create and reuse safe analytical workflows.

- [ ] Generate declarative tool manifests, not executable code.
- [ ] Validate manifests against the capability registry.
- [ ] Create independent fixtures and known-answer tests.
- [ ] Preview outputs and assumptions before approval.
- [ ] Version and save approved workflows.
- [ ] Add parameterized execution against updated datasets.
- [ ] Measure repair success, reuse rate, and tokens per successful workflow.

**Exit criteria:** A user can create, inspect, approve, save, and rerun a workflow with a documented version and trace.

## Release gates

No phase is complete because the code exists. It is complete when:
1. Acceptance criteria are met.
2. Tests and evaluation pass.
3. Documentation is updated.
4. Known limitations are recorded.
5. The change can be reproduced from a clean checkout.
