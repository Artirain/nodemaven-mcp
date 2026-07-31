"""Thin async client for the NodeMaven REST API."""

from __future__ import annotations

from typing import Any

import httpx

from . import config

LOCATION_LEVELS = ("countries", "regions", "cities", "isps", "zipcodes")
STATISTICS_KINDS = ("data", "requests", "domains")

DEFAULT_TIMEOUT = 30.0


class ApiError(RuntimeError):
    """Raised when the NodeMaven API returns an error or cannot be reached."""


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"x-api-key {api_key}",
        "Content-Type": "application/json",
    }


async def request(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """GET a NodeMaven API path and return the decoded JSON body.

    `transport` is an injection point for tests; production calls leave it None.
    """
    api_key = config.require_api_key()
    url = f"{config.api_base_url()}/{path.strip('/')}/"

    async with httpx.AsyncClient(transport=transport, timeout=DEFAULT_TIMEOUT) as client:
        try:
            response = await client.get(url, params=params, headers=_headers(api_key))
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ApiError(describe_http_error(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ApiError(
                f"NodeMaven API timed out after {DEFAULT_TIMEOUT:.0f}s while calling {path}."
            ) from exc
        except httpx.HTTPError as exc:
            raise ApiError(f"Could not reach the NodeMaven API: {exc}") from exc

        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(
                f"NodeMaven API returned a non-JSON body for {path} "
                f"(status {response.status_code})."
            ) from exc


def describe_http_error(exc: httpx.HTTPStatusError) -> str:
    """Turn an HTTP failure into a message that tells the agent what to do next."""
    status = exc.response.status_code
    hints = {
        401: "NodeMaven rejected the API key. Check NODEMAVEN_API_KEY in your client config.",
        403: "The API key is valid but lacks access to this resource. Check your plan.",
        404: "Endpoint not found. The API path may have changed; see docs.nodemaven.com.",
        429: "Rate limit exceeded. Wait before retrying or reduce the request rate.",
    }
    hint = hints.get(status)
    if hint:
        return f"NodeMaven API error {status}: {hint}"
    if 500 <= status < 600:
        return f"NodeMaven API error {status}: the service is failing. Retry in a moment."
    return f"NodeMaven API error {status}."


async def list_locations(
    level: str,
    *,
    country: str | None = None,
    region: str | None = None,
    limit: int = 50,
    offset: int = 0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """List countries, regions, cities, ISPs or zip codes available for targeting."""
    if level not in LOCATION_LEVELS:
        raise ApiError(f"level must be one of {', '.join(LOCATION_LEVELS)}, got {level!r}.")

    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if country:
        params["country_code"] = country.lower()
    if region:
        params["region"] = region

    return await request(f"base/locations/{level}", params, transport=transport)


async def get_statistics(
    kind: str,
    *,
    limit: int = 50,
    offset: int = 0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Fetch traffic, request or per-domain usage statistics for the account."""
    if kind not in STATISTICS_KINDS:
        raise ApiError(f"kind must be one of {', '.join(STATISTICS_KINDS)}, got {kind!r}.")

    return await request(
        f"base/statistics/{kind}", {"limit": limit, "offset": offset}, transport=transport
    )
