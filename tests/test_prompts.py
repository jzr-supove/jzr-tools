"""Tests for ``jzr_tools.prompts`` with ``questionary`` replaced by a fake."""

from __future__ import annotations

import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeChoice:
    def __init__(self, title, value=None, disabled=None, checked=False, description=None, **extra):
        self.title = title
        self.value = value
        self.disabled = disabled
        self.checked = checked
        self.description = description
        self.extra = extra


class FakeSeparator:
    def __init__(self, line=None):
        self.line = line


class FakeQuestion:
    def __init__(self, answer):
        self.answer = answer
        self.asked = 0

    def ask(self, *args, **kwargs):
        self.asked += 1
        return self.answer


def make_questionary(answer):
    module = types.ModuleType("questionary")
    module.Choice = FakeChoice
    module.Separator = FakeSeparator
    module.calls = []

    def checkbox(message, choices=None, **kwargs):
        question = FakeQuestion(answer)
        module.calls.append(("checkbox", message, list(choices), kwargs, question))
        return question

    def confirm(message, default=True, **kwargs):
        question = FakeQuestion(answer)
        module.calls.append(("confirm", message, default, kwargs, question))
        return question

    module.checkbox = checkbox
    module.confirm = confirm
    return module


class PromptsCase(unittest.TestCase):
    def fake(self, answer):
        from jzr_tools import prompts

        module = make_questionary(answer)
        patcher = mock.patch.dict(sys.modules, {"questionary": module})
        patcher.start()
        self.addCleanup(patcher.stop)
        return prompts, module


class ChooseTests(PromptsCase):
    def test_choices_and_headings_become_questionary_choices_and_separators(self) -> None:
        prompts, q = self.fake(["a"])
        prompts.choose(
            "Pick",
            [
                prompts.Heading("Safe to remove"),
                prompts.Choice("a", "Alpha 1.0 GiB", checked=True),
                prompts.Choice("b", "Beta", checked=False, disabled="needs root"),
                prompts.Heading("Review"),
                prompts.Choice("c", "Gamma", description="what it costs"),
            ],
        )
        kind, message, choices, _kwargs, _question = q.calls[0]
        self.assertEqual((kind, message), ("checkbox", "Pick"))
        self.assertEqual([type(c) for c in choices], [FakeSeparator, FakeChoice, FakeChoice, FakeSeparator, FakeChoice])
        self.assertIn("Safe to remove", choices[0].line)
        self.assertIn("Review", choices[3].line)
        alpha, beta, gamma = choices[1], choices[2], choices[4]
        self.assertEqual((alpha.title, alpha.value, alpha.checked), ("Alpha 1.0 GiB", "a", True))
        self.assertFalse(alpha.disabled)
        self.assertEqual((beta.title, beta.value, beta.checked), ("Beta", "b", False))
        self.assertEqual(beta.disabled, "needs root")
        self.assertEqual((gamma.title, gamma.value, gamma.checked), ("Gamma", "c", False))
        self.assertEqual(gamma.description, "what it costs")

    def test_description_is_only_passed_when_given(self) -> None:
        prompts, q = self.fake([])

        class Strict(FakeChoice):
            def __init__(self, title, value=None, disabled=None, checked=False, **extra):
                if "description" in extra:
                    raise AssertionError("description passed although empty")
                super().__init__(title, value, disabled, checked, **extra)

        q.Choice = Strict
        prompts.choose("Pick", [prompts.Choice("a", "Alpha")])

    def test_returns_the_ticked_values(self) -> None:
        prompts, q = self.fake(["a", "c"])
        result = prompts.choose("Pick", [prompts.Choice("a", "A"), prompts.Choice("c", "C")])
        self.assertEqual(result, ["a", "c"])
        self.assertEqual(q.calls[0][4].asked, 1)

    def test_values_may_be_arbitrary_objects(self) -> None:
        marker = object()
        prompts, _q = self.fake([marker])
        self.assertEqual(prompts.choose("Pick", [prompts.Choice(marker, "A")]), [marker])

    def test_nothing_ticked_is_an_empty_list_not_none(self) -> None:
        prompts, _q = self.fake([])
        self.assertEqual(prompts.choose("Pick", [prompts.Choice("a", "A")]), [])

    def test_cancelling_returns_none(self) -> None:
        prompts, _q = self.fake(None)
        self.assertIsNone(prompts.choose("Pick", [prompts.Choice("a", "A")]))


class ConfirmTests(PromptsCase):
    def test_returns_the_answer(self) -> None:
        for answer in (True, False):
            with self.subTest(answer=answer):
                prompts, q = self.fake(answer)
                self.assertIs(prompts.confirm("Sure?"), answer)
                self.assertEqual(q.calls[-1][1], "Sure?")

    def test_default_is_no_unless_asked_otherwise(self) -> None:
        prompts, q = self.fake(True)
        prompts.confirm("Sure?")
        self.assertIs(q.calls[-1][2], False)

    def test_default_is_passed_through(self) -> None:
        prompts, q = self.fake(True)
        prompts.confirm("Sure?", default=True)
        self.assertIs(q.calls[-1][2], True)

    def test_cancelling_is_false(self) -> None:
        prompts, _q = self.fake(None)
        self.assertIs(prompts.confirm("Sure?", default=True), False)


class DataTests(unittest.TestCase):
    def test_choice_and_heading_are_frozen_with_documented_defaults(self) -> None:
        import dataclasses

        from jzr_tools import prompts

        choice = prompts.Choice("v", "label")
        self.assertEqual((choice.value, choice.label, choice.checked, choice.disabled, choice.description),
                         ("v", "label", False, "", ""))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            choice.checked = True  # type: ignore[misc]
        self.assertEqual(prompts.Heading("t").text, "t")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            prompts.Heading("t").text = "u"  # type: ignore[misc]


class ImportCostTests(unittest.TestCase):
    def test_importing_prompts_does_not_import_questionary(self) -> None:
        code = (
            "import sys, jzr_tools.prompts\n"
            "bad = [m for m in sys.modules if m.split('.')[0] in ('questionary', 'prompt_toolkit')]\n"
            "print('@@' + ','.join(bad))\n"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=PROJECT_ROOT)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "@@")


if __name__ == "__main__":
    unittest.main()
