"""Misc Helpers"""

import hashlib
import re
import unicodedata
from typing import Any

_WS = re.compile(r"\s+")
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def sanitize_string(s: Any) -> Any:
    """Collapse whitespace and normalise unicode; non-strings pass through."""
    if not isinstance(s, str):
        return s
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u00a0", " ").replace("\u200b", "")
    return _WS.sub(" ", s).strip()


def slugify(s: str, sep: str = "_") -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return _NON_SLUG.sub(sep, s.lower()).strip(sep)


def stable_hash(*parts: Any, length: int = 12) -> str:
    """Deterministic short hash - used for content-addressed ids."""
    joined = "\u241f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:length]


def coalesce(*values: Any) -> Any:
    """First value that is neither None nor an empty/whitespace string."""
    for v in values:
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        return v
    return None


def deep_get(obj: Any, path: str, default: Any = None) -> Any:
    """Fetch a nested value with a ``a.b[0].c`` style path."""
    cur = obj
    for token in re.findall(r"[^.\[\]]+", path or ""):
        if cur is None:
            return default
        if token.isdigit() and isinstance(cur, (list, tuple)):
            idx = int(token)
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(token)
        else:
            return default
    return default if cur is None else cur
