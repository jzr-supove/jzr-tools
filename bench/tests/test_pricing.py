"""Offline tests for jzr_bench.pricing: token counts by kind, the price fit and the cost split."""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import usage_fixtures as uf  # noqa: E402
from jzr_bench.pricing import fit_prices, split_cost, usage_by_kind  # noqa: E402

# The model_usage entry of a real run (bench/results/20261127T073019Z).
REAL_ENTRY = {
    "inputTokens": 2,
    "outputTokens": 4,
    "cacheReadInputTokens": 11837,
    "cacheCreationInputTokens": 5712,
    "webSearchRequests": 0,
    "costUSD": 0.024075700000000002,
    "contextWindow": 1000000,
    "maxOutputTokens": 128000,
    "thinkingTokens": 0,
    "cacheCreationEphemeral5mInputTokens": 0,
    "cacheCreationEphemeral1hInputTokens": 5712,
    "canonicalModel": "claude-sonnet-5-5",
    "provider": "firstParty",
    "costBasis": "list",
}

# Rows of (input, cache write 5 min, cache write 1 h, cache read, output) with all five kinds in use.
ROWS_5 = [
    (2, 1000, 5000, 10000, 4),
    (10, 0, 3000, 20000, 100),
    (5, 2000, 0, 50000, 300),
    (3, 500, 8000, 1000, 50),
    (1, 300, 100, 1000, 2000),
    (7, 4000, 400, 3000, 10),
]


def tokens_5(row: tuple[int, int, int, int, int]) -> dict[str, int]:
    inp, w5, w1, cr, out = row
    return {"input": inp, "cache_write_5m": w5, "cache_write_1h": w1, "cache_read": cr, "output": out}


class PricesCloseMixin(unittest.TestCase):
    def assert_prices(self, actual: dict[str, float], expected: dict[str, float]) -> None:
        self.assertEqual(set(actual), set(expected))
        for kind, price in expected.items():
            self.assertTrue(
                math.isclose(actual[kind], price, rel_tol=1e-6, abs_tol=1e-15),
                f"{kind}: {actual[kind]!r} != {price!r}",
            )


class UsageByKindTest(unittest.TestCase):
    def test_real_entry_with_ephemeral_keys(self) -> None:
        self.assertEqual(
            usage_by_kind(REAL_ENTRY),
            {"input": 2, "cache_read": 11837, "output": 4, "cache_write_5m": 0, "cache_write_1h": 5712},
        )

    def test_both_ephemeral_kinds_are_kept_apart(self) -> None:
        entry = dict(REAL_ENTRY)
        entry["cacheCreationEphemeral5mInputTokens"] = 700
        entry["cacheCreationEphemeral1hInputTokens"] = 5012
        kinds = usage_by_kind(entry)
        self.assertEqual(kinds["cache_write_5m"], 700)
        self.assertEqual(kinds["cache_write_1h"], 5012)
        self.assertNotIn("cache_write", kinds)

    def test_a_single_cache_write_kind_without_the_ephemeral_keys(self) -> None:
        entry = {k: v for k, v in REAL_ENTRY.items() if not k.startswith("cacheCreationEphemeral")}
        self.assertEqual(
            usage_by_kind(entry),
            {"input": 2, "cache_read": 11837, "output": 4, "cache_write": 5712},
        )

    def test_one_ephemeral_key_is_enough_to_split_the_cache_write(self) -> None:
        entry = {k: v for k, v in REAL_ENTRY.items() if k != "cacheCreationEphemeral5mInputTokens"}
        kinds = usage_by_kind(entry)
        self.assertEqual(kinds["cache_write_1h"], 5712)
        self.assertNotIn("cache_write", kinds)

    def test_the_ephemeral_split_replaces_the_total(self) -> None:
        # the total is not a price kind of its own once the split is there
        kinds = usage_by_kind(uf.entry(uf.tokens_1h((1, 10, 20, 3)), 0.0))
        self.assertEqual(kinds, {"input": 1, "cache_read": 20, "output": 3, "cache_write_5m": 0, "cache_write_1h": 10})

    def test_ignores_everything_that_is_not_a_token_count(self) -> None:
        kinds = usage_by_kind(REAL_ENTRY)
        self.assertEqual(
            set(kinds), {"input", "cache_read", "output", "cache_write_5m", "cache_write_1h"}
        )  # no thinkingTokens, webSearchRequests, contextWindow, costUSD


