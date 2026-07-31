# nodemaven-mcp

**Give your AI agent a residential IP.** An [MCP](https://modelcontextprotocol.io)
server that lets Claude Code, Claude Desktop, Cursor or any other MCP client route
web requests through [NodeMaven](https://nodemaven.com) residential and mobile
proxies: pick an exit country, verify what the target site sees, and pull the page.

```
you:    read this product page the way a shopper in Munich sees it
agent:  nodemaven_fetch(url=..., country="de", city="munich")
        -> 200, de, 14.99 EUR, exit IP 91.34.x.x (Vodafone), 812 ms
```

Unofficial, community-built, MIT licensed. Not affiliated with NodeMaven.

---

## Why

Agents are increasingly the thing doing the browsing, and they hit the same wall
everyone else does: datacenter IPs get blocked, and prices, stock and search
results change by geography. The proxy is already the fix - it just isn't
reachable from inside the agent's session.

This server closes that gap in five tools. Nothing here needs a scraping DSL: the
agent asks for a country, gets a working connection, and reads the page.

## Tools

| Tool | What it does | Needs |
|---|---|---|
| `nodemaven_build_proxy_url` | Builds a proxy URL with geo targeting encoded in the username, ready for curl, Playwright, Scrapy or requests | proxy creds |
| `nodemaven_check_proxy` | One request through the proxy: exit IP, geo, ISP, latency | proxy creds |
| `nodemaven_fetch` | Fetches a URL through the proxy, HTML converted to readable text | proxy creds |
| `nodemaven_list_locations` | Countries, regions, cities, ISPs, zip codes available for targeting | API key |
| `nodemaven_get_usage` | Traffic consumed, request counts, per-domain breakdown | API key |

Every tool takes `response_format`: `markdown` (default, compact) or `json`
(complete, for programmatic use), and paginates with `limit` / `offset`.

## Install

```bash
git clone https://github.com/Artirain/nodemaven-mcp
cd nodemaven-mcp
pip install -e .
```

Requires Python 3.10+.

## Configure

Two independent sets of credentials, both from the [NodeMaven dashboard](https://dashboard.nodemaven.com):

- `NODEMAVEN_API_KEY` - Profile -> API Key. Used by the location and usage tools.
- `NODEMAVEN_PROXY_USERNAME` / `NODEMAVEN_PROXY_PASSWORD` - Proxy Setup. Used by
  everything that sends traffic through the proxy.

You can set only one set; tools that need the other say so instead of failing
silently.

### Claude Code

```bash
claude mcp add nodemaven \
  -e NODEMAVEN_API_KEY=... \
  -e NODEMAVEN_PROXY_USERNAME=... \
  -e NODEMAVEN_PROXY_PASSWORD=... \
  -- python -m nodemaven_mcp
```

### Claude Desktop / Cursor

`claude_desktop_config.json` (or `.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "nodemaven": {
      "command": "python",
      "args": ["-m", "nodemaven_mcp"],
      "env": {
        "NODEMAVEN_API_KEY": "your-api-key",
        "NODEMAVEN_PROXY_USERNAME": "your-proxy-username",
        "NODEMAVEN_PROXY_PASSWORD": "your-proxy-password"
      }
    }
  }
}
```

A local `.env` in the working directory also works - see `.env.example`.
Environment variables always win over the file.

## Use it

Three things worth asking your agent, once it is connected:

**Check what a site sees**

> "Am I actually coming out of Germany? Use a Vodafone IP if you can."

The agent calls `nodemaven_check_proxy(country="de", isp="vodafone")` and reports
the exit IP, city and latency. Useful as a smoke test before a long run.

**Read geo-restricted content**

> "Fetch this page as a US shopper in New York and tell me the price."

`nodemaven_fetch(url=..., country="us", city="new york")` returns readable text,
truncated to 200 KB by default so it does not flood the context.

**Keep one identity across a flow**

> "Log into the demo account and walk through checkout, same IP the whole time."

Pass the same `session_id` (4-10 alphanumerics) on every call and NodeMaven pins
the exit IP; drop it and the IP rotates per request.

Targeting spellings matter. `nodemaven_list_locations(level="cities", country="de")`
gives the exact values NodeMaven accepts, which is the difference between a working
pool and an empty one.

## Design notes

**Passwords stay masked.** `build_proxy_url` returns `***` in place of the
password unless the caller passes `reveal_credentials=true`. Transcripts get
shared, pasted into issues and fed back into models; a proxy password should not
ride along by default.

**Errors are written for an agent, not a log file.** A refused connection says
that an over-narrow city or ISP filter is the usual cause and suggests widening
it. A 401 names the environment variable to check. An agent that can read the
error can retry correctly on the first attempt.

**Targeting is pure and tested.** NodeMaven encodes location in the proxy
username:

```
acct-country-us-region-california-city-los_angeles-isp-t_mobile_usa-sid-ab12cd-filter-medium
```

Building that string - normalizing `"Los Angeles"` to `los_angeles`, rejecting a
session id that would silently disable stickiness, validating the port against
the range for the chosen protocol - is pure logic, so it is covered by tests that
never touch the network.

**No parser dependency.** HTML-to-text runs on `html.parser` from the standard
library. The dependency list is `mcp`, `httpx` and `pydantic`.

## Development

```bash
pip install -e ".[dev]"
pytest -q          # 57 tests, no network, no credentials needed
ruff check .
```

CI runs the suite on Python 3.10, 3.11 and 3.12.

Contributions welcome - see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
