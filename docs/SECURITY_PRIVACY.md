# Security and privacy notes

**Status:** Baseline policy and design checklist. This is not a security certification or formal threat-model audit.

## 1. Assets to protect

- Source datasets and derived results.
- Chat transcripts and saved preferences.
- Local model files and configuration.
- Run traces and exported reports.
- Authentication identity passed from a reverse proxy.
- Workflow definitions and user-created tool manifests.

## 2. Trust boundaries

Treat as untrusted:
- User questions and uploaded files.
- Column names and cell values.
- File metadata and archive contents.
- Model-generated plans and workflow manifests.
- Imported workflow definitions.
- External connector responses if added later.

Dataset cell contents must never become system instructions. Prompt injection in a cell is data, not authority.

## 3. Execution safety

- Do not execute arbitrary model-generated code.
- Do not use eval or exec for analysis expressions.
- Keep expressions in a typed AST with allow-listed operators.
- Validate every column, value, operation, and output reference.
- Restrict file reads/writes to configured roots.
- Apply row, memory, time, and output-size budgets.
- Reject workflow cycles and unregistered capabilities.
- Treat chart labels and exported content as untrusted text.

## 4. Data handling

Document clearly:
- Which model backend is used.
- Whether inference is local or remote.
- Where datasets, chats, and traces are stored.
- Whether prompts or data leave the machine.
- What is cached and for how long.
- How to clear chats, caches, and exports.
- What backups contain.

Do not claim that the application is fully local if any configured model, telemetry, or external service receives user content.

## 5. Authentication and isolation

The existing architecture documents AUTH_HEADER as an identity handoff from a trusted proxy, not authentication by itself. A deployment must:
- Remove client-supplied copies of the identity header.
- Set the trusted identity at the proxy.
- Restrict direct access to the application port.
- Use TLS at the public boundary.
- Keep SQLite and data directories inaccessible to other users.
- Reassess the storage model before multi-replica deployment.

## 6. Secrets

- Never commit .env, tokens, credentials, or private dataset paths.
- Keep example configuration values non-secret.
- Rotate any credential that may have been committed.
- Avoid printing environment values in diagnostics.
- Keep model and dataset paths configurable.

## 7. Logging and traces

Default logs should contain event IDs, stage names, diagnostic codes, durations, and safe fingerprints—not raw rows or complete prompts. Full prompt logging must be explicit, local, and clearly described.

## 8. Threat scenarios to test

- CSV cell contains prompt-injection instructions.
- Malformed or oversized file exhausts memory.
- Path traversal in upload or export names.
- Ambiguous filters broaden a query.
- Many-to-many join unexpectedly multiplies values.
- Imported workflow references unknown or unsafe capability.
- Workflow attempts excessive rows, memory, or runtime.
- User A can access user B's chat or export.
- Client spoofs a proxy identity header.
- Trace or error message leaks a local path or sensitive value.

## 9. Release requirement

Security-sensitive changes require tests and a documented threat scenario. A local-first claim must match actual network behaviour and backend configuration.
