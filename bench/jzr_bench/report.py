"""Summarise run records per task and arm, and render them as Markdown."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from typing import Any

from .arms import ARMS

NONE = "–"

_MEDIANS = {
    "median_input_tokens": "input_tokens",
    "median_cache_creation_input_tokens": "cache_creation_input_tokens",
    "median_cache_read_input_tokens": "cache_read_input_tokens",
    "median_output_tokens": "output_tokens",
    "median_turns": "num_turns",
}


def _median(values: Iterable[Any]) -> float | None:
    present = [v for v in values if v is not None]
    return statistics.median(present) if present else None


def _used_jzr(record: dict) -> bool:
    return bool(record.get("used_jzr_cli") or record.get("used_jzr_skill") or record.get("used_jzr_mcp"))


def summarize(records: Iterable[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record["task"], record["arm"]), []).append(record)

    rows: list[dict] = []
    for (task, arm), group in groups.items():
        isolated = [r for r in group if r.get("isolation_ok")]
        passed = [r for r in isolated if r.get("passed")]
        duration_ms = _median(r.get("duration_ms") for r in isolated)
        row = {
            "task": task,
            "arm": arm,
            "runs": len(group),
            "passed": sum(1 for r in group if r.get("passed")),
            "isolation_failures": sum(1 for r in group if not r.get("isolation_ok")),
            "used_jzr": sum(1 for r in group if _used_jzr(r)),
            "median_cost_usd": _median(r.get("total_cost_usd") for r in isolated),
            "median_cost_passed_usd": _median(r.get("total_cost_usd") for r in passed),
            "cost_vs_baseline": None,
            "median_duration_s": None if duration_ms is None else duration_ms / 1000,
        }
        for key, field in _MEDIANS.items():
            row[key] = _median(r.get(field) for r in isolated)
        rows.append(row)

    baselines = {r["task"]: r["median_cost_passed_usd"] for r in rows if r["arm"] == "baseline"}
    for row in rows:
        base = baselines.get(row["task"])
        mine = row["median_cost_passed_usd"]
        if base and mine is not None:
            row["cost_vs_baseline"] = mine / base
    return rows


def _fmt(value: Any, kind: str = "num") -> str:
    if value is None:
        return NONE
    if kind == "usd":
        return f"${value:.4f}"
    if kind == "ratio":
        return f"{value:.2f}x"
    if kind == "s":
        return f"{value:.1f}"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value):,}"


_COLUMNS = [
    ("arm", "arm", "text"),
    ("passed / runs", None, "passed"),
    ("used jzr", "used_jzr", "num"),
    ("isolation fails", "isolation_failures", "num"),
    ("cost (passed)", "median_cost_passed_usd", "usd"),
    ("vs baseline", "cost_vs_baseline", "ratio"),
    ("cost (all)", "median_cost_usd", "usd"),
    ("input", "median_input_tokens", "num"),
    ("cache write", "median_cache_creation_input_tokens", "num"),
    ("cache read", "median_cache_read_input_tokens", "num"),
    ("output", "median_output_tokens", "num"),
    ("turns", "median_turns", "num"),
    ("time (s)", "median_duration_s", "s"),
]


def render_markdown(rows: list[dict], meta: dict | None = None) -> str:
    out = ["# Benchmark summary", ""]
    if meta:
        for key in ("started_at", "finished_at", "claude_code_version", "jzr_version", "git_commit", "model",
                    "resolved_models", "rounds", "seed", "auth"):  # fmt: skip
            if meta.get(key) not in (None, "", []):
                value = meta[key]
                if isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                out.append(f"- {key.replace('_', ' ')}: {value}")
        out.append("")
    out += [
        "Medians over runs whose isolation check passed. Costs are Claude Code's `total_cost_usd`;",
        '"cost (passed)" counts only runs whose result was correct, and "vs baseline" compares it',
        "with the baseline arm of the same task. Token columns are per run.",
        "",
    ]
    tasks: list[str] = []
    for row in rows:
        if row["task"] not in tasks:
            tasks.append(row["task"])
    for task in tasks:
        out += [f"## {task}", ""]
        out.append("| " + " | ".join(title for title, _, _ in _COLUMNS) + " |")
        out.append("|" + "|".join("---" if kind == "text" else "---:" for _, _, kind in _COLUMNS) + "|")
        task_rows = [r for r in rows if r["task"] == task]
        task_rows.sort(key=lambda r: ARMS.index(r["arm"]) if r["arm"] in ARMS else len(ARMS))
        for row in task_rows:
            cells = []
            for _, key, kind in _COLUMNS:
                if kind == "text":
                    cells.append(str(row[key]))
                elif kind == "passed":
                    cells.append(f"{row['passed']} / {row['runs']}")
                else:
                    cells.append(_fmt(row[key], kind))
            out.append("| " + " | ".join(cells) + " |")
        out.append("")
    return "\n".join(out)
