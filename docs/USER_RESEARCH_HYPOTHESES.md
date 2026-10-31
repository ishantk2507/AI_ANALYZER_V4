# User research hypotheses

**Status:** Hypotheses for validation. No real interviews or usability sessions are represented here.

## 1. Why this document exists

A prior design exercise simulated feedback from 50 technical and 50 non-technical users. That exercise is useful for generating testable hypotheses, but it is not customer research, survey data, or evidence of demand.

Use this document to turn those hypotheses into a real research plan.

## 2. Persona hypotheses

| Persona | Job | Likely friction | Hypothesis to test |
|---|---|---|---|
| Data analyst | Explore and explain data | Cannot inspect or reproduce the plan | Advanced plan and provenance view increases trust |
| BI developer | Reuse reports | Rebuilds the same analysis | Saved parameterized workflows reduce repeated effort |
| Data scientist | Explore distributions and relationships | Narrow operation set | Statistical capabilities are a meaningful adoption driver |
| Data engineer | Validate incoming files | Schema drift and data quality | Dataset profiling catches problems before analysis |
| Software engineer | Integrate analysis | No stable machine interface | Typed CLI/API contracts enable integration |
| ML engineer | Evaluate model decisions | Cannot inspect model/plan quality | Run traces and eval reports make failures diagnosable |
| Finance analyst | Compare periods and ratios | Wrong denominator, unit, or aggregation | Semantic definitions reduce material calculation errors |
| Small-business owner | Understand spreadsheet performance | Technical onboarding | Simple mode and suggested questions reduce time-to-answer |
| Operations manager | Identify operational changes | Results lack context | Comparisons and caveats make outputs useful |
| Executive | Get a concise answer | Too much implementation detail | Evidence summary with optional drill-down improves comprehension |

## 3. Cross-persona hypotheses

1. Users need to see the selected measure, filters, and comparison period.
2. Users prefer clarification over a silently guessed scope.
3. Dataset profiling should precede open-ended analysis.
4. Users want to save analyses they repeat.
5. Trust depends on inspectable evidence, not confident prose.
6. Privacy claims must be clear about model hosting, telemetry, persistence, and exports.
7. Simple and advanced users need different levels of detail.

## 4. Interview plan

Run 8–12 interviews before making major UX assumptions:
- 3–4 people who regularly analyse spreadsheets.
- 2–3 operations or finance users.
- 2–3 technical analysts or BI developers.
- 1–2 people who rarely use analytical tools.

Ask about their last real analysis task, not hypothetical feature preferences.

### Interview prompts
1. Show me the last spreadsheet or dataset you had to analyse.
2. What question were you trying to answer?
3. How did you calculate it?
4. What took the most time?
5. How did you check that the answer was correct?
6. Did you need to repeat the analysis later?
7. What would make you unwilling to upload or process this data?
8. What would you need to see before trusting an automated result?

Avoid pitching the product until the participant has described their current workflow.

## 5. Usability test tasks

Give participants a small, unfamiliar dataset and ask them to:
- Identify the date and measure columns.
- Find the top five regions by sales.
- Compare the latest year with the previous year.
- Explain one caveat in the result.
- Save the analysis and rerun it on a replacement file.

Observe completion without coaching. Record errors, clarification needs, time, and participant confidence.

## 6. Evidence log

For each session record:
- Participant code (not personal identifiers).
- Persona and relevant experience.
- Task and dataset.
- Observed behaviour.
- Direct quote, with permission.
- Severity and frequency.
- Product implication.
- Whether the finding is confirmed, contradicted, or unresolved.

Do not place personal or commercially sensitive participant data in the repository.

## 7. Prioritization

Prioritize a finding when it is:
- Observed in real behaviour, not merely requested as a feature.
- Repeated across participants or blocks a critical task.
- Connected to a measurable product outcome.
- Addressable within the current product scope.

## 8. Current research status

- Simulated cohort exercise: completed as a design exercise.
- Real interviews: not yet conducted.
- Usability sessions: not yet conducted.
- Product-market fit: not established.
