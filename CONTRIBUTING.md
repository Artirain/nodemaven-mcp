# Contributing

Thanks for taking the time. This project is small on purpose — the bar is that
every change stays easy to read a year from now.

## Setup

```bash
git clone https://github.com/artirain/nodemaven-mcp
cd nodemaven-mcp
pip install -e ".[dev]"
pytest -q
```

No NodeMaven account is needed to run the test suite: every network call goes
through `httpx.MockTransport`.

## Before opening a PR

```bash
ruff check .
ruff format .
pytest -q
```

## What makes a change easy to merge

- **A test that fails without your change.** Targeting rules and error messages
  are the contract of this server; both are cheap to cover.
- **Actionable errors.** An agent cannot read a stack trace and recover. Every
  failure should say what went wrong *and* what to try next.
- **No new runtime dependencies** unless the PR explains why the standard
  library and `httpx` cannot do it.
- **Never log or return credentials.** Proxy passwords stay masked unless the
  caller explicitly asks for them.

## Reporting a problem

Open an issue with the tool you called, the arguments (redact credentials), and
what you expected. If it involves live proxy behaviour, include the country and
whether a session id was used — targeting is the usual culprit.
