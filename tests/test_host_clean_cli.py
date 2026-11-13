"""Acceptance tests for the ``jzr host clean`` command.

``cleanup.scan`` / ``cleanup.clean`` and the prompts are faked, so nothing is
scanned or removed. The command decides "interactive" through the module-level
function ``jzr_tools.commands.host_clean._is_interactive() -> bool`` (stdin and
stdout are terminals); the tests patch that name.
"""

from __future__ import annotations

import json
import re
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
LINE = re.compile(r"^\s*\[(?P<mark>[x ])\]\s+(?P<id>\S+)\s+(?P<size>\d+(?:\.\d)? \w+)\s+(?P<title>.*?)\s*$")

# These tests pin the Linux report (the "Needs root" heading, plain command
# lines). The Windows report is pinned in test_host_clean_windows_cli, whose
# tests patch the platform themselves.
_linux_report = mock.patch("jzr_tools.core.cleanup._windows", return_value=False)


def setUpModule() -> None:  # noqa: N802
    _linux_report.start()


def tearDownModule() -> None:  # noqa: N802
    _linux_report.stop()


CACHE_ROOT = Path("home") / "someone" / ".cache"  # str() differs per platform


def make_item(
    item_id: str,
    size: int,
    tier: str = "safe",
    *,
    title: str | None = None,
    note: str | None = None,
    command: tuple[str, ...] = (),
    paths: tuple[Path, ...] = (),
) -> cleanup.Item:
    if not command and not paths:
        paths = (CACHE_ROOT / item_id,)
    return cleanup.Item(
        item_id, title or f"Title of {item_id}", tier, size, note or f"Note of {item_id}",
        paths=paths, command=command,
    )


def sample_items() -> list[cleanup.Item]:
    """In scan order: tier, then largest first."""
    return [
        make_item("docker-build-cache", 3 * GIB, command=("docker", "builder", "prune", "--all", "--force"),
                  title="Docker build cache"),
        make_item("pip", 512 * MIB, title="pip cache"),
        make_item("trash", 2 * GIB, "review", title="Trash"),
        make_item("cache:Mozilla", 256 * MIB, "review", title="Mozilla cache"),
        make_item("journal", 1 * GIB, "root", command=("journalctl", "--vacuum-size=500M"), title="systemd journal"),
    ]


class CleanCase(unittest.TestCase):
    """Runs the command with scan, clean and the prompts faked."""

    def setUp(self) -> None:
        self.items = sample_items()
        self.failing: dict[str, str] = {}
        self.freed: dict[str, int] = {}
        self.interactive = False
        self.choose_result: object = "ticked"  # "ticked" -> keep what was preselected
        self.confirm_result = True
        self.on_confirm = None

    def invoke(self, *args: str, interactive: bool | None = None, items: list[cleanup.Item] | None = None):
        runner = CliRunner()
        scan_items = self.items if items is None else items

        def clean(item: cleanup.Item, **kwargs: object) -> cleanup.Outcome:
            if item.id in self.failing:
                return cleanup.Outcome(item, False, 0, self.failing[item.id])
            return cleanup.Outcome(item, True, self.freed.get(item.id, item.size_bytes))

        def choose(message: str, entries: list[object]) -> object:
            if self.choose_result == "ticked":
                return [e.value for e in entries if isinstance(e, prompts.Choice) and e.checked]
            if callable(self.choose_result):
                return self.choose_result(entries)
            return self.choose_result

        with ExitStack() as stack:
            self.scan = stack.enter_context(mock.patch.object(cleanup, "scan", return_value=scan_items))
            self.clean = stack.enter_context(mock.patch.object(cleanup, "clean", side_effect=clean))
            self.choose = stack.enter_context(mock.patch.object(prompts, "choose", side_effect=choose))
            def confirm(*a: object, **k: object) -> bool:
                if self.on_confirm:
                    self.on_confirm()
                return self.confirm_result

            self.confirm = stack.enter_context(mock.patch.object(prompts, "confirm", side_effect=confirm))
            stack.enter_context(
                mock.patch(IS_INTERACTIVE, return_value=self.interactive if interactive is None else interactive)
            )
            return runner.invoke(app, ["host", "clean", *args])

    def cleaned_ids(self) -> list[str]:
        return [call.args[0].id for call in self.clean.call_args_list]

    def assert_nothing_removed(self) -> None:
        self.clean.assert_not_called()

    def assert_usage_error(self, result, *needles: str) -> None:
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("Error", result.stderr)
        for needle in needles:
            self.assertIn(needle, result.stderr)
        self.assert_nothing_removed()

    @staticmethod
    def report_lines(output: str) -> list[re.Match[str]]:
        return [m for m in (LINE.match(line) for line in output.splitlines()) if m]


