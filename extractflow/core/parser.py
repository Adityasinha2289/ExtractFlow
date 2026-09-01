"""Generic Parser component"""

from typing import Any, Callable

from extractflow.utils.helpers import sanitize_string
from extractflow.utils.logger import get_logger

log = get_logger("core.parser")


class BaseParser:
    """Turns one raw extraction into zero or more normalised records.

    Subclasses override :meth:`parse`. The base implementation applies the
    registered field transforms, which is enough for simple mappings.
    """

    #: field name -> callable applied to that field's raw value
    transforms: dict[str, Callable[[Any], Any]] = {}

    def parse(self, raw_data: Any) -> Any:
        if isinstance(raw_data, list):
            return [self.parse(item) for item in raw_data]
        if not isinstance(raw_data, dict):
            return sanitize_string(raw_data)
        return {
            key: (
                self.transforms[key](value)
                if key in self.transforms
                else sanitize_string(value)
            )
            for key, value in raw_data.items()
        }


class RecordParser(BaseParser):
    """Parser built from a declarative ``{field: callable}`` mapping.

    Transform failures are logged and yield ``None`` rather than killing the
    batch - a single malformed price string should never lose an entire source.
    """

    def __init__(
        self,
        transforms: dict[str, Callable[[Any], Any]] | None = None,
        drop_unmapped: bool = False,
    ):
        self.transforms = transforms or {}
        self.drop_unmapped = drop_unmapped

    def parse(self, raw_data: Any) -> Any:
        if isinstance(raw_data, list):
            return [self.parse(item) for item in raw_data]
        if not isinstance(raw_data, dict):
            return sanitize_string(raw_data)

        out: dict[str, Any] = {}
        for key, value in raw_data.items():
            fn = self.transforms.get(key)
            if fn is None:
                if not self.drop_unmapped:
                    out[key] = sanitize_string(value)
                continue
            try:
                out[key] = fn(value)
            except Exception as exc:
                log.warning("transform for %r failed on %r: %s", key, value, exc)
                out[key] = None
        return out
