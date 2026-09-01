"""Retry logic decorators"""

import functools
import random
import time
from typing import Callable, Iterable, Type

from extractflow.utils.logger import get_logger

log = get_logger("utils.retry")


def with_retry(
    func: Callable | None = None,
    *,
    attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    jitter: float = 0.25,
    exceptions: Iterable[Type[BaseException]] = (Exception,),
):
    """Retry a callable with exponential backoff.

    Usable bare (``@with_retry``) or configured (``@with_retry(attempts=5)``).
    The final attempt's exception propagates unchanged.
    """

    def decorate(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            wait = delay
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except tuple(exceptions) as exc:
                    if attempt == attempts:
                        raise
                    sleep_for = wait * (1 + random.uniform(-jitter, jitter))
                    log.warning(
                        "%s failed (attempt %d/%d): %s - retrying in %.1fs",
                        fn.__name__,
                        attempt,
                        attempts,
                        exc,
                        sleep_for,
                    )
                    time.sleep(max(0.0, sleep_for))
                    wait *= backoff

        return wrapper

    return decorate(func) if callable(func) else decorate
