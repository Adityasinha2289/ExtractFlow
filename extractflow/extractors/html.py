import re
from typing import Any

from bs4 import BeautifulSoup


class HTMLExtractor:
    """
    Configuration-driven HTML data extractor.
    Replaces hardcoded extraction logic with dynamic rules.
    """

    def __init__(self, mapping_config: dict[str, Any]):
        self.mapping = mapping_config

    def extract(self, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "html.parser")
        result = {}

        for field, rules in self.mapping.items():
            selector = rules.get("selector")
            regex = rules.get("regex")
            extract_type = rules.get("type", "text")

            if selector:
                elements = soup.select(selector)
                if not elements:
                    result[field] = None
                    continue

                if extract_type == "text":
                    val = elements[0].get_text(separator=" ", strip=True)
                    if regex:
                        match = re.search(regex, val)
                        val = match.group(1) if match else val
                    result[field] = val

                elif extract_type == "list":
                    result[field] = [
                        el.get_text(separator=" ", strip=True) for el in elements
                    ]

                elif extract_type == "attribute":
                    attr = rules.get("attribute")
                    result[field] = elements[0].get(attr)

        return result
