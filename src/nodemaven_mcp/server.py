"""MCP server exposing NodeMaven proxies to AI agents.

Tools let an agent pick an exit location, verify what the target site will see,
and pull pages through a residential or mobile IP without leaving the session.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from . import api, config, fetch
from .formatting import (
    ResponseFormat,
    dict_to_markdown,
    items_to_markdown,
    paginated,
    render,
)
from .proxy import ProxyTarget, ProxyTargetingError, build_target

mcp = FastMCP("nodemaven_mcp")

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
}


class TargetingInput(BaseModel):
    """Targeting parameters shared by every tool that opens a proxy connection."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    country: str = Field(
        ...,
        description="Two-letter ISO country code for the exit IP (e.g. 'us', 'de', 'br').",
        min_length=2,
        max_length=2,
    )
    region: str | None = Field(
        default=None,
        description="State or region to narrow the pool (e.g. 'california'). Optional.",
        max_length=64,
    )
    city: str | None = Field(
        default=None,
        description="City to narrow the pool (e.g. 'los angeles'). Optional.",
        max_length=64,
    )
    isp: str | None = Field(
        default=None,
        description="ISP or mobile carrier to pin (e.g. 't-mobile usa'). Optional.",
        max_length=64,
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "4-10 alphanumeric characters. Reuse the same value to keep the same exit IP "
            "across calls (sticky session); omit it to rotate on every request."
        ),
        max_length=10,
    )
    ip_quality_filter: str | None = Field(
        default=None,
        description="NodeMaven IP quality tier, e.g. 'medium' or 'high'. Optional.",
        max_length=32,
    )
    ipv4_only: bool = Field(default=False, description="Restrict the exit IP to IPv4.")
    protocol: str = Field(default="http", description="Proxy protocol: 'http' or 'socks5'.")
    port: int | None = Field(
        default=None,
        description="Override the proxy port. Defaults to the configured port for the protocol.",
        ge=1,
        le=65535,
    )


class BuildProxyUrlInput(TargetingInput):
    reveal_credentials: bool = Field(
        default=False,
        description=(
            "Include the real proxy password in the returned URL. Keep false unless the URL "
            "is being handed to a command that must authenticate; the password otherwise "
            "stays masked so it does not leak into transcripts."
        ),
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class CheckProxyInput(TargetingInput):
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class FetchInput(TargetingInput):
    url: str = Field(
        ...,
        description="Absolute http(s) URL to fetch through the proxy.",
        pattern=r"^https?://",
        max_length=2048,
    )
    max_bytes: int = Field(
        default=fetch.DEFAULT_MAX_BYTES,
        description="Truncate the response body to this many bytes.",
        ge=1000,
        le=2_000_000,
    )
    as_text: bool = Field(
        default=True,
        description="Strip HTML markup and return readable text instead of raw HTML.",
    )
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class ListLocationsInput(BaseModel):
    """Input model for browsing the targeting catalogue."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    level: str = Field(
        default="countries",
        description="One of: countries, regions, cities, isps, zipcodes.",
    )
    country: str | None = Field(
        default=None,
        description="Two-letter country code to scope regions, cities, ISPs or zip codes.",
        max_length=2,
    )
    region: str | None = Field(
        default=None, description="Region name to scope cities.", max_length=64
    )
    limit: int = Field(default=50, description="Maximum records to return.", ge=1, le=200)
    offset: int = Field(default=0, description="Records to skip, for pagination.", ge=0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


class UsageInput(BaseModel):
    """Input model for account usage statistics."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    kind: str = Field(
        default="data",
        description=(
            "'data' for traffic consumption, 'requests' for request counts, "
            "'domains' for per-domain usage."
        ),
    )
    limit: int = Field(default=50, description="Maximum records to return.", ge=1, le=200)
    offset: int = Field(default=0, description="Records to skip, for pagination.", ge=0)
    response_format: ResponseFormat = Field(default=ResponseFormat.MARKDOWN)


def _resolve_target(params: TargetingInput) -> ProxyTarget:
    """Turn validated targeting parameters into a connectable proxy endpoint."""
    username, password = config.require_proxy_credentials()
    port = params.port or config.default_port(params.protocol)
    return build_target(
        username,
        password,
        host=config.proxy_host(),
        country=params.country,
        protocol=params.protocol,
        port=port,
        region=params.region,
        city=params.city,
        isp=params.isp,
        session_id=params.session_id,
        ip_quality_filter=params.ip_quality_filter,
        ipv4_only=params.ipv4_only,
    )


