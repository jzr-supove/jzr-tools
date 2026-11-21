"""Acceptance tests for placeholders in stored commands (jzr_tools.core.bindings).

``{N}`` stands for the N-th argument given when the binding runs, ``{*}`` for
the arguments after the highest N, and ``{{N}}`` / ``{{*}}`` for the literal
text. These tests call the core functions directly; nothing is run.
"""

from __future__ import annotations

import base64
import unittest


def _bindings():
    from jzr_tools.core import bindings

    return bindings


def _encoded(text: str) -> str:
    return base64.b64encode(text.encode("utf-16-le")).decode("ascii")


class FillTests(unittest.TestCase):
    def fill(self, words, arguments):
        return _bindings().fill(words, arguments)

    def test_a_placeholder_alone_is_replaced_by_the_argument(self) -> None:
        self.assertEqual(
            self.fill(["git", "commit", "-m", "{1}", "--no-verify"], ["fix"]),
            ["git", "commit", "-m", "fix", "--no-verify"],
        )

    def test_a_placeholder_inside_a_word_keeps_the_text_around_it(self) -> None:
        self.assertEqual(self.fill(["git", "--m={1}"], ["hello"]), ["git", "--m=hello"])
        self.assertEqual(
            self.fill(["tail", "-f", "/var/log/{1}.log"], ["app"]),
            ["tail", "-f", "/var/log/app.log"],
        )

    def test_a_value_with_spaces_stays_one_word(self) -> None:
        self.assertEqual(
            self.fill(["git", "commit", "-m", "{1}"], ["fix the typo"]),
            ["git", "commit", "-m", "fix the typo"],
        )
        self.assertEqual(self.fill(["x", "--m={1}"], ["a b  c"]), ["x", "--m=a b  c"])

    def test_a_value_is_not_read_for_placeholders_or_escapes(self) -> None:
        self.assertEqual(self.fill(["echo", "{1}", "{2}"], ["{2}", "x"]), ["echo", "{2}", "x"])
        self.assertEqual(self.fill(["echo", "{1}"], ["{{1}}"]), ["echo", "{{1}}"])
        self.assertEqual(self.fill(["echo", "{1}"], ["{*}"]), ["echo", "{*}"])

    def test_the_same_number_twice_gets_the_same_value(self) -> None:
        self.assertEqual(
            self.fill(["cp", "{1}", "{1}.bak"], ["notes.txt"]),
            ["cp", "notes.txt", "notes.txt.bak"],
        )
        self.assertEqual(self.fill(["x", "{1}-{1}"], ["ab"]), ["x", "ab-ab"])

    def test_numbers_pick_arguments_in_any_order(self) -> None:
        self.assertEqual(self.fill(["mv", "{2}", "{1}"], ["a", "b"]), ["mv", "b", "a"])

    def test_numbers_above_nine_are_read_whole(self) -> None:
        arguments = [str(n) for n in range(1, 11)]
        words = ["x", "{10}", *(f"{{{n}}}" for n in range(1, 10))]
        self.assertEqual(self.fill(words, arguments), ["x", "10", *map(str, range(1, 10))])

    def test_star_expands_to_one_word_per_argument(self) -> None:
        self.assertEqual(
            self.fill(["git", "log", "{*}", "--oneline"], ["-5", "main"]),
            ["git", "log", "-5", "main", "--oneline"],
        )

    def test_star_with_no_arguments_leaves_no_word(self) -> None:
        self.assertEqual(
            self.fill(["git", "log", "{*}", "--oneline"], []),
            ["git", "log", "--oneline"],
        )

    def test_star_keeps_arguments_with_spaces_as_single_words(self) -> None:
        self.assertEqual(self.fill(["echo", "{*}"], ["a b", "c"]), ["echo", "a b", "c"])

    def test_extra_arguments_go_on_the_end_when_there_is_no_star(self) -> None:
        self.assertEqual(
            self.fill(["git", "commit", "-m", "{1}", "--no-verify"], ["msg", "-a", "--amend"]),
            ["git", "commit", "-m", "msg", "--no-verify", "-a", "--amend"],
        )

    def test_extra_arguments_go_to_star_and_nothing_is_added_at_the_end(self) -> None:
        self.assertEqual(
            self.fill(["run", "{*}", "--last"], ["a", "b"]),
            ["run", "a", "b", "--last"],
        )

    def test_a_number_and_star_together_split_the_arguments(self) -> None:
        self.assertEqual(
            self.fill(["ssh", "{1}", "tail", "{*}", "-n1"], ["host", "-f", "app.log"]),
            ["ssh", "host", "tail", "-f", "app.log", "-n1"],
        )
        self.assertEqual(
            self.fill(["ssh", "{1}", "tail", "{*}", "-n1"], ["host"]),
            ["ssh", "host", "tail", "-n1"],
        )

    def test_star_starts_after_the_highest_number_not_the_first(self) -> None:
        self.assertEqual(
            self.fill(["x", "{2}", "{1}", "{*}"], ["a", "b", "c", "d"]),
            ["x", "b", "a", "c", "d"],
        )

    def test_an_empty_argument_counts_as_given(self) -> None:
        self.assertEqual(
            self.fill(["git", "commit", "-m", "{1}"], [""]),
            ["git", "commit", "-m", ""],
        )
        self.assertEqual(self.fill(["x", "--m={1}"], [""]), ["x", "--m="])

    def test_a_doubled_brace_escape_gives_the_literal_placeholder_text(self) -> None:
        self.assertEqual(self.fill(["echo", "{{1}}"], []), ["echo", "{1}"])
        self.assertEqual(self.fill(["echo", "{{*}}"], []), ["echo", "{*}"])
        self.assertEqual(self.fill(["echo", "a{{2}}b"], []), ["echo", "a{2}b"])

    def test_an_escape_is_not_a_placeholder_so_arguments_still_go_on_the_end(self) -> None:
        self.assertEqual(self.fill(["echo", "{{1}}"], ["x"]), ["echo", "{1}", "x"])

    def test_an_escape_and_a_real_placeholder_in_one_word(self) -> None:
        self.assertEqual(self.fill(["echo", "{{1}}={1}"], ["v"]), ["echo", "{1}=v"])

    def test_other_braces_are_left_alone(self) -> None:
        words = ["curl", "-d", '{"a":1}', "{}", "{name}", "{1:main}", "{ 1 }", "{1,2}", "{ *}"]
        self.assertEqual(self.fill(words, []), words)

    def test_other_braces_are_left_alone_next_to_a_placeholder(self) -> None:
        self.assertEqual(
            self.fill(["curl", "-d", '{"id":{1}}'], ["7"]),
            ["curl", "-d", '{"id":7}'],
        )

    def test_a_command_without_placeholders_gets_every_argument_on_the_end(self) -> None:
        self.assertEqual(self.fill(["git", "status"], ["-s", "-b"]), ["git", "status", "-s", "-b"])
        self.assertEqual(self.fill(["git", "status"], []), ["git", "status"])

    def test_a_placeholder_may_stand_in_the_first_word(self) -> None:
        self.assertEqual(self.fill(["{1}", "--version"], ["python"]), ["python", "--version"])
        self.assertEqual(self.fill(["{1}-config", "--cflags"], ["python3"]), ["python3-config", "--cflags"])

    def test_the_words_given_are_not_changed(self) -> None:
        words = ["git", "{1}"]
        arguments = ["a", "b"]
        result = self.fill(words, arguments)
        self.assertEqual(words, ["git", "{1}"])
        self.assertEqual(arguments, ["a", "b"])
        self.assertIsInstance(result, list)

    def test_too_few_arguments_raise_missing_arguments_with_the_counts(self) -> None:
        b = _bindings()
        with self.assertRaises(b.MissingArguments) as caught:
            b.fill(["git", "commit", "-m", "{1}"], [])
        self.assertEqual(caught.exception.needed, 1)
        self.assertEqual(caught.exception.given, 0)
        with self.assertRaises(b.MissingArguments) as caught:
            b.fill(["cp", "{1}", "{2}"], ["a"])
        self.assertEqual(caught.exception.needed, 2)
        self.assertEqual(caught.exception.given, 1)

    def test_the_highest_number_sets_how_many_arguments_are_needed(self) -> None:
        b = _bindings()
        with self.assertRaises(b.MissingArguments) as caught:
            b.fill(["x", "{1}", "{2}", "{3}", "{*}"], ["a", "b"])
        self.assertEqual((caught.exception.needed, caught.exception.given), (3, 2))

    def test_star_alone_never_needs_arguments(self) -> None:
        self.assertEqual(self.fill(["x", "{*}"], []), ["x"])

    def test_bad_placeholders_raise_the_same_errors_as_the_check(self) -> None:
        b = _bindings()
        cases = [
            (["x", "--a={*}"], "{*} must be a word on its own."),
            (["x", "{*}", "{*}"], "{*} must be a word on its own."),
            (["x", "{2}"], "{2} is used but {1} is not; number the arguments from {1}."),
            (["x", "{0}"], "{0} is not a placeholder; they start at {1}."),
        ]
        for words, text in cases:
            with self.subTest(words=words):
                with self.assertRaises(b.BindingError) as caught:
                    b.fill(words, ["a", "b", "c"])
                self.assertNotIsInstance(caught.exception, b.MissingArguments)
                self.assertEqual(str(caught.exception), text)

    def test_a_bad_placeholder_is_reported_even_with_too_few_arguments(self) -> None:
        b = _bindings()
        with self.assertRaises(b.BindingError) as caught:
            b.fill(["x", "{3}"], [])
        self.assertNotIsInstance(caught.exception, b.MissingArguments)


