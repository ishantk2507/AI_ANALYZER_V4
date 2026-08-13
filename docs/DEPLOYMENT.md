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

## Getting the GPU involved

The app has two backends. `BACKEND=auto` uses Ollama when its server is
reachable and falls back to in-process llama.cpp otherwise. `ai-analyzer doctor`
reports which one is active and whether it can actually reach the GPU.

### Option A — Ollama (recommended, nothing to compile)

Ollama ships with CUDA, so this is the path that uses the GPU without building
anything. Register the GGUF once:

```bash
ollama serve
ollama create ai-analyzer-ministral-8b-iq4 -f modelfiles/ministral-8b-iq4
```

Templates for each supported quantisation are in
[`modelfiles/`](../modelfiles); `FROM` resolves relative to the file, so putting
the GGUF in `models/` needs no edit. `OLLAMA_AUTO_CREATE=true` does this on
first run in development — set it to **false** in production, because a web
request should not mutate model state.

Registering copies the whole GGUF into Ollama's store (~2GB for the 3B, ~4.7GB
for the 8B IQ4_XS), so budget the disk twice.

### Option B — a CUDA wheel for llama-cpp-python

The wheel on PyPI is **CPU-only**. Installing it and expecting the GPU is the
usual cause of "why is this so slow" — `doctor` reports this explicitly as
`llama-cpp gpu: cpu only`, and the UI shows a warning in the sidebar.

Install a prebuilt CUDA wheel instead, matching your CUDA toolkit version:

```bash
pip install llama-cpp-python \
  --force-reinstall --no-cache-dir \
  --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124
```

Or build it against your own toolkit, which needs a C++ compiler and CMake:

```bash
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python --force-reinstall --no-cache-dir
```

Verify with `ai-analyzer doctor` — the check calls
`llama_supports_gpu_offload()` on the loaded build, so it reports what is really
there rather than what was requested.

Use this option when you want one process with no separate service (a desktop
install, or an air-gapped box where running Ollama is not acceptable). Otherwise
prefer Ollama: it is less to maintain and reaches the same hardware.

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

## Bare-metal install with systemd

Ready-to-edit files are in [`deploy/`](../deploy). This is the layout they
assume.

```bash
# 1. Service account and directories.
sudo useradd --system --home /srv/ai-analyzer --shell /usr/sbin/nologin ai-analyzer
sudo mkdir -p /srv/ai-analyzer/{app,data,state} /var/log/ai-analyzer /etc/ai-analyzer
sudo chown -R ai-analyzer:ai-analyzer /srv/ai-analyzer/state /var/log/ai-analyzer

# 2. Code and dependencies.
sudo -u ai-analyzer git clone <repo-url> /srv/ai-analyzer/app
cd /srv/ai-analyzer/app
sudo -u ai-analyzer python3 -m venv .venv
sudo -u ai-analyzer .venv/bin/pip install -r requirements.lock.txt
sudo -u ai-analyzer .venv/bin/pip install . --no-deps

# 3. Configuration. Never a committed .env.
sudo cp .env.production.example /etc/ai-analyzer/production.env
sudo chown root:ai-analyzer /etc/ai-analyzer/production.env
sudo chmod 640 /etc/ai-analyzer/production.env
sudo editor /etc/ai-analyzer/production.env

# 4. Preflight as the service account, before any traffic.
sudo -u ai-analyzer .venv/bin/ai-analyzer doctor

# 5. Service.
sudo cp deploy/ai-analyzer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ai-analyzer
systemctl status ai-analyzer
```

The unit binds to `127.0.0.1:8501` and is hardened with `ProtectSystem=strict`,
so `/srv/ai-analyzer/state` and `/var/log/ai-analyzer` are the only writable
paths. Adding a directory the app must write to means adding it to
`ReadWritePaths=`.

## Reverse proxy and identity

A worked nginx configuration is in
[`deploy/nginx.conf.example`](../deploy/nginx.conf.example). Two parts of it are
not optional:

- **Websocket upgrade.** Streamlit holds an open websocket for the session. A
  proxy that omits the `Upgrade`/`Connection` headers serves a UI that loads and
  then hangs at "Connecting…" — the most common misconfiguration by a wide
  margin.
- **Clearing the inbound identity header.** See below.

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
