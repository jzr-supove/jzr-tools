"""Acceptance tests for the Windows additions to ``jzr host clean`` (the command).

``cleanup.scan`` / ``cleanup.clean`` and the prompts are faked, as in
``test_host_clean_cli``. The platform is faked through the module-level
function ``jzr_tools.core.cleanup._windows() -> bool`` (patched with
``create=True``; see the seam list in ``test_cleanup_windows``), and the
outcome of a removal carries ``Outcome.in_use`` (files kept because a program
held them open).
"""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools import prompts
from jzr_tools.cli import app
from jzr_tools.core import cleanup

MIB = 1024**2
GIB = 1024**3

IS_INTERACTIVE = "jzr_tools.commands.host_clean._is_interactive"
NPM = r"C:\Program Files\nodejs\npm.cmd"
TEMP_DIRECTORY = Path("scratch") / "Temp"


def temp_item(size: int = round(17.2 * GIB)) -> cleanup.Item:
    return cleanup.Item(
        "temp", "Temp files", "review", size, "Programs create these again.", paths=(TEMP_DIRECTORY,)
    )


def linux_items() -> list[cleanup.Item]:
    """What a scan on Linux reports: nothing of the Windows ids."""
    return [
        cleanup.Item("pip", "pip cache", "safe", 512 * MIB, "n", paths=(Path("cache") / "pip",)),
        cleanup.Item("trash", "Trash", "review", 2 * GIB, "n", paths=(Path("share") / "Trash",)),
    ]


class WindowsCleanCase(unittest.TestCase):
    windows = True

    def setUp(self) -> None:
        self.items: list[cleanup.Item] = [temp_item()]
        self.outcomes: dict[str, cleanup.Outcome] = {}
        self.interactive = False

    def invoke(self, *args: str, items: list[cleanup.Item] | None = None):
        scan_items = self.items if items is None else items

        def clean(item: cleanup.Item, **kwargs: object) -> cleanup.Outcome:
            return self.outcomes.get(item.id) or cleanup.Outcome(item, True, item.size_bytes)

        def choose(message: str, entries: list[object]) -> object:
            self.entries = entries
            return [e.value for e in entries if isinstance(e, prompts.Choice) and e.checked]

        with ExitStack() as stack:
            self.scan = stack.enter_context(mock.patch.object(cleanup, "scan", return_value=scan_items))
            self.clean = stack.enter_context(mock.patch.object(cleanup, "clean", side_effect=clean))
            stack.enter_context(mock.patch.object(prompts, "choose", side_effect=choose))
            stack.enter_context(mock.patch.object(prompts, "confirm", return_value=True))
            stack.enter_context(
                mock.patch.object(cleanup, "_windows", return_value=self.windows, create=True)
            )
            stack.enter_context(mock.patch(IS_INTERACTIVE, return_value=self.interactive))
            return CliRunner().invoke(app, ["host", "clean", *args])


class FilesInUseReportTests(WindowsCleanCase):
    def outcome(self, in_use: int, freed: int = round(17.2 * GIB)) -> None:
        self.outcomes["temp"] = cleanup.Outcome(self.items[0], True, freed, in_use=in_use)

    def test_the_line_reads_removed_size_then_how_many_files_were_kept(self) -> None:
        self.outcome(31)
        result = self.invoke("--yes", "--only", "temp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Temp files: removed 17.2 GiB; 31 files in use were kept", result.stdout.splitlines())

    def test_the_item_counts_as_cleaned_so_the_run_succeeds(self) -> None:
        self.outcome(31)
        result = self.invoke("--yes", "--only", "temp")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotIn("failed", result.output)
        self.assertEqual(result.stderr, "")
        self.assertIn("Removed about 17.2 GiB.", result.stdout)

    def test_the_title_comes_from_the_item(self) -> None:
        self.items = [
            cleanup.Item("crash-dumps", "Crash dumps", "review", 3 * GIB, "n", paths=(Path("dumps"),))
        ]
        self.outcomes["crash-dumps"] = cleanup.Outcome(self.items[0], True, 3 * GIB, in_use=4)
        result = self.invoke("--yes", "--only", "crash-dumps")
        self.assertIn("Crash dumps: removed 3.0 GiB; 4 files in use were kept", result.stdout)

    def test_no_files_in_use_leaves_the_line_as_before(self) -> None:
        self.outcome(0)
        result = self.invoke("--yes", "--only", "temp")
        lines = result.stdout.splitlines()
        self.assertIn("Temp files: removed 17.2 GiB", lines)
        self.assertNotIn("in use", result.output)

    def test_a_failed_item_is_still_reported_as_failed_on_stderr(self) -> None:
        self.outcomes["temp"] = cleanup.Outcome(self.items[0], False, 0, "denied")
        result = self.invoke("--yes", "--only", "temp")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("Temp files: failed: denied", result.stderr)