def _error(exc: Exception) -> str:
    """Format any expected failure as an actionable single-line error."""
    if isinstance(
        exc, config.ConfigError | ProxyTargetingError | api.ApiError | fetch.ProxyRequestError
    ):
        return f"Error: {exc}"
    return f"Error: unexpected {type(exc).__name__}: {exc}"


@mcp.tool(
    name="nodemaven_build_proxy_url",
    annotations={"title": "Build NodeMaven proxy URL", **READ_ONLY, "openWorldHint": False},
)
async def nodemaven_build_proxy_url(params: BuildProxyUrlInput) -> str:
    """Build a NodeMaven proxy URL with geo targeting encoded in the username.

    Use this to hand a ready connection string to another tool — curl, Playwright,
    Scrapy, requests — instead of guessing NodeMaven's username syntax. No network
    call is made. To verify the IP actually works, use nodemaven_check_proxy.

    Args:
        params (BuildProxyUrlInput): Validated targeting parameters containing:
            - country (str): Two-letter ISO code, required
            - region / city / isp (str | None): Optional narrowing
            - session_id (str | None): 4-10 alphanumerics for a sticky IP
            - ip_quality_filter (str | None): e.g. 'medium', 'high'
            - ipv4_only (bool), protocol (str), port (int | None)
            - reveal_credentials (bool): include the real password, default False
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Markdown or JSON with the schema:
        {
            "proxy_url": str,     # e.g. "http://user-country-us-sid-ab12:***@gate.nodemaven.com:8080"
            "username": str,      # targeting username
            "host": str,
            "port": int,
            "protocol": str,
            "password_masked": bool
        }
        On failure: "Error: <what went wrong and how to fix it>"

    Examples:
        - "give me a US proxy string for curl" -> country='us', reveal_credentials=True
        - "same IP for the whole login flow" -> session_id='ab12cd'
        - Don't use when: you want the page content (use nodemaven_fetch instead)
    """
    try:
        target = _resolve_target(params)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    payload: dict[str, Any] = {
        "proxy_url": target.url(reveal_password=params.reveal_credentials),
        "username": target.username,
        "host": target.host,
        "port": target.port,
        "protocol": target.protocol,
        "password_masked": not params.reveal_credentials,
    }
    return render(
        payload,
        params.response_format,
        markdown=dict_to_markdown(payload, title="NodeMaven proxy endpoint"),
    )


