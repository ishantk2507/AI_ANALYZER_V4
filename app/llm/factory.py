"""Backend selection.

`auto` prefers Ollama because it reaches the GPU without a rebuild, and falls
back to in-process llama.cpp so the app still runs when nothing is serving.
"""

from __future__ import annotations

import threading
from typing import Optional

from app.config import settings
from app.llm.base import BackendUnavailable, LLMBackend
from app.logging_setup import get_logger

log = get_logger(__name__)

_backend: Optional[LLMBackend] = None
_lock = threading.Lock()


def _build(kind: str) -> LLMBackend:
    if kind == "ollama":
        from app.llm.ollama_backend import OllamaBackend

        return OllamaBackend()
    if kind == "llama_cpp":
        from app.llm.llamacpp_backend import LlamaCppBackend

        return LlamaCppBackend()
    raise BackendUnavailable(f"unknown backend '{kind}'")


def _build_auto() -> LLMBackend:
    from app.llm.ollama_backend import server_is_up

    problems = []

    if server_is_up():
        try:
            backend = _build("ollama")
            log.info("Auto-selected the Ollama backend")
            return backend
        except BackendUnavailable as exc:
            log.warning("Ollama is running but unusable (%s); falling back to llama.cpp", exc)
            problems.append(f"ollama: {exc}")
    else:
        log.info("No Ollama server reachable; using in-process llama.cpp")
        problems.append(f"ollama: no server reachable at {settings.ollama_host}")

    try:
        return _build("llama_cpp")
    except BackendUnavailable as exc:
        problems.append(f"llama.cpp: {exc}")

    # Both paths failed. Say why for each, rather than reporting only the last.
    raise BackendUnavailable(
        "no inference backend is available:\n  - "
        + "\n  - ".join(problems)
        + "\nStart Ollama with `ollama serve`, or install llama-cpp-python into "
        "this interpreter."
    )


def get_backend(force: Optional[str] = None) -> LLMBackend:
    """Return the process-wide backend, constructing it on first use."""
    global _backend
    with _lock:
        if _backend is not None and force is None:
            return _backend

        kind = force or settings.backend
        _backend = _build_auto() if kind == "auto" else _build(kind)
        log.info("Inference backend ready: %s", _backend.info().describe())
        return _backend


def reset_backend() -> None:
    """Drop the cached backend, freeing model memory."""
    global _backend
    with _lock:
        if _backend is not None:
            _backend.close()
        _backend = None
