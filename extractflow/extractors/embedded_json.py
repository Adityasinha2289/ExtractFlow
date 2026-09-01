"""Embedded-JSON Extractor.

Many "JS-rendered" pages are not really dynamic: they ship their whole dataset
inline in a ``<script>`` tag and only the *rendering* is client-side. Pulling
that blob out is far cheaper, faster and more faithful than driving a browser,
so this extractor is always tried before falling back to Playwright.

Two shapes are supported:

* ``var NAME = {...};`` -- assignment blobs (AEM / JSP style pages)
* ``<script id="..." type="application/json">`` -- Next.js ``__NEXT_DATA__``
  and equivalents.
"""

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from extractflow.utils.helpers import deep_get
from extractflow.utils.logger import get_logger

log = get_logger("extractors.embedded_json")

_QUOTES = ('"', "'")


def _match_balanced(text: str, start: int) -> str | None:
    """Return the balanced ``{...}`` / ``[...]`` literal beginning at ``start``.

    String-aware, so braces appearing inside quoted values do not unbalance
    the scan.
    """
    opener = text[start]
    closer = {"{": "}", "[": "]"}.get(opener)
    if closer is None:
        return None
    depth = 0
    in_str: str | None = None
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in _QUOTES:
            in_str = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_assignment(html: str, variable: str) -> Any | None:
    """Parse ``var <variable> = <json literal>`` out of inline script text."""
    pattern = re.compile(
        r"(?:var|let|const)\s+" + re.escape(variable) + r"\s*=\s*(?=[\[{])"
    )
    for match in pattern.finditer(html or ""):
        literal = _match_balanced(html, match.end())
        if literal is None:
            continue
        try:
            return json.loads(literal)
        except json.JSONDecodeError as exc:
            log.warning("blob %r is not strict JSON: %s", variable, exc)
    return None


def extract_script_json(html: str, script_id: str = "__NEXT_DATA__") -> Any | None:
    """Parse a ``<script id=... type="application/json">`` payload."""
    soup = BeautifulSoup(html or "", "html.parser")
    tag = soup.find("script", id=script_id)
    if tag is None or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError as exc:
        log.warning("script#%s is not valid JSON: %s", script_id, exc)
        return None


def extract_embedded(html: str, spec: dict[str, Any]) -> dict[str, Any]:
    """Configuration-driven entry point.

    ``spec`` maps an output field name to ``{"variable": "offerData"}`` or
    ``{"script_id": "__NEXT_DATA__"}``, optionally with ``"path"`` to descend
    into the parsed blob using ``a.b[0].c`` notation.
    """
    out: dict[str, Any] = {}
    for field, rules in (spec or {}).items():
        if "variable" in rules:
            value = extract_assignment(html, rules["variable"])
        elif "script_id" in rules:
            value = extract_script_json(html, rules["script_id"])
        else:
            value = None
        if value is not None and rules.get("path"):
            value = deep_get(value, rules["path"])
        out[field] = value
    return out
