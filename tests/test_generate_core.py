"""Tests of the generator logic: UUIDs (v4 and v7), passwords, passphrases,
random numbers and random item picking and shuffling.

Randomness and the clock are injected where the outcome has to be exact; the
rest runs against the real random source and checks properties that must hold
for every draw.
"""

from __future__ import annotations

import ast
import inspect
import math
import string
import time
import unittest
import uuid

from jzr_tools.core import generate
from jzr_tools.core.generate import (
    MAX_UUIDS,
    SIMILAR,
    SYMBOLS,
    GenerateError,
    PasswordOptions,
    character_classes,
    check_password_options,
    format_uuid,
    load_words,
    make_passphrase,
    make_password,
    make_uuids,
    passphrase_bits,
    password_bits,
    pick_items,
    random_ints,
    read_items,
    shuffle_items,
)

COUNT_MESSAGE = "Choose a count from 1 to 100000."


def fixed_clock(*values: int):
    """A clock that returns the given values in turn, then the last one forever."""
    remaining = list(values)

    def clock() -> int:
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    return clock


def fake_randbits(**by_width: int):
    """randbits that answers by bit width, e.g. fake_randbits(b11=5, b62=7)."""
    table = {int(name[1:]): value for name, value in by_width.items()}

    def randbits(width: int) -> int:
        return table[width]

    return randbits


def parts(value: uuid.UUID) -> dict[str, int]:
    number = value.int
    return {
        "ms": number >> 80,
        "version": (number >> 76) & 0xF,
        "counter": (number >> 64) & 0xFFF,
        "variant": (number >> 62) & 0x3,
        "tail": number & ((1 << 62) - 1),
    }


class UuidV4Tests(unittest.TestCase):
    def test_version_and_variant(self) -> None:
        for value in make_uuids(200):
            self.assertEqual(value.version, 4)
            self.assertEqual(value.variant, uuid.RFC_4122)

    def test_returns_the_requested_number_of_distinct_values(self) -> None:
        values = make_uuids(50)
        self.assertEqual(len(values), 50)
        self.assertEqual(len(set(values)), 50)

    def test_boundary_counts_are_accepted(self) -> None:
        self.assertEqual(len(make_uuids(1)), 1)

    def test_count_outside_the_limits_is_refused_with_the_message(self) -> None:
        for count in (0, -1, MAX_UUIDS + 1):
            with self.subTest(count=count):
                with self.assertRaises(GenerateError) as caught:
                    make_uuids(count)
                self.assertEqual(str(caught.exception), COUNT_MESSAGE)

    def test_limit_is_one_hundred_thousand(self) -> None:
        self.assertEqual(MAX_UUIDS, 100_000)

    def test_unknown_version_is_refused(self) -> None:
        for version in (1, 5, 8):
            with self.subTest(version=version):
                with self.assertRaises(GenerateError):
                    make_uuids(1, version=version)


