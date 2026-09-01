"""JSON-LD Extractor"""

import json
from typing import Any

from bs4 import BeautifulSoup

from extractflow.utils.logger import get_logger

log = get_logger("extractors.jsonld")


def _flatten(node: Any, out: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _flatten(item, out)
    elif isinstance(node, dict):
        if "@graph" in node:
            _flatten(node["@graph"], out)
        else:
            out.append(node)


def extract_jsonld(content: str, types: list[str] | None = None) -> list[dict]:
    """Return every JSON-LD object on the page, optionally filtered by @type."""
    soup = BeautifulSoup(content or "", "html.parser")
    blocks: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        if not raw.strip():
            continue
        try:
            _flatten(json.loads(raw), blocks)
        except json.JSONDecodeError as exc:
            log.debug("skipping malformed JSON-LD block: %s", exc)

    if types:
        wanted = {t.lower() for t in types}

        def matches(block: dict) -> bool:
            declared = block.get("@type")
            names = declared if isinstance(declared, list) else [declared]
            return any(str(n).lower() in wanted for n in names if n)

        blocks = [b for b in blocks if matches(b)]
    return blocks
