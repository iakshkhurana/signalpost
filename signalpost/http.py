"""Budgeted, polite async HTTP client.

Every outbound attempt (including retries and robots.txt fetches) counts toward
the run budget, because the challenge counts *outbound requests*. Responses can
be cached on disk for development runs; cache hits are reported separately and
never counted as outbound requests.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from . import USER_AGENT


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    max_requests: int = 2000
    max_seconds: float = 45 * 60
    max_cost_usd: float = 10.0
    requests: int = 0
    cache_hits: int = 0
    llm_calls: int = 0
    llm_cost_usd: float = 0.0
    started: float = field(default_factory=time.monotonic)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def check(self) -> None:
        if self.requests >= self.max_requests:
            raise BudgetExceeded(f"request budget exhausted ({self.requests}/{self.max_requests})")
        if self.elapsed() >= self.max_seconds:
            raise BudgetExceeded(f"time budget exhausted ({self.elapsed():.0f}s/{self.max_seconds:.0f}s)")
        if self.llm_cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(f"cost budget exhausted (${self.llm_cost_usd:.2f}/${self.max_cost_usd:.2f})")

    def exhausted(self) -> bool:
        try:
            self.check()
            return False
        except BudgetExceeded:
            return True

    def remaining_requests(self) -> int:
        return max(0, self.max_requests - self.requests)

    def to_dict(self) -> dict:
        return {
            "outbound_requests": self.requests,
            "max_requests": self.max_requests,
            "cache_hits": self.cache_hits,
            "elapsed_seconds": round(self.elapsed(), 1),
            "max_seconds": self.max_seconds,
            "llm_calls": self.llm_calls,
            "llm_cost_usd": round(self.llm_cost_usd, 4),
            "max_cost_usd": self.max_cost_usd,
        }


@dataclass
class Tally:
    """Request counter. A per-source tally chains to the per-company tally, so
    one company cannot eat the whole budget and each source's count is exact."""
    requests: int = 0
    limit: int = 30
    parent: Optional["Tally"] = None

    def add(self) -> None:
        self.requests += 1
        if self.parent is not None:
            self.parent.add()

    def exhausted(self) -> bool:
        return self.requests >= self.limit or (self.parent is not None and self.parent.exhausted())

    def child(self) -> "Tally":
        return Tally(limit=self.limit, parent=self)


@dataclass
class Response:
    url: str
    status: Optional[int]
    text: Optional[str]
    final_url: str
    from_cache: bool = False
    error: Optional[str] = None
    headers: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def json(self):
        return json.loads(self.text) if self.text else None


_RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_HOST_LIMITS = {"query.wikidata.org": 2, "api.gleif.org": 3, "data.brreg.no": 6, "arbeidsplassen.nav.no": 1}
_HOST_INTERVAL = {"arbeidsplassen.nav.no": 6.0}     # minimum seconds between requests to a host (polite pacing)
_TRIP_SECONDS = 15 * 60                               # after a host answers 429 twice in a row, leave it alone this long


