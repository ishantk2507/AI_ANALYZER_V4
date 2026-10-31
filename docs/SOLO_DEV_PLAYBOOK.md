# Solo developer playbook

This is the operating system for building AI Analyzer as a one-person project. The goal is to reduce context switching, preserve decisions, and ship small verified increments.

## 1. Work in weekly outcomes

At the start of each week:
1. Pick one measurable outcome from the roadmap.
2. Select no more than three active issues.
3. Identify the smallest end-to-end slice.
4. Write acceptance criteria before implementation.
5. Reserve time for tests, documentation, and review.

Avoid keeping more than one major architectural change in progress.

## 2. Daily loop

### Start of session — 10 minutes
- Read the current milestone and active issue.
- Check git status and current branch.
- Read the relevant design decision and failing test.
- Write today's intended outcome in the issue.

### Build loop
1. Reproduce the problem.
2. Add or update a failing test.
3. Implement the smallest safe change.
4. Run the targeted test.
5. Run lint and the full test suite before completion.
6. Inspect the diff as if reviewing someone else's PR.
7. Update docs and issue status.

### End of session — 5 minutes
Record:
- What changed.
- Tests run and results.
- Current blocker.
- Next exact action.
- Any decision that should become an ADR.

Do not rely on chat history or memory as the project log.

## 3. Branch and commit conventions

Use short-lived branches:
- feat/<issue-id>-short-name
- fix/<issue-id>-short-name
- docs/<topic>
- test/<topic>
- chore/<topic>

Commit format:
- feat: add validated distinct-count capability
- fix: reject unresolved requested filters
- test: cover share reconciliation invariant
- docs: define evaluation case format
- chore: update locked dependencies

One commit should represent one coherent change. Avoid giant "update everything" commits.

## 4. Issue workflow

Backlog → Ready → In progress → Blocked → In review → Done

For a solo project, "In review" means a deliberate self-review:
- Read the diff without editing.
- Check failure paths and invalid inputs.
- Check data leakage and resource bounds.
- Run tests from a clean environment when practical.
- Verify documentation against the implementation.

Use a blocker label rather than leaving work silently stalled.

## 5. Pull request checklist

Every PR should state:
- Problem and user impact.
- What changed.
- What did not change.
- Tests and evaluation results.
- Latency/resource impact if relevant.
- Risks and rollback.
- Documentation updated.

Keep PRs small enough to understand in one sitting. Even when working alone, PRs preserve review discipline and a useful history.

## 6. Test pyramid

1. Unit tests for deterministic operations and validators.
2. Contract tests for planner/backend interfaces.
3. Integration tests with stub inference and temporary datasets.
4. Evaluation tests against known-answer questions.
5. Optional model smoke tests, marked separately and never required for ordinary CI.

Do not make core CI depend on a model download, GPU, external network, or private dataset.

## 7. Technical decision records

Create an ADR when a decision:
- Changes a safety boundary.
- Changes the plan or workflow language.
- Adds a new execution engine or data source.
- Changes persistence, privacy, or concurrency assumptions.
- Adds a model/backend strategy with meaningful performance trade-offs.

Use the template in docs/adr/0000-template.md. Mark superseded decisions rather than deleting their history.

## 8. Weekly review

At the end of each week:
- Compare completed work with the intended outcome.
- Review open P0 bugs and regressions.
- Check evaluation trends, not only test counts.
- Review p50/p95 latency and resource usage if changed.
- Close stale issues or rewrite them.
- Select the next week's outcome.

## 9. Time allocation guideline

For a typical feature week:
- 50% implementation.
- 20% tests and evaluation.
- 15% debugging and performance measurement.
- 10% documentation and release hygiene.
- 5% planning.

Correctness-critical work can shift more time into tests and evaluation.

## 10. Solo developer rules

- Do not start a new framework because a pattern looks fashionable.
- Prefer a measured bottleneck to an assumed bottleneck.
- Do not merge a performance optimization without a before/after benchmark.
- Do not accept an LLM self-critique as proof of correctness.
- Do not let a generated workflow run before static validation.
- Do not add a feature without defining how it can fail.
- Keep a working main branch and a reproducible setup.
