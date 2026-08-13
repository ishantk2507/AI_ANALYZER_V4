"""In-process llama.cpp backend.

Constrained decoding here goes through llama.cpp's GBNF grammar compiler, which
`response_format={"type": "json_object", "schema": ...}` drives for us.

Note: the stock PyPI wheel is CPU-only. `info().gpu` reports what the loaded
build actually supports so the UI can tell the truth about where inference runs.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.config import settings
from app.llm.base import BackendInfo, BackendUnavailable, ChatMessage, LLMBackend
from app.logging_setup import get_logger

log = get_logger(__name__)


def gpu_offload_supported() -> bool:
    try:
        from llama_cpp import llama_supports_gpu_offload

        return bool(llama_supports_gpu_offload())
    except Exception:
        return False


class LlamaCppBackend(LLMBackend):
    def __init__(self, model_path=None, lazy: bool = True):
        try:
            import llama_cpp  # noqa: F401
        except ImportError as exc:
            raise BackendUnavailable("llama-cpp-python is not installed") from exc

        self.model_path = model_path or settings.resolved_model_path()
        if self.model_path is None or not self.model_path.is_file():
            raise BackendUnavailable(
                "no GGUF model file found. Set MODEL_PATH in .env or drop the "
                "file into <project>/models/."
            )

        self._gpu = gpu_offload_supported()
        self._llm = None
        if not lazy:
            self._ensure_loaded()

    def _ensure_loaded(self):
        if self._llm is not None:
            return self._llm

        from llama_cpp import Llama

        n_gpu_layers = settings.model_n_gpu_layers if self._gpu else 0
        if not self._gpu:
            log.warning(
                "llama-cpp-python was built without GPU offload; running on CPU. "
                "See the README for the CUDA wheel."
            )

        log.info("Loading %s (n_ctx=%d, n_gpu_layers=%d)",
                 self.model_path.name, settings.model_n_ctx, n_gpu_layers)

        kwargs: Dict[str, Any] = {
            "model_path": str(self.model_path),
            "n_ctx": settings.model_n_ctx,
            "n_gpu_layers": n_gpu_layers,
            "verbose": False,
        }
        if settings.model_n_threads:
            kwargs["n_threads"] = settings.model_n_threads

        self._llm = Llama(**kwargs)
        return self._llm

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
        # One model per process here, so a per-call override cannot be honoured.
        if model and model != self.model_path.name:
            log.debug("Ignoring per-call model %r; llama.cpp holds one model", model)
        llm = self._ensure_loaded()

        kwargs: Dict[str, Any] = {}
        if schema is not None:
            kwargs["response_format"] = {"type": "json_object", "schema": schema}

        result = llm.create_chat_completion(
            messages=list(messages),
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop or [],
            **kwargs,
        )
        try:
            return result["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:  # pragma: no cover
            raise RuntimeError(f"unexpected llama.cpp response: {json.dumps(result)[:400]}") from exc

    def info(self) -> BackendInfo:
        return BackendInfo(
            name="llama_cpp",
            model=self.model_path.name,
            gpu=self._gpu,
            detail={
                "n_ctx": settings.model_n_ctx,
                "n_gpu_layers": settings.model_n_gpu_layers if self._gpu else 0,
                "loaded": self._llm is not None,
            },
        )

    def close(self) -> None:
        self._llm = None