class UuidV7Tests(unittest.TestCase):
    MS = 1_700_000_000_000

    def test_fields_for_one_uuid(self) -> None:
        values = make_uuids(
            1,
            version=7,
            clock_ms=fixed_clock(self.MS),
            randbits=fake_randbits(b11=100, b62=0x2ABCDEF012345678),
        )
        self.assertEqual(len(values), 1)
        fields = parts(values[0])
        self.assertEqual(fields["ms"], self.MS)
        self.assertEqual(fields["version"], 7)
        self.assertEqual(fields["variant"], 0b10)
        self.assertEqual(fields["counter"], 100)
        self.assertEqual(fields["tail"], 0x2ABCDEF012345678)
        self.assertEqual(values[0].version, 7)
        self.assertEqual(values[0].variant, uuid.RFC_4122)

    def test_timestamp_is_the_first_48_bits_of_the_string(self) -> None:
        value = make_uuids(
            1,
            version=7,
            clock_ms=fixed_clock(0x0123456789AB),
            randbits=fake_randbits(b11=0, b62=0),
        )[0]
        self.assertTrue(str(value).replace("-", "").startswith("0123456789ab7"))

    def test_counter_goes_up_by_one_inside_a_millisecond(self) -> None:
        values = make_uuids(
            5,
            version=7,
            clock_ms=fixed_clock(self.MS),
            randbits=fake_randbits(b11=10, b62=1),
        )
        self.assertEqual([parts(v)["counter"] for v in values], [10, 11, 12, 13, 14])
        self.assertEqual({parts(v)["ms"] for v in values}, {self.MS})

    def test_a_new_millisecond_restarts_the_counter(self) -> None:
        values = make_uuids(
            4,
            version=7,
            clock_ms=fixed_clock(self.MS, self.MS, self.MS + 5, self.MS + 5),
            randbits=fake_randbits(b11=7, b62=1),
        )
        fields = [parts(v) for v in values]
        self.assertEqual([f["ms"] for f in fields], [self.MS, self.MS, self.MS + 5, self.MS + 5])
        self.assertEqual([f["counter"] for f in fields], [7, 8, 7, 8])

    def test_counter_start_stays_below_half_the_range(self) -> None:
        asked = []

        def randbits(width: int) -> int:
            asked.append(width)
            return (1 << width) - 1

        values = make_uuids(1, version=7, clock_ms=fixed_clock(self.MS), randbits=randbits)
        self.assertLessEqual(parts(values[0])["counter"], 2047)
        self.assertIn(62, asked)

    def test_list_is_strictly_increasing_even_with_a_constant_clock(self) -> None:
        values = make_uuids(3000, version=7, clock_ms=fixed_clock(self.MS))
        numbers = [v.int for v in values]
        self.assertEqual(numbers, sorted(set(numbers)))
        strings = [str(v) for v in values]
        self.assertEqual(strings, sorted(set(strings)))

    def test_counter_overflow_moves_to_the_next_millisecond(self) -> None:
        # Counter starts at 2047, so 2049 UUIDs fit in one millisecond
        # (2047 .. 4095); the next one must use the following millisecond.
        values = make_uuids(
            2051,
            version=7,
            clock_ms=fixed_clock(self.MS),
            randbits=fake_randbits(b11=2047, b62=3),
        )
        fields = [parts(v) for v in values]
        self.assertEqual(fields[0]["counter"], 2047)
        self.assertEqual(fields[2048]["counter"], 4095)
        self.assertEqual(fields[2048]["ms"], self.MS)
        self.assertEqual(fields[2049]["ms"], self.MS + 1)
        self.assertEqual(fields[2049]["counter"], 2047)
        self.assertEqual(fields[2050]["ms"], self.MS + 1)
        self.assertEqual(fields[2050]["counter"], 2048)
        numbers = [v.int for v in values]
        self.assertEqual(numbers, sorted(set(numbers)))

    def test_clock_going_backwards_keeps_the_last_millisecond(self) -> None:
        values = make_uuids(
            4,
            version=7,
            clock_ms=fixed_clock(self.MS, self.MS - 1000, self.MS - 5000, self.MS + 3),
            randbits=fake_randbits(b11=20, b62=9),
        )
        fields = [parts(v) for v in values]
        self.assertEqual([f["ms"] for f in fields], [self.MS, self.MS, self.MS, self.MS + 3])
        self.assertEqual([f["counter"] for f in fields], [20, 21, 22, 20])
        numbers = [v.int for v in values]
        self.assertEqual(numbers, sorted(set(numbers)))

    def test_milliseconds_outside_48_bits_are_refused(self) -> None:
        for bad in (-1, 2**48, 2**60):
            with self.subTest(ms=bad):
                with self.assertRaises(GenerateError):
                    make_uuids(1, version=7, clock_ms=fixed_clock(bad))

    def test_largest_and_smallest_milliseconds_are_accepted(self) -> None:
        low = make_uuids(1, version=7, clock_ms=fixed_clock(0), randbits=fake_randbits(b11=0, b62=0))
        self.assertEqual(parts(low[0])["ms"], 0)
        high = make_uuids(
            1, version=7, clock_ms=fixed_clock(2**48 - 1), randbits=fake_randbits(b11=0, b62=0)
        )
        self.assertEqual(parts(high[0])["ms"], 2**48 - 1)

    def test_count_limits_apply_to_v7_too(self) -> None:
        for count in (0, MAX_UUIDS + 1):
            with self.subTest(count=count):
                with self.assertRaises(GenerateError) as caught:
                    make_uuids(count, version=7)
                self.assertEqual(str(caught.exception), COUNT_MESSAGE)

    def test_default_clock_is_the_current_time(self) -> None:
        before = time.time_ns() // 1_000_000
        value = make_uuids(1, version=7)[0]
        after = time.time_ns() // 1_000_000
        self.assertTrue(before <= parts(value)["ms"] <= after + 1)

    def test_real_randomness_gives_distinct_values_with_correct_bits(self) -> None:
        values = make_uuids(500, version=7)
        self.assertEqual(len(set(values)), 500)
        for value in values:
            fields = parts(value)
            self.assertEqual(fields["version"], 7)
            self.assertEqual(fields["variant"], 0b10)


