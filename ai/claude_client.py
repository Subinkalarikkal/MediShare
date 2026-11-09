"""Thin wrapper around the Anthropic SDK.

* Credentials come from ANTHROPIC_API_KEY (never hard-coded).
* All calls are optional: when Claude is not configured, callers fall back
  to deterministic behaviour and say so in the UI.
* Errors are mapped to a single ClaudeUnavailable exception so the UI can
  degrade gracefully.
"""
from __future__ import annotations

import logging
from typing import Any

from config import get_settings

log = logging.getLogger(__name__)


class ClaudeUnavailable(Exception):
    pass


_client = None


def set_client(client) -> None:
    """Inject a client (used by tests with a fake)."""
    global _client
    _client = client


def is_enabled() -> bool:
    return _client is not None or get_settings().claude_enabled


def get_client():
    global _client
    if _client is None:
        s = get_settings()
        if not s.claude_enabled:
            raise ClaudeUnavailable("Claude is not configured (ANTHROPIC_API_KEY missing or AI processing disabled).")
        import anthropic

        _client = anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=2, timeout=120)
    return _client


def create_message(*, system: str, messages: list[dict], max_tokens: int = 4000,
                   output_schema: dict | None = None, effort: str = "low") -> str:
    """Call Claude and return the concatenated text output."""
    import anthropic

    client = get_client()
    kwargs: dict[str, Any] = dict(model=get_settings().claude_model, max_tokens=max_tokens, system=system,
                                  messages=messages)
    output_config: dict[str, Any] = {"effort": effort}
    if output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": output_schema}
    kwargs["output_config"] = output_config
    # Server-side refusal fallback: if a safety classifier declines, the API
    # re-runs the request on a fallback model inside the same call.
    kwargs["betas"] = ["server-side-fallback-2026-07-01"]
    kwargs["fallbacks"] = "default"
    try:
        resp = client.beta.messages.create(**kwargs)
    except anthropic.RateLimitError as exc:
        raise ClaudeUnavailable("Claude rate limit reached – please retry shortly.") from exc
    except anthropic.AuthenticationError as exc:
        raise ClaudeUnavailable("Claude API key is invalid.") from exc
    except anthropic.APIStatusError as exc:
        raise ClaudeUnavailable(f"Claude API error ({exc.status_code}).") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeUnavailable("Could not reach the Claude API.") from exc
    if getattr(resp, "stop_reason", None) == "refusal":
        raise ClaudeUnavailable("Claude declined this request.")
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    if not text.strip():
        raise ClaudeUnavailable("Claude returned an empty response.")
    return text
