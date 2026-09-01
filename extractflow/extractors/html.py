import re
from typing import Any

from bs4 import BeautifulSoup

from extractflow.utils.helpers import sanitize_string


class HTMLExtractor:
    """
    Configuration-driven HTML data extractor.
    Replaces hardcoded extraction logic with dynamic rules.

    A rule is ``{selector, type, regex, attribute, ...}`` where ``type`` is one
    of:

    ``text``        first match's text (``regex`` group 1 narrows it)
    ``list``        text of every match
    ``attribute``   ``attribute`` of the first match
    ``attr_list``   ``attribute`` of every match
    ``html``        inner markup of the first match
    ``exists``      boolean - did the selector match anything
    ``class_list``  the class tokens on the first match (grids often encode
                    category/region as CSS classes rather than as content)

    ``extract_all`` applies a whole field mapping to each element matched by a
    root selector, which is what turns a card/tile grid into one record per
    tile.
    """

    def __init__(self, mapping_config: dict[str, Any]):
        self.mapping = mapping_config

    # -- single element -----------------------------------------------------
    def _apply(self, scope, rules: dict[str, Any]) -> Any:
        selector = rules.get("selector")
        extract_type = rules.get("type", "text")
        regex = rules.get("regex")

        elements = scope.select(selector) if selector else [scope]
        if not elements:
            return rules.get("default")

        if extract_type == "exists":
            return True

        if extract_type == "list":
            return [sanitize_string(el.get_text(" ", strip=True)) for el in elements]

        if extract_type == "attr_list":
            attr = rules.get("attribute", "href")
            return [el.get(attr) for el in elements if el.get(attr) is not None]

        if extract_type == "attribute":
            return elements[0].get(rules.get("attribute", "href"), rules.get("default"))

        if extract_type == "class_list":
            return list(elements[0].get("class") or [])

        if extract_type == "html":
            return elements[0].decode_contents()

        value = sanitize_string(elements[0].get_text(" ", strip=True))
        if regex:
            match = re.search(regex, value)
            value = (
                (match.group(1) if match.groups() else match.group(0))
                if match
                else rules.get("default")
            )
        return value

    # -- public API ---------------------------------------------------------
    def extract(self, html: str) -> dict[str, Any]:
        """Apply the mapping to the document as a whole."""
        soup = BeautifulSoup(html or "", "html.parser")
        result: dict[str, Any] = {}
        for field, rules in self.mapping.items():
            if not isinstance(rules, dict):
                continue
            if rules.get("selector") and not soup.select(rules["selector"]):
                result[field] = (
                    False if rules.get("type") == "exists" else rules.get("default")
                )
                continue
            result[field] = self._apply(soup, rules)
        return result

    def extract_all(self, html: str, root_selector: str) -> list[dict[str, Any]]:
        """Apply the mapping once per element matching ``root_selector``.

        Each record also carries ``_root_classes`` because listing grids
        routinely encode facets (category, region, availability) as CSS classes
        on the tile rather than as visible text.
        """
        soup = BeautifulSoup(html or "", "html.parser")
        records: list[dict[str, Any]] = []
        for node in soup.select(root_selector):
            record: dict[str, Any] = {}
            for field, rules in self.mapping.items():
                if not isinstance(rules, dict):
                    continue
                if rules.get("selector") and not node.select(rules["selector"]):
                    record[field] = (
                        False if rules.get("type") == "exists" else rules.get("default")
                    )
                    continue
                record[field] = self._apply(node, rules)
            record["_root_classes"] = list(node.get("class") or [])
            records.append(record)
        return records
