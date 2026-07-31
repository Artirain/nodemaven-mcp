import pytest

ENV_VARS = (
    "NODEMAVEN_API_KEY",
    "NODEMAVEN_PROXY_USERNAME",
    "NODEMAVEN_PROXY_PASSWORD",
    "NODEMAVEN_PROXY_HOST",
    "NODEMAVEN_HTTP_PORT",
    "NODEMAVEN_SOCKS5_PORT",
    "NODEMAVEN_API_BASE_URL",
    "NODEMAVEN_IP_CHECK_URL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Never let a developer's real credentials leak into a test run."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("NODEMAVEN_API_KEY", "test-api-key")
    monkeypatch.setenv("NODEMAVEN_PROXY_USERNAME", "acct1234")
    monkeypatch.setenv("NODEMAVEN_PROXY_PASSWORD", "s3cr3t/pass")
