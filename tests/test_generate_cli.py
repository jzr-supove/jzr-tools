"""CLI behaviour of `jzr uuid`, `jzr password` and `jzr random`."""

from __future__ import annotations

import contextlib
import importlib
import os
import unittest
import uuid
from functools import lru_cache
from pathlib import Path
from unittest import mock

import jzr_tools
from click.testing import CliRunner

from jzr_tools.cli import app

UPPER = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
LOWER = set("abcdefghijklmnopqrstuvwxyz")
DIGITS = set("0123456789")
SYMBOLS = set("!@#$%^&*()-_=+[]{};:,.?/~")
SIMILAR = set("Il1O0o")


@lru_cache(maxsize=1)
def packaged_words() -> frozenset[str]:
    path = Path(jzr_tools.__file__).parent / "data" / "eff_large_wordlist.txt"
    words = [w.strip() for w in path.read_text(encoding="ascii").splitlines() if w.strip()]
    return frozenset(words)


def split_into_words(text: str, count: int, separator: str) -> bool:
    """True when text is exactly `count` packaged words joined by separator."""
    words = packaged_words()
    seen: dict[tuple[int, int], bool] = {}

    def walk(pos: int, left: int) -> bool:
        key = (pos, left)
        if key in seen:
            return seen[key]
        found = False
        if left == 0:
            found = pos == len(text)
        else:
            for end in range(pos + 1, min(len(text), pos + 12) + 1):
                if text[pos:end] not in words:
                    continue
                if left == 1:
                    if walk(end, 0):
                        found = True
                        break
                elif text.startswith(separator, end) and walk(end + len(separator), left - 1):
                    found = True
                    break
        seen[key] = found
        return found

    return walk(0, count)


class GenerateCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        # Unknown command names consult the bindings file: keep it off the real one.
        patcher = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(Path(__file__).parent)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args: str, input: str | None = None):
        return self.runner.invoke(app, list(args), input=input)

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def out_lines(self, result) -> list[str]:
        return result.stdout.splitlines()

    @contextlib.contextmanager
    def clipboard(self, error: str | None = None):
        """Replace the clipboard function wherever the command may look it up."""
        side_effect = RuntimeError(error) if error else None
        with contextlib.ExitStack() as stack:
            targets = ["jzr_tools.core.system.copy_to_clipboard"]
            try:
                module = importlib.import_module("jzr_tools.commands.generate")
            except ImportError:
                module = None
            if module is not None and hasattr(module, "copy_to_clipboard"):
                targets.append("jzr_tools.commands.generate.copy_to_clipboard")
            fakes = [
                stack.enter_context(mock.patch(target, side_effect=side_effect))
                for target in targets
            ]
            yield fakes[-1]


# ---------------------------------------------------------------- uuid