class FormatUuidTests(unittest.TestCase):
    VALUE = uuid.UUID("0123abcd-4567-4def-89ab-0123456789ab")

    def test_default_is_lower_case_with_hyphens(self) -> None:
        self.assertEqual(format_uuid(self.VALUE), "0123abcd-4567-4def-89ab-0123456789ab")

    def test_upper(self) -> None:
        self.assertEqual(format_uuid(self.VALUE, upper=True), "0123ABCD-4567-4DEF-89AB-0123456789AB")

    def test_no_hyphens(self) -> None:
        self.assertEqual(format_uuid(self.VALUE, hyphens=False), "0123abcd45674def89ab0123456789ab")

    def test_upper_without_hyphens(self) -> None:
        self.assertEqual(
            format_uuid(self.VALUE, upper=True, hyphens=False), "0123ABCD45674DEF89AB0123456789AB"
        )


ALL_UPPER = string.ascii_uppercase
ALL_LOWER = string.ascii_lowercase
ALL_DIGITS = string.digits


class CharacterClassTests(unittest.TestCase):
    def test_symbols_has_25_distinct_characters(self) -> None:
        self.assertEqual(len(SYMBOLS), 25)
        self.assertEqual(len(set(SYMBOLS)), 25)
        self.assertEqual(SYMBOLS, "!@#$%^&*()-_=+[]{};:,.?/~")

    def test_all_classes_in_order(self) -> None:
        self.assertEqual(
            character_classes(PasswordOptions()), [ALL_UPPER, ALL_LOWER, ALL_DIGITS, SYMBOLS]
        )

    def test_only_chosen_classes_remain_in_order(self) -> None:
        options = PasswordOptions(upper=False, symbols=False)
        self.assertEqual(character_classes(options), [ALL_LOWER, ALL_DIGITS])
        options = PasswordOptions(lower=False, digits=False)
        self.assertEqual(character_classes(options), [ALL_UPPER, SYMBOLS])

    def test_no_class_chosen_gives_an_empty_list(self) -> None:
        options = PasswordOptions(upper=False, lower=False, digits=False, symbols=False)
        self.assertEqual(character_classes(options), [])

    def test_similar_characters_are_removed_from_every_class(self) -> None:
        classes = character_classes(PasswordOptions(no_similar=True))
        self.assertEqual(SIMILAR, "Il1O0o")
        self.assertEqual(len(classes), 4)
        for chars in classes:
            for similar in SIMILAR:
                self.assertNotIn(similar, chars)
        self.assertEqual(classes[0], "".join(c for c in ALL_UPPER if c not in "IO"))
        self.assertEqual(classes[1], "".join(c for c in ALL_LOWER if c not in "lo"))
        self.assertEqual(classes[2], "".join(c for c in ALL_DIGITS if c not in "10"))
        self.assertEqual(classes[3], SYMBOLS)


class PasswordOptionTests(unittest.TestCase):
    def message(self, options: PasswordOptions) -> str:
        with self.assertRaises(GenerateError) as caught:
            check_password_options(options)
        return str(caught.exception)

    def test_defaults_are_valid(self) -> None:
        self.assertIsNone(check_password_options(PasswordOptions()))
        self.assertEqual(PasswordOptions().length, 20)

    def test_no_class_chosen(self) -> None:
        options = PasswordOptions(upper=False, lower=False, digits=False, symbols=False)
        self.assertEqual(self.message(options), "Choose at least one type of character.")

    def test_length_outside_the_range(self) -> None:
        for length in (0, -5, 1025):
            with self.subTest(length=length):
                self.assertEqual(
                    self.message(PasswordOptions(length=length)),
                    "Choose a length from 1 to 1024.",
                )

    def test_length_boundaries_are_valid(self) -> None:
        check_password_options(PasswordOptions(length=1, upper=False, lower=False, symbols=False))
        check_password_options(PasswordOptions(length=1024))

    def test_length_shorter_than_the_number_of_classes(self) -> None:
        self.assertEqual(
            self.message(PasswordOptions(length=3)),
            "Length must allow at least one character from every selected type.",
        )
        check_password_options(PasswordOptions(length=4))