@mcp.tool(
    name="nodemaven_check_proxy",
    annotations={
        "title": "Check NodeMaven exit IP",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def nodemaven_check_proxy(params: CheckProxyInput) -> str:
    """Send one request through the proxy and report the exit IP, its geo and latency.

    Use this before a scraping run to confirm the targeting resolves to a real pool
    and that the exit IP is where you expect. Over-narrow targeting (rare city plus
    ISP filter) is the usual reason a connection fails.

    Args:
        params (CheckProxyInput): Targeting parameters plus response_format.

    Returns:
        str: Markdown or JSON with the schema:
        {
            "exit_ip": str,          # e.g. "104.28.51.7"
            "country": str,          # e.g. "US"
            "region": str, "city": str, "org": str,
            "latency_ms": int,
            "proxy": str,            # password-masked proxy URL
            "session_id_used": bool
        }
        On failure: "Error: <what went wrong and how to fix it>"

    Examples:
        - "am I really coming out of Germany?" -> country='de'
        - "does this sticky session hold?" -> session_id='ab12cd', call twice
        - Don't use when: you need the catalogue of available cities
          (use nodemaven_list_locations)
    """
    try:
        target = _resolve_target(params)
        payload = await fetch.check_exit_ip(target)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    return render(
        payload,
        params.response_format,
        markdown=dict_to_markdown(payload, title="NodeMaven exit IP"),
    )


@mcp.tool(
    name="nodemaven_fetch",
    annotations={
        "title": "Fetch a URL through NodeMaven",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def nodemaven_fetch(params: FetchInput) -> str:
    """Fetch a URL through a NodeMaven residential or mobile IP and return its content.

    Use this when a page is geo-restricted or blocks datacenter traffic. HTML is
    converted to readable text by default so it does not flood the context; set
    as_text=false when the markup itself matters.

    Args:
        params (FetchInput): Targeting parameters plus:
            - url (str): absolute http(s) URL
            - max_bytes (int): body truncation limit, default 200000
            - as_text (bool): strip HTML markup, default True
            - response_format (ResponseFormat): 'markdown' or 'json'

    Returns:
        str: Markdown or JSON with the schema:
        {
            "url": str,              # final URL after redirects
            "status_code": int,
            "content_type": str,
            "title": str | None,     # HTML <title> when available
            "latency_ms": int,
            "truncated": bool,
            "bytes": int,
            "body": str,
            "proxy": str             # password-masked proxy URL
        }
        On failure: "Error: <what went wrong and how to fix it>"

    Examples:
        - "read this page as a German visitor" -> url=..., country='de'
        - "check the price shown to US shoppers" -> url=..., country='us', city='new york'
        - Don't use when: the site is reachable without a proxy (use a plain fetch)
    """
    try:
        target = _resolve_target(params)
        payload = await fetch.fetch_url(
            target, params.url, max_bytes=params.max_bytes, as_text=params.as_text
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    body = payload.pop("body")
    markdown = dict_to_markdown(payload, title=f"Fetched {payload['url']}")
    payload["body"] = body
    return render(payload, params.response_format, markdown=f"{markdown}\n\n---\n\n{body}")


@mcp.tool(
    name="nodemaven_list_locations",
    annotations={"title": "List NodeMaven locations", **READ_ONLY, "openWorldHint": True},
)
async def nodemaven_list_locations(params: ListLocationsInput) -> str:
    """List the countries, regions, cities, ISPs or zip codes available for targeting.

    Use this to discover exact spellings before targeting — guessing a city name is
    the most common reason a proxy connection returns an empty pool.

    Args:
        params (ListLocationsInput): Validated parameters containing:
            - level (str): 'countries' | 'regions' | 'cities' | 'isps' | 'zipcodes'
            - country (str | None): two-letter code scoping the query
            - region (str | None): region name scoping cities
            - limit (int), offset (int), response_format (ResponseFormat)

    Returns:
        str: Markdown or JSON with the schema:
        {
            "total": int, "count": int, "offset": int, "limit": int,
            "has_more": bool, "next_offset": int | None,
            "items": [ { ... } ]     # raw records as returned by NodeMaven
        }
        On failure: "Error: <what went wrong and how to fix it>"

    Examples:
        - "which countries can I target?" -> level='countries'
        - "list German cities" -> level='cities', country='de'
        - "which carriers exist in the US?" -> level='isps', country='us'

    Requires NODEMAVEN_API_KEY.
    """
    try:
        raw = await api.list_locations(
            params.level,
            country=params.country,
            region=params.region,
            limit=params.limit,
            offset=params.offset,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    envelope = paginated(raw, limit=params.limit, offset=params.offset)
    return render(
        envelope,
        params.response_format,
        markdown=items_to_markdown(envelope, title=f"NodeMaven {params.level}"),
    )


@mcp.tool(
    name="nodemaven_get_usage",
    annotations={"title": "Get NodeMaven usage stats", **READ_ONLY, "openWorldHint": True},
)
async def nodemaven_get_usage(params: UsageInput) -> str:
    """Report account usage: traffic consumed, request counts or per-domain breakdown.

    Use this to check remaining traffic before a large run, or to attribute spend
    to the domains that caused it.

    Args:
        params (UsageInput): Validated parameters containing:
            - kind (str): 'data' | 'requests' | 'domains'
            - limit (int), offset (int), response_format (ResponseFormat)

    Returns:
        str: Markdown or JSON with the same pagination envelope as
        nodemaven_list_locations, where items are usage records.
        On failure: "Error: <what went wrong and how to fix it>"

    Examples:
        - "how much traffic have I burned?" -> kind='data'
        - "which domains cost me the most?" -> kind='domains'

    Requires NODEMAVEN_API_KEY.
    """
    try:
        raw = await api.get_statistics(params.kind, limit=params.limit, offset=params.offset)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    envelope = paginated(raw, limit=params.limit, offset=params.offset)
    return render(
        envelope,
        params.response_format,
        markdown=items_to_markdown(envelope, title=f"NodeMaven usage ({params.kind})"),
    )