class UuidTests(GenerateCliCase):
    def test_default_is_one_lowercase_hyphenated_v4(self) -> None:
        result = self.run_cli("uuid")
        self.assert_exit(result, 0)
        lines = self.out_lines(result)
        self.assertEqual(len(lines), 1, self.describe(result))
        self.assertRegex(lines[0], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        parsed = uuid.UUID(lines[0])
        self.assertEqual(parsed.version, 4)
        self.assertEqual(parsed.variant, uuid.RFC_4122)

    def test_count_prints_that_many_distinct_ids(self) -> None:
        for flag in ("-n", "--count"):
            with self.subTest(flag=flag):
                result = self.run_cli("uuid", flag, "25")
                self.assert_exit(result, 0)
                lines = self.out_lines(result)
                self.assertEqual(len(lines), 25)
                self.assertEqual(len(set(lines)), 25)

    def test_upper_prints_uppercase_hex(self) -> None:
        result = self.run_cli("uuid", "--upper", "-n", "5")
        self.assert_exit(result, 0)
        for line in self.out_lines(result):
            self.assertRegex(line, r"^[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}$")

    def test_no_hyphens_prints_32_hex_characters(self) -> None:
        result = self.run_cli("uuid", "--no-hyphens", "-n", "5")
        self.assert_exit(result, 0)
        for line in self.out_lines(result):
            self.assertRegex(line, r"^[0-9a-f]{32}$")
            self.assertEqual(uuid.UUID(line).version, 4)

    def test_upper_and_no_hyphens_combine(self) -> None:
        result = self.run_cli("uuid", "--upper", "--no-hyphens")
        self.assert_exit(result, 0)
        self.assertRegex(result.stdout.strip(), r"^[0-9A-F]{32}$")

    def test_v7_ids_have_version_7_and_sort_in_creation_order(self) -> None:
        result = self.run_cli("uuid", "--v7", "-n", "300")
        self.assert_exit(result, 0)
        lines = self.out_lines(result)
        self.assertEqual(len(lines), 300)
        for line in lines:
            parsed = uuid.UUID(line)
            self.assertEqual(parsed.version, 7, line)
            self.assertEqual(parsed.variant, uuid.RFC_4122, line)
        self.assertEqual(lines, sorted(lines))
        self.assertEqual(len(set(lines)), 300)

    def test_v7_timestamp_is_the_current_time(self) -> None:
        import time

        before = time.time_ns() // 1_000_000
        result = self.run_cli("uuid", "--v7")
        after = time.time_ns() // 1_000_000
        self.assert_exit(result, 0)
        millis = uuid.UUID(result.stdout.strip()).int >> 80
        self.assertTrue(before - 1 <= millis <= after + 1, (before, millis, after))

    def test_v7_works_with_upper_and_no_hyphens(self) -> None:
        result = self.run_cli("uuid", "--v7", "--upper", "--no-hyphens", "-n", "3")
        self.assert_exit(result, 0)
        for line in self.out_lines(result):
            self.assertRegex(line, r"^[0-9A-F]{32}$")
            self.assertEqual(uuid.UUID(line).version, 7)

    def test_counts_outside_the_limits_are_usage_errors(self) -> None:
        for count in ("0", "-1", "100001"):
            with self.subTest(count=count):
                result = self.run_cli("uuid", "-n", count)
                self.assert_exit(result, 2)
                self.assertIn("Choose a count from 1 to 100000.", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_a_count_that_is_not_a_number_is_a_usage_error(self) -> None:
        result = self.run_cli("uuid", "-n", "many")
        self.assert_exit(result, 2)
        self.assertIn("--count", result.stderr)

    def test_copy_copies_every_line_and_still_prints_them(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("uuid", "-n", "3", "-c")
        self.assert_exit(result, 0)
        printed = self.out_lines(result)
        self.assertEqual(len(printed), 3)
        copy.assert_called_once()
        self.assertEqual(copy.call_args.args[0], "\n".join(printed))

    def test_copy_alias_long_flag(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("uuid", "--copy", "--upper")
        self.assert_exit(result, 0)
        copy.assert_called_once()
        self.assertEqual(copy.call_args.args[0], result.stdout.strip())
        self.assertEqual(copy.call_args.args[0], copy.call_args.args[0].upper())

    def test_without_copy_the_clipboard_is_not_touched(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("uuid")
        self.assert_exit(result, 0)
        copy.assert_not_called()

    def test_clipboard_failure_exits_1_with_its_message_and_prints_nothing(self) -> None:
        with self.clipboard(error="No clipboard command found."):
            result = self.run_cli("uuid", "-c")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("No clipboard command found.", result.stderr)


# ------------------------------------------------------------ password


class PasswordTests(GenerateCliCase):
    def password(self, *args: str) -> str:
        result = self.run_cli("password", *args)
        self.assert_exit(result, 0)
        lines = self.out_lines(result)
        self.assertEqual(len(lines), 1, self.describe(result))
        return lines[0]

    def test_default_is_20_characters_from_all_four_classes(self) -> None:
        for _ in range(20):
            value = self.password()
            self.assertEqual(len(value), 20)
            chars = set(value)
            self.assertLessEqual(chars, UPPER | LOWER | DIGITS | SYMBOLS, value)
            for name, group in (("upper", UPPER), ("lower", LOWER), ("digits", DIGITS), ("symbols", SYMBOLS)):
                self.assertTrue(chars & group, f"no {name} character in {value}")

    def test_length_option(self) -> None:
        for flag in ("-l", "--length"):
            with self.subTest(flag=flag):
                self.assertEqual(len(self.password(flag, "37")), 37)
        self.assertEqual(len(self.password("-l", "4")), 4)
        self.assertEqual(len(self.password("-l", "1024")), 1024)

    def test_each_no_option_removes_exactly_its_class(self) -> None:
        cases = {
            "--no-upper": (UPPER, LOWER | DIGITS | SYMBOLS),
            "--no-lower": (LOWER, UPPER | DIGITS | SYMBOLS),
            "--no-digits": (DIGITS, UPPER | LOWER | SYMBOLS),
            "--no-symbols": (SYMBOLS, UPPER | LOWER | DIGITS),
        }
        for flag, (removed, kept) in cases.items():
            with self.subTest(flag=flag):
                value = self.password(flag, "-l", "300")
                chars = set(value)
                self.assertFalse(chars & removed, value)
                self.assertLessEqual(chars, kept)
                for group in (UPPER, LOWER, DIGITS, SYMBOLS):
                    if group <= kept:
                        self.assertTrue(chars & group, f"class missing from {value}")

    def test_a_single_class_is_enough(self) -> None:
        value = self.password("--no-upper", "--no-lower", "--no-symbols", "-l", "30")
        self.assertRegex(value, r"^[0-9]{30}$")

    def test_no_similar_drops_the_look_alike_characters_from_every_class(self) -> None:
        value = self.password("--no-similar", "-l", "1024")
        chars = set(value)
        self.assertFalse(chars & SIMILAR, sorted(chars & SIMILAR))
        self.assertLessEqual(chars, (UPPER | LOWER | DIGITS | SYMBOLS) - SIMILAR)
        for group in (UPPER, LOWER, DIGITS, SYMBOLS):
            self.assertTrue(chars & group)

    def test_count_prints_that_many_passwords(self) -> None:
        result = self.run_cli("password", "-n", "3", "-l", "12")
        self.assert_exit(result, 0)
        lines = self.out_lines(result)
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(len(line) == 12 for line in lines))
        self.assertEqual(len(set(lines)), 3)

    def test_count_option_long_name(self) -> None:
        result = self.run_cli("password", "--count", "2")
        self.assert_exit(result, 0)
        self.assertEqual(len(self.out_lines(result)), 2)

    def test_no_strength_line_when_stdout_is_not_a_terminal(self) -> None:
        result = self.run_cli("password")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, "")
        result = self.run_cli("password", "-w", "6")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, "")

    # ---- usage errors

    def assert_usage_error(self, args: list[str], message: str) -> None:
        result = self.run_cli("password", *args)
        self.assert_exit(result, 2)
        self.assertIn(message, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_no_character_class_left_is_a_usage_error(self) -> None:
        self.assert_usage_error(
            ["--no-upper", "--no-lower", "--no-digits", "--no-symbols"],
            "Choose at least one type of character.",
        )

    def test_length_outside_1_to_1024_is_a_usage_error(self) -> None:
        for length in ("0", "-5", "1025"):
            with self.subTest(length=length):
                self.assert_usage_error(["-l", length], "Choose a length from 1 to 1024.")

    def test_length_shorter_than_the_number_of_classes_is_a_usage_error(self) -> None:
        message = "Length must allow at least one character from every selected type."
        self.assert_usage_error(["-l", "3"], message)
        self.assert_usage_error(["-l", "2", "--no-symbols"], message)

    def test_length_equal_to_the_number_of_classes_works(self) -> None:
        value = self.password("-l", "4")
        chars = set(value)
        for group in (UPPER, LOWER, DIGITS, SYMBOLS):
            self.assertTrue(chars & group, value)

    def test_words_cannot_be_combined_with_length_or_character_options(self) -> None:
        message = "--words makes a passphrase; --length and the character options do not apply."
        for extra in (["-l", "20"], ["--length", "10"], ["--no-upper"], ["--no-lower"],
                      ["--no-digits"], ["--no-symbols"], ["--no-similar"]):
            with self.subTest(extra=extra):
                self.assert_usage_error(["-w", "6", *extra], message)

    def test_separator_without_words_is_a_usage_error(self) -> None:
        self.assert_usage_error(["--separator", "."], "--separator only applies with --words.")

    def test_word_count_outside_3_to_20_is_a_usage_error(self) -> None:
        for count in ("2", "21", "0"):
            with self.subTest(count=count):
                self.assert_usage_error(["-w", count], "Choose from 3 to 20 words.")

    # ---- passphrases

    def test_words_make_a_passphrase_of_packaged_words_joined_by_hyphens(self) -> None:
        for _ in range(10):
            value = self.password("-w", "6")
            self.assertTrue(split_into_words(value, 6, "-"), value)

    def test_words_count_and_long_option(self) -> None:
        value = self.password("--words", "3")
        self.assertTrue(split_into_words(value, 3, "-"), value)
        value = self.password("-w", "20")
        self.assertTrue(split_into_words(value, 20, "-"), value)

    def test_custom_separator_joins_the_words(self) -> None:
        value = self.password("-w", "5", "--separator", "+")
        parts = value.split("+")
        self.assertEqual(len(parts), 5, value)
        self.assertLessEqual(set(parts), packaged_words())

    def test_empty_separator_joins_the_words_directly(self) -> None:
        value = self.password("-w", "6", "--separator", "")
        self.assertTrue(split_into_words(value, 6, ""), value)

    def test_several_passphrases(self) -> None:
        result = self.run_cli("password", "-w", "4", "-n", "3", "--separator", " ")
        self.assert_exit(result, 0)
        lines = self.out_lines(result)
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertEqual(len(line.split(" ")), 4)
            self.assertLessEqual(set(line.split(" ")), packaged_words())

    # ---- copying

    def test_copy_prints_nothing_on_stdout_and_says_so_on_stderr(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("password", "-c", "--no-symbols")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(), "Copied a 20-character password (about 119 bits)."
        )
        copy.assert_called_once()
        copied = copy.call_args.args[0]
        self.assertEqual(len(copied), 20)
        self.assertRegex(copied, r"^[A-Za-z0-9]{20}$")
        self.assertNotIn(copied, result.stderr)

    def test_copy_message_for_a_default_password_rounds_to_129_bits(self) -> None:
        with self.clipboard():
            result = self.run_cli("password", "--copy")
        self.assert_exit(result, 0)
        self.assertEqual(
            result.stderr.strip(), "Copied a 20-character password (about 129 bits)."
        )

    def test_copy_message_uses_the_chosen_length(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("password", "-c", "-l", "10", "--no-upper", "--no-lower", "--no-symbols")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr.strip(), "Copied a 10-character password (about 33 bits).")
        self.assertRegex(copy.call_args.args[0], r"^[0-9]{10}$")

    def test_copy_several_passwords(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("password", "-c", "-n", "3", "--no-symbols")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(),
            "Copied 3 passwords (20 characters, about 119 bits each).",
        )
        copied = copy.call_args.args[0]
        self.assertEqual(len(copied.split("\n")), 3)
        self.assertTrue(all(len(line) == 20 for line in copied.split("\n")))

    def test_copy_a_passphrase(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("password", "-c", "-w", "6")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(), "Copied a 6-word passphrase (about 78 bits)."
        )
        self.assertTrue(split_into_words(copy.call_args.args[0], 6, "-"))

    def test_copy_several_passphrases(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("password", "-c", "-w", "6", "-n", "3")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            result.stderr.strip(),
            "Copied 3 passphrases (6 words, about 78 bits each).",
        )
        copied = copy.call_args.args[0].split("\n")
        self.assertEqual(len(copied), 3)
        self.assertTrue(all(split_into_words(line, 6, "-") for line in copied))

    def test_clipboard_failure_exits_1_and_prints_no_password(self) -> None:
        with self.clipboard(error="Clipboard command not found: clip"):
            result = self.run_cli("password", "-c")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("Clipboard command not found: clip", result.stderr)
        self.assertNotIn("Copied", result.stderr)

    def test_without_copy_the_clipboard_is_not_touched(self) -> None:
        with self.clipboard() as copy:
            result = self.run_cli("password")
        self.assert_exit(result, 0)
        copy.assert_not_called()


# -------------------------------------------------------------- random


class RandomIntTests(GenerateCliCase):
    def numbers(self, *args: str) -> list[int]:
        result = self.run_cli("random", "int", *args)
        self.assert_exit(result, 0)
        return [int(line) for line in self.out_lines(result)]

    def test_default_is_one_number_from_1_to_100(self) -> None:
        values = self.numbers()
        self.assertEqual(len(values), 1)
        self.assertTrue(1 <= values[0] <= 100)

    def test_default_range_covers_1_to_100_and_nothing_else(self) -> None:
        values = self.numbers("-n", "2000")
        self.assertEqual(len(values), 2000)
        self.assertGreaterEqual(min(values), 1)
        self.assertLessEqual(max(values), 100)
        self.assertGreater(len(set(values)), 50)

    def test_negative_bounds_work_without_a_double_dash(self) -> None:
        values = self.numbers("-10", "10", "-n", "400")
        self.assertTrue(all(-10 <= v <= 10 for v in values))
        self.assertTrue(any(v < 0 for v in values))
        self.assertEqual(set(values), set(range(-10, 11)))

    def test_both_ends_are_included(self) -> None:
        values = self.numbers("3", "4", "-n", "200")
        self.assertEqual(set(values), {3, 4})

    def test_equal_bounds_give_that_number(self) -> None:
        self.assertEqual(self.numbers("7", "7", "-n", "3"), [7, 7, 7])

    def test_one_bound_alone_is_a_usage_error(self) -> None:
        result = self.run_cli("random", "int", "5")
        self.assert_exit(result, 2)
        self.assertIn("Give both MIN and MAX, or neither.", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_min_above_max_is_a_usage_error(self) -> None:
        result = self.run_cli("random", "int", "10", "1")
        self.assert_exit(result, 2)
        self.assertIn("MIN must not be greater than MAX.", result.stderr)

    def test_count_below_one_is_a_usage_error(self) -> None:
        result = self.run_cli("random", "int", "-n", "0")
        self.assert_exit(result, 2)
        self.assertIn("Choose a count of 1 or more.", result.stderr)

    def test_unique_draws_every_value_once(self) -> None:
        values = self.numbers("--unique", "-n", "10", "1", "10")
        self.assertEqual(sorted(values), list(range(1, 11)))

    def test_unique_with_fewer_values_than_the_range(self) -> None:
        values = self.numbers("--unique", "-n", "5", "-3", "3")
        self.assertEqual(len(set(values)), 5)
        self.assertTrue(all(-3 <= v <= 3 for v in values))

    def test_more_unique_values_than_the_range_holds_is_a_usage_error(self) -> None:
        result = self.run_cli("random", "int", "--unique", "-n", "11", "1", "10")
        self.assert_exit(result, 2)
        self.assertIn("Only 10 different numbers lie between 1 and 10.", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_repeats_are_possible_without_unique(self) -> None:
        values = self.numbers("1", "2", "-n", "50")
        self.assertEqual(len(values), 50)
        self.assertLess(len(set(values)), 50)

    def test_huge_bounds_with_unique_do_not_build_the_range(self) -> None:
        low, high = 0, 10**30
        values = self.numbers("--unique", "-n", "5", str(low), str(high))
        self.assertEqual(len(set(values)), 5)
        self.assertTrue(all(low <= v <= high for v in values))
        self.assertTrue(any(v > 10**9 for v in values))


class RandomItemsTests(GenerateCliCase):
    def test_pick_one_of_the_arguments(self) -> None:
        result = self.run_cli("random", "pick", "red", "green", "blue")
        self.assert_exit(result, 0)
        lines = self.out_lines(result)
        self.assertEqual(len(lines), 1)
        self.assertIn(lines[0], {"red", "green", "blue"})

    def test_pick_eventually_returns_every_argument(self) -> None:
        seen = set()
        for _ in range(60):
            seen.add(self.run_cli("random", "pick", "a", "b", "c").stdout.strip())
        self.assertEqual(seen, {"a", "b", "c"})

    def test_pick_reads_lines_of_stdin_without_arguments(self) -> None:
        result = self.run_cli("random", "pick", "-n", "3", input="  one  \n\ntwo\n   \nthree\n")
        self.assert_exit(result, 0)
        self.assertEqual(sorted(self.out_lines(result)), ["one", "three", "two"])

    def test_pick_count_without_repeat_gives_distinct_positions(self) -> None:
        result = self.run_cli("random", "pick", "a", "b", "c", "d", "-n", "4")
        self.assert_exit(result, 0)
        self.assertEqual(sorted(self.out_lines(result)), ["a", "b", "c", "d"])

    def test_pick_keeps_duplicate_items_from_stdin(self) -> None:
        result = self.run_cli("random", "pick", "-n", "3", input="x\nx\ny\n")
        self.assert_exit(result, 0)
        self.assertEqual(sorted(self.out_lines(result)), ["x", "x", "y"])

    def test_pick_repeat_allows_more_picks_than_items(self) -> None:
        result = self.run_cli("random", "pick", "only", "--repeat", "-n", "5")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["only"] * 5)

    def test_pick_more_than_the_items_without_repeat_is_a_usage_error(self) -> None:
        result = self.run_cli("random", "pick", "a", "b", "-n", "3")
        self.assert_exit(result, 2)
        self.assertIn("Only 2 items to pick from; add --repeat to allow repeats.", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_pick_count_below_one_is_a_usage_error(self) -> None:
        result = self.run_cli("random", "pick", "a", "b", "-n", "0")
        self.assert_exit(result, 2)
        self.assertIn("Choose a count of 1 or more.", result.stderr)

    def test_pick_without_items_exits_1(self) -> None:
        for given in ("", "\n  \n\n"):
            with self.subTest(stdin=given):
                result = self.run_cli("random", "pick", input=given)
                self.assert_exit(result, 1)
                self.assertIn("No items to pick from.", result.stderr)
                self.assertEqual(result.stdout, "")

    def test_shuffle_arguments_is_a_permutation(self) -> None:
        items = [f"item{i}" for i in range(12)]
        result = self.run_cli("random", "shuffle", *items)
        self.assert_exit(result, 0)
        self.assertEqual(sorted(self.out_lines(result)), sorted(items))

    def test_shuffle_changes_the_order_at_least_sometimes(self) -> None:
        items = [str(i) for i in range(20)]
        orders = {tuple(self.out_lines(self.run_cli("random", "shuffle", *items))) for _ in range(4)}
        self.assertGreater(len(orders), 1)

    def test_shuffle_reads_stdin_drops_blank_lines_and_keeps_duplicates(self) -> None:
        result = self.run_cli("random", "shuffle", input=" b \n\na\n\na\n  \nc\n")
        self.assert_exit(result, 0)
        self.assertEqual(sorted(self.out_lines(result)), ["a", "a", "b", "c"])

    def test_shuffle_without_items_exits_1(self) -> None:
        result = self.run_cli("random", "shuffle", input="\n \n")
        self.assert_exit(result, 1)
        self.assertIn("No items to shuffle.", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_random_without_a_subcommand_does_not_generate_anything(self) -> None:
        result = self.run_cli("random")
        self.assertNotEqual(result.stdout.strip(), "")
        self.assertIn("int", result.stdout + result.stderr)
        self.assertIn("pick", result.stdout + result.stderr)
        self.assertIn("shuffle", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