class Http:
    def __init__(self, budget: Budget, *, concurrency: int = 12, per_host: int = 2, cache_dir: Optional[str] = None,
                 cache_ttl: int = 24 * 3600, timeout: float = 20.0, user_agent: str = USER_AGENT):
        self.budget = budget
        self.cache_dir = cache_dir
        self.cache_ttl = cache_ttl
        self.timeout = timeout
        self.user_agent = user_agent
        self._global = asyncio.Semaphore(concurrency)
        self._per_host: dict[str, asyncio.Semaphore] = {}
        self._last_request: dict[str, float] = {}
        self._tripped: dict[str, float] = {}        # host -> monotonic time until which we skip it (circuit breaker)
        self._429s: dict[str, int] = {}
        self._per_host_limit = per_host
        self._robots: dict[str, Optional[RobotFileParser]] = {}
        self._robots_error: dict[str, Optional[str]] = {}   # base -> connection error seen while fetching robots.txt
        self._robots_lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    async def __aenter__(self) -> "Http":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout, connect=10.0),
            follow_redirects=True,
            max_redirects=6,
            headers={"User-Agent": self.user_agent, "Accept-Language": "nb,no,en;q=0.8"},
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    # ------------------------------------------------------------------ cache
    def _cache_path(self, url: str, headers: Optional[dict]) -> Optional[str]:
        if not self.cache_dir:
            return None
        key = url + "|" + json.dumps(sorted((headers or {}).items()))
        return os.path.join(self.cache_dir, hashlib.sha1(key.encode()).hexdigest() + ".json")

    def _cache_get(self, path: Optional[str]) -> Optional[Response]:
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return None
        if time.time() - d.get("ts", 0) > self.cache_ttl:
            return None
        return Response(url=d["url"], status=d["status"], text=d["text"], final_url=d.get("final_url", d["url"]),
                        from_cache=True, headers=d.get("headers", {}))

    def _cache_put(self, path: Optional[str], resp: Response) -> None:
        if not path or resp.status not in (200, 404, 410):
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"url": resp.url, "status": resp.status, "text": resp.text, "final_url": resp.final_url,
                           "headers": resp.headers, "ts": time.time()}, f)
        except Exception:
            pass

    # ---------------------------------------------------------------- request
    def _host_sem(self, url: str) -> asyncio.Semaphore:
        host = urlsplit(url).netloc.lower()
        if host not in self._per_host:
            self._per_host[host] = asyncio.Semaphore(_HOST_LIMITS.get(host, self._per_host_limit))
        return self._per_host[host]

    async def get(self, url: str, *, headers: Optional[dict] = None, tally: Optional[Tally] = None,
                  max_bytes: int = 2_000_000, retries: int = 2, cacheable: bool = True) -> Response:
        assert self._client is not None, "use 'async with Http(...)'"
        cpath = self._cache_path(url, headers) if cacheable else None
        cached = self._cache_get(cpath)
        if cached:
            self.budget.cache_hits += 1
            return cached

        attempt = 0
        host0 = urlsplit(url).netloc.lower()
        if self._tripped.get(host0, 0.0) > time.monotonic():
            return Response(url=url, status=429, text=None, final_url=url,
                            error=f"{host0} rate-limited us (HTTP 429) earlier in this run; request skipped to respect the limit")
        while True:
            if tally is not None and tally.exhausted():
                return Response(url=url, status=None, text=None, final_url=url, error="per-company request limit reached")
            self.budget.check()  # raises BudgetExceeded
            self.budget.requests += 1
            if tally is not None:
                tally.add()
            attempt += 1
            try:
                async with self._global, self._host_sem(url):
                    host = urlsplit(url).netloc.lower()
                    interval = _HOST_INTERVAL.get(host)
                    if interval:
                        wait = self._last_request.get(host, 0.0) + interval - time.monotonic()
                        if wait > 0:
                            await asyncio.sleep(wait)
                        self._last_request[host] = time.monotonic()
                    async with self._client.stream("GET", url, headers=headers) as r:
                        status = r.status_code
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in r.aiter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                break
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        encoding = r.encoding or "utf-8"
                        try:
                            text = raw.decode(encoding, errors="replace")
                        except LookupError:
                            text = raw.decode("utf-8", errors="replace")
                        keep = ("content-type", "last-modified", "date", "retry-after")
                        resp = Response(url=url, status=status, text=text, final_url=str(r.url),
                                        headers={k.lower(): v for k, v in r.headers.items() if k.lower() in keep})
                if status in _RETRY_STATUSES and attempt <= (1 if status == 429 else retries):
                    try:
                        retry_after = float(resp.headers.get("retry-after") or 0)
                    except ValueError:
                        retry_after = 0.0
                    await asyncio.sleep(min(30.0, retry_after or (15.0 if status == 429 else 1.5 * attempt)))
                    continue
                if status == 429:
                    self._429s[host0] = self._429s.get(host0, 0) + 1
                    if self._429s[host0] >= 2:
                        self._tripped[host0] = time.monotonic() + _TRIP_SECONDS
                elif status and status < 400:
                    self._429s[host0] = 0
                self._cache_put(cpath, resp)
                return resp
            except BudgetExceeded:
                raise
            except (httpx.HTTPError, OSError, ValueError) as e:
                err = f"{type(e).__name__}: {e}"[:200]
                retryable = not isinstance(e, (httpx.UnsupportedProtocol, httpx.InvalidURL, httpx.TooManyRedirects))
                if attempt <= retries and retryable:
                    await asyncio.sleep(1.0 * attempt)
                    continue
                return Response(url=url, status=None, text=None, final_url=url, error=err)

    # ----------------------------------------------------------------- robots
    async def allowed_by_robots(self, url: str, *, tally: Optional[Tally] = None) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        async with self._robots_lock:
            if base not in self._robots:
                resp = await self.get(base + "/robots.txt", tally=tally, max_bytes=200_000, retries=0)
                self._robots_error[base] = resp.error
                rp: Optional[RobotFileParser] = None
                if resp.ok and resp.text and "<html" not in resp.text[:500].lower():
                    rp = RobotFileParser()
                    rp.parse(resp.text.splitlines())
                self._robots[base] = rp
        rp = self._robots[base]
        if rp is None:
            return True  # no robots.txt (or unreadable) -> allowed
        return rp.can_fetch(self.user_agent, url)

    def unreachable(self, url: str) -> bool:
        """True when the last robots.txt fetch for this host failed at the connection level (DNS, refused, timeout)."""
        parts = urlsplit(url)
        return bool(self._robots_error.get(f"{parts.scheme}://{parts.netloc}"))
