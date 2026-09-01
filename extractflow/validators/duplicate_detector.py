"""Duplicate Detection"""

from collections import defaultdict
from typing import Any, Callable, Iterable, Sequence

from extractflow.utils.helpers import deep_get
from extractflow.utils.logger import get_logger

log = get_logger("validators.duplicate")


def _key_for(record: dict, key: str | Sequence[str] | Callable) -> Any:
    if callable(key):
        return key(record)
    if isinstance(key, str):
        return deep_get(record, key)
    return tuple(deep_get(record, k) for k in key)


def detect_duplicates(
    data: Iterable[dict],
    key: str | Sequence[str] | Callable,
    ignore_null_keys: bool = True,
) -> dict[str, Any]:
    """Group records by ``key`` and report the collisions.

    ``key`` may be a dotted path, a sequence of paths (composite key), or a
    callable. Records whose key is null are reported separately rather than
    silently grouped together - two records that are both missing the key are
    not evidence that they are the same thing.
    """
    records = list(data or [])
    groups: dict[Any, list[int]] = defaultdict(list)
    unkeyed: list[int] = []

    for index, record in enumerate(records):
        value = _key_for(record, key)
        if value is None or (
            isinstance(value, tuple) and all(v is None for v in value)
        ):
            unkeyed.append(index)
            if ignore_null_keys:
                continue
        groups[value].append(index)

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    duplicate_indexes = sorted(
        i for group in duplicate_groups.values() for i in group[1:]
    )

    log.info(
        "duplicate scan: %d records, %d duplicate group(s), %d redundant record(s)",
        len(records),
        len(duplicate_groups),
        len(duplicate_indexes),
    )
    return {
        "total": len(records),
        "unique_count": len(records) - len(duplicate_indexes),
        "duplicate_count": len(duplicate_indexes),
        "duplicate_groups": {str(k): v for k, v in duplicate_groups.items()},
        "duplicate_indexes": duplicate_indexes,
        "unkeyed_indexes": unkeyed,
    }


def deduplicate(
    data: Iterable[dict],
    key: str | Sequence[str] | Callable,
    merge: Callable[[dict, dict], dict] | None = None,
) -> list[dict]:
    """Collapse records sharing ``key``.

    Without ``merge`` the first record of each group wins. With ``merge``, the
    group is folded left, which is how a source-precedence rule (keep the
    higher-authority record's field values, union the evidence) gets applied.
    """
    kept: dict[Any, dict] = {}
    order: list[Any] = []
    unkeyed: list[dict] = []

    for record in data or []:
        value = _key_for(record, key)
        if value is None:
            unkeyed.append(record)
            continue
        try:
            hash(value)
        except TypeError:
            value = str(value)
        if value not in kept:
            kept[value] = record
            order.append(value)
        elif merge is not None:
            kept[value] = merge(kept[value], record)

    return [kept[v] for v in order] + unkeyed
