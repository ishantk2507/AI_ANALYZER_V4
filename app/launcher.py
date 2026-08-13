"""Console entry point for the Streamlit UI.

    ai-analyzer-ui
    ai-analyzer-ui --server.port 8600 --server.address 0.0.0.0

Streamlit has to own the process -- it is a server, not a library call -- so this
hands over to `streamlit.web.cli` rather than importing `ui` directly. Every
argument is passed straight through, so any Streamlit flag works.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The UI module, resolved against this file so it is found whether the project
#: is run from a checkout or installed into site-packages.
UI_SCRIPT = Path(__file__).resolve().parent / "ui.py"


def main() -> None:
    try:
        from streamlit.web import cli as streamlit_cli
    except ImportError:  # pragma: no cover - streamlit is a hard dependency
        sys.exit(
            "Streamlit is not installed in this interpreter.\n"
            "Install it with:  pip install -e ."
        )

    if not UI_SCRIPT.is_file():  # pragma: no cover - only if the install is broken
        sys.exit(f"Cannot find the UI at {UI_SCRIPT}")

    # Defaults a server wants; an explicit flag on the command line wins because
    # Streamlit takes the last occurrence.
    defaults = ["--browser.gatherUsageStats", "false"]

    sys.argv = ["streamlit", "run", str(UI_SCRIPT), *defaults, *sys.argv[1:]]
    sys.exit(streamlit_cli.main())


if __name__ == "__main__":  # pragma: no cover
    main()
