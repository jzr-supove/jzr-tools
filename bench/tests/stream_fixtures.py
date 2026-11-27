"""Builders for synthetic Claude Code stream-json lines (shape taken from a real probe run).

Not a test module (no test_ prefix), so unittest discovery does not collect it.
"""

from __future__ import annotations

import json
from typing import Any

JZR_TOOLS = ["mcp__jzr__video_gif", "mcp__jzr__video_info", "mcp__jzr__hash"]
BASE_TOOLS = ["Task", "Bash", "Edit", "Read", "Skill", "Write", "WebFetch"]
OTHER_SKILLS = ["deep-research", "design", "debug", "verify"]
JZR_SKILLS = ["jzr-media", "jzr-pdf", "jzr-data", "jzr-system"]  # the four packaged skills


def init_event(
    *,
    skills: list[str] | None = None,
    mcp_servers: list[dict[str, Any]] | None = None,
    tools: list[str] | None = None,
    model: str = "claude-sonnet-5-5",
) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "cwd": "/sandbox/work",
        "session_id": "1bf6f025-3c47-49f2-bfe4-b241acb691ae",
        "tools": list(BASE_TOOLS if tools is None else tools),
        "mcp_servers": list([] if mcp_servers is None else mcp_servers),
        "model": model,
        "permissionMode": "bypassPermissions",
        "claude_code_version": "2.1.332",
        "skills": list(OTHER_SKILLS if skills is None else skills),
        "plugins": [{"name": "cc-plugin-privacy", "path": "builtin"}],
    }


def init_for_arm(arm: str) -> dict[str, Any]:
    """An init event that passes the isolation rules of the given arm."""
    if arm == "baseline":
        return init_event()
    if arm == "skill":
        return init_event(skills=[*JZR_SKILLS, *OTHER_SKILLS])
    if arm == "mcp":
        return init_event(
            tools=BASE_TOOLS + JZR_TOOLS,
            mcp_servers=[{"name": "jzr", "status": "connected", "source": "dynamic"}],
        )
    raise AssertionError(arm)


def assistant_event(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "id": "m1",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": list(blocks),
        },
        "session_id": "1bf6f025-3c47-49f2-bfe4-b241acb691ae",
    }


def assistant_usage_event(
    message_id: str,
    *,
    model: str = "claude-sonnet-5-5",
    input_tokens: int = 2,
    cache_creation: int = 5777,
    cache_read: int = 11837,
    output_tokens: int = 16,
    ephemeral: tuple[int, int] | None = (0, 5777),
    blocks: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """An assistant event as the real stream writes it: the message has an id, a model and a usage.

    `ephemeral` is the (5 minute, 1 hour) split of the cache write that the usage's `cache_creation` object carries;
    None leaves that object out. The same message id may come in several events, one per content block, each
    with the usage known at that moment.
    """
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "output_tokens": output_tokens,
        "service_tier": "standard",
    }
    if ephemeral is not None:
        usage["cache_creation"] = {
            "ephemeral_5m_input_tokens": ephemeral[0],
            "ephemeral_1h_input_tokens": ephemeral[1],
        }
    return {
        "type": "assistant",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": list(blocks) or [{"type": "text", "text": "working on it"}],
            "usage": usage,
        },
        "session_id": "1bf6f025-3c47-49f2-bfe4-b241acb691ae",
    }


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def tool_use(name: str, **input_: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": input_}


def bash(command: str) -> dict[str, Any]:
    return tool_use("Bash", command=command)


def user_tool_result(text: str = "done") -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_x", "content": text}],
        },
    }


def result_event(
    *,
    text: str = "ok",
    is_error: bool = False,
    cost: float | None = 0.0123,
    input_tokens: int = 10,
    cache_creation: int = 2000,
    cache_read: int = 30000,
    output_tokens: int = 150,
    num_turns: int = 3,
    duration_ms: int = 4567,
    model_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if model_usage is None:
        model_usage = {
            "claude-sonnet-5-5": {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cacheReadInputTokens": cache_read,
                "cacheCreationInputTokens": cache_creation,
                "costUSD": cost,
            }
        }
    return {
        "type": "result",
        "subtype": "success",
        "is_error": is_error,
        "result": text,
        "total_cost_usd": cost,
        "num_turns": num_turns,
        "duration_ms": duration_ms,
        "duration_api_ms": 4000,
        "session_id": "1bf6f025-3c47-49f2-bfe4-b241acb691ae",
        "usage": {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output_tokens,
            "service_tier": "standard",
        },
        "modelUsage": model_usage,
        "permission_denials": [],
    }


def to_lines(*events: dict[str, Any]) -> list[str]:
    return [json.dumps(e) for e in events]
