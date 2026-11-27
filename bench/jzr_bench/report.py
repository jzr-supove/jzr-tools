"""Summarise run records per task and arm, and render them as Markdown."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from typing import Any

from .arms import ARMS, MACHINES
from .pricing import fit_prices, split_cost

NONE = "–"
HELLO = "hello"
FULL = "full"
_MACHINE_SUFFIX = {"full": "", "bare": ", bare machine"}
BASELINE = "baseline"
_PARTS = {"median_written_usd": "written", "median_reread_usd": "reread", "median_output_usd": "output"}

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


def summarize(records: Iterable[dict], prices: dict | None = None) -> list[dict]:
    """One row per task and arm. `prices` (see pricing.fit_prices) are fitted from the records when not given."""
    records = list(records)
    if prices is None:
        prices = fit_prices(records)
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record.get("machine") or FULL, record["task"], record["arm"]), []).append(record)

    rows: list[dict] = []
    for (machine, task, arm), group in groups.items():
        isolated = [r for r in group if r.get("isolation_ok")]
        passed = [r for r in isolated if r.get("passed")]
        duration_ms = _median(r.get("duration_ms") for r in isolated)
        passed_costs = [r["total_cost_usd"] for r in passed if r.get("total_cost_usd") is not None]
        splits = [s for s in (split_cost(r, prices) for r in passed) if s is not None]
        row = {
            "machine": machine,
            "task": task,
            "arm": arm,
            "runs": len(group),
            "passed": sum(1 for r in group if r.get("passed")),
            "isolation_failures": sum(1 for r in group if not r.get("isolation_ok")),
            "used_jzr": sum(1 for r in group if _used_jzr(r)),
            "median_cost_usd": _median(r.get("total_cost_usd") for r in isolated),
            "median_cost_passed_usd": _median(r.get("total_cost_usd") for r in passed),
            "min_cost_passed_usd": min(passed_costs, default=None),
            "max_cost_passed_usd": max(passed_costs, default=None),
            "cost_vs_baseline": None,
            "cost_minus_hello_usd": None,
            "minus_hello_vs_baseline": None,
            "median_duration_s": None if duration_ms is None else duration_ms / 1000,
        }
        for key, field in _MEDIANS.items():
            row[key] = _median(r.get(field) for r in isolated)
        for key, part in _PARTS.items():
            row[key] = _median(s[part] for s in splits)
        rows.append(row)

    # A baseline and a hello belong to their own machine: nothing is borrowed across machines.
    hellos = {(r["machine"], r["arm"]): r["median_cost_passed_usd"] for r in rows if r["task"] == HELLO}
    for row in rows:
        hello = hellos.get((row["machine"], row["arm"]))
        mine = row["median_cost_passed_usd"]
        if row["task"] != HELLO and hello is not None and mine is not None:
            row["cost_minus_hello_usd"] = mine - hello

    baselines = {(r["machine"], r["task"]): r for r in rows if r["arm"] == BASELINE}
    for row in rows:
        base = baselines.get((row["machine"], row["task"]))
        if base is None:
            continue
        row["cost_vs_baseline"] = _ratio(row["median_cost_passed_usd"], base["median_cost_passed_usd"])
        row["minus_hello_vs_baseline"] = _ratio(row["cost_minus_hello_usd"], base["cost_minus_hello_usd"])
    return rows


def _ratio(mine: float | None, base: float | None) -> float | None:
    """mine / base, or None when either is unknown or the base is not positive."""
    if mine is None or base is None or base <= 0:
        return None
    return mine / base


def _sum(values: Iterable[float | None]) -> float | None:
    values = list(values)
    return None if any(v is None for v in values) else sum(values)


def _arm_order(arm: str) -> int:
    return ARMS.index(arm) if arm in ARMS else len(ARMS)


def overall(rows: list[dict], machine: str = FULL) -> list[dict]:
    """One row per arm of one machine: sums over the tasks (not hello) that every arm passed at least once."""
    rows = [r for r in rows if (r.get("machine") or FULL) == machine]
    arms = sorted({r["arm"] for r in rows}, key=_arm_order)
    cells = {(r["task"], r["arm"]): r for r in rows}
    tasks: list[str] = []
    for row in rows:
        task = row["task"]
        if task == HELLO or task in tasks:
            continue
        if all(cells.get((task, arm), {}).get("median_cost_passed_usd") is not None for arm in arms):
            tasks.append(task)
    if not tasks:
        return []

    def hello_cost(arm: str) -> float | None:
        return cells.get((HELLO, arm), {}).get("median_cost_passed_usd")

    result = []
    for arm in arms:
        mine = [cells[(task, arm)] for task in tasks]
        hello, base_hello = hello_cost(arm), hello_cost(BASELINE)
        result.append(
            {
                "arm": arm,
                "tasks": list(tasks),
                "cost_usd": _sum(r["median_cost_passed_usd"] for r in mine),
                "cost_vs_baseline": None,
                "minus_hello_usd": _sum(r["cost_minus_hello_usd"] for r in mine),
                "minus_hello_vs_baseline": None,
                "written_usd": _sum(r["median_written_usd"] for r in mine),
                "reread_usd": _sum(r["median_reread_usd"] for r in mine),
                "output_usd": _sum(r["median_output_usd"] for r in mine),
                "unused_session_usd": None if hello is None or base_hello is None else hello - base_hello,
            }
        )
    base = next((r for r in result if r["arm"] == BASELINE), None)
    if base is not None:
        for row in result:
            row["cost_vs_baseline"] = _ratio(row["cost_usd"], base["cost_usd"])
            row["minus_hello_vs_baseline"] = _ratio(row["minus_hello_usd"], base["minus_hello_usd"])
    return result


def _fmt(value: Any, kind: str = "num") -> str:
    if value is None:
        return NONE
    if kind == "usd":
        return f"-${-value:.4f}" if value < 0 else f"${value:.4f}"
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
    ("range", None, "range"),
    ("vs baseline", "cost_vs_baseline", "ratio"),
    ("minus hello", "cost_minus_hello_usd", "usd"),
    ("minus hello vs baseline", "minus_hello_vs_baseline", "ratio"),
    ("cost (all)", "median_cost_usd", "usd"),
    ("written $", "median_written_usd", "usd"),
    ("re-read $", "median_reread_usd", "usd"),
    ("output $", "median_output_usd", "usd"),
    ("input", "median_input_tokens", "num"),
    ("cache write", "median_cache_creation_input_tokens", "num"),
    ("cache read", "median_cache_read_input_tokens", "num"),
    ("output", "median_output_tokens", "num"),
    ("turns", "median_turns", "num"),
    ("time (s)", "median_duration_s", "s"),
]


_OVERALL_COLUMNS = [
    ("arm", "arm", "text"),
    ("cost", "cost_usd", "usd"),
    ("vs baseline", "cost_vs_baseline", "ratio"),
    ("minus hello", "minus_hello_usd", "usd"),
    ("minus hello vs baseline", "minus_hello_vs_baseline", "ratio"),
    ("written $", "written_usd", "usd"),
    ("re-read $", "reread_usd", "usd"),
    ("output $", "output_usd", "usd"),
    ("unused, per session", "unused_session_usd", "usd"),
]

_KIND_NAMES = {
    "input": "input",
    "cache_write_5m": "cache write (5 min)",
    "cache_write_1h": "cache write (1 h)",
    "cache_write": "cache write",
    "cache_read": "cache read",
    "output": "output",
}


def _table(columns: list, rows: list[dict]) -> list[str]:
    out = ["| " + " | ".join(title for title, _, _ in columns) + " |"]
    out.append("|" + "|".join("---" if kind == "text" else "---:" for _, _, kind in columns) + "|")
    for row in rows:
        cells = []
        for _, key, kind in columns:
            if kind == "text":
                cells.append(str(row[key]))
            elif kind == "passed":
                cells.append(f"{row['passed']} / {row['runs']}")
            elif kind == "range":
                low, high = row.get("min_cost_passed_usd"), row.get("max_cost_passed_usd")
                cells.append(NONE if low is None or high is None else f"{_fmt(low, 'usd')}{NONE}{_fmt(high, 'usd')}")
            else:
                cells.append(_fmt(row.get(key), kind))
        out.append("| " + " | ".join(cells) + " |")
    return out


def _price_lines(prices: dict) -> list[str]:
    if not prices:
        return ["The written, re-read and output dollars are left out: no prices could be fitted from these runs.", ""]
    out = ["Prices fitted from these runs, in USD per million tokens:", ""]
    for model, kinds in prices.items():
        known = [f"{_KIND_NAMES.get(kind, kind)} {kinds[kind] * 1e6:.4g}" for kind in _KIND_NAMES if kind in kinds]
        out.append(f"- {model}: {', '.join(known)}")
    return [*out, ""]


def render_markdown(rows: list[dict], meta: dict | None = None, prices: dict | None = None) -> str:
    out = ["# Benchmark summary", ""]
    if meta:
        for key in ("started_at", "finished_at", "claude_code_version", "jzr_version", "git_commit", "model",
                    "resolved_models", "rounds", "seed", "auth", "private_tmp", "bare_arms"):  # fmt: skip
            if meta.get(key) not in (None, "", []):
                value = meta[key]
                if isinstance(value, bool):
                    value = "true" if value else "false"
                elif isinstance(value, list):
                    value = ", ".join(str(v) for v in value)
                out.append(f"- {key.replace('_', ' ')}: {value}")
        for task, folder in (meta.get("sources") or {}).items():
            out.append(f"- {task}: runs from {folder}")
        out.append("")
    out += [
        "Medians over runs whose isolation check passed. Costs are Claude Code's `total_cost_usd`;",
        '"cost (passed)" counts only runs whose result was correct, "range" is its lowest and highest',
        'run, and "vs baseline" compares it with the baseline arm of the same task. "minus hello" is',
        "that cost less the same arm's `hello` cost: what the job cost on top of starting a session.",
        '"written $" (uncached input and cache writes), "re-read $" (cache reads) and "output $" split',
        "the cost of the passed runs by kind of token. Token columns are per run.",
        "",
    ]
    if prices is not None:
        out += _price_lines(prices)
    for machine in MACHINES:
        totals = overall(rows, machine)
        if not totals:
            continue
        out += [
            f"## All tasks{_MACHINE_SUFFIX[machine]}",
            "",
            f"Sums of the per-task values over {', '.join(totals[0]['tasks'])}: the tasks other than `hello`",
            'that every arm passed at least once. "unused, per session" is the arm\'s `hello` cost less the',
            "baseline's: what the arm adds to a session that never uses jzr.",
            "",
            *_table(_OVERALL_COLUMNS, totals),
            "",
        ]
    tasks: list[str] = []
    for row in rows:
        if row["task"] not in tasks:
            tasks.append(row["task"])
    for task in tasks:
        for machine in MACHINES:
            task_rows = [r for r in rows if r["task"] == task and (r.get("machine") or FULL) == machine]
            if task_rows:
                task_rows.sort(key=lambda r: _arm_order(r["arm"]))
                out += [f"## {task}{_MACHINE_SUFFIX[machine]}", "", *_table(_COLUMNS, task_rows), ""]
    return "\n".join(out)