class MissingArgumentsTests(unittest.TestCase):
    def raised(self, words, arguments):
        b = _bindings()
        with self.assertRaises(b.MissingArguments) as caught:
            b.fill(words, arguments)
        return caught.exception

    def test_it_is_a_binding_error(self) -> None:
        b = _bindings()
        self.assertTrue(issubclass(b.MissingArguments, b.BindingError))

    def test_one_argument_is_written_in_the_singular(self) -> None:
        self.assertEqual(str(self.raised(["x", "{1}"], [])), "needs 1 argument (got 0)")

    def test_several_arguments_are_written_in_the_plural(self) -> None:
        self.assertEqual(str(self.raised(["x", "{1}", "{2}"], ["a"])), "needs 2 arguments (got 1)")

    def test_the_singular_is_used_for_one_even_when_star_is_present(self) -> None:
        self.assertEqual(str(self.raised(["x", "{1}", "{*}"], [])), "needs 1 argument (got 0)")


class CheckPlaceholdersTests(unittest.TestCase):
    def message(self, words) -> str:
        b = _bindings()
        with self.assertRaises(b.BindingError) as caught:
            b.check_placeholders(words)
        return str(caught.exception)

    def ok(self, words) -> None:
        self.assertIsNone(_bindings().check_placeholders(words))

    def test_star_inside_a_word_is_refused(self) -> None:
        self.assertEqual(self.message(["x", "--a={*}"]), "{*} must be a word on its own.")
        self.assertEqual(self.message(["x", "pre{*}"]), "{*} must be a word on its own.")
        self.assertEqual(self.message(["x", "{*}post"]), "{*} must be a word on its own.")

    def test_star_twice_is_refused(self) -> None:
        self.assertEqual(self.message(["x", "{*}", "{*}"]), "{*} must be a word on its own.")

    def test_a_gap_names_the_lowest_missing_number_and_the_one_above_it(self) -> None:
        self.assertEqual(
            self.message(["x", "{2}"]),
            "{2} is used but {1} is not; number the arguments from {1}.",
        )
        self.assertEqual(
            self.message(["x", "{1}", "{3}"]),
            "{3} is used but {2} is not; number the arguments from {1}.",
        )
        self.assertEqual(
            self.message(["x", "{1}", "{2}", "{5}", "{7}"]),
            "{5} is used but {3} is not; number the arguments from {1}.",
        )

    def test_a_gap_is_found_whatever_the_order_the_numbers_appear_in(self) -> None:
        self.assertEqual(
            self.message(["x", "{3}", "{1}"]),
            "{3} is used but {2} is not; number the arguments from {1}.",
        )

    def test_zero_is_refused(self) -> None:
        self.assertEqual(self.message(["x", "{0}"]), "{0} is not a placeholder; they start at {1}.")

    def test_a_leading_zero_is_refused_and_named_as_written(self) -> None:
        self.assertEqual(self.message(["x", "{01}"]), "{01} is not a placeholder; they start at {1}.")
        self.assertEqual(self.message(["x", "{007}"]), "{007} is not a placeholder; they start at {1}.")

    def test_a_refused_number_inside_a_word_is_found(self) -> None:
        self.assertEqual(self.message(["x", "a{0}b"]), "{0} is not a placeholder; they start at {1}.")

    def test_good_placeholders_pass(self) -> None:
        self.ok(["git", "commit", "-m", "{1}", "--no-verify"])
        self.ok(["x", "{1}", "{2}", "{*}"])
        self.ok(["x", "{2}", "{1}", "{1}"])
        self.ok(["x", "--m={1}", "/var/log/{2}.log"])
        self.ok(["x", "{*}"])
        self.ok(["git", "status"])

    def test_escapes_are_never_errors(self) -> None:
        self.ok(["x", "{{1}}"])
        self.ok(["x", "{{*}}"])
        self.ok(["x", "{{*}}", "{{*}}", "{*}"])
        self.ok(["x", "a{{*}}b"])
        self.ok(["x", "{{0}}"])
        self.ok(["x", "{{2}}"])

    def test_a_lone_escape_does_not_make_a_gap_with_a_real_placeholder(self) -> None:
        self.ok(["x", "{{3}}", "{1}"])

    def test_other_braces_are_never_errors(self) -> None:
        self.ok(["curl", "-d", '{"a":1}', "{}", "{name}", "{1:main}", "{ 1 }", "{-1}"])


