"""Compare Ollama tok/s across quantizations and GPU-offload settings.

    python scripts/bench_model.py

Unloads between runs so each configuration gets a cold, uncontended GPU. Every
model named in RUNS must already be registered (see modelfiles/); ones that are
not are reported as FAILED and skipped.

Set OLLAMA_HOST to benchmark a server that is not on localhost.
"""

import json
import os
import time
import urllib.request

HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")

PROMPT = (
    "You are a data analyst. Given monthly revenue for a retail chain, write a "
    "short factual summary naming the highest and lowest months and the overall "
    "trend.\n\n"
    "Jan 412000\nFeb 388000\nMar 455000\nApr 470000\nMay 441000\nJun 503000\n"
    "Jul 528000\nAug 495000\nSep 461000\nOct 512000\nNov 587000\nDec 640000\n"
)

RUNS = [
    ("8B IQ4_XS  full-GPU", "ai-analyzer-ministral-8b-iq4", {"num_gpu": 99}),
    ("8B IQ4_XS  default ", "ai-analyzer-ministral-8b-iq4", {}),
    ("8B Q4_K_M  full-GPU", "ai-analyzer-ministral-8b", {"num_gpu": 99}),
    ("8B Q4_K_M  default ", "ai-analyzer-ministral-8b", {}),
    ("3B Q4_K_M  default ", "ai-analyzer-ministral", {}),
]


def post(path, payload):
    req = urllib.request.Request(
        HOST + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.load(resp)


def unload(model):
    try:
        post("/api/generate", {"model": model, "keep_alive": 0})
    except Exception:
        pass
    time.sleep(4)


def placement(model):
    with urllib.request.urlopen(HOST + "/api/ps", timeout=30) as resp:
        loaded = json.load(resp).get("models", [])
    for m in loaded:
        if m["name"].startswith(model):
            total, vram = m.get("size", 0), m.get("size_vram", 0)
            pct = round(100 * vram / total) if total else 0
            return f"{pct}% GPU"
    return "?"


print(f"{'config':<22} {'placement':<10} {'prompt t/s':>11} {'gen t/s':>9} {'total s':>8}")
print("-" * 64)

for label, model, opts in RUNS:
    unload(model)
    options = {"num_ctx": 4096, "temperature": 0, "num_predict": 160, **opts}
    try:
        # Warm the weights into VRAM so the timed pass excludes disk load.
        post("/api/generate", {"model": model, "prompt": "hi",
                               "options": options, "stream": False,
                               "keep_alive": "5m"})
        where = placement(model)
        d = post("/api/generate", {"model": model, "prompt": PROMPT,
                                   "options": options, "stream": False,
                                   "keep_alive": "5m"})
    except Exception as exc:
        print(f"{label:<22} FAILED: {str(exc)[:36]}")
        continue

    pc, pd = d.get("prompt_eval_count", 0), d.get("prompt_eval_duration", 1)
    ec, ed = d.get("eval_count", 0), d.get("eval_duration", 1)
    print(f"{label:<22} {where:<10} {pc / pd * 1e9:>11.1f} {ec / ed * 1e9:>9.1f} "
          f"{d.get('total_duration', 0) / 1e9:>8.1f}")
    unload(model)
