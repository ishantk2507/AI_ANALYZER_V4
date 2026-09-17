# AI Analyzer V4

AI Analyzer is a local-first data-analysis application. Ask a plain-English
question about tabular data and it selects a dataset, produces a validated
pandas analysis, renders a chart, and writes a grounded summary. It supports a
Streamlit web UI and a terminal CLI.

The model is used only for dataset routing, planning, and prose. It never
executes model-generated code: analysis runs through a closed, validated
`AnalysisSpec` and deterministic pandas operations.

## Handoff status

This repository is ready to hand over as an application project:

- 433 automated tests pass.
- Ruff linting is configured and enforced in GitHub Actions.
- Runtime state, models, data, logs, caches, and local secrets are ignored.
- The checked-in web configuration is safe for a reverse-proxy deployment
  (loopback binding and no browser stack traces).
- A non-root Docker image and production configuration template are included.

The project intentionally does **not** include a model or source datasets.
The deployer must supply both.

## Quick start

Prerequisites: Python 3.10–3.12, a `.parquet` or `.csv` dataset tree, and one
supported local inference backend. Ollama is the recommended backend.

```bash
python -m venv .venv
```

Activate the environment, then install the project and copy the configuration
template:

```bash
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
pip install -e ".[ollama,dev]"
Copy-Item .env.example .env

# Linux / macOS
source .venv/bin/activate
pip install -e ".[ollama,dev]"
cp .env.example .env
```

Put your data in `data/` or set `DATA_DIR` in `.env`. Start Ollama and register
the model named by `OLLAMA_MODEL_NAME`; model templates are in `modelfiles/`.
For example, after placing the matching GGUF in `models/`:

```bash
ollama create ai-analyzer-ministral-8b-iq4 -f modelfiles/ministral-8b-iq4
```

Check the environment and launch the UI:

```bash
python -m app.cli doctor
python -m app.launcher
```

On Windows, `run_ui.bat` is an equivalent shortcut. On Linux/macOS use
`./run_ui.sh`.

## Use it

Open `http://localhost:8501`, choose a dataset root in the sidebar if needed,
and ask a question such as “Top 10 makers in Delhi in 2025”. The UI persists
chats and standing preferences when `PERSIST_CHATS=true`.

The CLI is useful for operations and scripted one-off analysis:

```bash
ai-analyzer doctor
ai-analyzer datasets
ai-analyzer ask "Top 10 makers in Delhi in 2025"
ai-analyzer chat
ai-analyzer chats
ai-analyzer remember region Karnataka
ai-analyzer clear-cache
```

Run `ai-analyzer --help` for the complete command reference.

## Data layout

The catalog accepts Parquet (preferred) and CSV. A common layout is one vehicle
category per folder with one breakdown file per table:

```text
data/
  Two Wheeler/
    MAKER.parquet
    CLASS.parquet
    FUEL.parquet
  Three Wheeler/
    MAKER.parquet
    CLASS.parquet
```

Flat files directly under `data/` also work. Each file is profiled at startup;
the application discovers columns dynamically, so it does not require a fixed
schema. Numeric columns are analysis measures; text and recognised time columns
are dimensions. See [Data format](docs/DATA_FORMAT.md) for the operational
rules.

## Architecture

