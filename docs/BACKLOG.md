# Engineering backlog

This is the source-of-truth backlog proposal for GitHub Issues. Keep issue descriptions actionable and small enough for one focused work session where possible. Do not treat this document as evidence that work has started.

## P0 — Baseline and correctness

### BASE-01: Verify repository integrity from a clean clone
- **Why:** The inspected GitHub tree did not show the imported app/data package.
- **Tasks:** Verify default branch; inspect tracked tree; install dependencies; import app modules; run CLI doctor and tests.
- **Acceptance:** Clean clone imports all runtime modules and documented setup matches reality.
- **Risk:** Documentation and code may be from different snapshots.

### CORE-01: Introduce explicit analysis outcomes
- **Why:** An error-free response is not necessarily a semantically successful answer.
- **Tasks:** Add completed, completed-with-warnings, clarification-required, unsupported, failed statuses.
- **Acceptance:** UI and CLI render each status distinctly; tests cover transitions.

### CORE-02: Make filter resolution strict
- **Why:** Ignoring a requested filter can produce a confident answer to a different question.
- **Tasks:** Distinguish unresolved column, unresolved value, ambiguous value, partial match, and empty result.
- **Acceptance:** Required unresolved filters never silently disappear; user sees the exact unresolved condition.

### CORE-03: Remove arbitrary metric fallback
- **Why:** A valid numeric column is not necessarily the intended measure.
- **Tasks:** Resolve explicit measure, semantic default, or ask clarification.
- **Acceptance:** Ambiguous metric does not silently become TOTAL or first numeric column.

### CORE-04: Validate AnalysisSpec without silent coercion
- **Tasks:** Reject invalid enum values and incompatible field combinations; expose repairable errors.
- **Acceptance:** Invalid plans cannot execute as a different plan without an explicit repair record.

### CORE-05: Validate derivation preconditions
- **Tasks:** Define required periods, denominator rules, base effects, missing values, and zero handling.
- **Acceptance:** Unsupported growth/share calculations return explicit status and diagnostic.

### CORE-06: Improve narrative claim verification
- **Tasks:** Represent computed facts with stable IDs; require generated claims to reference facts; validate entity, period, measure, unit, and value.
- **Acceptance:** Unsupported or misattributed claims are rejected or replaced by deterministic text.

## P0 — Evaluation and traceability

### OBS-01: Add run IDs and structured stage events
- **Tasks:** Define run schema; capture route/plan/execute/validate/chart/narrate timings; record failures with stable codes.
- **Acceptance:** One request can be reconstructed from its trace without logging raw sensitive data by default.

### OBS-02: Capture backend telemetry
- **Tasks:** Return structured backend response; capture prompt tokens, output tokens, prompt eval duration, generation duration, model and backend details.
- **Acceptance:** UI/CLI can display model and generation metrics when provider supplies them; unavailable fields remain null.

### EVAL-01: Build a versioned evaluation corpus
- **Tasks:** Define case schema; create 50 known-answer cases across question types and datasets; include ambiguous and unsupported cases.
- **Acceptance:** Runner produces machine-readable results and a human-readable summary.

### EVAL-02: Add metamorphic and invariant tests
- **Tasks:** Row shuffle, partition/aggregate reconciliation, share totals, filter monotonicity, join cardinality, time-period invariants.
- **Acceptance:** Invariants are tested only where their mathematical preconditions hold.

## P1 — Context, performance, generalization

### PERF-01: Combine interpretation and narration experimentally
- **Acceptance:** Benchmark demonstrates lower calls/latency without lower claim support or answer correctness.

### CTX-01: Introduce structured analytical conversation state
- **Acceptance:** Follow-ups inherit only valid scope; explicit widening clears prior scope; state is tested independently of LLM summaries.

### DATA-01: Add semantic metadata
- **Acceptance:** Measures, dimensions, identifiers, units, aggregation rules, and time semantics are represented and validated.

### DATA-02: Separate domain packs from generic core
- **Acceptance:** VAHAN-specific aliases, insights, and assumptions are isolated and generic tests pass without them.

### DATA-03: Add schema drift and data quality diagnostics
- **Acceptance:** Missingness, duplicate keys, type changes, invalid dates, and distribution shifts are reported with clear severity.

### ENGINE-01: Benchmark DuckDB against pandas
- **Acceptance:** Same analytical contract and correctness suite; document dataset-size crossover and memory/latency trade-offs.

## P2 — Workflow composition

### FLOW-01: Define versioned workflow IR
- **Acceptance:** Schema validates step IDs, dependencies, input references, output types, and operation versions; cycles are rejected.

### FLOW-02: Implement capability registry
- **Acceptance:** Every capability declares input/output schema, preconditions, postconditions, determinism, cacheability, and resource limits.

### FLOW-03: Add checkpointed execution and suffix invalidation
- **Acceptance:** Changing one step invalidates only dependent outputs; unaffected valid steps are reused.

### FLOW-04: Add bounded repair
- **Acceptance:** One targeted repair attempt per failing step by default; every patch is recompiled and revalidated; otherwise clarify or stop.

### FLOW-05: Add reusable user-created workflows
- **Acceptance:** Declarative manifest, independent tests, preview, explicit approval, versioning, and reproducible execution.

## Definition of ready

An issue is ready when it has:
- User or system problem.
- Scope and explicit non-goals.
- Reproduction or motivating example.
- Acceptance criteria.
- Test strategy.
- Dependencies and risks.

## Definition of done

- Implementation complete and reviewed by the author.
- Unit/regression tests added.
- Relevant evaluation cases updated.
- Lint and full test suite pass.
- Documentation and changelog updated where applicable.
- Trace fields and error behaviour considered.
- No unrelated refactor bundled into the change.