class HelpTests(unittest.TestCase):
    def test_help_documents_every_option(self) -> None:
        result = CliRunner().invoke(app, ["host", "clean", "-h"])
        self.assertEqual(result.exit_code, 0, result.output)
        for flag in ("-n", "--dry-run", "-y", "--yes", "--only", "--json"):
            self.assertIn(flag, result.stdout)


class UsageErrorTests(CleanCase):
    def test_not_a_terminal_without_a_flag_is_a_usage_error_before_scanning(self) -> None:
        result = self.invoke(interactive=False)
        self.assert_usage_error(result)
        self.scan.assert_not_called()
        self.choose.assert_not_called()

    def test_not_a_terminal_with_only_but_no_yes_is_a_usage_error(self) -> None:
        result = self.invoke("--only", "pip", interactive=False)
        self.assert_usage_error(result)
        self.scan.assert_not_called()

    def test_real_non_terminal_streams_are_refused_without_patching(self) -> None:
        # CliRunner's streams are not terminals, so the genuine check must refuse.
        with mock.patch.object(cleanup, "scan", return_value=[]) as scan:
            result = CliRunner().invoke(app, ["host", "clean"])
        self.assertEqual(result.exit_code, 2, result.output)
        scan.assert_not_called()

    def test_json_with_yes_is_a_usage_error(self) -> None:
        result = self.invoke("--json", "--yes")
        self.assert_usage_error(result)
        self.scan.assert_not_called()

    def test_unknown_only_id_is_a_usage_error_naming_the_id(self) -> None:
        for flags in (["--yes"], ["--dry-run"], ["--json"]):
            with self.subTest(flags=flags):
                result = self.invoke("--only", "pip,bogus", *flags)
                self.assert_usage_error(result, "bogus")
                self.scan.assert_not_called()

    def test_unknown_id_in_a_repeated_only_is_a_usage_error(self) -> None:
        result = self.invoke("--only", "pip", "--only", "nonsense", "--yes")
        self.assert_usage_error(result, "nonsense")

    def test_cache_prefixed_ids_are_always_valid(self) -> None:
        result = self.invoke("--only", "cache:AnythingAtAll", "--yes")
        self.assertEqual(result.exit_code, 0, result.output)

    def test_ids_are_case_sensitive(self) -> None:
        self.assert_usage_error(self.invoke("--only", "PIP", "--yes"), "PIP")


