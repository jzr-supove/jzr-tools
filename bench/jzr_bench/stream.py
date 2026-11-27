"""Read Claude Code's stream-json output: usage, tool calls, jzr use and isolation."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .arms import SKILLS

JZR_MCP_PREFIX = "mcp__jzr__"
OLD_SKILL = "jzr"


@dataclass
class StreamMetrics:
    init: dict | None = None
    has_result: bool = False
    is_error: bool = True
    final_text: str = ""
    cost_usd: float | None = None
    input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    output_tokens: int | None = None
    num_turns: int | None = None
    duration_ms: int | None = None
    model_usage: dict = field(default_factory=dict)
    auth_failed: bool = False  # the API refused the credential (401 / authentication_error)
    tool_calls: dict[str, int] = field(default_factory=dict)
    bash_commands: list[str] = field(default_factory=list)
    skills_invoked: list[str] = field(default_factory=list)
    used_jzr_cli: bool = False
    used_jzr_skill: bool = False
    used_jzr_mcp: bool = False


def _bare(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def is_jzr_skill(name: str) -> bool:
    """One of jzr's skills, or the single `jzr` skill of earlier versions; also with a plugin prefix."""
    return _bare(name) == OLD_SKILL or _bare(name) in SKILLS


def _events(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def parse_stream(lines: Iterable[str]) -> StreamMetrics:
    m = StreamMetrics()
    for event in _events(lines):
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            m.init = event
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = str(block.get("name", ""))
                tool_input = block.get("input") or {}
                m.tool_calls[name] = m.tool_calls.get(name, 0) + 1
                if name == "Bash" and isinstance(tool_input.get("command"), str):
                    m.bash_commands.append(tool_input["command"])
                elif name == "Skill" and isinstance(tool_input.get("skill"), str):
                    m.skills_invoked.append(tool_input["skill"])
        elif kind == "result":
            usage = event.get("usage") or {}
            m.has_result = True
            m.is_error = bool(event.get("is_error"))
            m.final_text = event.get("result") or ""
            m.cost_usd = event.get("total_cost_usd")
            m.input_tokens = usage.get("input_tokens")
            m.cache_creation_input_tokens = usage.get("cache_creation_input_tokens")
            m.cache_read_input_tokens = usage.get("cache_read_input_tokens")
            m.output_tokens = usage.get("output_tokens")
            m.num_turns = event.get("num_turns")
            m.duration_ms = event.get("duration_ms")
            m.model_usage = event.get("modelUsage") or {}
            m.auth_failed = (
                event.get("api_error_status") == 401 or event.get("api_error_type") == "authentication_error"
            )

    m.used_jzr_cli = any(mentions_jzr_command(c) for c in m.bash_commands)
    m.used_jzr_skill = any(is_jzr_skill(s) for s in m.skills_invoked)
    m.used_jzr_mcp = any(name.startswith(JZR_MCP_PREFIX) for name in m.tool_calls)
    return m


# Separators between simple shell commands, including command substitution.
_SEPARATORS = re.compile(r"\$\(|&&|\|\||[;\n|&()`]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Programs that run the command given after their own options.
_WRAPPERS = {"env", "sudo", "time", "exec", "nohup", "command", "xargs", "timeout", "nice", "stdbuf"}
# Package runners that take the program anywhere in their arguments (uvx --from jzr-tools jzr ...).
_RUNNERS = {"uvx", "uv", "pipx"}


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1]


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def mentions_jzr_command(command: str) -> bool:
    """True when some simple command in the shell text runs jzr as its program."""
    for segment in _SEPARATORS.split(command):
        words = _words(segment)
        i = 0
        while i < len(words):
            word = words[i]
            if _ASSIGNMENT.match(word):
                i += 1
                continue
            name = _basename(word)
            if name == "jzr":
                return True
            if name in _RUNNERS:
                if any(_basename(w) == "jzr" for w in words[i + 1 :]):
                    return True
                break
            if name in _WRAPPERS:
                i += 1
                while i < len(words) and (
                    words[i].startswith("-") or _ASSIGNMENT.match(words[i]) or re.fullmatch(r"[\d.]+[smhd]?", words[i])
                ):
                    i += 1
                continue
            break
    return False


def check_isolation(arm: str, init: dict | None) -> list[str]:
    """What is wrong with the run's setup for this arm, from Claude Code's init event; [] if nothing."""
    if init is None:
        return ["no init event: cannot tell what the agent could see"]
    skills = [str(s) for s in init.get("skills") or []]
    tools = [str(t) for t in init.get("tools") or []]
    servers = [s for s in init.get("mcp_servers") or [] if isinstance(s, dict)]

    old_skill = any(_bare(s) == OLD_SKILL for s in skills)
    seen = {_bare(s) for s in skills if _bare(s) in SKILLS}
    jzr_tools = [t for t in tools if t.startswith(JZR_MCP_PREFIX)]
    jzr_servers = [s for s in servers if s.get("name") == "jzr"]

    problems: list[str] = []
    if old_skill:
        problems.append(f"{arm} arm can see the old jzr skill")
    if arm == "baseline":
        if seen:
            problems.append(f"baseline can see jzr skills ({', '.join(sorted(seen))})")
        if jzr_servers:
            problems.append("baseline has a jzr MCP server")
        if jzr_tools:
            problems.append(f"baseline has jzr MCP tools ({len(jzr_tools)})")
    elif arm == "skill":
        missing = SKILLS - seen
        if missing:
            problems.append(f"skill arm does not show {', '.join(sorted(missing))}")
        if jzr_tools:
            problems.append(f"skill arm has jzr MCP tools ({len(jzr_tools)})")
        if jzr_servers:
            problems.append("skill arm has a jzr MCP server")
    elif arm == "mcp":
        if not any(s.get("status") == "connected" for s in jzr_servers):
            status = ", ".join(str(s.get("status")) for s in jzr_servers) or "absent"
            problems.append(f"mcp arm: the jzr server is not connected ({status})")
        if not jzr_tools:
            problems.append("mcp arm has no mcp__jzr__ tools")
        if seen:
            problems.append(f"mcp arm can see jzr skills ({', '.join(sorted(seen))})")
    else:
        problems.append(f"unknown arm {arm!r}")
    return problems