class MakePasswordTests(unittest.TestCase):
    def test_length_and_alphabet(self) -> None:
        allowed = set(ALL_UPPER + ALL_LOWER + ALL_DIGITS + SYMBOLS)
        for length in (4, 20, 64, 1024):
            with self.subTest(length=length):
                password = make_password(PasswordOptions(length=length))
                self.assertEqual(len(password), length)
                self.assertLessEqual(set(password), allowed)

    def test_every_chosen_class_is_present(self) -> None:
        options = PasswordOptions(length=4)
        for _ in range(300):
            password = make_password(options)
            self.assertTrue(set(password) & set(ALL_UPPER))
            self.assertTrue(set(password) & set(ALL_LOWER))
            self.assertTrue(set(password) & set(ALL_DIGITS))
            self.assertTrue(set(password) & set(SYMBOLS))

    def test_unchosen_classes_never_appear(self) -> None:
        options = PasswordOptions(length=40, upper=False, symbols=False)
        for _ in range(100):
            password = make_password(options)
            self.assertLessEqual(set(password), set(ALL_LOWER + ALL_DIGITS))

    def test_similar_characters_never_appear(self) -> None:
        options = PasswordOptions(length=200, no_similar=True)
        for _ in range(100):
            self.assertFalse(set(make_password(options)) & set(SIMILAR))

    def test_single_class_password(self) -> None:
        options = PasswordOptions(length=30, upper=False, lower=False, symbols=False)
        self.assertLessEqual(set(make_password(options)), set(ALL_DIGITS))

    def test_invalid_options_are_refused(self) -> None:
        with self.assertRaises(GenerateError):
            make_password(PasswordOptions(length=2))
        with self.assertRaises(GenerateError):
            make_password(PasswordOptions(length=0))

    def test_draws_again_while_a_class_is_missing(self) -> None:
        # Two classes, two characters: the first attempt "AB" has no digit and
        # must be thrown away; the second attempt "C5" is complete.
        options = PasswordOptions(length=2, lower=False, symbols=False)
        script = iter("ABC5")
        drawn = []

        def choice(pool):
            char = next(script)
            self.assertIn(char, pool)
            drawn.append(char)
            return char

        self.assertEqual(make_password(options, choice=choice), "C5")
        self.assertEqual(drawn, list("ABC5"))

    def test_first_complete_draw_is_returned_without_further_draws(self) -> None:
        options = PasswordOptions(length=2, lower=False, symbols=False)
        script = iter("A5ZZ")
        self.assertEqual(make_password(options, choice=lambda pool: next(script)), "A5")
        self.assertEqual(next(script), "Z")

    def test_gives_up_when_a_class_can_never_appear(self) -> None:
        options = PasswordOptions(length=2, lower=False, symbols=False)
        with self.assertRaises(GenerateError):
            make_password(options, choice=lambda pool: "A")


class PasswordBitsTests(unittest.TestCase):
    def test_full_pool_of_20_characters(self) -> None:
        bits = password_bits(PasswordOptions(length=20))
        self.assertAlmostEqual(bits, 20 * math.log2(87), places=9)
        self.assertGreater(bits, 128.85)
        self.assertLess(bits, 128.86)

    def test_digits_only(self) -> None:
        options = PasswordOptions(length=6, upper=False, lower=False, symbols=False)
        self.assertAlmostEqual(password_bits(options), 6 * math.log2(10), places=9)

    def test_similar_characters_shrink_the_pool(self) -> None:
        options = PasswordOptions(length=10, no_similar=True)
        self.assertAlmostEqual(password_bits(options), 10 * math.log2(24 + 24 + 8 + 25), places=9)