class PlaceholderSignatureTests(unittest.TestCase):
    def sig(self, words):
        return _bindings().placeholder_signature(words)

    def test_no_placeholders(self) -> None:
        self.assertEqual(self.sig(["git", "status"]), (0, False))

    def test_the_highest_number_is_returned(self) -> None:
        self.assertEqual(self.sig(["x", "{1}"]), (1, False))
        self.assertEqual(self.sig(["x", "{2}", "{1}", "{1}"]), (2, False))
        self.assertEqual(self.sig(["x", "a{1}b{2}"]), (2, False))

    def test_star_is_reported(self) -> None:
        self.assertEqual(self.sig(["x", "{*}"]), (0, True))
        self.assertEqual(self.sig(["x", "{1}", "{*}"]), (1, True))

    def test_escapes_and_other_braces_do_not_count(self) -> None:
        self.assertEqual(self.sig(["x", "{{1}}", "{{*}}", "{}", "{1:main}"]), (0, False))

    def test_bad_placeholders_are_refused(self) -> None:
        b = _bindings()
        for words in (["x", "--a={*}"], ["x", "{2}"], ["x", "{0}"], ["x", "{*}", "{*}"]):
            with self.subTest(words=words):
                with self.assertRaises(b.BindingError):
                    b.placeholder_signature(words)


