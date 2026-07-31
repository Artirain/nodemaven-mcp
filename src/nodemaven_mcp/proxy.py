"""Proxy username and URL construction.

NodeMaven encodes targeting in the proxy username:

    USERNAME-country-us-region-california-city-los_angeles-sid-ab12cd-filter-medium

Everything here is pure and side-effect free so the rules stay testable without
touching the network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

PROTOCOLS = ("http", "socks5")

# Ports published by NodeMaven for each protocol.
PORT_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "http": ((8080, 9080), (32000, 33000)),
    "socks5": ((1080, 2080), (42000, 43000)),
}

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9]{4,10}$")
COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")

MASKED_PASSWORD = "***"


class ProxyTargetingError(ValueError):
    """Raised when targeting parameters cannot produce a valid proxy username."""


@dataclass(frozen=True)
class ProxyTarget:
    """Resolved proxy endpoint plus the username that encodes its targeting."""

    username: str
    password: str
    host: str
    port: int
    protocol: str

    def url(self, *, reveal_password: bool = False) -> str:
        password = quote(self.password, safe="") if reveal_password else MASKED_PASSWORD
        return (
            f"{self.protocol}://{quote(self.username, safe='')}:{password}@{self.host}:{self.port}"
        )


def normalize_label(value: str, *, field: str) -> str:
    """Normalize a location label to the form NodeMaven expects.

    "Los Angeles" -> "los_angeles", "T-Mobile USA" -> "t_mobile_usa".
    """
    cleaned = re.sub(r"[\s\-]+", "_", value.strip().lower())
    cleaned = re.sub(r"[^a-z0-9_]", "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        raise ProxyTargetingError(f"{field} became empty after normalization: {value!r}")
    return cleaned


def validate_port(port: int, protocol: str) -> int:
    ranges = PORT_RANGES[protocol]
    if any(low <= port <= high for low, high in ranges):
        return port
    readable = ", ".join(f"{low}-{high}" for low, high in ranges)
    raise ProxyTargetingError(
        f"Port {port} is not a valid {protocol} port for NodeMaven. Valid ranges: {readable}."
    )


def build_username(
    base_username: str,
    *,
    country: str,
    region: str | None = None,
    city: str | None = None,
    isp: str | None = None,
    session_id: str | None = None,
    ip_quality_filter: str | None = None,
    ipv4_only: bool = False,
) -> str:
    """Build the targeting username for a NodeMaven proxy connection.

    Country is required by NodeMaven; region, city and ISP narrow it further.
    A session_id pins the exit IP (sticky session) instead of rotating per request.
    """
    if not base_username.strip():
        raise ProxyTargetingError("base_username is empty; check NODEMAVEN_PROXY_USERNAME.")
    if not COUNTRY_RE.match(country.strip()):
        raise ProxyTargetingError(
            f"country must be a two-letter ISO code such as 'us' or 'de', got {country!r}."
        )

    parts = [base_username.strip(), "country", country.strip().lower()]

    for field, value in (("region", region), ("city", city), ("isp", isp)):
        if value:
            parts += [field, normalize_label(value, field=field)]

    if session_id:
        if not SESSION_ID_RE.match(session_id):
            raise ProxyTargetingError(
                "session_id must be 4-10 alphanumeric characters (no dashes or underscores), "
                f"got {session_id!r}."
            )
        parts += ["sid", session_id]

    if ip_quality_filter:
        parts += ["filter", normalize_label(ip_quality_filter, field="ip_quality_filter")]

    if ipv4_only:
        parts += ["ipv4", "true"]

    return "-".join(parts)


def build_target(
    base_username: str,
    password: str,
    *,
    host: str,
    country: str,
    protocol: str = "http",
    port: int | None = None,
    region: str | None = None,
    city: str | None = None,
    isp: str | None = None,
    session_id: str | None = None,
    ip_quality_filter: str | None = None,
    ipv4_only: bool = False,
) -> ProxyTarget:
    """Resolve targeting parameters into a connectable proxy endpoint."""
    if protocol not in PROTOCOLS:
        raise ProxyTargetingError(
            f"protocol must be one of {', '.join(PROTOCOLS)}, got {protocol!r}."
        )
    if port is None:
        raise ProxyTargetingError("port must be resolved before building a proxy target.")

    return ProxyTarget(
        username=build_username(
            base_username,
            country=country,
            region=region,
            city=city,
            isp=isp,
            session_id=session_id,
            ip_quality_filter=ip_quality_filter,
            ipv4_only=ipv4_only,
        ),
        password=password,
        host=host,
        port=validate_port(port, protocol),
        protocol=protocol,
    )