class WordListTests(unittest.TestCase):
    def test_has_7776_unique_sorted_lowercase_ascii_words(self) -> None:
        words = load_words()
        self.assertEqual(len(words), 7776)
        self.assertEqual(len(set(words)), 7776)
        self.assertEqual(list(words), sorted(words))
        for word in words:
            self.assertTrue(word.isascii())
            self.assertEqual(word, word.lower())
            self.assertEqual(word, word.strip())
            self.assertTrue(word)

    def test_some_words_contain_a_hyphen(self) -> None:
        hyphenated = [w for w in load_words() if "-" in w]
        self.assertGreater(len(hyphenated), 0)

    def test_words_use_only_letters_and_hyphens(self) -> None:
        for word in load_words():
            self.assertTrue(all(c.isalpha() or c == "-" for c in word), word)

    def test_is_loaded_once(self) -> None:
        self.assertIs(load_words(), load_words())


class PassphraseTests(unittest.TestCase):
    def test_default_is_six_words_joined_by_hyphens(self) -> None:
        # Words may contain hyphens themselves, so check by rebuilding.
        script = iter(range(6))
        words = load_words()
        phrase = make_passphrase(choice=lambda seq: seq[next(script)])
        self.assertEqual(phrase, "-".join(words[:6]))

    def test_word_count_and_separator(self) -> None:
        words = load_words()
        picks = iter([5, 100, 7000, 2])
        phrase = make_passphrase(4, " ", choice=lambda seq: seq[next(picks)])
        self.assertEqual(phrase, " ".join(words[i] for i in (5, 100, 7000, 2)))

    def test_empty_separator_runs_the_words_together(self) -> None:
        words = load_words()
        picks = iter([1, 2, 3])
        phrase = make_passphrase(3, "", choice=lambda seq: seq[next(picks)])
        self.assertEqual(phrase, words[1] + words[2] + words[3])

    def test_choices_are_made_from_the_whole_list(self) -> None:
        seen = []

        def choice(seq):
            seen.append(len(seq))
            return seq[0]

        make_passphrase(3, choice=choice)
        self.assertEqual(seen, [7776, 7776, 7776])

    def test_words_are_drawn_independently_so_repeats_are_possible(self) -> None:
        word = load_words()[42]
        self.assertEqual(make_passphrase(3, "+", choice=lambda seq: seq[42]), "+".join([word] * 3))

    def test_real_draw_uses_listed_words(self) -> None:
        words = set(load_words())
        for _ in range(50):
            phrase = make_passphrase(5, "|")
            self.assertEqual(len(phrase.split("|")), 5)
            self.assertLessEqual(set(phrase.split("|")), words)

    def test_word_count_limits(self) -> None:
        for count in (0, 2, 21, -1):
            with self.subTest(words=count):
                with self.assertRaises(GenerateError) as caught:
                    make_passphrase(count)
                self.assertEqual(str(caught.exception), "Choose from 3 to 20 words.")
        words = load_words()
        self.assertEqual(make_passphrase(3, "+", choice=lambda seq: seq[0]), "+".join(words[:1] * 3))
        self.assertEqual(
            make_passphrase(20, "+", choice=lambda seq: seq[0]), "+".join(words[:1] * 20)
        )

    def test_separator_of_eight_characters_is_accepted_and_nine_is_not(self) -> None:
        first = load_words()[0]
        self.assertEqual(
            make_passphrase(3, "<>" * 4, choice=lambda seq: seq[0]), ("<>" * 4).join([first] * 3)
        )
        with self.assertRaises(GenerateError):
            make_passphrase(3, "x" * 9)

    def test_separator_with_a_control_character_is_refused(self) -> None:
        for separator in ("\n", "a\tb", "\x00", "\x1b"):
            with self.subTest(separator=repr(separator)):
                with self.assertRaises(GenerateError):
                    make_passphrase(3, separator)

    def test_bits(self) -> None:
        self.assertAlmostEqual(passphrase_bits(6), 6 * math.log2(7776), places=9)
        self.assertGreater(passphrase_bits(6), 77.5)
        self.assertLess(passphrase_bits(6), 77.6)
        self.assertAlmostEqual(passphrase_bits(3), 3 * math.log2(7776), places=9)