class ArityNoteTests(unittest.TestCase):
    def note(self, words) -> str:
        return _bindings().arity_note(words)

    def test_nothing_to_say_without_placeholders(self) -> None:
        self.assertEqual(self.note(["git", "status"]), "")
        self.assertEqual(self.note(["echo", "{{1}}", "{}"]), "")

    def test_one_argument_is_singular(self) -> None:
        self.assertEqual(self.note(["git", "commit", "-m", "{1}"]), "takes 1 argument")
        self.assertEqual(self.note(["x", "{1}", "{1}"]), "takes 1 argument")

    def test_several_arguments_are_plural(self) -> None:
        self.assertEqual(self.note(["x", "{1}", "{2}"]), "takes 2 arguments")
        self.assertEqual(self.note(["x", "{3}", "{2}", "{1}"]), "takes 3 arguments")

    def test_star_adds_and_more(self) -> None:
        self.assertEqual(self.note(["x", "{1}", "{*}"]), "takes 1 argument and more")
        self.assertEqual(self.note(["x", "{1}", "{2}", "{*}"]), "takes 2 arguments and more")

    def test_star_alone_takes_any_arguments(self) -> None:
        self.assertEqual(self.note(["git", "log", "{*}", "--oneline"]), "takes any arguments")

    def test_bad_placeholders_are_refused(self) -> None:
        b = _bindings()
        with self.assertRaises(b.BindingError):
            b.arity_note(["x", "{2}"])


