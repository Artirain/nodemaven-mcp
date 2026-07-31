"""MCP server exposing NodeMaven proxies to AI agents.

Tools let an agent pick an exit location, verify what the target site will see,
and pull pages through a residential or mobile IP without leaving the session.

Tool arguments are flat on purpose: clients render one field per parameter and
models fill them without nesting everything under a wrapper object. The Pydantic
models below still own validation - each tool builds one from its arguments.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import api, config, fetch
from .formatting import (
    ResponseFormat,
    dict_to_markdown,
    items_to_markdown,
    paginated,
    render,
)
from .proxy import COUNTRY_RE, ProxyTarget, ProxyTargetingError, build_target

mcp = FastMCP("nodemaven_mcp")

READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
}

# Shared argument types, so every tool describes targeting the same way.
Country = Annotated[
    str, Field(description="Two-letter ISO country code for the exit IP, e.g. 'us', 'de', 'br'.")
]
Region = Annotated[
    str | None, Field(description="State or region to narrow the pool, e.g. 'california'.")
]
City = Annotated[str | None, Field(description="City to narrow the pool, e.g. 'los angeles'.")]
Isp = Annotated[str | None, Field(description="ISP or mobile carrier to pin, e.g. 't-mobile usa'.")]
SessionId = Annotated[
    str | None,
    Field(
        description=(
            "4-10 alphanumeric characters. Reuse the same value to keep the same exit IP "
            "across calls (sticky session); omit it to rotate on every request."
        )
    ),
]
QualityFilter = Annotated[
    str | None, Field(description="NodeMaven IP quality tier, e.g. 'medium' or 'high'.")
]
Ipv4Only = Annotated[bool, Field(description="Restrict the exit IP to IPv4.")]
Protocol = Annotated[str, Field(description="Proxy protocol: 'http' or 'socks5'.")]
Port = Annotated[
    int | None,
    Field(description="Override the proxy port. Defaults to the port set for the protocol."),
]
Format = Annotated[
    ResponseFormat,
    Field(description="'markdown' for a compact summary, 'json' for the full payload."),
]
Limit = Annotated[int, Field(description="Maximum records to return.", ge=1, le=200)]
Offset = Annotated[int, Field(description="Records to skip, for pagination.", ge=0)]


class TargetingInput(BaseModel):
    """Targeting parameters shared by every tool that opens a proxy connection."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    country: str = Field(..., max_length=64)
    region: str | None = Field(default=None, max_length=64)
    city: str | None = Field(default=None, max_length=64)
    isp: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=10)
    ip_quality_filter: str | None = Field(default=None, max_length=32)
    ipv4_only: bool = False
    protocol: str = "http"
    port: int | None = Field(default=None, ge=1, le=65535)

    @field_validator("country")
    @classmethod
    def validate_country(cls, value: str) -> str:
        """Reject full country names with a message that says what to send instead."""
        if not COUNTRY_RE.match(value):
            raise ValueError(
                f"country must be a two-letter ISO code, not {value!r}. "
                "Use 'de' for Germany, 'us' for the United States, 'br' for Brazil; "
                "call nodemaven_list_locations(level='countries') to look a code up."
            )
        return value.lower()


class BuildProxyUrlInput(TargetingInput):
    reveal_credentials: bool = False


class FetchInput(TargetingInput):
    url: str = Field(..., pattern=r"^https?://", max_length=2048)
    max_bytes: int = Field(default=fetch.DEFAULT_MAX_BYTES, ge=1000, le=2_000_000)
    as_text: bool = True


def _targeting(**kwargs: Any) -> dict[str, Any]:
    """Collect the targeting arguments every proxy tool accepts."""
    return kwargs


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
    if isinstance(exc, ValidationError):
        problems = []
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"]) or "input"
            message = error["msg"].removeprefix("Value error, ")
            problems.append(message if message.startswith(field) else f"{field}: {message}")
        return "Error: " + " ".join(problems)
    if isinstance(
        exc, config.ConfigError | ProxyTargetingError | api.ApiError | fetch.ProxyRequestError
    ):
        return f"Error: {exc}"
    return f"Error: unexpected {type(exc).__name__}: {exc}"


