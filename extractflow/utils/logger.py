"""Logging Utility"""

import logging
import os
import sys

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def configure(level: str | None = None, fmt: str = _DEFAULT_FORMAT) -> None:
    """Install a single stderr handler for the whole process (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = level or os.environ.get("EXTRACTFLOW_LOG_LEVEL", "INFO")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(fmt))
    root = logging.getLogger("extractflow")
    root.setLevel(level.upper())
    root.addHandler(handler)
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure()
    if not name.startswith("extractflow"):
        name = f"extractflow.{name}"
    return logging.getLogger(name)