```mermaid
flowchart TD

subgraph group_entry["Entry points"]
  node_ui["Streamlit UI<br/>presentation<br/>[ui.py]"]
  node_launcher["Streamlit launcher<br/>entrypoint<br/>[launcher.py]"]
  node_cli["CLI<br/>entrypoint<br/>[cli.py]"]
end

subgraph group_agent["Guarded planning"]
  node_orchestrator{{"Agent orchestrator<br/>orchestration<br/>[orchestrator.py]"}}
  node_agent_context["Prompts, memory &amp; hints<br/>agent context<br/>[prompts.py]"]
  node_verifier{{"Intent verifier<br/>safety boundary<br/>[verify.py]"}}
end

subgraph group_analysis["Deterministic analysis"]
  node_spec["AnalysisSpec<br/>closed contract<br/>[spec.py]"]
  node_executor["Pandas executor<br/>deterministic execution<br/>[executor.py]"]
  node_results["Insights &amp; charts<br/>result generation<br/>[insights.py]"]
  node_interactive["Follow-up interactions<br/>interaction layer<br/>[interactive.py]"]
end

subgraph group_llm["LLM boundary"]
  node_llm_factory["LLM provider factory<br/>provider selection<br/>[factory.py]"]
  node_llm_provider["LLM provider abstraction<br/>provider interface<br/>[base.py]"]
  node_inference{{"Local inference endpoint<br/>external inference<br/>[ollama_backend.py]"}}
end

node_config["Runtime config<br/>configuration<br/>[config.py]"]
node_session["Session state<br/>[session.py]"]
node_store["Dataset catalog<br/>storage access<br/>[store.py]"]
node_cache["Result cache<br/>[cache.py]"]
node_data[("Filesystem datasets<br/>external data")]

node_launcher -->|"starts"| node_ui
node_ui -->|"questions"| node_orchestrator
node_cli -->|"commands"| node_orchestrator
node_config -.->|"runtime settings"| node_orchestrator
node_ui -->|"history &amp; preferences"| node_session
node_orchestrator -->|"uses"| node_agent_context
node_orchestrator -->|"matches dataset"| node_store
node_store -->|"discovers &amp; reads"| node_data
node_store -->|"reuses catalog results"| node_cache
node_orchestrator -->|"requests routing and prose"| node_llm_factory
node_llm_factory -->|"selects backend"| node_llm_provider
node_llm_provider -->|"inference requests"| node_inference
node_orchestrator -->|"model-derived intent"| node_verifier
node_verifier -->|"valid specification"| node_spec
node_spec -->|"closed operations"| node_executor
node_executor -->|"selected data"| node_store
node_executor -->|"analysis output"| node_results
node_results -->|"supports follow-ups"| node_interactive
node_results -->|"chart &amp; narrative"| node_ui
node_results -->|"output"| node_cli

click node_ui "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/ui.py"
click node_launcher "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/launcher.py"
click node_cli "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/cli.py"
click node_config "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/config.py"
click node_session "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/session.py"
click node_orchestrator "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/agent/orchestrator.py"
click node_agent_context "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/agent/prompts.py"
click node_verifier "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/agent/verify.py"
click node_spec "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/analysis/spec.py"
click node_executor "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/analysis/executor.py"
click node_results "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/analysis/insights.py"
click node_interactive "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/analysis/interactive.py"
click node_store "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/store.py"
click node_cache "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/cache.py"
click node_llm_factory "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/llm/factory.py"
click node_llm_provider "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/llm/base.py"
click node_inference "https://github.com/ishantk2507/ai_analyzer_v4/blob/master/app/llm/ollama_backend.py"

classDef toneNeutral fill:#f8fafc,stroke:#334155,stroke-width:1.5px,color:#0f172a
classDef toneBlue fill:#dbeafe,stroke:#2563eb,stroke-width:1.5px,color:#172554
classDef toneAmber fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#78350f
classDef toneMint fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#14532d
classDef toneRose fill:#ffe4e6,stroke:#e11d48,stroke-width:1.5px,color:#881337
classDef toneIndigo fill:#e0e7ff,stroke:#4f46e5,stroke-width:1.5px,color:#312e81
classDef toneTeal fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#134e4a
class node_ui,node_launcher,node_cli toneBlue
class node_orchestrator,node_agent_context,node_verifier toneAmber
class node_spec,node_executor,node_results,node_interactive toneMint
class node_llm_factory,node_llm_provider,node_inference toneRose
class node_config,node_session,node_store,node_cache,node_data toneNeutral
```

## Configuration and deployment

Copy `.env.example` for local development. Every documented setting is in
[Configuration](docs/CONFIGURATION.md). For a server, begin with
`.env.production.example` and follow the
[Deployment guide](docs/DEPLOYMENT.md). In particular, do not expose Streamlit
directly to the internet and do not set `AUTH_HEADER` unless a trusted reverse
proxy authenticates users and overwrites that header.

Build the production image with:

```bash
docker build -t ai-analyzer:3.0.0 .
```

The image expects an externally provisioned Ollama endpoint and a read-only
`/data` mount; it does not download models or include private data.

## Development

```bash
python -m pytest
python -m ruff check app tests scripts
```

433 tests, none of which need a model, a GPU, or a network. GitHub Actions runs
them on Python 3.10–3.12 and validates the locked dependency set on 3.11.

**Before changing the analysis pipeline, read
[Design decisions](docs/DESIGN-DECISIONS.md).** Most of the guards in
`app/agent/` exist because the real model produced a specific wrong answer, and
[Testing](docs/TESTING.md) catalogues which failure each one prevents.

## Documentation

| Document | What it covers |
|---|---|
| [Architecture](docs/ARCHITECTURE.md) | Module map, the `AnalysisSpec` boundary, data lifecycle, failure handling |
| [Design decisions](docs/DESIGN-DECISIONS.md) | Why the pipeline is fixed rather than agentic, why insights are computed, model benchmarks, known limitations |
| [Configuration](docs/CONFIGURATION.md) | Every setting, its default, and when to change it |
| [Data format](docs/DATA_FORMAT.md) | What the catalog discovers and how files are profiled |
| [Deployment](docs/DEPLOYMENT.md) | Server install, reverse proxy, authentication, Docker |
| [Operations](docs/OPERATIONS.md) | Running, monitoring, backup, release procedure |
| [Testing](docs/TESTING.md) | Suite layout, the regression table, test conventions |

## Repository map

```text
app/           Application source: UI, CLI, analysis pipeline, data catalog, LLM adapters
tests/         Unit and integration-style tests using a stub LLM
docs/          Architecture, design decisions, configuration, deployment, operations, testing
deploy/        systemd unit and nginx reverse-proxy example
modelfiles/    Ollama templates for supported GGUF files
scripts/       Maintenance utilities, including model benchmarks
models/        Where to put the GGUF (contents gitignored)
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: `pytest` and `ruff check` must
pass, and read [Design decisions](docs/DESIGN-DECISIONS.md) before touching the
agent or analysis layers — most of the code there is load-bearing for a reason
that is written down.

## License

MIT. See [LICENSE](LICENSE).