class PowerShellScriptBlockTests(unittest.TestCase):
    TAIL = ["-inputFormat", "xml", "-outputFormat", "text"]

    def block(self, words):
        return _bindings().powershell_script_block(words)

    def test_a_word_of_its_own_is_recognised(self) -> None:
        words = ["python", "x.py", "-encodedCommand", "MQA=", "-inputFormat", "xml", "-outputFormat", "text"]
        self.assertEqual(self.block(words), "{1}")

    def test_a_block_at_the_start_of_a_word_is_recognised(self) -> None:
        words = ["--m=", "-encodedCommand", "MQA=", "-inputFormat", "xml", "-outputFormat", "text"]
        self.assertEqual(self.block(words), "{1}")

    def test_a_block_in_the_middle_of_a_word_is_recognised(self) -> None:
        words = ["x", "-encodedCommand", "MQA=", "y", "-inputFormat", "xml", "-outputFormat", "text"]
        self.assertEqual(self.block(words), "{1}")

    def test_the_text_is_the_decoded_base64_in_braces(self) -> None:
        for text in ("*", "2", "name", "a b"):
            with self.subTest(text=text):
                words = ["git", "-encodedCommand", _encoded(text), *self.TAIL]
                self.assertEqual(self.block(words), "{" + text + "}")

    def test_the_flag_is_matched_without_regard_to_case(self) -> None:
        for flag in ("-ENCODEDCOMMAND", "-encodedcommand", "-EncodedCommand"):
            with self.subTest(flag=flag):
                self.assertEqual(self.block(["x", flag, "MQA=", *self.TAIL]), "{1}")

    def test_text_that_does_not_decode_is_shown_as_dots(self) -> None:
        self.assertEqual(self.block(["x", "-encodedCommand", "not base64!", *self.TAIL]), "{...}")
        # Valid base64, but an odd number of bytes cannot be UTF-16.
        self.assertEqual(self.block(["x", "-encodedCommand", "QQ==", *self.TAIL]), "{...}")

    def test_ordinary_words_are_not_a_script_block(self) -> None:
        self.assertIsNone(self.block(["git", "commit", "-m", "{1}"]))
        self.assertIsNone(self.block(["git", "status"]))
        self.assertIsNone(self.block([]))

    def test_the_flag_without_the_four_trailing_words_is_not_a_script_block(self) -> None:
        self.assertIsNone(self.block(["x", "-encodedCommand", "MQA="]))
        self.assertIsNone(self.block(["x", "-encodedCommand", "MQA=", "-inputFormat", "xml"]))
        self.assertIsNone(
            self.block(["x", "-encodedCommand", "MQA=", "-inputFormat", "xml", "-outputFormat"])
        )

    def test_the_trailing_words_without_the_flag_are_not_a_script_block(self) -> None:
        self.assertIsNone(self.block(["x", *self.TAIL]))

    def test_the_trailing_words_must_have_the_values_powershell_writes(self) -> None:
        self.assertIsNone(self.block(["x", "-encodedCommand", "MQA=", "-inputFormat", "text", "-outputFormat", "xml"]))
        self.assertIsNone(self.block(["x", "-encodedCommand", "MQA=", "-outputFormat", "text", "-inputFormat", "xml"]))


if __name__ == "__main__":
    unittest.main()