class FitPricesTest(PricesCloseMixin):
    def test_recovers_known_prices_exactly(self) -> None:
        fitted = fit_prices(uf.fit_records())
        self.assertEqual(set(fitted), {uf.MODEL})
        # 5 min cache writes are zero in every row, so that kind is left out
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_1H)

    def test_prices_are_per_token_not_per_million(self) -> None:
        fitted = fit_prices(uf.fit_records())[uf.MODEL]
        self.assertLess(fitted["output"], 1e-3)
        self.assertGreater(fitted["output"], 1e-7)

    def test_kinds_without_tokens_in_any_row_are_left_out(self) -> None:
        rows = [(i, w, r, 0) for i, w, r, _ in uf.ROWS_4]  # output never used
        fitted = fit_prices(uf.fit_records(rows=rows))
        self.assert_prices(fitted[uf.MODEL], {k: v for k, v in uf.PRICES_1H.items() if k != "output"})

    def test_a_kind_used_in_one_row_only_stays_in(self) -> None:
        rows = [(i, w, r, 0) for i, w, r, _ in uf.ROWS_4]
        rows[3] = uf.ROWS_4[3]  # one row has output tokens
        fitted = fit_prices(uf.fit_records(rows=rows))
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_1H)

    def test_both_cache_write_kinds_when_the_ephemeral_keys_are_present(self) -> None:
        records = [
            uf.usage_record({uf.MODEL: (tokens_5(row), uf.PRICES_5M_1H)}, round=i + 1) for i, row in enumerate(ROWS_5)
        ]
        fitted = fit_prices(records)
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_5M_1H)

    def test_one_cache_write_kind_when_the_ephemeral_keys_are_absent(self) -> None:
        fitted = fit_prices(uf.fit_records(uf.PRICES_PLAIN))
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_PLAIN)
        self.assertNotIn("cache_write_1h", fitted[uf.MODEL])
        self.assertNotIn("cache_write_5m", fitted[uf.MODEL])

    def test_accepts_any_iterable(self) -> None:
        fitted = fit_prices(r for r in uf.fit_records())
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_1H)

    def test_no_records_no_prices(self) -> None:
        self.assertEqual(fit_prices([]), {})

    def test_float_noise_in_cost_usd_is_accepted(self) -> None:
        # the real run's cost is 0.024075700000000002: 2 in, 5712 cache write, 11837 cache read, 4 out
        records = uf.fit_records()
        self.assertEqual(records[0]["model_usage"][uf.MODEL]["inputTokens"], 2)
        records[0]["model_usage"][uf.MODEL]["costUSD"] = REAL_ENTRY["costUSD"]
        self.assertNotEqual(REAL_ENTRY["costUSD"], 0.0240757)  # it really is the noisy float
        fitted = fit_prices(records)
        self.assertIn(uf.MODEL, fitted)
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_1H)

    def test_costs_off_by_less_than_the_tolerance_are_accepted(self) -> None:
        records = uf.fit_records()
        for i, record in enumerate(records):
            record["model_usage"][uf.MODEL]["costUSD"] += 1e-8 * (1 if i % 2 else -1)
        self.assertIn(uf.MODEL, fit_prices(records))

    def test_one_cost_off_by_a_thousandth_of_a_dollar_gives_no_prices(self) -> None:
        # with more rows than kinds the fit is over-determined, so a wrong cost cannot be absorbed
        rows = [*uf.ROWS_4, (4, 2500, 7000, 40), (9, 900, 9000, 900)]
        self.assertIn(uf.MODEL, fit_prices(uf.fit_records(rows=rows)))  # the same rows are fine when exact
        for bad in range(len(rows)):
            with self.subTest(row=bad):
                records = uf.fit_records(rows=rows)
                records[bad]["model_usage"][uf.MODEL]["costUSD"] += 0.001
                self.assertEqual(fit_prices(records), {})

    def test_as_many_rows_as_kinds_is_enough_when_they_are_independent(self) -> None:
        # 4 kinds, 4 independent rows: the one solution is the known prices
        records = uf.fit_records(rows=uf.ROWS_4[:4])
        fitted = fit_prices(records)
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_1H)

    def test_identical_rows_are_not_unique(self) -> None:
        records = uf.fit_records(rows=[uf.ROWS_4[0]] * 6)
        self.assertEqual(fit_prices(records), {})

    def test_too_few_rows_are_not_unique(self) -> None:
        for count in (1, 2, 3):
            with self.subTest(rows=count):
                self.assertEqual(fit_prices(uf.fit_records(rows=uf.ROWS_4[:count])), {})

    def test_proportional_kinds_are_not_unique(self) -> None:
        # cache reads are always ten times the cache writes: their prices cannot be told apart
        rows = [(i, w, 10 * w, o) for i, w, _, o in uf.ROWS_4]
        self.assertEqual(fit_prices(uf.fit_records(rows=rows)), {})

    def test_isolation_and_pass_do_not_matter_for_prices(self) -> None:
        records = uf.fit_records()
        records[0]["passed"] = False
        records[1]["isolation_ok"] = False
        records[2]["passed"] = False
        records[2]["isolation_ok"] = False
        # even with only four rows, two of them from failed or non-isolated runs
        self.assert_prices(fit_prices(records[:4])[uf.MODEL], uf.PRICES_1H)
        self.assert_prices(fit_prices(records)[uf.MODEL], uf.PRICES_1H)

    def test_records_without_model_usage_are_ignored(self) -> None:
        records = uf.fit_records()
        without = {"run_id": "x", "task": "t", "arm": "a", "total_cost_usd": 123.0}
        records += [without, {**without, "model_usage": {}}, {**without, "model_usage": None}]
        self.assert_prices(fit_prices(records)[uf.MODEL], uf.PRICES_1H)

    def test_a_cost_of_none_is_ignored_not_read_as_zero(self) -> None:
        records = uf.fit_records()
        extra = uf.usage_record({uf.MODEL: (uf.tokens_1h((4, 4000, 40000, 400)), uf.PRICES_1H)}, round=9)
        extra["model_usage"][uf.MODEL]["costUSD"] = None
        self.assert_prices(fit_prices([*records, extra])[uf.MODEL], uf.PRICES_1H)

    def test_a_missing_cost_usd_key_is_ignored_too(self) -> None:
        records = uf.fit_records()
        extra = uf.usage_record({uf.MODEL: (uf.tokens_1h((4, 4000, 40000, 400)), uf.PRICES_1H)}, round=9)
        del extra["model_usage"][uf.MODEL]["costUSD"]
        self.assert_prices(fit_prices([*records, extra])[uf.MODEL], uf.PRICES_1H)

    def test_zero_cost_is_a_cost(self) -> None:
        # a zero costUSD is a number, so it takes part in the fit and makes it inexact
        records = uf.fit_records(rows=[*uf.ROWS_4, (4, 2500, 7000, 40)])
        records[-1]["model_usage"][uf.MODEL]["costUSD"] = 0.0
        self.assertEqual(fit_prices(records), {})

    def test_two_models_are_fitted_independently(self) -> None:
        records = [
            uf.usage_record(
                {
                    uf.MODEL: (uf.tokens_1h(row), uf.PRICES_1H),
                    uf.OTHER_MODEL: (tokens_5(ROWS_5[i]), uf.PRICES_5M_1H),
                },
                round=i + 1,
            )
            for i, row in enumerate(uf.ROWS_4)
        ]
        fitted = fit_prices(records)
        self.assertEqual(set(fitted), {uf.MODEL, uf.OTHER_MODEL})
        self.assert_prices(fitted[uf.MODEL], uf.PRICES_1H)
        self.assert_prices(fitted[uf.OTHER_MODEL], uf.PRICES_5M_1H)

    def test_a_model_without_a_unique_fit_does_not_take_the_others_down(self) -> None:
        records = [
            uf.usage_record(
                {
                    uf.MODEL: (uf.tokens_1h(row), uf.PRICES_1H),
                    uf.OTHER_MODEL: (uf.tokens_1h(uf.ROWS_4[0]), uf.PRICES_1H),  # same tokens every time
                },
                round=i + 1,
            )
            for i, row in enumerate(uf.ROWS_4)
        ]
        fitted = fit_prices(records)
        self.assertIn(uf.MODEL, fitted)
        self.assertNotIn(uf.OTHER_MODEL, fitted)

    def test_a_model_seen_in_few_records_only_is_fitted_on_its_own_rows(self) -> None:
        # the second model appears in 2 records only: not enough for it, plenty for the first
        records = uf.fit_records()
        for record in records[:2]:
            record["model_usage"][uf.OTHER_MODEL] = uf.entry(
                uf.tokens_1h((1, 2, 3, 4)), uf.cost_of(uf.tokens_1h((1, 2, 3, 4)), uf.PRICES_1H)
            )
        fitted = fit_prices(records)
        self.assertEqual(set(fitted), {uf.MODEL})


