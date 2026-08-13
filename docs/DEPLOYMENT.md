# Production deployment

## Deployment model

Run one Streamlit application process behind an authenticated HTTPS reverse
proxy. Run Ollama separately on a private network or on the same host. Mount
source data read-only and persist SQLite state, generated charts, caches, and
logs on application-owned storage.

```text
Browser -- HTTPS/auth -- Reverse proxy -- loopback :8501 AI Analyzer -- private network -- Ollama
                                             |
                                             +-- read-only /data
                                             +-- durable SQLite, cache, outputs, and logs
```

Do not expose port 8501 directly to the public internet. This project does not
add its own application authentication.

## Preflight checklist

1. Provision and register the model in Ollama; confirm its exact name with
   `ollama list`.
2. Validate input data and mount it read-only.
3. Place production configuration in a protected secret/environment store,
   beginning with `.env.production.example`.
4. Set `OLLAMA_AUTO_CREATE=false`; a web request must not mutate model state.
5. Leave `AUTH_HEADER` empty for single-user use. Enable it only with a trusted
   authenticated reverse proxy.
6. Establish backup, retention, and log collection before accepting users.

Run `ai-analyzer doctor` as the service account before switching traffic. It
checks backend reachability, model setup, data discovery, and database access.

## Docker

The supplied `Dockerfile` builds the app only. It intentionally excludes data,
GGUF files, `.env`, state, logs, and development files.

```bash
docker build -t ai-analyzer:3.0.0 .
docker run --rm -p 127.0.0.1:8501:8501 \
  --env-file /etc/ai-analyzer/production.env \
  -v /srv/ai-analyzer/data:/data:ro \
  -v /srv/ai-analyzer/state:/var/lib/ai-analyzer \
  -v /srv/ai-analyzer/logs:/var/log/ai-analyzer \
  ai-analyzer:3.0.0
```

The image runs as UID 10001. Mounted state and log directories must be writable
by that user; `/data` must be readable. The orchestration health endpoint is
`/_stcore/health`. It confirms web-process availability, not data/model fitness,
so retain the `doctor` preflight.

For a non-container deployment, create a virtual environment, install from the
locked requirements plus the package, run a process manager, and bind Streamlit
to `127.0.0.1:8501`.

## Reverse proxy and identity

Terminate TLS and authenticate before traffic reaches Streamlit. For multi-user
chat isolation, the proxy must remove incoming client-supplied identity headers,
authenticate the request, and set `AUTH_HEADER` from the authenticated principal
only. Passing client-supplied headers through is a data-isolation vulnerability:
an attacker could select another user's stored chats.

Keep Ollama private as well. The application needs only outbound access from the
web process to the configured Ollama endpoint.

## Backups, updates, and scale

Back up `STORE_PATH` as a SQLite-consistent snapshot and back up source data
separately. Caches and generated charts are rebuildable. For a quiet single
process, stop the service briefly before copying SQLite; otherwise use the
SQLite backup API or an infrastructure snapshot with consistency guarantees.

Deploy a new image/environment after a staging `doctor` and test run. Keep the
same state mount to retain chat history. The application runs one process and
uses SQLite, so do not share its database file across horizontally scaled
replicas. Move persistence to a multi-writer datastore before scaling the web
tier.
