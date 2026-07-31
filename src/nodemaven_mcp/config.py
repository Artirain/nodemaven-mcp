"""Environment-backed configuration.

Credentials are read lazily so the server can start (and expose its tool list)
even when only part of the configuration is present. Tools that need a missing
value fail with an actionable message instead of crashing at import time.
"""

from __future__ import annotations

import os

DEFAULT_API_BASE_URL = "https://api.nodemaven.com/api/v2"
DEFAULT_PROXY_HOST = "gate.nodemaven.com"
DEFAULT_HTTP_PORT = 8080
DEFAULT_SOCKS5_PORT = 1080


class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing."""


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is None:
        return None
    value = value.strip()
    return value or None


def api_base_url() -> str:
    return (_get("NODEMAVEN_API_BASE_URL") or DEFAULT_API_BASE_URL).rstrip("/")


def proxy_host() -> str:
    return _get("NODEMAVEN_PROXY_HOST") or DEFAULT_PROXY_HOST


def default_port(protocol: str) -> int:
    if protocol == "socks5":
        return int(_get("NODEMAVEN_SOCKS5_PORT") or DEFAULT_SOCKS5_PORT)
    return int(_get("NODEMAVEN_HTTP_PORT") or DEFAULT_HTTP_PORT)


def require_api_key() -> str:
    key = _get("NODEMAVEN_API_KEY")
    if not key:
        raise ConfigError(
            "NODEMAVEN_API_KEY is not set. Copy it from the NodeMaven dashboard "
            "(Profile -> API Key) and expose it to this MCP server via the `env` block "
            "of your client config or a local .env file."
        )
    return key


def require_proxy_credentials() -> tuple[str, str]:
    username = _get("NODEMAVEN_PROXY_USERNAME")
    password = _get("NODEMAVEN_PROXY_PASSWORD")
    missing = [
        name
        for name, value in (
            ("NODEMAVEN_PROXY_USERNAME", username),
            ("NODEMAVEN_PROXY_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            f"{' and '.join(missing)} not set. Both come from the NodeMaven dashboard "
            "(Proxy Setup) and are required for tools that send traffic through the proxy."
        )
    return username, password  # type: ignore[return-value]
