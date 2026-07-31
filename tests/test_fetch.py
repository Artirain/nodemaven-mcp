import httpx
import pytest

from nodemaven_mcp import fetch
from nodemaven_mcp.proxy import ProxyTarget

TARGET = ProxyTarget(
    username="acct-country-us-sid-ab12cd",
    password="s3cr3t",
    host="gate.nodemaven.com",
    port=8080,
    protocol="http",
)


def test_html_to_text_drops_scripts_and_keeps_title():
    html = """
    <html><head><title>Pricing</title><style>.a{color:red}</style></head>
    <body><script>track()</script><h1>Plans</h1><p>From $2.20/GB</p></body></html>
    """
    text, title = fetch.html_to_text(html)

    assert title == "Pricing"
    assert "Plans" in text and "From $2.20/GB" in text
    assert "track()" not in text and "color:red" not in text


async def test_check_exit_ip_reports_geo_and_masks_password():
    body = {"ip": "104.28.51.7", "country": "US", "city": "Los Angeles", "org": "AS7922 Comcast"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))

    result = await fetch.check_exit_ip(TARGET, transport=transport)

    assert result["exit_ip"] == "104.28.51.7"
    assert result["city"] == "Los Angeles"
    assert result["session_id_used"] is True
    assert result["latency_ms"] >= 0
    assert "s3cr3t" not in result["proxy"]


async def test_check_exit_ip_uses_configured_check_url(monkeypatch):
    monkeypatch.setenv("NODEMAVEN_IP_CHECK_URL", "https://api.ipify.test/?format=json")
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"ip": "1.2.3.4"})

    await fetch.check_exit_ip(TARGET, transport=httpx.MockTransport(handler))

    assert captured[0].url.host == "api.ipify.test"


async def test_proxy_error_explains_narrow_targeting():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ProxyError("407 Proxy Authentication Required", request=request)

    with pytest.raises(fetch.ProxyRequestError, match="over-narrow city or ISP filter"):
        await fetch.check_exit_ip(TARGET, transport=httpx.MockTransport(handler))


async def test_fetch_url_converts_html_to_text():
    html = "<html><head><title>Shop</title></head><body><p>Price: 10 EUR</p></body></html>"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    result = await fetch.fetch_url(TARGET, "https://example.test/p/1", transport=transport)

    assert result["status_code"] == 200
    assert result["title"] == "Shop"
    assert result["body"].strip() == "Shop\nPrice: 10 EUR"
    assert result["truncated"] is False


async def test_fetch_url_keeps_raw_html_when_asked():
    html = "<html><body><p>Hi</p></body></html>"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=html, headers={"content-type": "text/html"})
    )

    result = await fetch.fetch_url(
        TARGET, "https://example.test", as_text=False, transport=transport
    )

    assert result["body"] == html


async def test_fetch_url_truncates_large_bodies():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="x" * 5000, headers={"content-type": "text/plain"})
    )

    result = await fetch.fetch_url(
        TARGET, "https://example.test", max_bytes=1000, transport=transport
    )

    assert result["truncated"] is True
    assert result["bytes"] == 1000
