"""Execution Pipeline linking Crawler, Scraper, Parser"""

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from extractflow.utils.logger import get_logger

log = get_logger("core.pipeline")


@dataclass
class StageResult:
    name: str
    ok: bool
    count: int
    elapsed_ms: int
    error: str | None = None


@dataclass
class PipelineResult:
    records: list[Any] = field(default_factory=list)
    stages: list[StageResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        parts = [
            f"{s.name}={'ok' if s.ok else 'FAIL'}({s.count} in {s.elapsed_ms}ms)"
            for s in self.stages
        ]
        return " -> ".join(parts)


class Pipeline:
    """Runs an ordered list of ``(name, callable)`` stages over a payload.

    Each stage receives the previous stage's output. A stage that raises is
    recorded and aborts the run; a stage that returns ``None`` passes the
    previous payload through unchanged, which keeps optional stages (enrichment,
    export) from having to defensively return their input.
    """

    def __init__(
        self,
        stages: list[tuple[str, Callable[[Any], Any]]] | None = None,
        config: dict[str, Any] | None = None,
    ):
        self.stages = stages or []
        self.config = config or {}

    def add(self, name: str, fn: Callable[[Any], Any]) -> "Pipeline":
        self.stages.append((name, fn))
        return self

    def run(self, payload: Any = None) -> PipelineResult:
        result = PipelineResult()
        current = payload

        for name, fn in self.stages:
            started = time.monotonic()
            try:
                produced = fn(current)
            except Exception as exc:
                elapsed = int((time.monotonic() - started) * 1000)
                log.exception("stage %r failed", name)
                result.stages.append(
                    StageResult(name, False, 0, elapsed, f"{type(exc).__name__}: {exc}")
                )
                result.errors.append(f"{name}: {exc}")
                return result

            if produced is not None:
                current = produced
            elapsed = int((time.monotonic() - started) * 1000)
            count = len(current) if hasattr(current, "__len__") else 1
            result.stages.append(StageResult(name, True, count, elapsed))
            log.info("stage %s: %d record(s) in %dms", name, count, elapsed)

        result.records = (
            current
            if isinstance(current, list)
            else ([] if current is None else [current])
        )
        return result
