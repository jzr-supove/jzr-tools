"""Synthetic run records with per-model usage, for the price fit and the report tests.

Prices are USD per token by kind of token, the same shape `jzr_bench.pricing.fit_prices` returns:
{"input", "cache_read", "output"} plus either {"cache_write"} or {"cache_write_5m", "cache_write_1h"}.
A model's cost in a record is computed from them, so a fit over enough varied records must give them back.
"""

from __future__ import annotations

from typing import Any

MODEL = "claude-sonnet-5-5"
OTHER_MODEL = "claude-haiku-4-5"

# Sonnet-like prices with a 1 hour cache write, as in the real records.
PRICES_1H = {"input": 2e-6, "cache_write_1h": 4e-6, "cache_read": 0.1e-6, "output": 10e-6}
# Different prices for a second model, with both cache write kinds in use.
PRICES_5M_1H = {
    "input": 1e-6,
    "cache_write_5m": 1.25e-6,
    "cache_write_1h": 2e-6,
    "cache_read": 0.1e-6,
    "output": 5e-6,
}
# One cache write kind, as when Claude Code reports no ephemeral split.
PRICES_PLAIN = {"input": 3e-6, "cache_write": 3.75e-6, "cache_read": 0.3e-6, "output": 15e-6}

# (input, cache write, cache read, output) token counts; any four of these are linearly independent,
# and so are all six, so a fit over them is unique.
ROWS_4 = [
    (2, 5712, 11837, 4),
    (10, 3000, 20000, 100),
    (5, 500, 50000, 300),
    (3, 8000, 1000, 50),
    (1, 100, 1000, 2000),
    (7, 400, 3000, 10),
]


def tokens_1h(row: tuple[int, int, int, int]) -> dict[str, int]:
    inp, cw, cr, out = row
    return {"input": inp, "cache_write_5m": 0, "cache_write_1h": cw, "cache_read": cr, "output": out}


def tokens_plain(row: tuple[int, int, int, int]) -> dict[str, int]:
    inp, cw, cr, out = row
    return {"input": inp, "cache_write": cw, "cache_read": cr, "output": out}


def cost_of(tokens: dict[str, int], prices: dict[str, float]) -> float:
    return sum(count * prices[kind] for kind, count in tokens.items() if count)


def entry(tokens: dict[str, int], cost: float | None) -> dict[str, Any]:
    """A `modelUsage` entry the way Claude Code writes it.

    `tokens` uses the kinds of `usage_by_kind`; the two ephemeral keys are written only when the
    tokens have a "cache_write_5m" or "cache_write_1h" kind.
    """
    ephemeral = "cache_write_5m" in tokens or "cache_write_1h" in tokens
    written = (
        tokens.get("cache_write_5m", 0) + tokens.get("cache_write_1h", 0) if ephemeral else tokens.get("cache_write", 0)
    )
    result: dict[str, Any] = {
        "inputTokens": tokens.get("input", 0),
        "outputTokens": tokens.get("output", 0),
        "cacheReadInputTokens": tokens.get("cache_read", 0),
        "cacheCreationInputTokens": written,
        "webSearchRequests": 0,
        "costUSD": cost,
        "contextWindow": 1000000,
        "maxOutputTokens": 128000,
        "thinkingTokens": 0,
    }
    if ephemeral:
        result["cacheCreationEphemeral5mInputTokens"] = tokens.get("cache_write_5m", 0)
        result["cacheCreationEphemeral1hInputTokens"] = tokens.get("cache_write_1h", 0)
    return result


AUTO: Any = object()  # `cost` default: the sum of the models' costs


def usage_record(
    per_model: dict[str, tuple[dict[str, int], dict[str, float]]],
    *,
    task: str = "hello",
    arm: str = "baseline",
    round: int = 1,
    passed: bool = True,
    isolation_ok: bool = True,
    cost: float | None = AUTO,
) -> dict[str, Any]:
    """A run record whose models used `tokens` at `prices` ({model: (tokens, prices)}).

    `total_cost_usd` is the sum of the models' costs unless `cost` overrides it (None is a missing cost).
    """
    usage: dict[str, Any] = {}
    total = 0.0
    totals = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    for model, (tokens, prices) in per_model.items():
        model_cost = cost_of(tokens, prices)
        total += model_cost
        usage[model] = entry(tokens, model_cost)
        totals["input"] += tokens.get("input", 0)
        totals["output"] += tokens.get("output", 0)
        totals["cache_read"] += tokens.get("cache_read", 0)
        totals["cache_write"] += (
            tokens.get("cache_write", 0) + tokens.get("cache_write_5m", 0) + tokens.get("cache_write_1h", 0)
        )
    return {
        "run_id": f"r{round:02d}-{task}-{arm}",
        "task": task,
        "arm": arm,
        "round": round,
        "passed": passed,
        "isolation_ok": isolation_ok,
        "total_cost_usd": total if cost is AUTO else cost,
        "input_tokens": totals["input"],
        "cache_creation_input_tokens": totals["cache_write"],
        "cache_read_input_tokens": totals["cache_read"],
        "output_tokens": totals["output"],
        "num_turns": 2,
        "duration_ms": 4000,
        "used_jzr_cli": False,
        "used_jzr_skill": False,
        "used_jzr_mcp": False,
        "model_usage": usage,
    }


def fit_records(
    prices: dict[str, float] = PRICES_1H,
    *,
    model: str = MODEL,
    rows: list[tuple[int, int, int, int]] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """One record per token row, all one model at `prices` (1h cache writes, or plain when `prices` has "cache_write")."""
    make = tokens_plain if "cache_write" in prices else tokens_1h
    return [
        usage_record({model: (make(row), prices)}, round=i + 1, **kwargs)
        for i, row in enumerate(rows if rows is not None else ROWS_4)
    ]
