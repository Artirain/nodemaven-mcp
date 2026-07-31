"""Shared response shaping: pagination envelopes, JSON/Markdown rendering."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

ITEM_KEYS = ("results", "data", "items")
TOTAL_KEYS = ("count", "total", "total_count")


class ResponseFormat(str, Enum):
    """Output format for tool responses."""

    MARKDOWN = "markdown"
    JSON = "json"


def extract_items(payload: dict[str, Any]) -> list[Any]:
    """Pull the list of records out of an API payload regardless of its wrapper key."""
    for key in ITEM_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def extract_total(payload: dict[str, Any], fallback: int) -> int:
    for key in TOTAL_KEYS:
        value = payload.get(key)
        if isinstance(value, int):
            return value
    return fallback


def paginated(payload: dict[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
    """Wrap an API payload in a consistent pagination envelope."""
    items = extract_items(payload)
    total = extract_total(payload, len(items) + offset)
    has_more = total > offset + len(items)
    return {
        "total": total,
        "count": len(items),
        "offset": offset,
        "limit": limit,
        "has_more": has_more,
        "next_offset": offset + len(items) if has_more else None,
        "items": items,
    }


def as_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def dict_to_markdown(payload: dict[str, Any], *, title: str) -> str:
    """Render a flat mapping as a Markdown bullet list, skipping empty values."""
    lines = [f"# {title}", ""]
    for key, value in payload.items():
        if value in (None, "", [], {}):
            continue
        lines.append(f"- **{key.replace('_', ' ')}**: {value}")
    return "\n".join(lines)


def items_to_markdown(envelope: dict[str, Any], *, title: str) -> str:
    """Render a pagination envelope as a compact Markdown list."""
    lines = [
        f"# {title}",
        "",
        f"Showing {envelope['count']} of {envelope['total']} (offset {envelope['offset']}).",
        "",
    ]
    for item in envelope["items"]:
        if isinstance(item, dict):
            label = item.get("name") or item.get("city") or item.get("region") or item.get("id")
            code = item.get("code") or item.get("country_code") or item.get("iso")
            suffix = f" (`{code}`)" if code else ""
            extras = {
                k: v for k, v in item.items() if k not in {"name", "code", "country_code", "iso"}
            }
            lines.append(f"- **{label}**{suffix}" + (f" — {as_inline(extras)}" if extras else ""))
        else:
            lines.append(f"- {item}")

    if envelope["has_more"]:
        lines += ["", f"More available: call again with offset={envelope['next_offset']}."]
    return "\n".join(lines)


def as_inline(payload: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in payload.items() if v not in (None, ""))


def render(
    payload: dict[str, Any],
    response_format: ResponseFormat,
    *,
    markdown: str,
) -> str:
    """Return the Markdown rendering or the raw JSON payload, per the caller's choice."""
    if response_format == ResponseFormat.JSON:
        return as_json(payload)
    return markdown
