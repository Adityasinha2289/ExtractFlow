"""Excel Exporter"""

from typing import Iterable

from extractflow.exporters.csv_exporter import _flatten
from extractflow.utils.filesystem import ensure_parent
from extractflow.utils.logger import get_logger

log = get_logger("exporters.excel")


class ExcelExporterUnavailable(RuntimeError):
    """Raised when no .xlsx writer is installed."""


def export_excel(
    data: Iterable[dict], path: str, sheet_name: str = "data", flatten: bool = True
) -> str:
    """Write records to an .xlsx workbook.

    Requires ``openpyxl`` (or ``pandas`` with an xlsx engine). Excel is an
    optional output, so the dependency stays optional and the failure is
    explicit rather than an ImportError from deep inside a run.
    """
    rows = [_flatten(r) if flatten and isinstance(r, dict) else r for r in (data or [])]
    ensure_parent(path)

    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise ExcelExporterUnavailable(
            "export_excel needs openpyxl - install it, or use export_csv"
        ) from exc

    columns: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name[:31] or "data"
    sheet.append(columns)
    for row in rows:
        sheet.append(
            [
                (
                    value
                    if isinstance(value, (int, float, str, type(None)))
                    else str(value)
                )
                for value in (row.get(c) for c in columns)
            ]
        )
    workbook.save(path)
    log.info("wrote %s (%d rows, %d columns)", path, len(rows), len(columns))
    return path
