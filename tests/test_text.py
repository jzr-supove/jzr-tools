"""Acceptance tests for slugify / kebab bug fixes."""

from __future__ import annotations

from datetime import date
import re
import unittest
from unittest import mock

from click.testing import CliRunner

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
            kebab_case("ApiSix deployment Notes"), "api-six-deployment-notes"
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


class CamelCaseSplitTests(unittest.TestCase):
    def test_camel_and_pascal_case_words_are_split(self) -> None:
        cases = {
            "myVarName": "my-var-name",
            "HTTPServer": "http-server",
            "ApiSix": "api-six",
            "getHTTPResponse2": "get-http-response2",
            "iPhone": "i-phone",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slugify(text), expected)

    def test_already_separated_text_is_unchanged(self) -> None:
        self.assertEqual(slugify("hello world"), "hello-world")
        self.assertEqual(slugify("hello_world-again"), "hello-world-again")

    def test_digits_stay_attached_to_the_preceding_word(self) -> None:
        self.assertEqual(slugify("version2Beta"), "version2-beta")

    def test_preserve_case_keeps_letters_but_still_splits(self) -> None:
        self.assertEqual(slugify("myVarName", lowercase=False), "my-Var-Name")

    def test_separator_is_used_between_split_words(self) -> None:
        self.assertEqual(slugify("myVarName", separator="_"), "my_var_name")
        self.assertEqual(slugify("myVarName", separator="--"), "my--var--name")

    def test_kebab_case_splits_camel_case_and_keeps_date_prefix(self) -> None:
        self.assertEqual(kebab_case("myVarName"), "my-var-name")
        self.assertEqual(
            kebab_case("HTTPServer", date_prefix=date(2026, 7, 11)),
            "2026-07-11_http-server",
        )


class SlugCommandTests(unittest.TestCase):
    def test_slug_splits_camel_case_text(self) -> None:
        result = runner.invoke(app, ["text", "slug", "getHTTPResponse2"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "get-http-response2")

    def test_slug_preserve_case_splits_without_lowercasing(self) -> None:
        result = runner.invoke(app, ["text", "slug", "myVarName", "--preserve-case"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "my-Var-Name")

    def test_slug_separator_applies_between_split_words(self) -> None:
        result = runner.invoke(app, ["text", "slug", "myVarName", "-s", "_"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "my_var_name")

    def test_slug_reads_stdin_when_text_is_omitted(self) -> None:
        result = runner.invoke(app, ["text", "slug"], input="ApiSix deployment\n\nNotes")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "api-six-deployment-notes")

    def test_slug_date_prefix_uses_underscore_and_iso_date(self) -> None:
        with mock.patch("jzr_tools.commands.text.date") as date_mock:
            date_mock.today.return_value = date(2026, 7, 11)

            result = runner.invoke(app, ["text", "slug", "Hello World", "--date"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "2026-07-11_hello-world")

    def test_slug_include_date_flag_is_the_same_as_date(self) -> None:
        result = runner.invoke(app, ["text", "slug", "Hello World", "--include-date"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.stdout.strip(), r"^\d{4}-\d{2}-\d{2}_hello-world$")
        self.assertTrue(result.stdout.startswith(date.today().isoformat() + "_"))

    def test_slug_date_prefix_keeps_underscore_with_another_separator(self) -> None:
        with mock.patch("jzr_tools.commands.text.date") as date_mock:
            date_mock.today.return_value = date(2026, 7, 11)

            result = runner.invoke(
                app, ["text", "slug", "Hello World", "--date", "-s", "."]
            )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "2026-07-11_hello.world")

    def test_slug_of_punctuation_only_with_date_prints_no_prefix(self) -> None:
        with mock.patch("jzr_tools.commands.text.date") as date_mock:
            date_mock.today.return_value = date(2026, 7, 11)

            result = runner.invoke(app, ["text", "slug", "!!!", "--date"])

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(result.stderr.strip())

    def test_slug_of_empty_stdin_is_an_error(self) -> None:
        result = runner.invoke(app, ["text", "slug"], input="  \n")

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(result.stderr.strip())

    def test_slug_copy_sends_the_result_to_the_clipboard(self) -> None:
        with mock.patch("jzr_tools.commands.text.copy_to_clipboard") as copy_mock:
            result = runner.invoke(app, ["text", "slug", "myVarName", "-c"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "my-var-name")
        copy_mock.assert_called_once_with("my-var-name")

    def test_slug_copy_long_flag_copies_too(self) -> None:
        with mock.patch("jzr_tools.commands.text.copy_to_clipboard") as copy_mock:
            result = runner.invoke(app, ["text", "slug", "Hello World", "--copy"])

        self.assertEqual(result.exit_code, 0, result.output)
        copy_mock.assert_called_once_with("hello-world")

    def test_slug_copy_failure_reports_on_stderr_and_exits_1(self) -> None:
        with mock.patch(
            "jzr_tools.commands.text.copy_to_clipboard",
            side_effect=RuntimeError("no clipboard tool found"),
        ):
            result = runner.invoke(app, ["text", "slug", "Hello World", "-c"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("no clipboard tool found", result.stderr)


class KebabAliasTests(unittest.TestCase):
    def test_kebab_accepts_separator_and_preserve_case(self) -> None:
        result = runner.invoke(
            app, ["text", "kebab", "myVarName", "-s", "_", "--preserve-case"]
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "my_Var_Name")

    def test_kebab_gives_the_same_output_as_slug(self) -> None:
        cases = (["myVarName"], ["Hello World", "-s", "."], ["HTTPServer", "--preserve-case"])
        for args in cases:
            with self.subTest(args=args):
                slug_result = runner.invoke(app, ["text", "slug", *args])
                kebab_result = runner.invoke(app, ["text", "kebab", *args])

                self.assertEqual(kebab_result.exit_code, 0, kebab_result.output)
                self.assertEqual(kebab_result.stdout, slug_result.stdout)

    def test_kebab_reads_stdin_and_adds_date_prefix(self) -> None:
        with mock.patch("jzr_tools.commands.text.date") as date_mock:
            date_mock.today.return_value = date(2026, 7, 11)

            result = runner.invoke(app, ["text", "kebab", "--date"], input="myVarName")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "2026-07-11_my-var-name")

    def test_kebab_is_hidden_from_text_help_but_slug_is_listed(self) -> None:
        result = runner.invoke(app, ["text", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        commands = result.stdout.split("Commands:", 1)[1]
        listed = re.findall(r"^  (\S+)", commands, flags=re.MULTILINE)
        self.assertIn("slug", listed)
        self.assertNotIn("kebab", listed)

    def test_kebab_help_still_works_when_hidden(self) -> None:
        result = runner.invoke(app, ["text", "kebab", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--separator", result.stdout)
        self.assertIn("--preserve-case", result.stdout)


if __name__ == "__main__":
    unittest.main()