@mcp.tool(
    name="nodemaven_build_proxy_url",
    annotations={"title": "Build NodeMaven proxy URL", **READ_ONLY, "openWorldHint": False},
)
async def nodemaven_build_proxy_url(
    country: Country,
    region: Region = None,
    city: City = None,
    isp: Isp = None,
    session_id: SessionId = None,
    ip_quality_filter: QualityFilter = None,
    ipv4_only: Ipv4Only = False,
    protocol: Protocol = "http",
    port: Port = None,
    reveal_credentials: Annotated[
        bool,
        Field(
            description=(
                "Include the real proxy password in the returned URL. Keep false unless the "
                "URL goes straight to a command that must authenticate; the password otherwise "
                "stays masked so it does not leak into transcripts."
            )
        ),
    ] = False,
    response_format: Format = ResponseFormat.MARKDOWN,
) -> str:
    """Build a NodeMaven proxy URL with geo targeting encoded in the username.

    Use this to hand a ready connection string to another tool - curl, Playwright,
    Scrapy, requests - instead of guessing NodeMaven's username syntax. No network
    call is made. To verify the IP actually works, use nodemaven_check_proxy.

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
        - "give me a US proxy string for curl" -> country='us', reveal_credentials=true
        - "same IP for the whole login flow" -> session_id='ab12cd'
        - Don't use when: you want the page content (use nodemaven_fetch instead)
    """
    try:
        params = BuildProxyUrlInput(
            **_targeting(
                country=country,
                region=region,
                city=city,
                isp=isp,
                session_id=session_id,
                ip_quality_filter=ip_quality_filter,
                ipv4_only=ipv4_only,
                protocol=protocol,
                port=port,
            ),
            reveal_credentials=reveal_credentials,
        )
        target = _resolve_target(params)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    payload: dict[str, Any] = {
        "proxy_url": target.url(reveal_password=reveal_credentials),
        "username": target.username,
        "host": target.host,
        "port": target.port,
        "protocol": target.protocol,
        "password_masked": not reveal_credentials,
    }
    return render(
        payload,
        response_format,
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
async def nodemaven_check_proxy(
    country: Country,
    region: Region = None,
    city: City = None,
    isp: Isp = None,
    session_id: SessionId = None,
    ip_quality_filter: QualityFilter = None,
    ipv4_only: Ipv4Only = False,
    protocol: Protocol = "http",
    port: Port = None,
    response_format: Format = ResponseFormat.MARKDOWN,
) -> str:
    """Send one request through the proxy and report the exit IP, its geo and latency.

    Use this before a scraping run to confirm the targeting resolves to a real pool
    and that the exit IP is where you expect. Over-narrow targeting (a rare city plus
    an ISP filter) is the usual reason a connection fails.

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
        params = TargetingInput(
            **_targeting(
                country=country,
                region=region,
                city=city,
                isp=isp,
                session_id=session_id,
                ip_quality_filter=ip_quality_filter,
                ipv4_only=ipv4_only,
                protocol=protocol,
                port=port,
            )
        )
        payload = await fetch.check_exit_ip(_resolve_target(params))
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    return render(
        payload,
        response_format,
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
async def nodemaven_fetch(
    url: Annotated[str, Field(description="Absolute http(s) URL to fetch through the proxy.")],
    country: Country,
    region: Region = None,
    city: City = None,
    isp: Isp = None,
    session_id: SessionId = None,
    ip_quality_filter: QualityFilter = None,
    ipv4_only: Ipv4Only = False,
    protocol: Protocol = "http",
    port: Port = None,
    max_bytes: Annotated[
        int, Field(description="Truncate the response body to this many bytes.")
    ] = fetch.DEFAULT_MAX_BYTES,
    as_text: Annotated[
        bool, Field(description="Strip HTML markup and return readable text instead of raw HTML.")
    ] = True,
    response_format: Format = ResponseFormat.MARKDOWN,
) -> str:
    """Fetch a URL through a NodeMaven residential or mobile IP and return its content.

    Use this when a page is geo-restricted or blocks datacenter traffic. HTML is
    converted to readable text by default so it does not flood the context; set
    as_text=false when the markup itself matters.

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
        params = FetchInput(
            **_targeting(
                country=country,
                region=region,
                city=city,
                isp=isp,
                session_id=session_id,
                ip_quality_filter=ip_quality_filter,
                ipv4_only=ipv4_only,
                protocol=protocol,
                port=port,
            ),
            url=url,
            max_bytes=max_bytes,
            as_text=as_text,
        )
        payload = await fetch.fetch_url(
            _resolve_target(params), params.url, max_bytes=params.max_bytes, as_text=params.as_text
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    body = payload.pop("body")
    markdown = dict_to_markdown(payload, title=f"Fetched {payload['url']}")
    payload["body"] = body
    return render(payload, response_format, markdown=f"{markdown}\n\n---\n\n{body}")


@mcp.tool(
    name="nodemaven_list_locations",
    annotations={"title": "List NodeMaven locations", **READ_ONLY, "openWorldHint": True},
)
async def nodemaven_list_locations(
    level: Annotated[
        str, Field(description="One of: countries, regions, cities, isps, zipcodes.")
    ] = "countries",
    country: Annotated[
        str | None,
        Field(description="Two-letter country code scoping regions, cities, ISPs or zip codes."),
    ] = None,
    region: Annotated[str | None, Field(description="Region name scoping cities.")] = None,
    limit: Limit = 50,
    offset: Offset = 0,
    response_format: Format = ResponseFormat.MARKDOWN,
) -> str:
    """List the countries, regions, cities, ISPs or zip codes available for targeting.

    Use this to discover exact spellings before targeting - guessing a city name is
    the most common reason a proxy connection returns an empty pool.

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
            level, country=country, region=region, limit=limit, offset=offset
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    envelope = paginated(raw, limit=limit, offset=offset)
    return render(
        envelope,
        response_format,
        markdown=items_to_markdown(envelope, title=f"NodeMaven {level}"),
    )


@mcp.tool(
    name="nodemaven_get_usage",
    annotations={"title": "Get NodeMaven usage stats", **READ_ONLY, "openWorldHint": True},
)
async def nodemaven_get_usage(
    kind: Annotated[
        str,
        Field(
            description=(
                "'data' for traffic consumption, 'requests' for request counts, "
                "'domains' for per-domain usage."
            )
        ),
    ] = "data",
    limit: Limit = 50,
    offset: Offset = 0,
    response_format: Format = ResponseFormat.MARKDOWN,
) -> str:
    """Report account usage: traffic consumed, request counts or per-domain breakdown.

    Use this to check remaining traffic before a large run, or to attribute spend
    to the domains that caused it.

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
        raw = await api.get_statistics(kind, limit=limit, offset=offset)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent as text
        return _error(exc)

    envelope = paginated(raw, limit=limit, offset=offset)
    return render(
        envelope,
        response_format,
        markdown=items_to_markdown(envelope, title=f"NodeMaven usage ({kind})"),
    )
