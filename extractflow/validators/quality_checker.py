"""Data Quality Metrics"""

from collections import Counter
from typing import Any, Iterable, Sequence

from extractflow.utils.helpers import deep_get
from extractflow.utils.logger import get_logger

log = get_logger("validators.quality")


def _is_populated(value: Any) -> bool:
    """Null and empty containers count as absent; ``0`` and ``False`` do not.

    A rate of zero is a real extracted value, so it must never be conflated
    with "we did not find this field".
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def completeness(record: dict, fields: Sequence[str]) -> float:
    """Fraction of ``fields`` populated on one record, in [0.0, 1.0]."""
    if not fields:
        return 0.0
    filled = sum(1 for path in fields if _is_populated(deep_get(record, path)))
    return round(filled / len(fields), 4)


def check_quality(
    data: Iterable[dict],
    fields: Sequence[str] | None = None,
    group_by: str | None = None,
) -> dict[str, Any]:
    """Coverage report over a record set.

    Returns per-field populated counts in the ``"n/total"`` form the RenoCred
    master datasets use for ``coverage_by_field``, plus per-record completeness
    and an optional breakdown by a grouping field.
    """
    records = list(data or [])
    total = len(records)
    if not records:
        return {"total": 0, "coverage_by_field": {}, "completeness": {}, "groups": {}}

    if fields is None:
        fields = sorted({k for r in records if isinstance(r, dict) for k in r})

    coverage = {
        path: f"{sum(1 for r in records if _is_populated(deep_get(r, path)))}/{total}"
        for path in fields
    }

    scores = [completeness(r, fields) for r in records]
    groups: dict[str, Any] = {}
    if group_by:
        by_group: Counter = Counter(str(deep_get(r, group_by)) for r in records)
        groups = dict(by_group)

    log.info("quality: %d records over %d fields", total, len(fields))
    return {
        "total": total,
        "coverage_by_field": coverage,
        "completeness": {
            "mean": round(sum(scores) / len(scores), 4),
            "min": min(scores),
            "max": max(scores),
            "below_50pct": sum(1 for s in scores if s < 0.5),
        },
        "per_record": scores,
        "groups": groups,
    }