class RandomIntTests(unittest.TestCase):
    def test_both_ends_are_reachable(self) -> None:
        asked = []

        def lowest(n: int) -> int:
            asked.append(n)
            return 0

        def highest(n: int) -> int:
            return n - 1

        self.assertEqual(random_ints(5, 9, randbelow=lowest), [5])
        self.assertEqual(asked, [5])
        self.assertEqual(random_ints(5, 9, randbelow=highest), [9])

    def test_negative_ranges(self) -> None:
        self.assertEqual(random_ints(-10, 10, randbelow=lambda n: 0), [-10])
        self.assertEqual(random_ints(-10, 10, randbelow=lambda n: n - 1), [10])
        self.assertEqual(random_ints(-3, -3), [-3])

    def test_single_value_range(self) -> None:
        self.assertEqual(random_ints(7, 7, 4), [7, 7, 7, 7])

    def test_count_and_bounds_with_real_randomness(self) -> None:
        values = random_ints(1, 6, 500)
        self.assertEqual(len(values), 500)
        self.assertEqual(set(values), {1, 2, 3, 4, 5, 6})

    def test_values_come_in_draw_order(self) -> None:
        draws = iter([3, 0, 2])
        self.assertEqual(random_ints(10, 20, 3, randbelow=lambda n: next(draws)), [13, 10, 12])

    def test_unique_values_are_distinct(self) -> None:
        values = random_ints(1, 100, 60, unique=True)
        self.assertEqual(len(values), 60)
        self.assertEqual(len(set(values)), 60)
        self.assertTrue(all(1 <= v <= 100 for v in values))

    def test_unique_with_count_equal_to_the_range_returns_every_value(self) -> None:
        values = random_ints(-5, 4, 10, unique=True)
        self.assertEqual(sorted(values), list(range(-5, 5)))

    def test_unique_stays_distinct_with_extreme_fake_draws(self) -> None:
        for fake in (lambda n: 0, lambda n: n - 1):
            values = random_ints(0, 9, 10, unique=True, randbelow=fake)
            self.assertEqual(sorted(values), list(range(10)))
            partial = random_ints(0, 9, 5, unique=True, randbelow=fake)
            self.assertEqual(len(set(partial)), 5)
            self.assertTrue(all(0 <= v <= 9 for v in partial))

    def test_unique_over_a_huge_range_does_not_build_the_range(self) -> None:
        started = time.monotonic()
        values = random_ints(0, 10**18, 5, unique=True)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(set(values)), 5)
        self.assertTrue(all(0 <= v <= 10**18 for v in values))

    def test_non_unique_over_a_huge_range(self) -> None:
        values = random_ints(-(10**30), 10**30, 3)
        self.assertEqual(len(values), 3)
        self.assertTrue(all(-(10**30) <= v <= 10**30 for v in values))

    def test_low_above_high(self) -> None:
        with self.assertRaises(GenerateError) as caught:
            random_ints(10, 1)
        self.assertEqual(str(caught.exception), "MIN must not be greater than MAX.")

    def test_count_below_one(self) -> None:
        for count in (0, -2):
            with self.subTest(count=count):
                with self.assertRaises(GenerateError) as caught:
                    random_ints(1, 10, count)
                self.assertEqual(str(caught.exception), "Choose a count of 1 or more.")

    def test_unique_count_above_the_range(self) -> None:
        with self.assertRaises(GenerateError) as caught:
            random_ints(1, 3, 4, unique=True)
        self.assertEqual(str(caught.exception), "Only 3 different numbers lie between 1 and 3.")
        with self.assertRaises(GenerateError) as caught:
            random_ints(-2, 2, 6, unique=True)
        self.assertEqual(str(caught.exception), "Only 5 different numbers lie between -2 and 2.")

    def test_repeats_may_exceed_the_range_without_unique(self) -> None:
        self.assertEqual(len(random_ints(1, 3, 10)), 10)


class ReadItemsTests(unittest.TestCase):
    def test_lines_are_trimmed_and_blanks_dropped(self) -> None:
        self.assertEqual(read_items("  a \n\n\tb\r\n   \nc"), ["a", "b", "c"])

    def test_duplicates_and_inner_spaces_are_kept(self) -> None:
        self.assertEqual(read_items("a\nb c\na\n"), ["a", "b c", "a"])

    def test_empty_text(self) -> None:
        self.assertEqual(read_items(""), [])
        self.assertEqual(read_items("\n \n"), [])


