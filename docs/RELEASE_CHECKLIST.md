# Release checklist

Use this checklist for tagged releases and meaningful public milestones.

## 1. Scope and quality
- [ ] Release goal and user-visible changes are stated.
- [ ] All included issues meet their acceptance criteria.
- [ ] No unresolved P0 correctness or security defect.
- [ ] Known limitations and unsupported operations are documented.
- [ ] Version and changelog are updated.

## 2. Reproducibility
- [ ] Fresh clone setup tested on a supported Python version.
- [ ] Dependency lock and install instructions agree.
- [ ] CLI doctor succeeds in a representative environment.
- [ ] No private dataset, model binary, database, log, or secret is committed.
- [ ] Clean-clone import check succeeds.

## 3. Automated quality
- [ ] Ruff passes.
- [ ] Unit and integration tests pass.
- [ ] Regression suite passes.
- [ ] Evaluation suite passes with commit/model metadata.
- [ ] Optional model smoke test is recorded separately.
- [ ] No unexpected warnings or unhandled exceptions.

## 4. Correctness and trust
- [ ] Required filters are not silently dropped.
- [ ] Metric and aggregation choices are visible.
- [ ] Units and denominators are correct.
- [ ] Empty, ambiguous, and unsupported requests have explicit statuses.
- [ ] Narrative claims are grounded in computed facts.
- [ ] Provenance is available for supported outputs.

## 5. Performance
- [ ] p50/p95 latency recorded.
- [ ] Prompt/output tokens and model calls recorded where available.
- [ ] Peak RAM/VRAM and CPU/GPU offload checked.
- [ ] Cache behaviour checked.
- [ ] Any material regression has an explicit rationale.

## 6. Security and privacy
- [ ] Secrets and private data scan completed.
- [ ] Data storage and retention behaviour documented.
- [ ] Proxy/auth assumptions reviewed for deployments.
- [ ] Trace redaction and retention checked.
- [ ] File and resource limits tested.

## 7. Documentation and delivery
- [ ] README quick start matches actual commands.
- [ ] Architecture and configuration docs match implementation.
- [ ] Migration notes included if necessary.
- [ ] Release notes summarize changes, limitations, and known issues.
- [ ] Tag and GitHub release created only after all required checks pass.

## 8. Rollback
- [ ] Previous known-good version is identifiable.
- [ ] Configuration changes are reversible.
- [ ] Persistent-store migrations have a backup and rollback strategy.
- [ ] Rollback instructions are written for the release.
