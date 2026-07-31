"""Requests that actually travel through the proxy: exit-IP checks and page fetches."""

from __future__ import annotations

import os
import time
from html.parser import HTMLParser
from typing import Any

import httpx

from .proxy import ProxyTarget

DEFAULT_IP_CHECK_URL = "https://ipinfo.io/json"
DEFAULT_TIMEOUT = 45.0
DEFAULT_MAX_BYTES = 200_000

SKIPPED_TAGS = {"script", "style", "noscript", "template", "svg"}


class ProxyRequestError(RuntimeError):
    """Raised when a request through the proxy fails."""


class _TextExtractor(HTMLParser):
    """Collapse HTML into readable text without pulling in a parser dependency."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIPPED_TAGS:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title and self.title is None:
            self.title = text
        self._chunks.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self._chunks)


def html_to_text(html: str) -> tuple[str, str | None]:
    """Return (text, title) for an HTML document."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text, parser.title


def ip_check_url() -> str:
    return os.environ.get("NODEMAVEN_IP_CHECK_URL", "").strip() or DEFAULT_IP_CHECK_URL


def _describe_transport_error(exc: httpx.HTTPError, target: ProxyTarget) -> str:
    if isinstance(exc, httpx.ProxyError):
        return (
            f"Proxy refused the connection ({target.host}:{target.port}). Verify "
            "NODEMAVEN_PROXY_USERNAME / NODEMAVEN_PROXY_PASSWORD and that the targeting "
            "combination exists — an over-narrow city or ISP filter can leave an empty pool."
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Request timed out after {DEFAULT_TIMEOUT:.0f}s through {target.host}:{target.port}. "
            "Retry with a new session_id or a broader location."
        )
    return f"Request through the proxy failed: {exc}"


async def _client(target: ProxyTarget, transport: httpx.AsyncBaseTransport | None):
    if transport is not None:
        return httpx.AsyncClient(transport=transport, timeout=DEFAULT_TIMEOUT)
    return httpx.AsyncClient(proxy=target.url(reveal_password=True), timeout=DEFAULT_TIMEOUT)


async def check_exit_ip(
    target: ProxyTarget,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Send one request through the proxy and report the exit IP, geo and latency."""
    started = time.perf_counter()
    async with await _client(target, transport) as client:
        try:
            response = await client.get(ip_check_url())
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProxyRequestError(
                f"IP check endpoint returned {exc.response.status_code}. "
                "Set NODEMAVEN_IP_CHECK_URL to a different service if this persists."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProxyRequestError(_describe_transport_error(exc, target)) from exc

        latency_ms = round((time.perf_counter() - started) * 1000)
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:500]}

    return {
        "exit_ip": payload.get("ip"),
        "country": payload.get("country"),
        "region": payload.get("region"),
        "city": payload.get("city"),
        "org": payload.get("org"),
        "latency_ms": latency_ms,
        "proxy": target.url(),
        "session_id_used": "-sid-" in target.username,
    }


async def fetch_url(
    target: ProxyTarget,
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    as_text: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fetch a URL through the proxy and return its body, truncated to max_bytes."""
    started = time.perf_counter()
    async with await _client(target, transport) as client:
        try:
            response = await client.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise ProxyRequestError(_describe_transport_error(exc, target)) from exc

        latency_ms = round((time.perf_counter() - started) * 1000)
        raw = response.content[:max_bytes]
        truncated = len(response.content) > len(raw)
        content_type = response.headers.get("content-type", "")
        body = raw.decode(response.encoding or "utf-8", errors="replace")

    title = None
    if as_text and "html" in content_type.lower():
        body, title = html_to_text(body)

    return {
        "url": str(response.url),
        "status_code": response.status_code,
        "content_type": content_type,
        "title": title,
        "latency_ms": latency_ms,
        "truncated": truncated,
        "bytes": len(raw),
        "body": body,
        "proxy": target.url(),
    }