class YesTests(CleanCase):
    def test_yes_alone_removes_the_safe_tier_only_without_prompting(self) -> None:
        result = self.invoke("--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.cleaned_ids(), ["docker-build-cache", "pip"])
        self.choose.assert_not_called()
        self.confirm.assert_not_called()

    def test_yes_cleans_without_letting_root_commands_ask_for_a_password(self) -> None:
        self.invoke("--yes", "--only", "journal")
        self.assertEqual(self.cleaned_ids(), ["journal"])
        self.assertIs(self.clean.call_args.kwargs.get("ask_password"), False)

    def test_yes_works_on_a_terminal_too_without_the_picker(self) -> None:
        result = self.invoke("--yes", interactive=True)
        self.assertEqual(result.exit_code, 0, result.output)
        self.choose.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(self.cleaned_ids(), ["docker-build-cache", "pip"])

    def test_yes_with_only_restricts_to_the_named_items_of_any_tier(self) -> None:
        result = self.invoke("--yes", "--only", "trash,pip,journal")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.cleaned_ids(), ["pip", "trash", "journal"])  # scan order

    def test_only_may_be_repeated_and_comma_separated(self) -> None:
        self.invoke("--yes", "--only", "pip", "--only", "trash,cache:Mozilla")
        self.assertEqual(self.cleaned_ids(), ["pip", "trash", "cache:Mozilla"])

    def test_review_and_root_items_are_not_removed_unless_named(self) -> None:
        self.invoke("--yes")
        for item_id in ("trash", "cache:Mozilla", "journal"):
            self.assertNotIn(item_id, self.cleaned_ids())

    def test_item_is_cleaned_exactly_as_scanned(self) -> None:
        self.invoke("--yes", "--only", "pip")
        self.assertEqual(self.clean.call_args.args[0], self.items[1])

    def test_short_flags_work(self) -> None:
        result = self.invoke("-y")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.cleaned_ids(), ["docker-build-cache", "pip"])

    def test_yes_when_safe_tier_is_empty_removes_nothing_and_succeeds(self) -> None:
        only_review = [i for i in self.items if i.tier != "safe"]
        result = self.invoke("--yes", items=only_review)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assert_nothing_removed()


