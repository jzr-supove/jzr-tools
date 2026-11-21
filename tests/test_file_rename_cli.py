"""CLI behaviour of `jzr file rename`, run in temporary folders."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from jzr_tools.cli import app

HINT = "Run again with --apply to rename."


class RenameCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-rename-cli-")
        self.addCleanup(self._tmp.cleanup)
        self._old_cwd = os.getcwd()
        self.folder = Path(self._tmp.name).resolve()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, self._old_cwd)

    def make(self, *names: str, text: str | None = None) -> None:
        for name in names:
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text if text is not None else f"content of {name}", encoding="utf-8")

    def run_rename(self, *args: str):
        return self.runner.invoke(app, ["file", "rename", *args])

    def names(self, sub: str = ".") -> list[str]:
        return sorted(p.name for p in (self.folder / sub).iterdir() if p.is_file())

    def lines(self, text: str) -> list[str]:
        return [line for line in text.splitlines() if line.strip()]

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))


class PreviewAndApplyTests(RenameCliCase):
    def test_preview_is_the_default_and_changes_nothing(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "b.txt", "--prefix", "new-")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["a.txt", "b.txt"])
        self.assertEqual(
            self.lines(result.stdout),
            [
                "a.txt -> new-a.txt",
                "b.txt -> new-b.txt",
                f"2 files would be renamed. {HINT}",
            ],
        )

    def test_apply_renames_and_keeps_the_contents(self) -> None:
        self.make("a.txt", "b.txt")
        for flag in ("-y", "--apply"):
            with self.subTest(flag=flag):
                result = self.run_rename("a.txt", "b.txt", "--prefix", "new-", flag)
                self.assert_exit(result, 0)
                self.assertEqual(self.names(), ["new-a.txt", "new-b.txt"])
                self.assertEqual(
                    (self.folder / "new-a.txt").read_text(encoding="utf-8"), "content of a.txt"
                )
                self.assertEqual(self.lines(result.stdout)[-1], "Renamed 2 files.")
                self.run_rename("new-a.txt", "new-b.txt", "--replace", "new-", "", "-y")
                self.assertEqual(self.names(), ["a.txt", "b.txt"])

    def test_apply_summary_for_one_file_is_singular(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--prefix", "x-", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.lines(result.stdout), ["a.txt -> x-a.txt", "Renamed 1 file."])

    def test_preview_summary_for_one_file_is_singular(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--prefix", "x-")
        self.assert_exit(result, 0)
        self.assertEqual(self.lines(result.stdout)[-1], f"1 file would be renamed. {HINT}")

    def test_hidden_dry_run_flag_previews_and_is_not_in_help(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--prefix", "x-", "--dry-run")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["a.txt"])
        self.assertIn("a.txt -> x-a.txt", result.stdout)
        helped = self.run_rename("--help")
        self.assert_exit(helped, 0)
        self.assertNotIn("--dry-run", helped.stdout)
        self.assertIn("--apply", helped.stdout)

    def test_apply_together_with_dry_run_is_a_usage_error(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--prefix", "x-", "--apply", "--dry-run")
        self.assert_exit(result, 2)
        self.assertIn("--apply and --dry-run cannot be used together.", result.stderr)
        self.assertEqual(self.names(), ["a.txt"])

    def test_no_temporary_files_are_left_after_apply(self) -> None:
        self.make("ab.txt", "ba.txt", "c.txt")
        result = self.run_rename(
            "ab.txt", "ba.txt", "c.txt", "--regex", r"^(.)(.)$", r"\2\1", "-y"
        )
        self.assert_exit(result, 0)
        self.assertFalse([n for n in self.names() if n.startswith("jzr-tmp-")])

    def test_no_temporary_files_are_left_when_a_file_is_skipped(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "--replace", "a", "b", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["a.txt", "b.txt"])


class UsageErrorTests(RenameCliCase):
    def test_no_renaming_option_is_a_usage_error_naming_the_options(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt")
        self.assert_exit(result, 2)
        self.assertIn("Nothing to change", result.stderr)
        for option in ("--replace", "--regex", "--case", "--prefix", "--suffix", "--template", "--ext", "--lower-ext"):
            self.assertIn(option, result.stderr)
        self.assertEqual(self.names(), ["a.txt"])

    def test_listed_sources_with_scan_options_are_a_usage_error(self) -> None:
        self.make("a.txt", "pics/b.txt")
        for extra in (["--dir", "pics"], ["--recursive"], ["--hidden"]):
            with self.subTest(extra=extra):
                result = self.run_rename("a.txt", "--prefix", "x-", *extra)
                self.assert_exit(result, 2)
                self.assertIn(
                    "--dir, --recursive and --hidden only apply when no SOURCES are listed.",
                    result.stderr,
                )
                self.assertEqual(self.names(), ["a.txt"])

    def test_bad_regex_is_a_usage_error_with_the_pattern_and_reason(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--regex", "(", "x")
        self.assert_exit(result, 2)
        self.assertIn("'(' is not a valid regular expression", result.stderr)

    def test_bad_pattern_is_a_usage_error(self) -> None:
        self.make("pics/a.txt")
        result = self.run_rename("--dir", "pics", "--pattern", "(", "--prefix", "x-")
        self.assert_exit(result, 2)
        self.assertEqual(self.names("pics"), ["a.txt"])

    def test_unknown_template_field_is_a_usage_error(self) -> None:
        self.make("a.txt")
        for template in ("{bogus}", "{0}", "{}", "{n:zz}"):
            with self.subTest(template=template):
                result = self.run_rename("a.txt", "--template", template)
                self.assert_exit(result, 2)
                self.assertEqual(self.names(), ["a.txt"])

    def test_unknown_case_is_a_usage_error_and_help_lists_the_cases(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--case", "shouting")
        self.assert_exit(result, 2)
        helped = self.run_rename("--help")
        self.assertIn("lower|upper|title|slug|snake", helped.stdout)
        self.assertNotIn("kebab", helped.stdout)

    def test_kebab_is_accepted_as_slug(self) -> None:
        self.make("Hello World.txt")
        result = self.run_rename("Hello World.txt", "--case", "kebab", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["hello-world.txt"])

    def test_nothing_is_renamed_when_the_usage_is_wrong(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "b.txt", "--regex", "(", "x", "-y")
        self.assert_exit(result, 2)
        self.assertEqual(self.names(), ["a.txt", "b.txt"])


class ScanTests(RenameCliCase):
    def test_scan_with_pattern_renames_only_matching_files(self) -> None:
        self.make("pics/a.jpg", "pics/b.jpg", "pics/c.txt")
        result = self.run_rename(
            "--dir", "pics", "--pattern", r"\.jpg$", "--prefix", "x-", "-y"
        )
        self.assert_exit(result, 0)
        self.assertEqual(self.names("pics"), ["c.txt", "x-a.jpg", "x-b.jpg"])
        self.assertIn(f"{os.path.join('pics', 'a.jpg')} -> x-a.jpg", result.stdout)

    def test_scan_without_recursive_leaves_subfolders_alone(self) -> None:
        self.make("pics/a.jpg", "pics/sub/b.jpg")
        result = self.run_rename("--dir", "pics", "--prefix", "x-", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names("pics"), ["x-a.jpg"])
        self.assertEqual(self.names("pics/sub"), ["b.jpg"])

    def test_recursive_scan_reaches_subfolders_and_shows_their_path(self) -> None:
        self.make("pics/a.jpg", "pics/sub/b.jpg")
        result = self.run_rename("--dir", "pics", "-r", "--prefix", "x-")
        self.assert_exit(result, 0)
        self.assertIn(f"{os.path.join('pics', 'sub', 'b.jpg')} -> x-b.jpg", result.stdout)
        self.assertEqual(self.names("pics/sub"), ["b.jpg"])

    def test_scan_defaults_to_the_current_folder(self) -> None:
        self.make("a.txt")
        result = self.run_rename("--prefix", "x-", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["x-a.txt"])

    def test_hidden_files_are_skipped_unless_asked_for(self) -> None:
        self.make("pics/.dot.jpg", "pics/vis.jpg", "pics/.cache/in.jpg")
        result = self.run_rename("--dir", "pics", "-r", "--prefix", "x-", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names("pics"), [".dot.jpg", "x-vis.jpg"])
        self.assertEqual(self.names("pics/.cache"), ["in.jpg"])
        result = self.run_rename("--dir", "pics", "-r", "--hidden", "--prefix", "y-", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names("pics"), ["y-.dot.jpg", "y-x-vis.jpg"])
        self.assertEqual(self.names("pics/.cache"), ["y-in.jpg"])

    def test_a_listed_hidden_file_is_always_used(self) -> None:
        self.make(".dot.txt")
        result = self.run_rename(".dot.txt", "--prefix", "x-", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["x-.dot.txt"])

    def test_an_empty_scan_reports_no_matching_files(self) -> None:
        (self.folder / "empty").mkdir()
        result = self.run_rename("--dir", "empty", "--prefix", "x-")
        self.assert_exit(result, 1)
        self.assertIn("No matching files.", result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_a_pattern_that_matches_nothing_reports_no_matching_files(self) -> None:
        self.make("pics/a.jpg")
        result = self.run_rename("--dir", "pics", "-p", r"\.png$", "--prefix", "x-")
        self.assert_exit(result, 1)
        self.assertIn("No matching files.", result.stderr)

    def test_a_listed_folder_fails_alone_with_a_hint(self) -> None:
        self.make("a.txt", "photos/p.jpg")
        result = self.run_rename("photos", "a.txt", "--prefix", "x-", "-y")
        self.assert_exit(result, 1)
        self.assertIn("fail photos: photos is a folder; use --dir photos", result.stderr)
        self.assertEqual(self.names(), ["x-a.txt"])
        self.assertEqual(self.names("photos"), ["p.jpg"])


class NumberingAndOrderTests(RenameCliCase):
    def test_scan_numbers_in_natural_order(self) -> None:
        self.make("pics/img10.jpg", "pics/img2.jpg", "pics/img1.jpg")
        result = self.run_rename("--dir", "pics", "--template", "{n:02}", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names("pics"), ["01.jpg", "02.jpg", "03.jpg"])
        contents = [
            (self.folder / "pics" / n).read_text(encoding="utf-8")
            for n in ("01.jpg", "02.jpg", "03.jpg")
        ]
        self.assertEqual(
            contents,
            ["content of pics/img1.jpg", "content of pics/img2.jpg", "content of pics/img10.jpg"],
        )

    def test_preview_lists_the_scan_in_natural_order(self) -> None:
        self.make("pics/img10.jpg", "pics/img2.jpg")
        result = self.run_rename("--dir", "pics", "--template", "{n}")
        self.assert_exit(result, 0)
        lines = self.lines(result.stdout)
        self.assertEqual(
            lines[:2],
            [
                f"{os.path.join('pics', 'img2.jpg')} -> 1.jpg",
                f"{os.path.join('pics', 'img10.jpg')} -> 2.jpg",
            ],
        )

    def test_listed_sources_are_numbered_in_the_order_given(self) -> None:
        self.make("z.txt", "a.txt", "m.txt")
        result = self.run_rename("z.txt", "a.txt", "m.txt", "--template", "{n}")
        self.assert_exit(result, 0)
        self.assertEqual(
            self.lines(result.stdout)[:3],
            ["z.txt -> 1.txt", "a.txt -> 2.txt", "m.txt -> 3.txt"],
        )

    def test_sort_name_orders_listed_sources_by_name(self) -> None:
        self.make("b.txt", "c.txt", "a.txt")
        result = self.run_rename(
            "b.txt", "c.txt", "a.txt", "--sort", "name", "--template", "n{n}", "-y"
        )
        self.assert_exit(result, 0)
        for original, number in (("a", 1), ("b", 2), ("c", 3)):
            self.assertEqual(
                (self.folder / f"n{number}.txt").read_text(encoding="utf-8"),
                f"content of {original}.txt",
            )

    def test_reverse_flips_the_order(self) -> None:
        self.make("b.txt", "c.txt", "a.txt")
        result = self.run_rename(
            "b.txt", "c.txt", "a.txt", "--sort", "name", "--reverse", "--template", "n{n}", "-y"
        )
        self.assert_exit(result, 0)
        for original, number in (("c", 1), ("b", 2), ("a", 3)):
            self.assertEqual(
                (self.folder / f"n{number}.txt").read_text(encoding="utf-8"),
                f"content of {original}.txt",
            )

    def test_reverse_alone_flips_the_given_order(self) -> None:
        self.make("z.txt", "a.txt")
        result = self.run_rename("z.txt", "a.txt", "--reverse", "--template", "{n}")
        self.assert_exit(result, 0)
        self.assertEqual(self.lines(result.stdout)[:2], ["a.txt -> 1.txt", "z.txt -> 2.txt"])

    def test_start_sets_the_first_number(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "b.txt", "--template", "{n:03}", "--start", "10")
        self.assert_exit(result, 0)
        self.assertEqual(self.lines(result.stdout)[:2], ["a.txt -> 010.txt", "b.txt -> 011.txt"])

    def test_the_counter_restarts_in_each_folder_with_recursive(self) -> None:
        self.make("pics/a.jpg", "pics/b.jpg", "pics/sub/c.jpg", "pics/sub/d.jpg")
        result = self.run_rename("--dir", "pics", "-r", "--template", "{n}", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names("pics"), ["1.jpg", "2.jpg"])
        self.assertEqual(self.names("pics/sub"), ["1.jpg", "2.jpg"])
        self.assertEqual(
            (self.folder / "pics" / "sub" / "1.jpg").read_text(encoding="utf-8"),
            "content of pics/sub/c.jpg",
        )

    def test_the_parent_field_is_the_folder_name(self) -> None:
        self.make("holiday/a.jpg")
        result = self.run_rename("--dir", "holiday", "--template", "{parent}-{n}", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names("holiday"), ["holiday-1.jpg"])

    def test_the_date_field_is_the_modified_date(self) -> None:
        self.make("a.jpg")
        stamp = 1700000000  # 2023-11-14 or 2023-11-15 depending on the zone
        os.utime(self.folder / "a.jpg", (stamp, stamp))
        result = self.run_rename("a.jpg", "--template", "{date}-{n:02}", "-y")
        self.assert_exit(result, 0)
        (name,) = self.names()
        self.assertRegex(name, r"^2023-11-1[45]-01\.jpg$")


class OutputLineTests(RenameCliCase):
    def test_result_lines_go_to_stdout_and_skip_lines_to_stderr(self) -> None:
        self.make("a.txt", "b.txt", "c.txt")
        result = self.run_rename("a.txt", "c.txt", "--replace", "a", "b")
        self.assert_exit(result, 0)
        self.assertEqual(self.lines(result.stderr), ["skip a.txt: b.txt exists"])
        self.assertEqual(
            self.lines(result.stdout),
            ["0 files would be renamed, 1 skipped, 1 unchanged."],
        )

    def test_an_existing_target_skips_the_file_and_is_not_a_failure(self) -> None:
        self.make("a.txt", "b.txt")
        for extra in ([], ["-y"]):
            with self.subTest(extra=extra):
                result = self.run_rename("a.txt", "--replace", "a", "b", *extra)
                self.assert_exit(result, 0)
                self.assertIn("skip a.txt: b.txt exists", result.stderr)
                self.assertEqual(self.names(), ["a.txt", "b.txt"])
                self.assertEqual(
                    (self.folder / "b.txt").read_text(encoding="utf-8"), "content of b.txt"
                )
        self.assertEqual(self.lines(result.stdout), ["Renamed 0 files, 1 skipped."])

    def test_two_files_with_the_same_new_name_get_counters_and_a_note(self) -> None:
        self.make("a.txt", "b.txt", "c.txt")
        result = self.run_rename("a.txt", "b.txt", "c.txt", "--template", "x")
        self.assert_exit(result, 0)
        self.assertEqual(
            self.lines(result.stdout),
            [
                "a.txt -> x.txt",
                "b.txt -> x-2.txt (clash: -2 added)",
                "c.txt -> x-3.txt (clash: -3 added)",
                f"3 files would be renamed. {HINT}",
            ],
        )
        self.run_rename("a.txt", "b.txt", "c.txt", "--template", "x", "-y")
        self.assertEqual(self.names(), ["x-2.txt", "x-3.txt", "x.txt"])
        self.assertEqual((self.folder / "x.txt").read_text(encoding="utf-8"), "content of a.txt")

    def test_clash_is_found_without_regard_to_case(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "b.txt", "--regex", r"^(.)$", "X", "--case", "lower", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["x-2.txt", "x.txt"])

    def test_a_name_problem_fails_that_file_in_preview_and_apply(self) -> None:
        self.make("a.txt")
        for extra, summary in (
            ([], "0 files would be renamed, 1 failed."),
            (["-y"], "Renamed 0 files, 1 failed."),
        ):
            with self.subTest(extra=extra):
                result = self.run_rename("a.txt", "--suffix", "?", *extra)
                self.assert_exit(result, 1)
                self.assertIn('fail a.txt: new name "a?.txt" has a character Windows does not allow (?)', result.stderr)
                self.assertEqual(self.lines(result.stdout), [summary])
                self.assertEqual(self.names(), ["a.txt"])

    def test_one_bad_file_fails_alone(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "b.txt", "--regex", r"^a$", "a/b", "-y")
        self.assert_exit(result, 1)
        self.assertIn('fail a.txt: new name "a/b.txt" would leave the folder', result.stderr)
        self.assertEqual(self.names(), ["a.txt", "b.txt"])

    def test_a_windows_device_name_is_refused(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--regex", r"^a$", "con", "-y")
        self.assert_exit(result, 1)
        self.assertIn("fail a.txt:", result.stderr)
        self.assertIn("CON", result.stderr)
        self.assertEqual(self.names(), ["a.txt"])

    def test_unchanged_files_are_counted_and_listed_only_when_verbose(self) -> None:
        self.make("a.txt", "c.txt")
        plain = self.run_rename("a.txt", "c.txt", "--replace", "a", "z")
        self.assert_exit(plain, 0)
        self.assertEqual(
            self.lines(plain.stdout),
            ["a.txt -> z.txt", f"1 file would be renamed, 1 unchanged. {HINT}"],
        )
        verbose = self.run_rename("a.txt", "c.txt", "--replace", "a", "z", "-v")
        self.assert_exit(verbose, 0)
        self.assertIn("same c.txt", verbose.stdout)
        self.assertIn("a.txt -> z.txt", verbose.stdout)

    def test_apply_summary_counts_skipped_failed_and_unchanged(self) -> None:
        self.make("a.txt", "b.txt", "c.txt", "d.txt")
        result = self.run_rename(
            "a.txt", "c.txt", "d.txt", "--regex", r"^a$", "b", "--regex", r"^d$", "d?", "-y"
        )
        self.assert_exit(result, 1)
        self.assertEqual(
            self.lines(result.stdout)[-1], "Renamed 0 files, 1 skipped, 1 failed, 1 unchanged."
        )

    def test_nothing_to_rename_has_no_hint(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--prefix", "")
        self.assert_exit(result, 0)
        self.assertEqual(self.lines(result.stdout), ["0 files would be renamed, 1 unchanged."])
        self.assertNotIn(HINT, result.stdout)


class RenamingBehaviourTests(RenameCliCase):
    def test_a_swap_ends_with_the_contents_exchanged(self) -> None:
        (self.folder / "ab.txt").write_text("first", encoding="utf-8")
        (self.folder / "ba.txt").write_text("second", encoding="utf-8")
        result = self.run_rename("ab.txt", "ba.txt", "--regex", r"^(.)(.)$", r"\2\1", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["ab.txt", "ba.txt"])
        self.assertEqual((self.folder / "ab.txt").read_text(encoding="utf-8"), "second")
        self.assertEqual((self.folder / "ba.txt").read_text(encoding="utf-8"), "first")
        self.assertEqual(self.lines(result.stdout)[-1], "Renamed 2 files.")

    def test_a_chain_renames_every_file(self) -> None:
        self.make("1.txt", "2.txt")
        result = self.run_rename("1.txt", "2.txt", "--template", "{n}", "--start", "2", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(self.names(), ["2.txt", "3.txt"])
        self.assertEqual((self.folder / "2.txt").read_text(encoding="utf-8"), "content of 1.txt")
        self.assertEqual((self.folder / "3.txt").read_text(encoding="utf-8"), "content of 2.txt")

    def test_a_case_only_change_renames_the_file(self) -> None:
        self.make("IMG.JPG")
        result = self.run_rename("IMG.JPG", "--case", "lower", "--lower-ext", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["img.jpg"])
        self.assertEqual(self.lines(result.stdout)[0], "IMG.JPG -> img.jpg")

    def test_lower_ext_changes_only_the_extension(self) -> None:
        self.make("IMG_1.JPG")
        result = self.run_rename("IMG_1.JPG", "--lower-ext", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["IMG_1.jpg"])

    def test_case_options_leave_the_extension_alone(self) -> None:
        self.make("Photo.JPG")
        result = self.run_rename("Photo.JPG", "--case", "upper", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["PHOTO.JPG"])

    def test_ext_replaces_the_extension_and_empty_removes_it(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "--ext", "md", "-y")
        self.assert_exit(result, 0)
        result = self.run_rename("b.txt", "--ext", "", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["a.md", "b"])

    def test_only_the_last_extension_is_treated_as_the_extension(self) -> None:
        self.make("a.tar.gz")
        result = self.run_rename("a.tar.gz", "--prefix", "x-", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["x-a.tar.gz"])

    def test_replace_is_literal_and_ignore_case_is_optional(self) -> None:
        self.make("Foo.a.txt", "FOO.txt")
        result = self.run_rename("Foo.a.txt", "--replace", ".a", "-x", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["FOO.txt", "Foo-x.txt"])
        result = self.run_rename("FOO.txt", "--replace", "foo", "bar")
        self.assertEqual(self.lines(result.stdout)[-1], "0 files would be renamed, 1 unchanged.")
        result = self.run_rename("FOO.txt", "--replace", "foo", "bar", "-i")
        self.assertEqual(self.lines(result.stdout)[0], "FOO.txt -> bar.txt")

    def test_replace_never_touches_the_extension(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--replace", "txt", "md", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["a.txt"])

    def test_snake_case(self) -> None:
        self.make("Hello World.txt")
        result = self.run_rename("Hello World.txt", "--case", "snake", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["hello_world.txt"])

    def test_a_cyrillic_name_becomes_a_latin_slug(self) -> None:
        self.make("Привет Мир.txt")
        result = self.run_rename("Привет Мир.txt", "--case", "slug", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["privet-mir.txt"])

    def test_the_steps_run_in_a_fixed_order_whatever_the_command_line_order(self) -> None:
        self.make("Hello.txt")
        result = self.run_rename(
            "Hello.txt", "--suffix", "-end", "--case", "upper", "--replace", "Hel", "J", "-y"
        )
        self.assert_exit(result, 0)
        self.assertEqual(self.names(), ["JLO-end.txt"])


class JsonTests(RenameCliCase):
    def entries(self, result) -> list[dict]:
        data = json.loads(result.stdout)
        self.assertIsInstance(data, list)
        for entry in data:
            self.assertEqual(set(entry), {"source", "target", "status", "detail"})
        return data

    def test_preview_json_is_only_a_list_with_status_rename(self) -> None:
        self.make("a.txt", "c.txt")
        result = self.run_rename("a.txt", "c.txt", "--replace", "a", "z", "--json")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, "")
        entries = self.entries(result)
        self.assertEqual([e["status"] for e in entries], ["rename", "unchanged"])
        self.assertEqual(entries[0]["source"], "a.txt")
        self.assertEqual(Path(entries[0]["target"]).name, "z.txt")
        self.assertEqual(
            os.path.normcase(os.path.abspath(entries[0]["target"])),
            os.path.normcase(str(self.folder / "z.txt")),
        )
        self.assertEqual(self.names(), ["a.txt", "c.txt"])

    def test_apply_json_has_status_renamed(self) -> None:
        self.make("a.txt")
        result = self.run_rename("a.txt", "--prefix", "x-", "--json", "-y")
        self.assert_exit(result, 0)
        self.assertEqual(result.stderr, "")
        (entry,) = self.entries(result)
        self.assertEqual(entry["status"], "renamed")
        self.assertEqual(self.names(), ["x-a.txt"])

    def test_json_reports_skips_and_failures_without_writing_to_stderr(self) -> None:
        self.make("a.txt", "b.txt", "c.txt")
        result = self.run_rename(
            "a.txt", "c.txt", "--regex", r"^a$", "b", "--regex", r"^c$", "c?", "--json"
        )
        self.assert_exit(result, 1)
        self.assertEqual(result.stderr, "")
        entries = self.entries(result)
        self.assertEqual([e["status"] for e in entries], ["skipped", "failed"])
        self.assertEqual(entries[0]["detail"], "b.txt exists")
        self.assertIn("Windows does not allow", entries[1]["detail"])

    def test_json_detail_carries_the_clash_note(self) -> None:
        self.make("a.txt", "b.txt")
        result = self.run_rename("a.txt", "b.txt", "--template", "x", "--json")
        self.assert_exit(result, 0)
        entries = self.entries(result)
        self.assertEqual(entries[0]["detail"], "")
        self.assertEqual(entries[1]["detail"], "clash: -2 added")


if __name__ == "__main__":
    unittest.main()