class SplitCostTest(PricesCloseMixin):
    PRICES: dict[str, dict[str, float]] = {uf.MODEL: uf.PRICES_1H}

    def record(self, **kwargs: Any) -> dict[str, Any]:
        return uf.usage_record({uf.MODEL: (uf.tokens_1h((2, 5712, 11837, 4)), uf.PRICES_1H)}, **kwargs)

    def test_written_is_uncached_input_plus_cache_writes(self) -> None:
        parts = split_cost(self.record(), self.PRICES)
        assert parts is not None
        self.assertEqual(set(parts), {"written", "reread", "output"})
        self.assertAlmostEqual(parts["written"], 2 * 2e-6 + 5712 * 4e-6, places=12)
        self.assertAlmostEqual(parts["reread"], 11837 * 0.1e-6, places=12)
        self.assertAlmostEqual(parts["output"], 4 * 10e-6, places=12)

    def test_the_parts_add_up_to_the_cost_of_the_record(self) -> None:
        record = self.record()
        parts = split_cost(record, self.PRICES)
        assert parts is not None
        self.assertAlmostEqual(sum(parts.values()), record["total_cost_usd"], places=9)
        self.assertAlmostEqual(sum(parts.values()), 0.0240757, places=9)

    def test_both_cache_write_kinds_count_as_written(self) -> None:
        tokens = tokens_5((10, 1000, 2000, 5000, 20))
        record = uf.usage_record({uf.MODEL: (tokens, uf.PRICES_5M_1H)})
        parts = split_cost(record, {uf.MODEL: uf.PRICES_5M_1H})
        assert parts is not None
        self.assertAlmostEqual(parts["written"], 10 * 1e-6 + 1000 * 1.25e-6 + 2000 * 2e-6, places=12)
        self.assertAlmostEqual(parts["reread"], 5000 * 0.1e-6, places=12)
        self.assertAlmostEqual(parts["output"], 20 * 5e-6, places=12)

    def test_a_single_cache_write_kind_counts_as_written(self) -> None:
        record = uf.usage_record({uf.MODEL: (uf.tokens_plain((10, 1000, 5000, 20)), uf.PRICES_PLAIN)})
        parts = split_cost(record, {uf.MODEL: uf.PRICES_PLAIN})
        assert parts is not None
        self.assertAlmostEqual(parts["written"], 10 * 3e-6 + 1000 * 3.75e-6, places=12)
        self.assertAlmostEqual(parts["reread"], 5000 * 0.3e-6, places=12)
        self.assertAlmostEqual(parts["output"], 20 * 15e-6, places=12)

    def test_summed_over_the_models_of_the_record(self) -> None:
        a = (uf.tokens_1h((2, 5712, 11837, 4)), uf.PRICES_1H)
        b = (tokens_5((10, 1000, 2000, 5000, 20)), uf.PRICES_5M_1H)
        record = uf.usage_record({uf.MODEL: a, uf.OTHER_MODEL: b})
        prices = {uf.MODEL: uf.PRICES_1H, uf.OTHER_MODEL: uf.PRICES_5M_1H}
        parts = split_cost(record, prices)
        assert parts is not None
        self.assertAlmostEqual(
            parts["written"], (2 * 2e-6 + 5712 * 4e-6) + (10 * 1e-6 + 1000 * 1.25e-6 + 2000 * 2e-6), places=12
        )
        self.assertAlmostEqual(parts["reread"], 11837 * 0.1e-6 + 5000 * 0.1e-6, places=12)
        self.assertAlmostEqual(parts["output"], 4 * 10e-6 + 20 * 5e-6, places=12)
        self.assertAlmostEqual(sum(parts.values()), record["total_cost_usd"], places=9)

    def test_uses_the_prices_it_is_given_not_the_cost_in_the_record(self) -> None:
        doubled = {uf.MODEL: {kind: 2 * price for kind, price in uf.PRICES_1H.items()}}
        parts = split_cost(self.record(), doubled)
        assert parts is not None
        self.assertAlmostEqual(parts["output"], 2 * 4 * 10e-6, places=12)

    def test_fitted_prices_split_every_record_to_its_cost(self) -> None:
        records = uf.fit_records()
        prices = fit_prices(records)
        for record in records:
            parts = split_cost(record, prices)
            assert parts is not None
            self.assertAlmostEqual(sum(parts.values()), record["total_cost_usd"], places=9)

    def test_fitted_prices_that_left_out_a_zero_kind_still_split(self) -> None:
        # the fit has no cache_write_5m price, and the record's 5 min cache write is 0 tokens
        records = uf.fit_records()
        prices = fit_prices(records)
        self.assertNotIn("cache_write_5m", prices[uf.MODEL])
        self.assertEqual(records[0]["model_usage"][uf.MODEL]["cacheCreationEphemeral5mInputTokens"], 0)
        self.assertIsNotNone(split_cost(records[0], prices))

    def test_none_when_the_model_has_no_prices(self) -> None:
        self.assertIsNone(split_cost(self.record(), {}))
        self.assertIsNone(split_cost(self.record(), {uf.OTHER_MODEL: uf.PRICES_1H}))

    def test_none_when_one_of_several_models_has_no_prices(self) -> None:
        record = uf.usage_record(
            {
                uf.MODEL: (uf.tokens_1h((2, 5712, 11837, 4)), uf.PRICES_1H),
                uf.OTHER_MODEL: (tokens_5((10, 1000, 2000, 5000, 20)), uf.PRICES_5M_1H),
            }
        )
        self.assertIsNone(split_cost(record, {uf.MODEL: uf.PRICES_1H}))
        self.assertIsNotNone(split_cost(record, {uf.MODEL: uf.PRICES_1H, uf.OTHER_MODEL: uf.PRICES_5M_1H}))

    def test_none_without_model_usage(self) -> None:
        record = self.record()
        for usage in ({}, None):
            with self.subTest(model_usage=usage):
                self.assertIsNone(split_cost({**record, "model_usage": usage}, self.PRICES))
        del record["model_usage"]
        self.assertIsNone(split_cost(record, self.PRICES))


if __name__ == "__main__":
    unittest.main()