class PickItemsTests(unittest.TestCase):
    def test_default_picks_one(self) -> None:
        self.assertIn(pick_items(["a", "b", "c"])[0], "abc")
        self.assertEqual(len(pick_items(["a", "b", "c"])), 1)

    def test_without_repeat_picks_distinct_positions(self) -> None:
        items = ["a", "b", "c", "d", "e"]
        for _ in range(100):
            picked = pick_items(items, 3)
            self.assertEqual(len(picked), 3)
            self.assertEqual(len(set(picked)), 3)

    def test_without_repeat_all_items_come_back_even_with_extreme_draws(self) -> None:
        items = ["a", "a", "b"]
        for fake in (lambda n: 0, lambda n: n - 1):
            self.assertEqual(sorted(pick_items(items, 3, randbelow=fake)), ["a", "a", "b"])

    def test_duplicate_items_may_both_appear(self) -> None:
        picked = pick_items(["x", "x"], 2)
        self.assertEqual(picked, ["x", "x"])

    def test_repeat_draws_from_the_whole_list_each_time(self) -> None:
        asked = []

        def lowest(n: int) -> int:
            asked.append(n)
            return 0

        self.assertEqual(pick_items(["a", "b", "c"], 4, repeat=True, randbelow=lowest), ["a"] * 4)
        self.assertEqual(asked, [3, 3, 3, 3])
        self.assertEqual(
            pick_items(["a", "b", "c"], 2, repeat=True, randbelow=lambda n: n - 1), ["c", "c"]
        )

    def test_repeat_allows_more_than_the_list_length(self) -> None:
        self.assertEqual(len(pick_items(["a", "b"], 9, repeat=True)), 9)

    def test_no_items(self) -> None:
        for repeat in (False, True):
            with self.subTest(repeat=repeat):
                with self.assertRaises(GenerateError) as caught:
                    pick_items([], 1, repeat=repeat)
                self.assertEqual(str(caught.exception), "No items to pick from.")

    def test_count_below_one(self) -> None:
        with self.assertRaises(GenerateError) as caught:
            pick_items(["a"], 0)
        self.assertEqual(str(caught.exception), "Choose a count of 1 or more.")

    def test_count_above_the_list_without_repeat(self) -> None:
        with self.assertRaises(GenerateError) as caught:
            pick_items(["a", "b"], 3)
        self.assertEqual(
            str(caught.exception), "Only 2 items to pick from; add --repeat to allow repeats."
        )


class ShuffleItemsTests(unittest.TestCase):
    def test_result_is_a_permutation(self) -> None:
        items = [f"item{i}" for i in range(30)]
        for _ in range(20):
            shuffled = shuffle_items(items)
            self.assertEqual(sorted(shuffled), sorted(items))

    def test_duplicates_are_kept(self) -> None:
        self.assertEqual(sorted(shuffle_items(["a", "a", "b"])), ["a", "a", "b"])

    def test_input_is_not_changed(self) -> None:
        items = ["a", "b", "c", "d"]
        shuffle_items(items)
        self.assertEqual(items, ["a", "b", "c", "d"])

    def test_single_item(self) -> None:
        self.assertEqual(shuffle_items(["only"]), ["only"])

    def test_order_actually_varies(self) -> None:
        items = list(range(10))
        orders = {tuple(shuffle_items([str(i) for i in items])) for _ in range(20)}
        self.assertGreater(len(orders), 1)

    def test_extreme_fake_draws_still_give_a_permutation(self) -> None:
        items = list("abcdef")
        for fake in (lambda n: 0, lambda n: n - 1):
            self.assertEqual(sorted(shuffle_items(items, randbelow=fake)), items)

    def test_empty_list(self) -> None:
        with self.assertRaises(GenerateError) as caught:
            shuffle_items([])
        self.assertEqual(str(caught.exception), "No items to shuffle.")


class RandomnessSourceTests(unittest.TestCase):
    def test_module_never_imports_the_standard_random_module(self) -> None:
        tree = ast.parse(inspect.getsource(generate))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotEqual(alias.name.split(".")[0], "random")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual((node.module or "").split(".")[0], "random")

    def test_error_type_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(GenerateError, ValueError))


if __name__ == "__main__":
    unittest.main()
