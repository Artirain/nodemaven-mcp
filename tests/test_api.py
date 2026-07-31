import httpx
import pytest

from nodemaven_mcp import api, config


def transport_returning(status_code=200, json_body=None, capture=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture.append(request)
        return httpx.Response(status_code, json=json_body if json_body is not None else {})

    return httpx.MockTransport(handler)


async def test_request_without_api_key_explains_how_to_fix():
    with pytest.raises(config.ConfigError, match="NODEMAVEN_API_KEY"):
        await api.request("base/locations/countries", transport=transport_returning())


async def test_request_sends_api_key_header_and_trailing_slash(credentials):
    captured: list[httpx.Request] = []
    await api.list_locations(
        "countries", limit=10, offset=0, transport=transport_returning(capture=captured)
    )

    request = captured[0]
    assert request.headers["Authorization"] == "x-api-key test-api-key"
    assert str(request.url).startswith("https://api.nodemaven.com/api/v2/base/locations/countries/")
    assert request.url.params["limit"] == "10"


async def test_list_locations_scopes_by_country(credentials):
    captured: list[httpx.Request] = []
    await api.list_locations(
        "cities", country="DE", transport=transport_returning(capture=captured)
    )

    assert captured[0].url.params["country_code"] == "de"


async def test_unknown_location_level_is_rejected(credentials):
    with pytest.raises(api.ApiError, match="level must be one of"):
        await api.list_locations("planets", transport=transport_returning())


async def test_unknown_statistics_kind_is_rejected(credentials):
    with pytest.raises(api.ApiError, match="kind must be one of"):
        await api.get_statistics("weather", transport=transport_returning())


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "rejected the API key"),
        (403, "lacks access"),
        (404, "Endpoint not found"),
        (429, "Rate limit exceeded"),
        (503, "service is failing"),
    ],
)
async def test_http_errors_are_actionable(credentials, status, expected):
    with pytest.raises(api.ApiError, match=expected):
        await api.request(
            "base/locations/countries", transport=transport_returning(status_code=status)
        )


async def test_non_json_body_is_reported(credentials):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with pytest.raises(api.ApiError, match="non-JSON body"):
        await api.request("base/locations/countries", transport=httpx.MockTransport(handler))


async def test_timeout_is_reported(credentials):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("too slow", request=request)

    with pytest.raises(api.ApiError, match="timed out"):
        await api.request("base/locations/countries", transport=httpx.MockTransport(handler))


async def test_api_base_url_override(credentials, monkeypatch):
    monkeypatch.setenv("NODEMAVEN_API_BASE_URL", "https://staging.nodemaven.test/api/v2/")
    captured: list[httpx.Request] = []
    await api.get_statistics("data", transport=transport_returning(capture=captured))

    assert str(captured[0].url).startswith("https://staging.nodemaven.test/api/v2/base/statistics/")
