"""FS Utility"""

import json
import os
import tempfile
from typing import Any


def ensure_dir(path: str) -> str:
    """Create ``path`` (and parents) if missing; returns the path."""
    if path:
        os.makedirs(path, exist_ok=True)
    return path


def ensure_parent(path: str) -> str:
    """Create the parent directory of a file path; returns the path."""
    parent = os.path.dirname(os.path.abspath(path))
    ensure_dir(parent)
    return path


def atomic_write(path: str, text: str, encoding: str = "utf-8") -> str:
    """Write via a temp file + replace so readers never see a partial file."""
    ensure_parent(path)
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def write_json(path: str, obj: Any, indent: int = 2) -> str:
    return atomic_write(
        path, json.dumps(obj, indent=indent, ensure_ascii=False, default=str)
    )


def read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
