# Observability and run trace specification

## 1. Goals

A run trace should answer:
- What did the user ask?
- Which dataset snapshot was used?
- What did the planner decide?
- Which operations ran?
- What did validation accept or reject?
- Which facts support the final answer?
- Where did time and tokens go?
- What failed, and what should happen next?

## 2. Run lifecycle

Suggested stages:
1. request_received
2. dataset_resolved
3. intent_resolved
4. plan_generated
5. plan_validated
6. execution_started / execution_completed
7. step_validated
8. repair_started / repair_completed (if needed)
9. facts_created
10. answer_generated
11. answer_verified
12. response_completed

Emit a terminal event even on early failure.

## 3. Event envelope

Each JSONL event should contain:
- schema_version
- run_id
- event_id
- timestamp_utc
- stage
- event_type
- status
- duration_ms (when applicable)
- dataset_fingerprint (when applicable)
- model/backend metadata (when applicable)
- plan_version and prompt_version
- diagnostic_code (when applicable)
- input/output hashes (where safe)
- parent_step_id (for workflow steps)

Do not log raw dataset rows, secrets, full prompts, or user data by default. Provide an explicit local debug mode with clear retention controls if full prompt logging is required.

## 4. Run summary

A run summary should contain:
- run_id and created time
- final status
- dataset ID and fingerprint
- normalized intent
- validated plan hash
- execution steps and output references
- computed fact IDs
- claim-to-fact links
- warnings and diagnostics
- model name/digest, backend, quantization, context
- prompt tokens, output tokens, prompt evaluation duration, generation duration
- per-stage and total latency
- cache hit/miss and cache key version
- software commit and configuration fingerprint

Unavailable provider telemetry must be null, not fabricated.

## 5. Stable diagnostic codes

Start with a small controlled vocabulary:
- DATASET_NOT_FOUND
- DATASET_CHANGED
- SCHEMA_MISMATCH
- AMBIGUOUS_MEASURE
- UNKNOWN_MEASURE
- UNKNOWN_FILTER_COLUMN
- UNKNOWN_FILTER_VALUE
- AMBIGUOUS_FILTER_VALUE
- FILTER_PARTIAL_MATCH
- INVALID_PLAN
- UNSUPPORTED_OPERATION
- INSUFFICIENT_PERIODS
- INVALID_DENOMINATOR
- EMPTY_RESULT
- JOIN_CARDINALITY_VIOLATION
- RESOURCE_LIMIT_EXCEEDED
- MODEL_UNAVAILABLE
- MODEL_OUTPUT_INVALID
- CLAIM_UNSUPPORTED
- INTERNAL_ERROR

Keep the human message separate from the machine code.

## 6. Privacy and retention

- Default to local-only trace storage.
- Make raw prompt capture opt-in.
- Redact paths and identifiers in exported traces.
- Set a documented retention period and cleanup policy.
- Do not store full input data in traces.
- Keep user feedback linked by run ID, not by embedding personal details.
- Treat traces as potentially sensitive because even metadata can reveal business activity.

## 7. Metrics

At minimum:
- Request count by status.
- Stage latency distributions.
- Model calls and tokens per request.
- Cache hit rate.
- Validation failure counts by code.
- Repair attempt and success counts.
- Evaluation correctness by task tag.
- Memory and resource-limit events.

Use JSONL and a CLI report initially. Add Prometheus or dashboards only when there is a real deployment requirement.

## 8. Implementation order

1. Add run ID and stage timings to existing answer lifecycle.
2. Add structured diagnostics.
3. Add backend response metadata.
4. Add dataset/config/prompt/code fingerprints.
5. Add JSONL writer and retention.
6. Add evaluation report integration.
