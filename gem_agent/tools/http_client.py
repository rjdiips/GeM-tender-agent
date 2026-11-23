"""One HTTP client per GeM host: cookie jar, keep-alive, CSRF upkeep, error handling."""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from gem_agent.config import Settings, get_settings
from gem_agent.tools.rate_limit import AdaptiveRateLimiter

FormData = Mapping[str, Any] | Callable[[], Awaitable[Mapping[str, Any]]] | None

BIDPLUS = "https://bidplus.gem.gov.in"
TOKEN_PAGE = "/advance-search"
TOKEN_RE = re.compile(r"csrf_bd_gem_nk'\s*:\s*'([0-9a-f]+)'")


class HostNotAllowed(ValueError):
    """The URL's host is outside the allowlist."""


def host_allowed(host: str, suffix: str) -> bool:
    return bool(host) and (host == suffix or host.endswith("." + suffix))


class GemHttpError(Exception):
    """A request still failed after all retries."""

    def __init__(self, url: str, status: int | None, reason: str) -> None:
        super().__init__(f"{url}: {status or 'no response'} ({reason})")
        self.url, self.status, self.reason = url, status, reason


class TokenNotFound(GemHttpError):
    """The CSRF token is no longer where the page used to embed it."""


def is_no_data(resp: httpx.Response) -> bool:
    """GeM reports an empty result as HTTP 404 with "No data found"."""
    return resp.status_code == 404 and "No data found" in resp.text


def is_real_error(resp: httpx.Response) -> bool:
    s = resp.status_code
    return s >= 500 or s in (403, 429) or (s == 404 and not is_no_data(resp))


class HostClient:
    """All traffic to one host: serialised, rate limited, retried with back-off."""

    def __init__(
        self,
        host: str,
        *,
        limiter: AdaptiveRateLimiter,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None,
        clock: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
    ) -> None:
        self.host, self.limiter = host, limiter
        self.on_reset: list[Callable[[], None]] = []
        self._settings, self._transport = settings, transport
        self._clock, self._sleep = clock, sleep
        self._lock = asyncio.Lock()
        self._client = self._new_client()
        self._generation = 0  # bumped by every reset, so concurrent requests renew a stale session only once

    def _new_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            headers={"User-Agent": self._settings.user_agent},
            follow_redirects=True,
            timeout=self._settings.http_timeout_s,
            # httpx runs request hooks before every hop, so a redirect cannot leave the allowlist.
            event_hooks={"request": [self._check_host]},
        )

    async def _check_host(self, request: httpx.Request) -> None:
        if not host_allowed(request.url.host, self._settings.allowed_host_suffix):
            raise HostNotAllowed(str(request.url))

    async def reset(self) -> None:
        """Fresh session: new cookie jar and connections."""
        old, self._client = self._client, self._new_client()
        self._generation += 1
        await old.aclose()
        for hook in self.on_reset:
            hook()

    async def request(
        self, method: str, url: str, *, data: FormData = None, headers: Mapping[str, str] | None = None
    ) -> httpx.Response:
        retries = self._settings.http_retries
        status: int | None = None
        reason = ""
        renew = False
        generation = self._generation
        for attempt in range(retries + 1):
            if attempt:
                await self._sleep(self._settings.http_backoff_s * 2 ** (attempt - 1))
            # A 403 means GeM rejected our session (stale cookie or CSRF token): retrying with it cannot work, so
            # renew it at once. Other failures get a fresh session only on the last try. Either way, skip the
            # reset when another request already renewed the session since this one built its form.
            if (renew or attempt == retries) and generation == self._generation:
                await self.reset()
            generation = self._generation
            form = await data() if callable(data) else data
            resp: httpx.Response | None = None
            async with self._lock, self.limiter.turn():
                started = self._clock()
                try:
                    resp = await self._client.request(method, url, data=form, headers=headers)
                    ok = not is_real_error(resp)
                except httpx.TransportError as exc:
                    ok, reason = False, type(exc).__name__
                self.limiter.record(ok=ok, latency=self._clock() - started)
            if resp is not None and ok:
                return resp
            if resp is not None:
                status, reason = resp.status_code, resp.text[:200]
            else:
                status = None
            renew = status == 403
        raise GemHttpError(url, status, reason)

    async def aclose(self) -> None:
        await self._client.aclose()


class ClientPool:
    """One HostClient per allowed host, shared by every run."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        shared_dir: Path | None = None,
    ) -> None:
        """`shared_dir` makes the per-host limits hold across processes; services use `ClientPool.shared()`."""
        self.settings = settings or get_settings()
        self._transport, self._clock, self._sleep = transport, clock, sleep
        self._shared_dir = shared_dir
        self._clients: dict[str, HostClient] = {}

    @classmethod
    def shared(cls, settings: Settings | None = None) -> ClientPool:
        """The pool every service process uses: limits shared through files in settings.rate_limit_dir."""
        settings = settings or get_settings()
        return cls(settings=settings, shared_dir=settings.rate_limit_dir)

    def is_allowed(self, url: str) -> bool:
        return host_allowed(httpx.URL(url).host, self.settings.allowed_host_suffix)

    def client_for(self, url: str) -> HostClient:
        if not self.is_allowed(url):
            raise HostNotAllowed(url)
        host = httpx.URL(url).host
        if host not in self._clients:
            s = self.settings
            limiter = AdaptiveRateLimiter(
                floor=s.rate_limit_floor_s,
                slow=s.rate_limit_error_s,
                recover_after=s.rate_limit_recover_min * 60,
                spike_median=s.rate_limit_spike_median_s,
                clock=self._clock,
                sleep=self._sleep,
                shared_path=self._shared_dir / host if self._shared_dir else None,
            )
            self._clients[host] = HostClient(
                host, limiter=limiter, settings=s, transport=self._transport, clock=self._clock, sleep=self._sleep
            )
        return self._clients[host]

    async def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        return await self.client_for(url).request(method, url, **kw)

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()


class BidplusSession:
    """bidplus.gem.gov.in with its CSRF token, read from page JavaScript and refreshed with each fresh session."""

    def __init__(self, pool: ClientPool, base: str = BIDPLUS) -> None:
        self.pool, self.base = pool, base
        self._token: str | None = None
        self._token_lock = asyncio.Lock()
        pool.client_for(base).on_reset.append(self._forget_token)

    def _forget_token(self) -> None:
        self._token = None

    async def token(self) -> str:
        async with self._token_lock:
            if self._token is None:
                page = await self.get(TOKEN_PAGE)
                match = TOKEN_RE.search(page.text)
                if not match:
                    raise TokenNotFound(self.base + TOKEN_PAGE, page.status_code, "csrf_bd_gem_nk not in page")
                self._token = match.group(1)
            return self._token

    async def get(self, path: str) -> httpx.Response:
        return await self.pool.request("GET", self.url(path))

    async def post_form(self, path: str, data: Mapping[str, Any], referer: str = TOKEN_PAGE) -> httpx.Response:
        async def form() -> dict[str, Any]:
            return {**data, "csrf_bd_gem_nk": await self.token()}

        headers = {"X-Requested-With": "XMLHttpRequest", "Referer": self.url(referer)}
        return await self.pool.request("POST", self.url(path), data=form, headers=headers)

    def url(self, path: str) -> str:
        return path if path.startswith("http") else f"{self.base}/{path.lstrip('/')}"