class OnlyTests(CleanCase):
    def test_only_on_a_terminal_skips_the_picker_and_asks_to_confirm(self) -> None:
        result = self.invoke("--only", "pip,trash", interactive=True)
        self.assertEqual(result.exit_code, 0, result.output)
        self.choose.assert_not_called()
        self.confirm.assert_called_once()
        self.assertEqual(self.cleaned_ids(), ["pip", "trash"])

    def test_only_confirmation_declined_removes_nothing(self) -> None:
        self.confirm_result = False
        result = self.invoke("--only", "pip", interactive=True)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assert_nothing_removed()

    def test_only_a_known_id_with_nothing_found_is_reported_not_an_error(self) -> None:
        result = self.invoke("--only", "yarn", "--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertRegex(result.output.lower(), r"yarn.*nothing to remove|nothing to remove.*yarn")
        self.assert_nothing_removed()

    def test_found_and_missing_ids_together_remove_the_found_ones(self) -> None:
        result = self.invoke("--only", "yarn,pip", "--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.cleaned_ids(), ["pip"])
        self.assertIn("yarn", result.output)


class InteractiveTests(CleanCase):
    def setUp(self) -> None:
        super().setUp()
        self.interactive = True

    def entries(self) -> list[object]:
        self.choose.assert_called_once()
        call = self.choose.call_args
        return list(call.args[1] if len(call.args) > 1 else call.kwargs["entries"])

    def choices(self) -> list[prompts.Choice]:
        return [e for e in self.entries() if isinstance(e, prompts.Choice)]

    def test_the_picker_offers_every_item_with_title_size_and_note(self) -> None:
        self.invoke()
        by_item = {c.value.id: c for c in self.choices()}
        self.assertEqual(set(by_item), {i.id for i in self.items})
        for item in self.items:
            choice = by_item[item.id]
            self.assertIs(choice.value, item)
            self.assertIn(item.title, choice.label)
            self.assertIn(item.note, choice.label + choice.description)
        self.assertIn("3.0 GiB", by_item["docker-build-cache"].label)
        self.assertIn("512.0 MiB", by_item["pip"].label)
        self.assertIn(self.items[1].note, by_item["pip"].description)

    def test_only_the_safe_tier_is_preticked(self) -> None:
        self.invoke()
        for choice in self.choices():
            self.assertEqual(choice.checked, choice.value.tier == "safe", choice.value.id)
            self.assertFalse(choice.disabled)

    def test_tiers_appear_as_ordered_headings_with_their_items_below(self) -> None:
        self.invoke()
        entries = self.entries()
        headings = [(i, e.text) for i, e in enumerate(entries) if isinstance(e, prompts.Heading)]
        self.assertEqual(len(headings), 3)
        texts = [t for _, t in headings]
        self.assertIn("Safe to remove", texts[0])
        self.assertIn("Review first", texts[1])
        self.assertIn("Needs root", texts[2])
        tiers = []
        for entry in entries:
            if isinstance(entry, prompts.Choice):
                tiers.append(entry.value.tier)
        self.assertEqual(tiers, ["safe", "safe", "review", "review", "root"])
        positions = [i for i, _ in headings]
        self.assertLess(positions[0], next(i for i, e in enumerate(entries) if isinstance(e, prompts.Choice)))
        self.assertEqual(
            [entries[i].value.id for i in range(len(entries)) if isinstance(entries[i], prompts.Choice)],
            [i.id for i in self.items],
        )

    def test_removes_exactly_what_was_ticked_and_confirmed(self) -> None:
        self.choose_result = lambda entries: [
            e.value for e in entries if isinstance(e, prompts.Choice) and e.value.id in ("pip", "journal", "trash")
        ]
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.cleaned_ids(), ["pip", "trash", "journal"])

    def test_default_selection_removes_the_safe_tier(self) -> None:
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.cleaned_ids(), ["docker-build-cache", "pip"])

    def test_the_confirmation_states_count_and_total_and_defaults_to_no(self) -> None:
        self.invoke()
        self.confirm.assert_called_once()
        call = self.confirm.call_args
        self.assertEqual(call.args[0], "Remove 2 items, about 3.5 GiB?")
        self.assertIs(call.kwargs.get("default", False), False)

    def test_the_confirmation_is_singular_for_one_item(self) -> None:
        self.choose_result = lambda entries: [self.items[2]]
        self.invoke()
        self.assertEqual(self.confirm.call_args.args[0], "Remove 1 item, about 2.0 GiB?")

    def test_nothing_is_removed_before_the_confirmation(self) -> None:
        removed_at_confirmation: list[int] = []
        self.on_confirm = lambda: removed_at_confirmation.append(self.clean.call_count)
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(removed_at_confirmation, [0])
        self.assertEqual(self.clean.call_count, 2)

    def test_declining_the_confirmation_removes_nothing_and_exits_zero(self) -> None:
        self.confirm_result = False
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assert_nothing_removed()

    def test_cancelling_the_list_removes_nothing_and_exits_one(self) -> None:
        self.choose_result = None
        result = self.invoke()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("Cancelled", result.output)
        self.assert_nothing_removed()
        self.confirm.assert_not_called()

    def test_ticking_nothing_removes_nothing_and_exits_zero(self) -> None:
        self.choose_result = []
        result = self.invoke()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assert_nothing_removed()
        self.confirm.assert_not_called()

    def test_interactive_cleans_may_ask_for_a_password(self) -> None:
        self.choose_result = lambda entries: [self.items[4]]
        self.invoke()
        self.assertEqual(self.cleaned_ids(), ["journal"])
        self.assertIsNot(self.clean.call_args.kwargs.get("ask_password", True), False)

    def test_nothing_found_never_opens_the_picker(self) -> None:
        result = self.invoke(items=[])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Nothing to clean.", result.stdout)
        self.choose.assert_not_called()
        self.confirm.assert_not_called()

    def test_dry_run_on_a_terminal_asks_nothing(self) -> None:
        result = self.invoke("--dry-run")
        self.assertEqual(result.exit_code, 0, result.output)
        self.choose.assert_not_called()
        self.confirm.assert_not_called()
        self.assert_nothing_removed()

    def test_json_on_a_terminal_asks_nothing(self) -> None:
        result = self.invoke("--json")
        self.assertEqual(result.exit_code, 0, result.output)
        self.choose.assert_not_called()
        self.confirm.assert_not_called()
        self.assert_nothing_removed()


