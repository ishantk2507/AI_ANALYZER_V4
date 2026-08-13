# Configuration reference

Configuration precedence is: environment variables, `.env` in the project root,
then application defaults. Paths may be absolute or relative to the project root.
Copy `.env.example` for local use; never commit a populated `.env` file.

## Inference

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKEND` | `auto` | `auto`, `ollama`, or `llama_cpp`; auto prefers reachable Ollama. |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama service URL. |
| `OLLAMA_MODEL_NAME` | `ai-analyzer-ministral` | Registered Ollama model. |
| `OLLAMA_AUTO_CREATE` | `true` | Create a local GGUF-backed Ollama model if missing. Set `false` in production. |
| `MODEL_PATH` | auto-discovered | GGUF path for registration or llama.cpp. |
| `INSIGHT_MODEL` | empty | Optional Ollama model for interpretation only. |
| `MODEL_N_CTX` | `4096` | Model context length. |
| `MODEL_N_GPU_LAYERS` | `-1` | GPU layers; `-1` requests full offload. |
| `MODEL_N_THREADS` | unset | CPU threads for llama.cpp. |
| `STRUCTURED_TEMPERATURE` | `0.0` | Temperature for routing and plans. |
| `NARRATIVE_TEMPERATURE` | `0.35` | Temperature for prose. |
| `MAX_OUTPUT_TOKENS` | `640` | Per-stage generation limit. |

When unset, `MODEL_PATH` is searched by filename in `models/` and the user's
Downloads folder. Containers should use a pre-registered Ollama model instead.

## Data and analysis

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATA_DIR` | `data/` | Root containing CSV/Parquet files. |
| `OUTPUT_DIR` | `outputs/` | Generated PNG charts. |
| `CACHE_DIR` | `.cache/` | Disposable profiles and model responses. |
| `MAX_ROWS_IN_PROMPT` | `15` | Result rows available to narration. |
| `MAX_RESULT_ROWS` | `500` | Returned-result hard cap. |
| `MAX_SAMPLE_VALUES` | `12` | Sample values shown per column. |
| `HIGH_CARDINALITY_THRESHOLD` | `40` | Distinct-value cutoff for fuzzy matching. |
| `MAX_REPAIR_ATTEMPTS` | `2` | Bounded retries after invalid model output. |
| `VERIFY_NUMBERS` | `true` | Verify numeric claims in prose; leave enabled. |
| `GROWTH_MIN_BASE` | `50` | Absolute base for the growth-ranking guard. |
| `GROWTH_MIN_BASE_SHARE` | `0.00005` | Relative base for the growth-ranking guard. |
| `GROWTH_MIN_ROWS` | `3` | Skip the guard when fewer rows would survive. |
| `BASE_BREAKDOWN` | `CLASS` | Default breakdown; empty disables this preference. |
| `WIDEN_MIN_GAIN_SHARE` | `0.02` | Minimum coverage gain needed to widen a table choice. |
| `MAX_INSIGHTS` | `4` | Computed findings per answer. |
| `MAX_FOLLOWUPS` | `3` | Suggested next questions. |
| `DATASET_CACHE_SIZE` | `4` | Loaded-dataset LRU size. |
| `CHART_DPI` | `120` | Exported PNG resolution. |
| `CHART_THEME` | `light` | Chart fallback theme. |

## Persistence, identity, and logging

| Variable | Default | Purpose |
| --- | --- | --- |
| `STORE_PATH` | `var/analyzer.db` | SQLite database; back it up. |
| `PERSIST_CHATS` | `true` | Set `false` for in-memory chats. |
| `MAX_USER_MEMORIES` | `12` | Standing preferences per user. |
| `MAX_CHATS_LISTED` | `50` | Sidebar chat-list limit. |
| `DEFAULT_USER` | `local` | Identity in single-user mode. |
| `AUTH_HEADER` | empty | Trusted proxy-injected identity header. |
| `LOG_DIR` | `logs/` | Rotating `app.log` directory. |
| `LOG_LEVEL` | `INFO` | File log threshold. |
| `LOG_CONSOLE_LEVEL` | `WARNING` | Standard-error threshold. |

The log rotates at 5 MiB and retains three backups. `STORE_PATH` is not a cache:
deleting it permanently removes persisted chats and preferences.

## Streamlit settings

`.streamlit/config.toml` uses secure defaults: loopback binding, CORS/XSRF
protection, disabled telemetry, and no browser stack traces. Streamlit command
line options or `STREAMLIT_*` environment variables override the file. Keep the
app on loopback in a VM deployment and put a TLS reverse proxy in front of it.
