"""Central configuration.

Every path is resolved against the project root, so the app behaves the same
no matter which directory you launch it from. Values come from (in order of
precedence): environment variables, the `.env` file, then these defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]

#: The quantised model this project is built around.
MODEL_FILENAME = "Ministral-3-3B-Instruct-2512-Q4_K_M.gguf"

#: Searched in order when MODEL_PATH is not set explicitly. `models/` is the
#: documented home; `~/Downloads` is where a freshly fetched GGUF usually lands,
#: and picking it up from there saves copying 5GB before the first run.
_MODEL_SEARCH_DIRS = (
    PROJECT_ROOT / "models",
    Path.home() / "Downloads",
)

#: Searched in order when DATA_DIR is not set explicitly.
_DATA_SEARCH_DIRS = (PROJECT_ROOT / "data",)


def discover_model() -> Optional[Path]:
    """Locate the GGUF without forcing the user to copy a 2GB file around."""
    for directory in _MODEL_SEARCH_DIRS:
        candidate = directory / MODEL_FILENAME
        if candidate.is_file():
            return candidate

    # Fall back to any GGUF the user dropped into the project's models/ dir.
    local_models = PROJECT_ROOT / "models"
    if local_models.is_dir():
        for candidate in sorted(local_models.glob("*.gguf")):
            return candidate
    return None


def discover_data_dir() -> Path:
    """Prefer a populated data directory over an empty one."""
    for directory in _DATA_SEARCH_DIRS:
        if directory.is_dir() and any(directory.iterdir()):
            return directory
    return PROJECT_ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `model_*` fields would otherwise collide with pydantic's namespace.
        protected_namespaces=(),
    )

    # --- Model / inference -------------------------------------------------
    model_path: Optional[Path] = None
    backend: Literal["auto", "ollama", "llama_cpp"] = "auto"

    ollama_host: str = "http://localhost:11434"
    ollama_model_name: str = "ai-analyzer-ministral"
    #: An optional larger model for the interpretation stage only. Writing the
    #: business reading is the one job a 3B does badly -- it produced "the skew
    #: toward scooters, far exceeding even motorcycles", which is a category
    #: error. Empty means use the main model for everything.
    #:
    #: Only worth setting when both models fit in VRAM at once; otherwise Ollama
    #: swaps them on every question. On a 6GB card an 8B Q4 does not fit
    #: alongside a 3B, so see BACKEND / OLLAMA_MODEL_NAME to switch wholesale.
    insight_model: str = ""
    #: Register the GGUF with Ollama automatically on first run.
    ollama_auto_create: bool = True

    model_n_ctx: int = 4096
    #: -1 offloads every layer to the GPU; both backends honour it. This forces
    #: the issue rather than asking: Ollama would otherwise size the split from
    #: its own conservative VRAM estimate and leave part of an 8B on the CPU.
    #:
    #: The cost of forcing is that a model which genuinely does not fit fails to
    #: load instead of spilling to CPU. Name an explicit layer count to get a
    #: deliberate split back. Sizes on a 6GB card at num_ctx 4096: 3B Q4_K_M
    #: ~2.3GB, 8B IQ4_XS ~5.4GB (fits), 8B Q4_K_M ~5.9GB (fits, ~100MB spare).
    model_n_gpu_layers: int = -1
    model_n_threads: Optional[int] = None

    #: Structured output wants near-greedy decoding; prose wants a little slack.
    structured_temperature: float = 0.0
    narrative_temperature: float = 0.35
    max_output_tokens: int = 640

    # --- Data --------------------------------------------------------------
    data_dir: Optional[Path] = None
    output_dir: Path = PROJECT_ROOT / "outputs"
    cache_dir: Path = PROJECT_ROOT / ".cache"
    log_dir: Path = PROJECT_ROOT / "logs"

    #: Rows of the result table shown to the model when writing the narrative.
    max_rows_in_prompt: int = 15
    #: Hard cap on rows kept in a result table.
    max_result_rows: int = 500
    #: Distinct values listed per column when grounding the model in real data.
    max_sample_values: int = 12
    #: A column with more distinct values than this is treated as high-cardinality
    #: and gets fuzzy value matching instead of an exhaustive value list.
    high_cardinality_threshold: int = 40

    #: How many times the model may retry after a schema/validation failure.
    max_repair_attempts: int = 2
    #: Check every figure in the narrative against the computed table, and fall
    #: back to a table-derived summary when the model invents one.
    verify_numbers: bool = True

    # --- Analysis quality ---------------------------------------------------
    #: A growth ranking ignores rows whose starting value is below
    #: max(growth_min_base, total_base * growth_min_base_share). Without this,
    #: "fastest growing" is always whoever sold one unit last year.
    growth_min_base: int = 50
    growth_min_base_share: float = 0.00005
    #: ...unless fewer than this many rows would survive, meaning the data is
    #: genuinely small rather than long-tailed.
    growth_min_rows: int = 3

    #: The canonical breakdown to answer from when the question does not ask
    #: about a specific one. Every breakdown partitions the same registrations,
    #: so the choice only matters for consistency -- and vehicle class is the
    #: authoritative view. Set to "" to always use the fullest table instead.
    base_breakdown: str = "CLASS"

    #: Leave the base table only for one holding at least this much more of the
    #: metric. Compared on registrations, not row count -- MAKER has 6.5x the
    #: rows of CLASS but only 0.6% more data.
    widen_min_gain_share: float = 0.02

    #: How many computed insights and follow-up questions to surface.
    max_insights: int = 4
    max_followups: int = 3
    #: Datasets held in the in-memory LRU at once.
    dataset_cache_size: int = 4

    # --- Persistence -------------------------------------------------------
    #: Chats, turns and user memory. Deliberately not under `cache_dir`: that is
    #: disposable and the UI offers a button that empties it, whereas losing a
    #: user's chat history is a bug.
    store_path: Path = PROJECT_ROOT / "var" / "analyzer.db"
    #: Off runs entirely in memory -- chats live as long as the process does.
    persist_chats: bool = True
    #: Standing facts injected into every prompt. Capped because they compete
    #: with the schema and the result table for a 4K window.
    max_user_memories: int = 12
    #: Chats offered in the sidebar's list.
    max_chats_listed: int = 50

    # --- Multi-user --------------------------------------------------------
    #: Who a session belongs to when nothing identifies the user. Single-user
    #: installs never see anything else.
    default_user: str = "local"
    #: Name of the request header a trusted reverse proxy sets to the
    #: authenticated username, e.g. "X-Forwarded-User". Empty keeps the app in
    #: single-user mode. Only ever set this behind a proxy that *strips* the
    #: header from client requests -- see docs/DEPLOYMENT.md, because a
    #: forgeable header is not authentication.
    auth_header: str = ""

    # --- Presentation ------------------------------------------------------
    chart_dpi: int = 120
    chart_theme: Literal["light", "dark"] = "light"

    log_level: str = "INFO"
    log_console_level: str = "WARNING"

    @field_validator("model_path", mode="before")
    @classmethod
    def _resolve_model_path(cls, value):
        if value in (None, "", "None"):
            return None
        path = Path(value)
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    @field_validator("data_dir", "output_dir", "cache_dir", "log_dir", mode="before")
    @classmethod
    def _resolve_dir(cls, value):
        if value in (None, "", "None"):
            return None
        path = Path(value)
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    @field_validator("store_path", mode="before")
    @classmethod
    def _resolve_store_path(cls, value):
        # Unlike the directories above this one is not Optional, so an empty
        # STORE_PATH= in .env has to fall back rather than resolve to None.
        if value in (None, "", "None"):
            return PROJECT_ROOT / "var" / "analyzer.db"
        path = Path(value)
        return path if path.is_absolute() else (PROJECT_ROOT / path)

    def resolved_model_path(self) -> Optional[Path]:
        if self.model_path and self.model_path.is_file():
            return self.model_path
        return discover_model()

    def resolved_data_dir(self) -> Path:
        if self.data_dir:
            return self.data_dir
        return discover_data_dir()

    def ensure_dirs(self) -> None:
        for directory in (self.output_dir, self.cache_dir, self.log_dir,
                          self.store_path.parent):
            directory.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "charts").mkdir(parents=True, exist_ok=True)


settings = Settings()
