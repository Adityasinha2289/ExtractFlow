"""HTML Table Extractor"""

from typing import Any

from bs4 import BeautifulSoup

from extractflow.utils.helpers import sanitize_string


def _cells(row) -> list[str]:
    return [
        sanitize_string(c.get_text(" ", strip=True)) for c in row.find_all(["th", "td"])
    ]


def extract_tables(
    content: str, selector: str | None = None, min_rows: int = 2
) -> list[dict[str, Any]]:
    """Extract tables as ``{caption, headers, rows, records}``.

    ``records`` zips each body row against the header row, which is what
    downstream normalisation usually wants; ``rows`` is kept as-is for
    headerless or ragged tables.
    """
    soup = BeautifulSoup(content or "", "html.parser")
    tables = soup.select(selector) if selector else soup.find_all("table")
    out: list[dict[str, Any]] = []

    for table in tables:
        rows = [_cells(tr) for tr in table.find_all("tr")]
        rows = [r for r in rows if any(cell for cell in r)]
        if len(rows) < min_rows:
            continue
        headers, body = rows[0], rows[1:]
        records = [
            {
                (headers[i] if i < len(headers) and headers[i] else f"col_{i}"): value
                for i, value in enumerate(row)
            }
            for row in body
        ]
        caption = table.find("caption")
        out.append(
            {
                "caption": (
                    sanitize_string(caption.get_text(" ", strip=True))
                    if caption
                    else None
                ),
                "headers": headers,
                "rows": body,
                "records": records,
            }
        )
    return out
