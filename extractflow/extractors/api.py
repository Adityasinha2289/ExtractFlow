"""API JSON Extractor"""

import json
from typing import Any

from extractflow.utils.helpers import deep_get
from extractflow.utils.logger import get_logger

log = get_logger("extractors.api")


def extract_api(response: Any, path: str | None = None) -> Any:
    """Normalise an API payload to Python data.

    Accepts a ``requests.Response``, an ExtractFlow ``FetchResult``, a raw JSON
    string/bytes, or already-parsed data, so a configuration can point at an
    endpoint without the caller caring how it was retrieved.
    """
    payload = response
    if hasattr(response, "json") and callable(response.json):
        try:
            payload = response.json()
        except Exception:
            payload = getattr(response, "text", None)
    elif hasattr(response, "text"):
        payload = response.text

    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("response body is not JSON: %s", exc)
            return None

    return deep_get(payload, path) if path else payload
