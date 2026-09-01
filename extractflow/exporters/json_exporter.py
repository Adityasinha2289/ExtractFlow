"""JSON Exporter"""

import json
from typing import Any

from extractflow.utils.filesystem import atomic_write
from extractflow.utils.logger import get_logger

log = get_logger("exporters.json")


def export_json(data: Any, path: str, indent: int = 2, lines: bool = False) -> str:
    """Write ``data`` to ``path``.

    ``lines=True`` emits newline-delimited JSON (one record per line), which is
    what you want for large record sets that will be streamed back in.
    """
    if lines:
        rows = data if isinstance(data, list) else [data]
        text = "\n".join(json.dumps(r, ensure_ascii=False, default=str) for r in rows)
        text += "\n"
    else:
        text = json.dumps(data, indent=indent, ensure_ascii=False, default=str)
    atomic_write(path, text)
    count = len(data) if hasattr(data, "__len__") else 1
    log.info("wrote %s (%d top-level item(s), %d bytes)", path, count, len(text))
    return path