class OnlyIdsOnLinuxTests(WindowsCleanCase):
    windows = False

    def setUp(self) -> None:
        super().setUp()
        self.items = linux_items()

    def test_a_windows_id_on_linux_is_nothing_to_remove_not_a_usage_error(self) -> None:
        result = self.invoke("--only", "windows-temp", "--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("windows-temp: nothing to remove", result.stdout)
        self.clean.assert_not_called()

    def test_every_windows_id_is_accepted(self) -> None:
        for item_id in ("gpu-shaders", "temp", "crash-dumps", "windows-temp", "windows-update"):
            with self.subTest(item_id=item_id):
                result = self.invoke("--only", item_id, "--dry-run")
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn(f"{item_id}: nothing to remove", result.stdout)

    def test_windows_and_linux_ids_can_be_mixed(self) -> None:
        result = self.invoke("--only", "pip,windows-update", "--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([c.args[0].id for c in self.clean.call_args_list], ["pip"])
        self.assertIn("windows-update: nothing to remove", result.stdout)

    def test_the_known_list_in_the_usage_error_names_the_windows_ids(self) -> None:
        result = self.invoke("--only", "bogus", "--yes")
        self.assertEqual(result.exit_code, 2, result.output)
        for item_id in ("bogus", "windows-temp", "gpu-shaders", "crash-dumps"):
            self.assertIn(item_id, result.stderr)


class OnlyIdsOnWindowsTests(WindowsCleanCase):
    def test_linux_ids_are_accepted_on_windows_too(self) -> None:
        for item_id in ("journal", "dnf", "apt", "thumbnails", "mesa-shaders"):
            with self.subTest(item_id=item_id):
                result = self.invoke("--only", item_id, "--yes")
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn(f"{item_id}: nothing to remove", result.stdout)


class CommandQuotingTests(WindowsCleanCase):
    def setUp(self) -> None:
        super().setUp()
        self.items = [
            cleanup.Item("npm", "npm cache", "safe", 512 * MIB, "n", command=(NPM, "cache", "clean", "--force"))
        ]

    def test_dry_run_quotes_the_would_run_line_the_windows_way(self) -> None:
        result = self.invoke("--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(f'would run: "{NPM}" cache clean --force', result.stdout.splitlines())

    def test_json_action_is_quoted_the_windows_way(self) -> None:
        result = self.invoke("--json")
        (entry,) = json.loads(result.stdout)["items"]
        self.assertEqual(entry["action"], f'"{NPM}" cache clean --force')

    def test_the_picker_description_shows_the_quoted_runs_line(self) -> None:
        self.interactive = True
        self.invoke()
        (choice,) = [e for e in self.entries if isinstance(e, prompts.Choice)]
        self.assertIn(f'Runs: "{NPM}" cache clean --force', choice.description)

    def test_linux_keeps_the_plain_join(self) -> None:
        self.windows = False
        result = self.invoke("--dry-run")
        self.assertIn(f"would run: {NPM} cache clean --force", result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