class ReportTests(CleanCase):
    def test_groups_by_tier_under_the_three_headings_in_order(self) -> None:
        out = self.invoke("--dry-run").stdout
        positions = [out.index(h) for h in ("Safe to remove", "Review first", "Needs root")]
        self.assertEqual(positions, sorted(positions))
        for item_id, heading_before, heading_after in (
            ("docker-build-cache", None, "Review first"),
            ("pip", None, "Review first"),
            ("trash", "Review first", "Needs root"),
            ("cache:Mozilla", "Review first", "Needs root"),
            ("journal", "Needs root", None),
        ):
            at = out.index(f" {item_id} ")
            if heading_before:
                self.assertGreater(at, out.index(heading_before))
            if heading_after:
                self.assertLess(at, out.index(heading_after))

    def test_each_line_has_mark_id_size_and_title(self) -> None:
        lines = {m["id"]: m for m in self.report_lines(self.invoke("--dry-run").stdout)}
        self.assertEqual(set(lines), {i.id for i in self.items})
        self.assertEqual(lines["docker-build-cache"]["size"], "3.0 GiB")
        self.assertEqual(lines["docker-build-cache"]["title"], "Docker build cache")
        self.assertEqual(lines["pip"]["size"], "512.0 MiB")
        self.assertEqual(lines["cache:Mozilla"]["size"], "256.0 MiB")
        self.assertEqual(lines["journal"]["title"], "systemd journal")

    def test_marks_show_the_safe_tier_ticked(self) -> None:
        lines = {m["id"]: m["mark"] for m in self.report_lines(self.invoke("--dry-run").stdout)}
        self.assertEqual(
            lines,
            {"docker-build-cache": "x", "pip": "x", "trash": " ", "cache:Mozilla": " ", "journal": " "},
        )

    def test_marks_follow_only(self) -> None:
        lines = {m["id"]: m["mark"] for m in self.report_lines(self.invoke("--dry-run", "--only", "trash,pip").stdout)}
        self.assertEqual(lines["trash"], "x")
        self.assertEqual(lines["pip"], "x")
        self.assertEqual(lines["docker-build-cache"], " ")
        self.assertEqual(lines["journal"], " ")

    def test_sizes_are_right_aligned(self) -> None:
        matches = self.report_lines(self.invoke("--dry-run").stdout)
        self.assertEqual(len(matches), len(self.items))
        self.assertEqual(len({m.end("size") for m in matches}), 1, "size column is ragged")

    def test_items_keep_their_largest_first_order_within_a_tier(self) -> None:
        ids = [m["id"] for m in self.report_lines(self.invoke("--dry-run").stdout)]
        self.assertEqual(ids, [i.id for i in self.items])

    def test_ends_with_the_selection_summary(self) -> None:
        out = self.invoke("--dry-run").stdout
        self.assertIn("Selected: 2 items, about 3.5 GiB", out)

    def test_summary_uses_the_singular_for_one_item(self) -> None:
        out = self.invoke("--dry-run", "--only", "trash").stdout
        self.assertIn("Selected: 1 item, about 2.0 GiB", out)

    def test_the_report_is_printed_for_yes_and_only_runs_too(self) -> None:
        for flags in (["--yes"], ["--yes", "--only", "pip"]):
            with self.subTest(flags=flags):
                out = self.invoke(*flags).stdout
                self.assertIn("Safe to remove", out)
                self.assertIn("Selected:", out)

    def test_no_scanning_line_when_stderr_is_not_a_terminal(self) -> None:
        result = self.invoke("--dry-run")
        self.assertNotIn("Scanning", result.output)


