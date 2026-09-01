"""Generic Crawler component"""

import time
import urllib.parse as urlparse
import urllib.robotparser as robotparser
from dataclasses import dataclass, field
from typing import Any

import requests

from extractflow.utils.logger import get_logger
from extractflow.utils.retry import with_retry

log = get_logger("core.crawler")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


@dataclass
class FetchResult:
    """One retrieved document plus the provenance a verifier needs."""

    url: str
    final_url: str
    status: int
    content_type: str
    text: str
    content: bytes
    elapsed_ms: int
    from_robots_block: bool = False
    error: str | None = None
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


class BaseCrawler:
    """Polite HTTP crawler: robots.txt aware, per-host rate limited, retrying.

    ``config`` keys: ``user_agent``, ``timeout`` (s), ``retries``,
    ``respect_robots`` (default True), ``default_delay`` (s between requests to
    the same host), ``headers``.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.user_agent = self.config.get("user_agent", DEFAULT_UA)
        self.timeout = float(self.config.get("timeout", 30))
        self.retries = int(self.config.get("retries", 3))
        self.respect_robots = bool(self.config.get("respect_robots", True))
        self.default_delay = float(self.config.get("default_delay", 1.0))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
                "Accept-Language": "en-IN,en;q=0.9",
                **self.config.get("headers", {}),
            }
        )
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._crawl_delay: dict[str, float] = {}

    # -- robots -------------------------------------------------------------
    def _robots_for(self, url: str):
        host = urlparse.urlsplit(url)._replace(path="", query="", fragment="").geturl()
        if host in self._robots:
            return self._robots[host]
        parser = None
        try:
            resp = self.session.get(f"{host}/robots.txt", timeout=self.timeout)
            ctype = resp.headers.get("content-type", "")
            # Some SPA hosts serve index.html for /robots.txt - that is not a
            # robots file and must not be parsed as one.
            if resp.status_code == 200 and "html" not in ctype.lower():
                parser = robotparser.RobotFileParser()
                parser.parse(resp.text.splitlines())
                delay = parser.crawl_delay(self.user_agent) or parser.crawl_delay("*")
                if delay:
                    self._crawl_delay[host] = float(delay)
                    log.info("robots.txt for %s requests crawl-delay=%ss", host, delay)
        except Exception as exc:  # network/parse failure -> no rules on file
            log.warning("robots.txt unavailable for %s (%s)", host, exc)
        self._robots[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parser = self._robots_for(url)
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    # -- rate limiting ------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urlparse.urlsplit(url).netloc
        base = urlparse.urlsplit(url)._replace(path="", query="", fragment="").geturl()
        delay = max(self.default_delay, self._crawl_delay.get(base, 0.0))
        last = self._last_hit.get(host)
        if last is not None:
            wait = delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    # -- fetching -----------------------------------------------------------
    def fetch(self, url: str, **kwargs: Any) -> FetchResult:
        """Fetch one URL. Never raises - failures come back on the result."""
        if not self.allowed(url):
            log.warning("robots.txt disallows %s - skipping", url)
            return FetchResult(url, url, 0, "", "", b"", 0, True, "robots-disallowed")

        self._throttle(url)
        started = time.monotonic()

        @with_retry(
            attempts=self.retries, delay=1.0, exceptions=(requests.RequestException,)
        )
        def _get():
            return self.session.get(url, timeout=self.timeout, **kwargs)

        try:
            resp = _get()
        except requests.RequestException as exc:
            log.error("fetch failed %s: %s", url, exc)
            return FetchResult(
                url,
                url,
                0,
                "",
                "",
                b"",
                int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding or "utf-8"
        return FetchResult(
            url=url,
            final_url=resp.url,
            status=resp.status_code,
            content_type=resp.headers.get("content-type", ""),
            text=resp.text,
            content=resp.content,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            headers=dict(resp.headers),
        )

    def crawl(self, start_url: str, **kwargs: Any) -> FetchResult:
        return self.fetch(start_url, **kwargs)
