"""A small on-disk cache for model responses.

Executing a spec against 50k rows of parquet takes milliseconds; generating it
takes seconds. So the thing worth caching is the model call, not the pandas.
Keyed by the exact messages, schema and temperature, so a changed prompt or
schema never returns a stale plan.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any, Dict, List, Optional

from app.config import settings
from app.logging_setup import get_logger

log = get_logger(__name__)

_lock = threading.Lock()
_memory: Dict[str, str] = {}


def _key(model: str, messages: List[dict], schema: Optional[dict], temperature: float) -> str:
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "schema": schema,
            "temperature": round(float(temperature), 3),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _path(key: str):
    directory = settings.cache_dir / "responses"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{key}.json"


def get(model: str, messages: List[dict], schema, temperature: float) -> Optional[str]:
    key = _key(model, messages, schema, temperature)
    with _lock:
        hit = _memory.get(key)
    if hit is not None:
        return hit

    path = _path(key)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text("utf-8"))["response"]
    except Exception:
        return None
    with _lock:
        _memory[key] = value
    return value


def put(model: str, messages: List[dict], schema, temperature: float, response: str) -> None:
    key = _key(model, messages, schema, temperature)
    with _lock:
        _memory[key] = response
    try:
        _path(key).write_text(
            json.dumps({"response": response}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:  # pragma: no cover
        log.debug("Could not persist a cached response")


def clear() -> Dict[str, Any]:
    """Drop every cached response. Returns what was removed."""
    with _lock:
        _memory.clear()

    directory = settings.cache_dir / "responses"
    removed = 0
    if directory.is_dir():
        for path in directory.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:  # pragma: no cover
                pass
    return {"removed": removed}
