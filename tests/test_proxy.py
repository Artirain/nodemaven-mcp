import pytest

from nodemaven_mcp.proxy import (
    ProxyTarget,
    ProxyTargetingError,
    build_target,
    build_username,
    normalize_label,
    validate_port,
)


def test_username_requires_country_only():
    assert build_username("acct", country="us") == "acct-country-us"


def test_username_lowercases_country():
    assert build_username("acct", country="DE") == "acct-country-de"


def test_username_orders_targeting_segments():
    username = build_username(
        "acct",
        country="us",
        region="California",
        city="Los Angeles",
        isp="T-Mobile USA",
        session_id="ab12cd",
        ip_quality_filter="medium",
    )
    assert username == (
        "acct-country-us-region-california-city-los_angeles"
        "-isp-t_mobile_usa-sid-ab12cd-filter-medium"
    )


def test_username_appends_ipv4_flag_last():
    assert build_username("acct", country="us", ipv4_only=True) == "acct-country-us-ipv4-true"


@pytest.mark.parametrize("country", ["usa", "u", "", "12"])
def test_invalid_country_is_rejected(country):
    with pytest.raises(ProxyTargetingError, match="two-letter"):
        build_username("acct", country=country)


@pytest.mark.parametrize("session_id", ["abc", "abcdefghijk", "ab-12", "ab_12"])
def test_invalid_session_id_is_rejected(session_id):
    with pytest.raises(ProxyTargetingError, match="4-10 alphanumeric"):
        build_username("acct", country="us", session_id=session_id)


def test_empty_base_username_is_rejected():
    with pytest.raises(ProxyTargetingError, match="NODEMAVEN_PROXY_USERNAME"):
        build_username("   ", country="us")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("New York", "new_york"),
        ("  Rio de Janeiro ", "rio_de_janeiro"),
        ("T-Mobile USA", "t_mobile_usa"),
        ("Nordrhein-Westfalen", "nordrhein_westfalen"),
    ],
)
def test_normalize_label(raw, expected):
    assert normalize_label(raw, field="city") == expected


def test_normalize_label_rejects_symbols_only():
    with pytest.raises(ProxyTargetingError, match="empty after normalization"):
        normalize_label("!!!", field="city")


@pytest.mark.parametrize(("port", "protocol"), [(8080, "http"), (32500, "http"), (1080, "socks5")])
def test_valid_ports(port, protocol):
    assert validate_port(port, protocol) == port


def test_http_port_rejected_for_socks5():
    with pytest.raises(ProxyTargetingError, match="1080-2080"):
        validate_port(8080, "socks5")


def test_unknown_protocol_is_rejected():
    with pytest.raises(ProxyTargetingError, match="protocol must be"):
        build_target("acct", "pw", host="gate.nodemaven.com", country="us", protocol="ftp", port=80)


def test_target_url_masks_password_by_default():
    target = build_target(
        "acct", "s3cr3t", host="gate.nodemaven.com", country="us", protocol="http", port=8080
    )
    assert target.url() == "http://acct-country-us:***@gate.nodemaven.com:8080"
    assert "s3cr3t" not in target.url()


def test_target_url_can_reveal_and_escapes_password():
    target = ProxyTarget(
        username="acct-country-us",
        password="p@ss/word",
        host="gate.nodemaven.com",
        port=8080,
        protocol="http",
    )
    assert target.url(reveal_password=True) == (
        "http://acct-country-us:p%40ss%2Fword@gate.nodemaven.com:8080"
    )
