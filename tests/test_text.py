"""Acceptance tests for slugify / kebab bug fixes (spec section B)."""

from __future__ import annotations

from datetime import date
import unittest
from unittest import mock

from typer.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.text import kebab_case, slugify

runner = CliRunner()


class SeparatorTests(unittest.TestCase):
    def test_multi_character_separator_is_inserted_literally(self) -> None:
        self.assertEqual(slugify("banana split", separator="ab"), "bananaabsplit")

    def test_backslash_separator_does_not_raise(self) -> None:
        self.assertEqual(slugify("a b", separator="\\"), "a\\b")

    def test_backslash_digit_separator_is_not_a_backreference(self) -> None:
        self.assertEqual(slugify("a b", separator="\\1"), "a\\1b")

    def test_surrounding_separators_are_trimmed_as_a_whole(self) -> None:
        self.assertEqual(slugify("  --a  b-- ", separator="--"), "a--b")

    def test_cli_accepts_backslash_separator(self) -> None:
        result = runner.invoke(app, ["text", "slug", "a b", "-s", "\\"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "a\\b")


class TransliterationTests(unittest.TestCase):
    def _check(self, cases: dict[str, str], **kwargs: object) -> None:
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slugify(text, **kwargs), expected)

    def test_russian_text_is_transliterated(self) -> None:
        self._check(
            {
                "Привет мир": "privet-mir",
                "Щука и ёжик": "shchuka-i-yozhik",
                "объявление": "obyavlenie",
            }
        )

    def test_russian_letter_mapping(self) -> None:
        self._check(
            {
                "ж": "zh",
                "х": "kh",
                "ц": "ts",
                "ч": "ch",
                "ш": "sh",
                "щ": "shch",
                "ю": "yu",
                "я": "ya",
                "ё": "yo",
                "й": "y",
                "ы": "y",
                "э": "e",
                "аъб": "ab",
                "аьб": "ab",
            }
        )

    def test_uzbek_cyrillic_letters(self) -> None:
        self._check(
            {
                "Ўзбекистон": "ozbekiston",
                "Қашқадарё": "qashqadaryo",
                "ғ": "g",
                "ҳ": "h",
            }
        )

    def test_latin_letters_without_decomposition(self) -> None:
        self._check(
            {
                "Straße Łódź Ørsted": "strasse-lodz-orsted",
                "æ": "ae",
                "œ": "oe",
                "đ": "d",
                "ð": "d",
                "þ": "th",
                "ı": "i",
            }
        )

    def test_case_is_kept_when_lowercase_disabled(self) -> None:
        self._check(
            {"Привет Мир": "Privet-Mir", "Щука": "Shchuka"},
            lowercase=False,
        )

    def test_existing_behaviour_is_unchanged(self) -> None:
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        self.assertEqual(slugify("Crème brûlée"), "creme-brulee")
        self.assertEqual(
            kebab_case("ApiSix deployment Notes"), "apisix-deployment-notes"
        )


class EmptyResultTests(unittest.TestCase):
    def test_slug_of_punctuation_only_is_an_error(self) -> None:
        result = runner.invoke(app, ["text", "slug", "!!!"])

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(result.stderr.strip())

    def test_kebab_of_punctuation_only_with_date_prints_no_prefix(self) -> None:
        with mock.patch("jzr_tools.commands.text.date") as date_mock:
            date_mock.today.return_value = date(2026, 7, 11)

            result = runner.invoke(app, ["text", "kebab", "!!!", "--date"])

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout.strip(), "")
        self.assertNotIn("2026", result.stdout)
        self.assertTrue(result.stderr.strip())

    def test_kebab_of_punctuation_only_without_date_is_an_error(self) -> None:
        result = runner.invoke(app, ["text", "kebab", "!!!"])

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(result.stderr.strip())


if __name__ == "__main__":
    unittest.main()
