"""Schema Validation"""

from typing import Any

from extractflow.utils.helpers import deep_get
from extractflow.utils.logger import get_logger

log = get_logger("validators.schema")

_TYPES = {
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def validate(data: Any, schema: dict[str, Any] | None) -> dict[str, Any]:
    """Validate ``data`` against a lightweight schema.

    The schema is a subset of JSON Schema chosen to cover what extraction
    configs actually need, so no runtime dependency is required::

        {
          "required": ["identity.offer_id", "benefit.benefit_type"],
          "types":    {"benefit.discount_percentage": "number"},
          "enums":    {"quality.verification_status": ["VERIFIED", "EXPIRED"]},
          "non_empty": ["merchant.merchant_name"]
        }

    Dotted paths address nested blocks. Returns
    ``{"valid": bool, "errors": [...], "warnings": [...]}`` - it reports rather
    than raises, because a single bad record must not abort a batch.
    """
    errors: list[str] = []
    warnings: list[str] = []
    schema = schema or {}

    if not isinstance(data, dict):
        return {"valid": False, "errors": ["record is not an object"], "warnings": []}

    for path in schema.get("required", []):
        if deep_get(data, path) is None:
            errors.append(f"missing required field: {path}")

    for path in schema.get("non_empty", []):
        value = deep_get(data, path)
        if value is None or (isinstance(value, (str, list, dict)) and len(value) == 0):
            errors.append(f"field must not be empty: {path}")

    for path, expected in (schema.get("types") or {}).items():
        value = deep_get(data, path)
        if value is None:
            continue  # absence is governed by "required", not by "types"
        py_type = _TYPES.get(expected)
        if py_type and not isinstance(value, py_type):
            # bool is a subclass of int - do not let True satisfy "integer"
            if not (expected in {"number", "integer"} and isinstance(value, bool)):
                if isinstance(value, py_type):
                    continue
            errors.append(
                f"{path}: expected {expected}, got {type(value).__name__} ({value!r})"
            )

    for path, allowed in (schema.get("enums") or {}).items():
        value = deep_get(data, path)
        if value is not None and value not in allowed:
            errors.append(f"{path}: {value!r} not in {allowed}")

    for path in schema.get("recommended", []):
        if deep_get(data, path) is None:
            warnings.append(f"recommended field is null: {path}")

    return {"valid": not errors, "errors": errors, "warnings": warnings}


def validate_all(records: list[dict], schema: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a batch; returns per-record results plus a roll-up."""
    results = [validate(r, schema) for r in records]
    invalid = [i for i, r in enumerate(results) if not r["valid"]]
    return {
        "total": len(records),
        "valid_count": len(records) - len(invalid),
        "invalid_count": len(invalid),
        "invalid_indexes": invalid,
        "results": results,
    }
