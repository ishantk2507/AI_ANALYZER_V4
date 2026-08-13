# Operations and handoff guide

## Routine commands

```bash
ai-analyzer doctor                 # backend, model, data, and store health
ai-analyzer datasets               # catalogued tables and schemas
ai-analyzer ask "..."              # one-off analysis
ai-analyzer chat                   # persistent interactive terminal chat
ai-analyzer chats                  # list stored chats
ai-analyzer clear-cache            # remove disposable model-response cache
```

Use the same Unix account or container identity for these commands as the web
service so paths, access controls, data, and SQLite state match production.

## What may be cleaned

Safe to delete and regenerated automatically:

- `.cache/` — data profiles and model-response cache.
- `outputs/` — generated chart exports.
- `logs/` — after exporting logs needed for incident analysis.
- Python `__pycache__/`, `.pytest_cache/`, and `.ruff_cache/` directories.

Do not delete `var/analyzer.db` unless intentionally removing every persisted
chat and user preference. Do not delete source data, models, or deployment
configuration without separate retention decisions.

## Monitoring and troubleshooting

| Symptom | First checks |
| --- | --- |
| App will not answer | Run `ai-analyzer doctor`; verify Ollama and model name. |
| No datasets found | Check `DATA_DIR`, mount permissions, and file layout. |
| Slow answers | Check model/GPU placement, context length, and concurrent users. |
| Wrong or empty filter | Inspect returned notes and actual source values. |
| Chat history missing | Check `PERSIST_CHATS`, `STORE_PATH`, permissions, and identity header. |
| Browser error lacks trace | Expected in production; inspect `LOG_DIR/app.log`. |

Logs rotate at 5 MiB with three backups. Capture the log, app version,
redacted configuration, exact question, and data-file version for incidents.

## Release verification

```bash
python -m pytest
python -m ruff check app tests scripts
python -m app.cli doctor
```

GitHub Actions runs tests on Python 3.10, 3.11, and 3.12, and runs the locked
dependency set on Python 3.11. Update `requirements.lock.txt` only after testing
a dependency change; then update its header with the tested Python version and
test count.

## Incoming-owner checklist

Transfer these separately through an approved secret/data channel:

1. Production environment configuration, never a committed `.env`.
2. Model files or access to the provisioned Ollama registry.
3. Source data and data-refresh ownership.
4. SQLite backup and restore procedure, if history must be retained.
5. Reverse-proxy authentication, TLS certificate, deployment, and monitoring
   ownership.

This repository contains application code and templates only; operational
secrets, data, models, and user state do not belong in source control.
