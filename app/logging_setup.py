"""Logging: a verbose rotating file log plus a quiet console."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from app.config import settings

_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    settings.ensure_dirs()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    file_handler = RotatingFileHandler(
        settings.log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)-28s %(message)s")
    )

    console = logging.StreamHandler()
    console.setLevel(getattr(logging, settings.log_console_level.upper(), logging.WARNING))
    console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))

    root.handlers = [file_handler, console]

    # These are chatty and rarely useful here.
    for noisy in ("httpx", "httpcore", "matplotlib", "PIL", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
