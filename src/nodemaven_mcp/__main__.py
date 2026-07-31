"""Entry point: runs the MCP server over stdio."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import __version__
from .server import mcp


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without adding a dependency.

    Existing environment variables win, so client config always overrides the file.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    if "--version" in sys.argv:
        print(f"nodemaven-mcp {__version__}")
        return
    if "--help" in sys.argv or "-h" in sys.argv:
        print(
            "nodemaven-mcp - MCP server for NodeMaven proxies (stdio transport).\n\n"
            "Configure NODEMAVEN_API_KEY, NODEMAVEN_PROXY_USERNAME and "
            "NODEMAVEN_PROXY_PASSWORD, then point your MCP client at this command.\n"
            "See README.md for client configuration snippets."
        )
        return

    load_env_file(Path.cwd() / ".env")
    mcp.run()


if __name__ == "__main__":
    main()
