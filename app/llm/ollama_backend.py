"""Ollama backend.

Ollama ships with CUDA, so this is the path that reaches the GPU without
compiling anything. The local GGUF is registered once via a Modelfile.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.llm.base import BackendInfo, BackendUnavailable, ChatMessage, LLMBackend
from app.logging_setup import get_logger

log = get_logger(__name__)


def server_is_up(host: Optional[str] = None, timeout: float = 2.0) -> bool:
    try:
        import ollama
    except ImportError:
        return False
    try:
        ollama.Client(host=host or settings.ollama_host, timeout=timeout).list()
        return True
    except Exception:
        return False


class OllamaBackend(LLMBackend):
    def __init__(self, model_name: Optional[str] = None, host: Optional[str] = None):
        try:
            import ollama
        except ImportError as exc:  # pragma: no cover
            raise BackendUnavailable("the `ollama` python package is not installed") from exc

        self.host = host or settings.ollama_host
        self.model_name = model_name or settings.ollama_model_name
        self._client = ollama.Client(host=self.host, timeout=600.0)

        try:
            available = self._list_models()
        except Exception as exc:
            raise BackendUnavailable(
                f"cannot reach the Ollama server at {self.host}. Start it with `ollama serve`."
            ) from exc

        if not self._model_present(available):
            if not settings.ollama_auto_create:
                raise BackendUnavailable(
                    f"model '{self.model_name}' is not registered with Ollama and "
                    "OLLAMA_AUTO_CREATE is off"
                )
            self._register_gguf()

    # -- setup --------------------------------------------------------------

    def _list_models(self) -> List[str]:
        response = self._client.list()
        models = response.get("models", []) if isinstance(response, dict) else response.models
        names: List[str] = []
        for model in models:
            name = model.get("model") if isinstance(model, dict) else getattr(model, "model", None)
            if name:
                names.append(name)
        return names

    def _model_present(self, available: List[str]) -> bool:
        # Ollama normalises bare names to "<name>:latest".
        wanted = {self.model_name, f"{self.model_name}:latest"}
        return any(name in wanted for name in available)

    def _register_gguf(self) -> None:
        """Create the Ollama model from the local GGUF.

        Done through the CLI rather than the HTTP API because the CLI resolves
        local file paths directly; the API would need a manual blob upload.
        """
        gguf = settings.resolved_model_path()
        if gguf is None:
            raise BackendUnavailable(
                "no GGUF found to register with Ollama. Set MODEL_PATH in .env."
            )

        cli = shutil.which("ollama")
        if cli is None:
            raise BackendUnavailable(
                "the `ollama` executable is not on PATH, so the GGUF cannot be registered "
                f"automatically. Run: ollama create {self.model_name} "
                "-f modelfiles/<name> (see modelfiles/ for the templates)"
            )

        modelfile = "\n".join(
            [
                f"FROM {gguf.as_posix()}",
                f"PARAMETER num_ctx {settings.model_n_ctx}",
                "PARAMETER temperature 0",
                "",
            ]
        )

        log.info("Registering %s with Ollama from %s (copies %.1fGB into Ollama's "
                 "store, once)", self.model_name, gguf,
                 gguf.stat().st_size / 1e9)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Modelfile"
            path.write_text(modelfile, encoding="utf-8")
            proc = subprocess.run(
                [cli, "create", self.model_name, "-f", str(path)],
                capture_output=True,
                text=True,
                timeout=1800,
            )
        if proc.returncode != 0:
            raise BackendUnavailable(
                f"`ollama create` failed: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        log.info("Registered Ollama model %s", self.model_name)

    # -- inference ----------------------------------------------------------

    def chat(
        self,
        messages: List[ChatMessage],
        *,
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        max_tokens: int = 512,
        stop: Optional[List[str]] = None,
        model: Optional[str] = None,
    ) -> str:
        options: Dict[str, Any] = {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": settings.model_n_ctx,
        }
        # Ollama's own estimate leaves a safety margin that, on a 6GB card, keeps
        # ~20% of an 8B Q4 on the CPU -- and those few layers cost more than they
        # look: 14.6 tok/s at 72% GPU vs 43.8 at 100%. Naming a layer count
        # overrides the estimate. 999 means "all"; Ollama clamps to the real count.
        if settings.model_n_gpu_layers is not None:
            options["num_gpu"] = (
                999 if settings.model_n_gpu_layers < 0 else settings.model_n_gpu_layers
            )
        if stop:
            options["stop"] = stop

        kwargs: Dict[str, Any] = {}
        if schema is not None:
            # Ollama accepts a JSON Schema here and constrains sampling to it.
            kwargs["format"] = schema

        response = self._client.chat(
            model=model or self.model_name,
            messages=list(messages),
            options=options,
            **kwargs,
        )
        return _extract_content(response)

    def info(self) -> BackendInfo:
        return BackendInfo(
            name="ollama",
            model=self.model_name,
            gpu=True,  # Ollama offloads to CUDA automatically when it fits.
            detail={"host": self.host, "n_ctx": settings.model_n_ctx},
        )


def _extract_content(response: Any) -> str:
    """ollama-python has returned both dicts and pydantic objects over time."""
    if isinstance(response, dict):
        return (response.get("message") or {}).get("content", "") or ""
    message = getattr(response, "message", None)
    return getattr(message, "content", "") or ""
