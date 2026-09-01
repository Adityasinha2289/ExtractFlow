"""CSV Exporter"""

import csv
import json
from typing import Any, Iterable

from extractflow.utils.filesystem import ensure_parent
from extractflow.utils.logger import get_logger

log = get_logger("exporters.csv")


def _flatten(record: dict, parent: str = "", sep: str = ".") -> dict:
    """Flatten nested dicts to dotted keys; lists become compact JSON."""
    flat: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{parent}{sep}{key}" if parent else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, name, sep))
        elif isinstance(value, (list, tuple)):
            flat[name] = json.dumps(list(value), ensure_ascii=False, default=str)
        else:
            flat[name] = value
    return flat


def export_csv(
    data: Iterable[dict],
    path: str,
    columns: list[str] | None = None,
    flatten: bool = True,
) -> str:
    """Write records to CSV, unioning keys across records for the header."""
    rows = [_flatten(r) if flatten and isinstance(r, dict) else r for r in (data or [])]
    if columns is None:
        columns = []
        seen = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)

    ensure_parent(path)
    # newline="" per the csv module contract; utf-8-sig so Excel reads INR text.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    log.info("wrote %s (%d rows, %d columns)", path, len(rows), len(columns))
    return path