class DryRunTests(CleanCase):
    def test_removes_nothing_and_asks_nothing_even_with_yes(self) -> None:
        result = self.invoke("--dry-run", "--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assert_nothing_removed()
        self.confirm.assert_not_called()
        self.choose.assert_not_called()

    def test_each_selected_item_gets_a_would_line(self) -> None:
        out = self.invoke("--dry-run").stdout
        would = [l for l in out.splitlines() if l.startswith(("would run: ", "would empty: "))]
        self.assertEqual(len(would), 2)
        self.assertIn("would run: docker builder prune --all --force", would)
        self.assertTrue(any(l.startswith("would empty: ") and str(CACHE_ROOT / "pip") in l for l in would))

    def test_only_selected_items_get_a_would_line(self) -> None:
        out = self.invoke("--dry-run", "--only", "journal").stdout
        would = [l for l in out.splitlines() if l.startswith(("would run: ", "would empty: "))]
        self.assertEqual(len(would), 1)
        self.assertIn("journalctl --vacuum-size=500M", would[0])
        self.assertNotIn("docker", would[0])

    def test_directory_items_list_every_path(self) -> None:
        files, info = Path("h") / "u" / "Trash" / "files", Path("h") / "u" / "Trash" / "info"
        item = make_item("trash", 2 * GIB, "review", paths=(files, info))
        out = self.invoke("--dry-run", "--only", "trash", items=[item]).stdout
        line = next(l for l in out.splitlines() if l.startswith("would empty: "))
        self.assertIn(str(files), line)
        self.assertIn(str(info), line)

    def test_last_line_says_nothing_was_removed(self) -> None:
        out = self.invoke("--dry-run").stdout
        self.assertEqual(out.rstrip().splitlines()[-1], "Dry run: nothing was removed.")

    def test_dry_run_with_nothing_selected_is_still_clean(self) -> None:
        result = self.invoke("--dry-run", items=[i for i in self.items if i.tier != "safe"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.rstrip().splitlines()[-1], "Dry run: nothing was removed.")
        self.assert_nothing_removed()

    def test_short_flag(self) -> None:
        result = self.invoke("-n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assert_nothing_removed()
        self.assertIn("Dry run: nothing was removed.", result.stdout)

    def test_dry_run_scans_once(self) -> None:
        self.invoke("--dry-run")
        self.scan.assert_called_once()


class JsonTests(CleanCase):
    def document(self, *args: str) -> dict:
        result = self.invoke("--json", *args)
        self.assertEqual(result.exit_code, 0, result.output)
        return json.loads(result.stdout)

    def test_document_shape(self) -> None:
        doc = self.document()
        self.assertEqual(set(doc), {"items", "total_bytes", "selected_bytes"})
        self.assertEqual(len(doc["items"]), len(self.items))
        for entry in doc["items"]:
            self.assertEqual(
                set(entry), {"id", "title", "tier", "size_bytes", "note", "action", "selected"}
            )

    def test_item_fields_come_from_the_scan(self) -> None:
        doc = self.document()
        by_id = {e["id"]: e for e in doc["items"]}
        self.assertEqual([e["id"] for e in doc["items"]], [i.id for i in self.items])
        build = by_id["docker-build-cache"]
        self.assertEqual(build["title"], "Docker build cache")
        self.assertEqual(build["tier"], "safe")
        self.assertEqual(build["size_bytes"], 3 * GIB)
        self.assertEqual(build["note"], "Note of docker-build-cache")
        self.assertEqual(build["action"], "docker builder prune --all --force")
        self.assertIn(str(CACHE_ROOT / "pip"), by_id["pip"]["action"])

    def test_selected_follows_the_safe_tier_by_default(self) -> None:
        doc = self.document()
        self.assertEqual(
            {e["id"]: e["selected"] for e in doc["items"]},
            {"docker-build-cache": True, "pip": True, "trash": False, "cache:Mozilla": False, "journal": False},
        )
        self.assertEqual(doc["selected_bytes"], 3 * GIB + 512 * MIB)

    def test_selected_follows_only(self) -> None:
        doc = self.document("--only", "journal,trash")
        self.assertEqual({e["id"] for e in doc["items"] if e["selected"]}, {"journal", "trash"})
        self.assertEqual(doc["selected_bytes"], 3 * GIB)

    def test_total_is_the_sum_of_every_item(self) -> None:
        doc = self.document()
        self.assertEqual(doc["total_bytes"], sum(i.size_bytes for i in self.items))

    def test_json_removes_nothing_and_prints_only_the_document(self) -> None:
        result = self.invoke("--json")
        self.assert_nothing_removed()
        self.confirm.assert_not_called()
        json.loads(result.stdout)  # nothing but the document on stdout

    def test_nothing_found_is_an_empty_document(self) -> None:
        result = self.invoke("--json", items=[])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            json.loads(result.stdout), {"items": [], "total_bytes": 0, "selected_bytes": 0}
        )

    def test_unselected_only_ids_with_nothing_found_do_not_fail(self) -> None:
        doc = self.document("--only", "yarn")
        self.assertEqual(doc["selected_bytes"], 0)
        self.assertFalse(any(e["selected"] for e in doc["items"]))


class RemovalTests(CleanCase):
    def test_one_line_per_item_with_the_freed_size(self) -> None:
        self.freed = {"docker-build-cache": 3 * GIB, "pip": 300 * MIB}
        result = self.invoke("--yes")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Docker build cache: removed 3.0 GiB", result.stdout)
        self.assertIn("pip cache: removed 300.0 MiB", result.stdout)

    def test_the_last_line_totals_what_came_back(self) -> None:
        self.freed = {"docker-build-cache": 3 * GIB, "pip": 512 * MIB}
        result = self.invoke("--yes")
        last = result.stdout.rstrip().splitlines()[-1]
        self.assertTrue(last.startswith("Removed about 3.5 GiB."), last)

    def test_the_total_is_the_freed_figure_not_the_scanned_one(self) -> None:
        self.freed = {"docker-build-cache": 1 * GIB, "pip": 0}
        last = self.invoke("--yes").stdout.rstrip().splitlines()[-1]
        self.assertTrue(last.startswith("Removed about 1.0 GiB."), last)

    def test_removal_follows_scan_order(self) -> None:
        self.invoke("--yes", "--only", "journal,pip,trash")
        self.assertEqual(self.cleaned_ids(), ["pip", "trash", "journal"])

    def test_a_failure_is_reported_on_stderr_and_the_rest_still_run(self) -> None:
        self.failing = {"docker-build-cache": "Cannot connect to the Docker daemon"}
        result = self.invoke("--yes")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("Docker build cache: failed: Cannot connect to the Docker daemon", result.stderr)
        self.assertNotIn("Docker build cache: failed", result.stdout)
        self.assertEqual(self.cleaned_ids(), ["docker-build-cache", "pip"])
        self.assertIn("pip cache: removed", result.stdout)

    def test_failed_items_add_nothing_to_the_total(self) -> None:
        self.failing = {"docker-build-cache": "boom"}
        self.freed = {"pip": 512 * MIB}
        last = self.invoke("--yes").stdout.rstrip().splitlines()[-1]
        self.assertTrue(last.startswith("Removed about 512.0 MiB."), last)

    def test_all_failing_still_runs_everything_and_exits_one(self) -> None:
        self.failing = {"docker-build-cache": "a", "pip": "b"}
        result = self.invoke("--yes")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(self.cleaned_ids(), ["docker-build-cache", "pip"])
        self.assertIn("pip cache: failed: b", result.stderr)

    def test_failure_in_the_interactive_flow_exits_one(self) -> None:
        self.interactive = True
        self.failing = {"pip": "denied"}
        result = self.invoke()
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("denied", result.stderr)


class NothingFoundTests(CleanCase):
    def test_says_so_and_exits_zero(self) -> None:
        for flags in (["--yes"], ["--dry-run"]):
            with self.subTest(flags=flags):
                result = self.invoke(*flags, items=[])
                self.assertEqual(result.exit_code, 0, result.output)
                self.assertIn("Nothing to clean.", result.stdout)
                self.assert_nothing_removed()

    def test_nothing_found_with_only_is_not_an_error(self) -> None:
        result = self.invoke("--only", "pip", "--yes", items=[])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assert_nothing_removed()

    def test_nothing_found_does_not_print_removal_totals(self) -> None:
        result = self.invoke("--yes", items=[])
        self.assertNotIn("Removed about", result.stdout)


if __name__ == "__main__":
    unittest.main()
