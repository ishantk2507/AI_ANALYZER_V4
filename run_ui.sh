#!/usr/bin/env bash
# Launches the Streamlit UI on Linux / macOS.
#
# Prefers the project's own .venv, then whatever `python3` resolves to on PATH.
# Pass extra Streamlit flags through, e.g.:  ./run_ui.sh --server.port 8600
set -euo pipefail

cd "$(dirname "$0")"

PYTHON=".venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3 || command -v python)"

if ! "$PYTHON" -c "import streamlit" 2>/dev/null; then
    echo "Streamlit is not installed for $PYTHON." >&2
    echo "Run:  python3 -m venv .venv && .venv/bin/pip install -e ." >&2
    exit 1
fi

exec "$PYTHON" -m streamlit run app/ui.py \
    --server.address localhost --server.port 8501 "$@"
