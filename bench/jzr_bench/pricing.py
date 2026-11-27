"""Prices per kind of token, fitted from the runs themselves, and a run's cost split with them.

Claude Code reports a cost and token counts per model but not the prices it used. Over enough runs the
prices follow from those: cost = sum of tokens of each kind times that kind's price. A fit is used only
when it is unique and reproduces every run's cost, so nothing here comes from a price table.
"""

from __future__ import annotations

from collections.abc import Iterable
from fractions import Fraction

WRITTEN_KINDS = ("input", "cache_write", "cache_write_5m", "cache_write_1h")
KINDS = ("input", "cache_write_5m", "cache_write_1h", "cache_write", "cache_read", "output")
TOLERANCE_USD = 1e-6

_EPHEMERAL = {
    "cache_write_5m": "cacheCreationEphemeral5mInputTokens",
    "cache_write_1h": "cacheCreationEphemeral1hInputTokens",
}


def _count(entry: dict, key: str) -> int:
    value = entry.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def usage_by_kind(entry: dict) -> dict[str, int]:
    """One `modelUsage` entry as token counts by kind.

    Cache writes are two kinds (5 minutes, 1 hour) when the entry reports them apart, else one.
    """
    kinds = {
        "input": _count(entry, "inputTokens"),
        "cache_read": _count(entry, "cacheReadInputTokens"),
        "output": _count(entry, "outputTokens"),
    }
    if any(key in entry for key in _EPHEMERAL.values()):
        for kind, key in _EPHEMERAL.items():
            kinds[kind] = _count(entry, key)
    else:
        kinds["cache_write"] = _count(entry, "cacheCreationInputTokens")
    return kinds


def _solve(matrix: list[list[Fraction]], right: list[Fraction]) -> list[Fraction] | None:
    """The unique solution of a square system, or None when there is not exactly one."""
    size = len(right)
    rows = [[*matrix[i], right[i]] for i in range(size)]
    for column in range(size):
        pivot = next((r for r in range(column, size) if rows[r][column] != 0), None)
        if pivot is None:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        lead = rows[column][column]
        rows[column] = [value / lead for value in rows[column]]
        for other in range(size):
            if other != column and rows[other][column] != 0:
                factor = rows[other][column]
                rows[other] = [a - factor * b for a, b in zip(rows[other], rows[column], strict=True)]
    return [rows[i][size] for i in range(size)]


def _fit(rows: list[tuple[dict[str, int], float]]) -> dict[str, float] | None:
    kinds = [kind for kind in KINDS if any(tokens.get(kind) for tokens, _ in rows)]
    if not kinds:
        return None
    counts = [[Fraction(tokens.get(kind, 0)) for kind in kinds] for tokens, _ in rows]
    costs = [Fraction(str(cost)) for _, cost in rows]
    size = len(kinds)
    normal = [[sum(row[i] * row[j] for row in counts) for j in range(size)] for i in range(size)]
    right = [sum(row[i] * cost for row, cost in zip(counts, costs, strict=True)) for i in range(size)]
    solution = _solve(normal, right)
    if solution is None:
        return None
    for row, cost in zip(counts, costs, strict=True):
        if abs(sum(a * b for a, b in zip(row, solution, strict=True)) - cost) > TOLERANCE_USD:
            return None
    return {kind: float(price) for kind, price in zip(kinds, solution, strict=True)}


def fit_prices(records: Iterable[dict]) -> dict[str, dict[str, float]]:
    """model -> kind -> USD per token, for every model whose runs give one exact set of prices."""
    per_model: dict[str, list[tuple[dict[str, int], float]]] = {}
    for record in records:
        usage = record.get("model_usage")
        if not isinstance(usage, dict):
            continue
        for model, entry in usage.items():
            if not isinstance(entry, dict):
                continue
            cost = entry.get("costUSD")
            if isinstance(cost, bool) or not isinstance(cost, (int, float)):
                continue
            per_model.setdefault(model, []).append((usage_by_kind(entry), cost))
    prices = {}
    for model, rows in per_model.items():
        fitted = _fit(rows)
        if fitted is not None:
            prices[model] = fitted
    return prices


def split_cost(record: dict, prices: dict[str, dict[str, float]]) -> dict[str, float] | None:
    """A run's cost as written (uncached input and cache writes), re-read and output dollars.

    None when the run has no per-model usage or used tokens that have no price.
    """
    usage = record.get("model_usage")
    if not isinstance(usage, dict) or not usage:
        return None
    parts = {"written": 0.0, "reread": 0.0, "output": 0.0}
    for model, entry in usage.items():
        model_prices = prices.get(model)
        if model_prices is None or not isinstance(entry, dict):
            return None
        for kind, count in usage_by_kind(entry).items():
            if not count:
                continue
            price = model_prices.get(kind)
            if price is None:
                return None
            part = "written" if kind in WRITTEN_KINDS else "reread" if kind == "cache_read" else "output"
            parts[part] += count * price
    return parts
