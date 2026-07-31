import json

import pytest

from nodemaven_mcp import api, fetch, server
from nodemaven_mcp.server import (
    TargetingInput,
    nodemaven_build_proxy_url,
    nodemaven_check_proxy,
    nodemaven_get_usage,
    nodemaven_list_locations,
)

EXPECTED_TOOLS = {
    "nodemaven_build_proxy_url",
    "nodemaven_check_proxy",
    "nodemaven_fetch",
    "nodemaven_list_locations",
    "nodemaven_get_usage",
}


async def test_all_tools_are_registered_and_documented():
    tools = await server.mcp.list_tools()

    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description and len(tool.description) > 80, tool.name
        assert tool.annotations is not None, tool.name
        assert tool.annotations.title, tool.name


async def test_targeting_arguments_are_flat_in_the_schema():
    """Clients should render one field per parameter, not a single wrapper object."""
    tools = {tool.name: tool for tool in await server.mcp.list_tools()}
    schema = tools["nodemaven_check_proxy"].inputSchema

    assert "params" not in schema["properties"]
    assert {"country", "city", "session_id", "response_format"} <= set(schema["properties"])
    assert schema["required"] == ["country"]
    assert schema["properties"]["country"]["description"].startswith("Two-letter ISO")


async def test_build_proxy_url_masks_password(credentials):
    result = await nodemaven_build_proxy_url(country="us", city="Los Angeles", session_id="ab12cd")

    assert "acct1234-country-us-city-los_angeles-sid-ab12cd" in result
    assert "***" in result
    assert "s3cr3t" not in result


async def test_build_proxy_url_can_reveal_credentials(credentials):
    result = await nodemaven_build_proxy_url(
        country="us", reveal_credentials=True, response_format="json"
    )

    payload = json.loads(result)
    assert payload["password_masked"] is False
    assert "s3cr3t" in payload["proxy_url"]


async def test_build_proxy_url_uses_socks5_default_port(credentials):
    result = await nodemaven_build_proxy_url(
        country="us", protocol="socks5", response_format="json"
    )

    payload = json.loads(result)
    assert payload["port"] == 1080
    assert payload["proxy_url"].startswith("socks5://")


async def test_missing_credentials_produce_actionable_error():
    result = await nodemaven_build_proxy_url(country="us")

    assert result.startswith("Error:")
    assert "NODEMAVEN_PROXY_USERNAME" in result


async def test_invalid_session_id_is_reported_as_error(credentials):
    result = await nodemaven_build_proxy_url(country="us", session_id="ab-12cd")

    assert result.startswith("Error:")
    assert "4-10 alphanumeric" in result


@pytest.mark.parametrize("country", ["germany", "u", "12"])
async def test_full_country_name_is_rejected_with_guidance(credentials, country):
    result = await nodemaven_build_proxy_url(country=country)

    assert result.startswith("Error:")
    assert "two-letter ISO code" in result
    assert "nodemaven_list_locations" in result


async def test_country_is_normalized_to_lowercase(credentials):
    result = await nodemaven_build_proxy_url(country="DE", response_format="json")

    assert json.loads(result)["username"].endswith("country-de")


async def test_targeting_model_still_validates_country():
    with pytest.raises(ValueError, match="two-letter ISO code"):
        TargetingInput(country="germany")


async def test_check_proxy_surfaces_proxy_failures(credentials, monkeypatch):
    async def boom(*args, **kwargs):
        raise fetch.ProxyRequestError("Proxy refused the connection")

    monkeypatch.setattr(fetch, "check_exit_ip", boom)
    result = await nodemaven_check_proxy(country="us")

    assert result == "Error: Proxy refused the connection"


async def test_list_locations_paginates(credentials, monkeypatch):
    async def fake_list_locations(level, **kwargs):
        return {"count": 120, "results": [{"name": "Germany", "code": "de"}]}

    monkeypatch.setattr(api, "list_locations", fake_list_locations)
    result = await nodemaven_list_locations(level="countries", limit=1, response_format="json")

    payload = json.loads(result)
    assert payload["total"] == 120
    assert payload["has_more"] is True
    assert payload["next_offset"] == 1


async def test_list_locations_markdown_mentions_next_offset(credentials, monkeypatch):
    async def fake_list_locations(level, **kwargs):
        return {"count": 2, "results": [{"name": "Berlin"}]}

    monkeypatch.setattr(api, "list_locations", fake_list_locations)
    result = await nodemaven_list_locations(level="cities", limit=1)

    assert "Berlin" in result
    assert "offset=1" in result


async def test_usage_errors_are_forwarded(credentials, monkeypatch):
    async def fake_statistics(kind, **kwargs):
        raise api.ApiError("NodeMaven API error 429: Rate limit exceeded.")

    monkeypatch.setattr(api, "get_statistics", fake_statistics)
    result = await nodemaven_get_usage(kind="data")

    assert "429" in result and result.startswith("Error:")
