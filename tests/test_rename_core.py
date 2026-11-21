"""Tests of the rename logic: name splitting and the naming steps, option and
name checks, finding files, planning (clashes, chains, swaps) and the
two-phase apply, including failures and Ctrl+C.

Everything runs on real files in temporary folders; the only fake is the
`rename` callable handed to `apply_renames` to force failures.
"""

from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

from jzr_tools.core import text
from jzr_tools.core.rename import (
    RenameError,
    RenameInterrupted,
    RenameItem,
    RenameOptions,
    apply_renames,
    check_options,
    find_files,
    is_hidden,
    name_problem,
    natural_key,
    new_name,
    order_sources,
    plan_renames,
    split_name,
)

DAY = date(2026, 11, 18)


def renamed(name: str, *, n: int = 1, parent: str = "dir", modified: date = DAY, **fields) -> str:
    return new_name(name, RenameOptions(**fields), n=n, parent=parent, modified=modified)


class TempFolderCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()

    def write(self, relative: str, content: str = "") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content else relative, encoding="utf-8")
        return path

    def names(self, folder: Path | None = None) -> list[str]:
        return sorted(os.listdir(folder or self.root))


class SplitNameTests(unittest.TestCase):
    def test_splits_at_the_last_dot(self) -> None:
        self.assertEqual(split_name("a.tar.gz"), ("a.tar", ".gz"))
        self.assertEqual(split_name("photo.jpg"), ("photo", ".jpg"))

    def test_leading_dot_is_not_an_extension(self) -> None:
        self.assertEqual(split_name(".bashrc"), (".bashrc", ""))

    def test_trailing_dot_is_not_an_extension(self) -> None:
        self.assertEqual(split_name("file."), ("file.", ""))
        self.assertEqual(split_name("a.b."), ("a.b.", ""))

    def test_no_dot(self) -> None:
        self.assertEqual(split_name("x"), ("x", ""))

    def test_dot_file_with_extension(self) -> None:
        self.assertEqual(split_name(".config.json"), (".config", ".json"))


class NaturalKeyTests(unittest.TestCase):
    def test_numbers_compare_as_numbers(self) -> None:
        names = ["img10.jpg", "img2.jpg", "img1.jpg"]
        self.assertEqual(sorted(names, key=natural_key), ["img1.jpg", "img2.jpg", "img10.jpg"])

    def test_case_is_ignored(self) -> None:
        names = ["Img11", "img10", "img2"]
        self.assertEqual(sorted(names, key=natural_key), ["img2", "img10", "Img11"])

    def test_case_does_not_split_alphabetical_order(self) -> None:
        names = ["b", "A", "c"]
        self.assertEqual(sorted(names, key=natural_key), ["A", "b", "c"])


class OrderSourcesTests(TempFolderCase):
    def setUp(self) -> None:
        super().setUp()
        self.big = self.write("b.txt", "x" * 300)
        self.small = self.write("c.txt", "x" * 10)
        self.mid = self.write("a10.txt", "x" * 100)
        self.low = self.write("a2.txt", "x" * 100)
        for offset, path in enumerate([self.mid, self.big, self.small, self.low]):
            os.utime(path, (1_600_000_000 + offset * 100, 1_600_000_000 + offset * 100))
        self.given = [self.big, self.small, self.mid, self.low]

    def test_without_sort_keeps_the_given_order(self) -> None:
        self.assertEqual(order_sources(self.given, sort=None, reverse=False), self.given)

    def test_name_uses_natural_order(self) -> None:
        got = order_sources(self.given, sort="name", reverse=False)
        self.assertEqual([p.name for p in got], ["a2.txt", "a10.txt", "b.txt", "c.txt"])

    def test_mtime_oldest_first(self) -> None:
        got = order_sources(self.given, sort="mtime", reverse=False)
        self.assertEqual([p.name for p in got], ["a10.txt", "b.txt", "c.txt", "a2.txt"])

    def test_size_smallest_first_and_ties_by_name(self) -> None:
        got = order_sources(self.given, sort="size", reverse=False)
        self.assertEqual([p.name for p in got], ["c.txt", "a2.txt", "a10.txt", "b.txt"])

    def test_reverse_flips_the_result(self) -> None:
        got = order_sources(self.given, sort="name", reverse=True)
        self.assertEqual([p.name for p in got], ["c.txt", "b.txt", "a10.txt", "a2.txt"])

    def test_reverse_without_sort_flips_the_given_order(self) -> None:
        got = order_sources(self.given, sort=None, reverse=True)
        self.assertEqual(got, list(reversed(self.given)))


class NewNameStepTests(unittest.TestCase):
    def test_replace_is_literal_and_replaces_every_occurrence(self) -> None:
        self.assertEqual(renamed("a.b a.b.txt", replace=((".b", "-"),)), "a- a-.txt")

    def test_replace_is_case_sensitive_by_default(self) -> None:
        self.assertEqual(renamed("IMG_1.jpg", replace=(("img", "pic"),)), "IMG_1.jpg")

    def test_replace_ignore_case(self) -> None:
        self.assertEqual(
            renamed("IMG_1.jpg", replace=(("img", "pic"),), ignore_case=True), "pic_1.jpg"
        )

    def test_ignore_case_does_not_affect_regex(self) -> None:
        self.assertEqual(
            renamed("IMG_1.jpg", regex=(("img", "pic"),), ignore_case=True), "IMG_1.jpg"
        )

    def test_several_replacements_apply_in_the_given_order(self) -> None:
        self.assertEqual(renamed("a.txt", replace=(("a", "b"), ("b", "c"))), "c.txt")

    def test_replace_never_touches_the_extension(self) -> None:
        self.assertEqual(renamed("txt.txt", replace=(("txt", "x"),)), "x.txt")

    def test_regex_numbered_groups(self) -> None:
        self.assertEqual(renamed("12-ab.txt", regex=((r"(\d+)-(\w+)", r"\2-\1"),)), "ab-12.txt")

    def test_regex_named_groups(self) -> None:
        self.assertEqual(
            renamed("12-ab.txt", regex=((r"(?P<num>\d+)", r"[\g<num>]"),)), "[12]-ab.txt"
        )

    def test_regex_never_touches_the_extension(self) -> None:
        self.assertEqual(renamed("a.txt", regex=((r"t", "T"),)), "a.txt")

    def test_case_lower_upper_title(self) -> None:
        self.assertEqual(renamed("Hello World.TXT", case="lower"), "hello world.TXT")
        self.assertEqual(renamed("Hello World.txt", case="upper"), "HELLO WORLD.txt")
        self.assertEqual(renamed("hello wORLD.txt", case="title"), "Hello World.txt")

    def test_case_slug(self) -> None:
        self.assertEqual(renamed("Hello World.txt", case="slug"), "hello-world.txt")
        self.assertEqual(
            renamed("myCamelFile v2.txt", case="slug"),
            text.slugify("myCamelFile v2") + ".txt",
        )

    def test_case_snake(self) -> None:
        self.assertEqual(
            renamed("Hello World.txt", case="snake"),
            text.slugify("Hello World", separator="_") + ".txt",
        )
        self.assertIn("_", renamed("Hello World.txt", case="snake"))

    def test_kebab_is_the_same_as_slug(self) -> None:
        self.assertEqual(
            renamed("Hello World.txt", case="kebab"), renamed("Hello World.txt", case="slug")
        )

    def test_slug_transliterates_a_cyrillic_stem(self) -> None:
        stem = "Привет мир"
        expected = text.slugify(stem) + ".txt"
        self.assertTrue(expected.isascii())
        self.assertEqual(renamed(stem + ".txt", case="slug"), expected)

    def test_case_steps_keep_the_extension_as_it_is(self) -> None:
        self.assertEqual(renamed("IMG.JPG", case="lower"), "img.JPG")

    def test_prefix_and_suffix_go_around_the_stem(self) -> None:
        self.assertEqual(renamed("a.txt", prefix="x-", suffix="-y"), "x-a-y.txt")

    def test_prefix_on_a_name_without_extension(self) -> None:
        self.assertEqual(renamed("README", prefix="x-", suffix="!"), "x-README!")

    def test_template_builds_the_stem_and_keeps_the_extension(self) -> None:
        self.assertEqual(renamed("IMG_1.JPG", template="{date}-{n:02}"), "2026-11-18-01.JPG")

    def test_template_counter_format_spec(self) -> None:
        self.assertEqual(renamed("a.txt", template="pic{n:03}", n=7), "pic007.txt")
        self.assertEqual(renamed("a.txt", template="{n}", n=12), "12.txt")

    def test_template_parent_and_ext_fields(self) -> None:
        self.assertEqual(
            renamed("a.tar.gz", template="{parent}-{name}-{ext}", parent="holiday"),
            "holiday-a.tar-gz.gz",
        )

    def test_template_date_comes_from_the_modified_argument(self) -> None:
        self.assertEqual(
            renamed("a.txt", template="{date}", modified=date(2020, 5, 17)), "2020-05-17.txt"
        )

    def test_ext_replaces_the_extension(self) -> None:
        self.assertEqual(renamed("a.txt", ext=".PNG"), "a.PNG")
        self.assertEqual(renamed("a.txt", ext="png"), "a.png")
        self.assertEqual(renamed("a", ext="txt"), "a.txt")

    def test_empty_ext_removes_the_extension(self) -> None:
        self.assertEqual(renamed("a.txt", ext=""), "a")

    def test_lower_ext(self) -> None:
        self.assertEqual(renamed("IMG_1.JPG", lower_ext=True), "IMG_1.jpg")

    def test_lower_ext_applies_after_ext(self) -> None:
        self.assertEqual(renamed("a.txt", ext=".PNG", lower_ext=True), "a.png")

    def test_no_options_changes_nothing(self) -> None:
        self.assertEqual(renamed("Some File.TXT"), "Some File.TXT")


class NewNameOrderTests(unittest.TestCase):
    def test_replace_runs_before_case(self) -> None:
        # replace first: "Aa" -> "ba" -> "BA"; case first would give "bA".
        self.assertEqual(renamed("Aa.txt", replace=(("A", "b"),), case="upper"), "BA.txt")

    def test_replace_runs_before_regex(self) -> None:
        self.assertEqual(renamed("a.txt", replace=(("a", "b"),), regex=(("b", "c"),)), "c.txt")

    def test_regex_runs_before_case(self) -> None:
        self.assertEqual(renamed("ab.txt", regex=(("a", "x"),), case="upper"), "XB.txt")

    def test_case_runs_before_prefix_and_suffix(self) -> None:
        self.assertEqual(renamed("a.txt", case="upper", prefix="x_", suffix="_y"), "x_A_y.txt")

    def test_prefix_and_suffix_run_before_template(self) -> None:
        self.assertEqual(renamed("a.txt", prefix="p-", suffix="-s", template="[{name}]"), "[p-a-s].txt")

    def test_template_runs_after_case(self) -> None:
        self.assertEqual(renamed("Ab.txt", case="lower", template="{name}-{n}"), "ab-1.txt")

    def test_template_text_itself_is_not_changed_by_earlier_steps(self) -> None:
        self.assertEqual(renamed("a.txt", case="upper", template="lit-{name}"), "lit-A.txt")

    def test_extension_steps_do_not_depend_on_name_steps(self) -> None:
        self.assertEqual(
            renamed("A.JPG", case="lower", prefix="x", ext=".PNG", lower_ext=True), "xa.png"
        )


class CheckOptionsTests(unittest.TestCase):
    def assertRejected(self, expected: str, **fields) -> None:
        with self.assertRaises(RenameError) as caught:
            check_options(RenameOptions(**fields))
        self.assertIn(expected, str(caught.exception))

    def test_valid_options_pass(self) -> None:
        check_options(RenameOptions())
        check_options(
            RenameOptions(
                replace=(("a", "b"),),
                regex=((r"(\d+)", r"\1"),),
                case="kebab",
                template="{name}-{n:03}-{parent}-{date}-{ext}",
                start=0,
            )
        )

    def test_bad_regex_names_the_pattern_and_the_error(self) -> None:
        self.assertRejected("'(' is not a valid regular expression", regex=(("(", "x"),))

    def test_unknown_case(self) -> None:
        self.assertRaises(RenameError, check_options, RenameOptions(case="shout"))

    def test_unknown_template_field(self) -> None:
        self.assertRejected("taken", template="{taken}")

    def test_positional_and_empty_template_fields(self) -> None:
        for template in ("{0}", "{}", "{name.x}", "{name[0]}"):
            with self.subTest(template=template):
                self.assertRaises(RenameError, check_options, RenameOptions(template=template))

    def test_bad_format_spec(self) -> None:
        self.assertRaises(RenameError, check_options, RenameOptions(template="{n:zz}"))

    def test_unbalanced_template_braces(self) -> None:
        self.assertRaises(RenameError, check_options, RenameOptions(template="{n"))

    def test_negative_start(self) -> None:
        self.assertRaises(RenameError, check_options, RenameOptions(start=-1))

    def test_changes_anything(self) -> None:
        self.assertFalse(RenameOptions().changes_anything())
        self.assertFalse(RenameOptions(start=5).changes_anything())
        for fields in (
            {"replace": (("a", "b"),)},
            {"regex": (("a", "b"),)},
            {"case": "lower"},
            {"prefix": "x"},
            {"suffix": "x"},
            {"template": "{n}"},
            {"ext": ""},
            {"ext": ".txt"},
            {"lower_ext": True},
        ):
            with self.subTest(fields=fields):
                self.assertTrue(RenameOptions(**fields).changes_anything())


class NameProblemTests(unittest.TestCase):
    def test_good_names_have_no_problem(self) -> None:
        for name in ("a.jpg", "photo 1 (copy).tar.gz", "console.txt", "com10.txt", "conx", ".bashrc", "é.txt"):
            with self.subTest(name=name):
                self.assertIsNone(name_problem(name))

    def test_empty(self) -> None:
        self.assertEqual(name_problem(""), "the new name is empty")

    def test_dot_and_dotdot(self) -> None:
        self.assertEqual(name_problem("."), 'new name "." is not a file name')
        self.assertEqual(name_problem(".."), 'new name ".." is not a file name')

    def test_separators_leave_the_folder(self) -> None:
        self.assertEqual(name_problem("a/b.jpg"), 'new name "a/b.jpg" would leave the folder')
        self.assertIn("would leave the folder", name_problem("a\\b.jpg"))

    def test_characters_windows_does_not_allow(self) -> None:
        for char in '<>:"|?*':
            with self.subTest(char=char):
                problem = name_problem(f"x{char}.jpg")
                self.assertIsNotNone(problem)
                self.assertIn("a character Windows does not allow", problem)
                self.assertIn(f"({char})", problem)

    def test_control_characters_are_shown_as_hex(self) -> None:
        problem = name_problem("a\x01b.txt")
        self.assertIn("a character Windows does not allow", problem)
        self.assertIn("\\x01", problem)
        self.assertIn("a character Windows does not allow", name_problem("a\x00b"))

    def test_trailing_dot_or_space(self) -> None:
        self.assertIn("ends in a dot or space", name_problem("x."))
        self.assertIn("ends in a dot or space", name_problem("x "))

    def test_device_names_in_any_case_with_or_without_extension(self) -> None:
        for name in ("CON", "con.txt", "Prn", "aux.tar.gz", "NUL", "COM1", "com9.log", "LPT1", "lpt9.x"):
            with self.subTest(name=name):
                problem = name_problem(name)
                self.assertIsNotNone(problem)
                self.assertIn("device name on Windows", problem)

    def test_device_message_names_the_device(self) -> None:
        self.assertEqual(
            name_problem("con.txt"), 'new name "con.txt" is a device name on Windows (CON)'
        )

    def test_length_limit_is_255_characters(self) -> None:
        self.assertIsNone(name_problem("a" * 251 + ".txt"))
        self.assertEqual(
            name_problem("a" * 252 + ".txt"), "the new name is longer than 255 characters"
        )


class HiddenAndFindFilesTests(TempFolderCase):
    def test_is_hidden_by_leading_dot(self) -> None:
        self.assertTrue(is_hidden(Path(".env")))
        self.assertTrue(is_hidden(self.root / ".env"))
        self.assertFalse(is_hidden(Path("env")))
        self.assertFalse(is_hidden(self.root / "visible.txt"))

    @unittest.skipUnless(sys.platform == "win32", "needs the Windows hidden attribute")
    def test_is_hidden_by_windows_attribute(self) -> None:
        import ctypes

        for flag, name in ((0x2, "hidden.txt"), (0x4, "system.txt")):
            with self.subTest(name=name):
                path = self.write(name)
                self.assertFalse(is_hidden(path))
                self.assertTrue(ctypes.windll.kernel32.SetFileAttributesW(str(path), flag))
                self.assertTrue(is_hidden(path))

    def relative(self, found: list[Path]) -> list[str]:
        return [p.relative_to(self.root).as_posix() for p in found]

    def make_tree(self) -> None:
        for name in (
            "img10.jpg",
            "img2.jpg",
            "Img1.png",
            ".secret.jpg",
            "sub/inner.jpg",
            "sub/.hid.jpg",
            ".git/config.jpg",
            ".git/deep/x.jpg",
        ):
            self.write(name)

    def test_top_level_in_natural_order_skipping_dot_files(self) -> None:
        self.make_tree()
        found = find_files(self.root, pattern=None, recursive=False, hidden=False)
        self.assertEqual(self.relative(found), ["Img1.png", "img2.jpg", "img10.jpg"])

    def test_hidden_includes_dot_files(self) -> None:
        self.make_tree()
        found = find_files(self.root, pattern=None, recursive=False, hidden=True)
        self.assertEqual(
            sorted(self.relative(found)), [".secret.jpg", "Img1.png", "img10.jpg", "img2.jpg"]
        )

    def test_never_returns_folders(self) -> None:
        self.make_tree()
        found = find_files(self.root, pattern=None, recursive=False, hidden=True)
        self.assertTrue(all(p.is_file() for p in found))
        self.assertNotIn("sub", self.relative(found))

    def test_recursive_enters_folders_but_not_hidden_ones(self) -> None:
        self.make_tree()
        found = find_files(self.root, pattern=None, recursive=True, hidden=False)
        self.assertEqual(
            sorted(self.relative(found)),
            ["Img1.png", "img10.jpg", "img2.jpg", "sub/inner.jpg"],
        )

    def test_recursive_with_hidden_enters_hidden_folders(self) -> None:
        self.make_tree()
        found = find_files(self.root, pattern=None, recursive=True, hidden=True)
        self.assertEqual(
            sorted(self.relative(found)),
            [
                ".git/config.jpg",
                ".git/deep/x.jpg",
                ".secret.jpg",
                "Img1.png",
                "img10.jpg",
                "img2.jpg",
                "sub/.hid.jpg",
                "sub/inner.jpg",
            ],
        )

    def test_recursive_keeps_natural_order_inside_a_folder(self) -> None:
        for name in ("sub/p10.txt", "sub/p9.txt", "sub/p1.txt"):
            self.write(name)
        found = find_files(self.root, pattern=None, recursive=True, hidden=False)
        self.assertEqual(self.relative(found), ["sub/p1.txt", "sub/p9.txt", "sub/p10.txt"])

    def test_pattern_is_searched_in_the_relative_posix_path(self) -> None:
        for name in ("a.jpg", "b.png", "sub/c.jpg", "other/d.jpg"):
            self.write(name)
        by_ext = find_files(self.root, pattern=re.compile(r"\.jpg$"), recursive=True, hidden=False)
        self.assertEqual(sorted(self.relative(by_ext)), ["a.jpg", "other/d.jpg", "sub/c.jpg"])
        by_folder = find_files(self.root, pattern=re.compile(r"^sub/"), recursive=True, hidden=False)
        self.assertEqual(self.relative(by_folder), ["sub/c.jpg"])

    def test_pattern_does_not_match_the_absolute_part_of_the_path(self) -> None:
        self.write("a.jpg")
        found = find_files(
            self.root, pattern=re.compile(re.escape(self.root.name)), recursive=False, hidden=False
        )
        self.assertEqual(found, [])


def by_source(items: list[RenameItem]) -> dict[str, RenameItem]:
    return {item.source.name: item for item in items}


class PlanRenamesTests(TempFolderCase):
    def test_returns_one_item_per_source_in_order(self) -> None:
        sources = [self.write("b.txt"), self.write("a.txt"), self.write("c.txt")]
        items = plan_renames(sources, RenameOptions(prefix="x-"))
        self.assertEqual([i.source for i in items], sources)
        self.assertEqual([i.target.name for i in items], ["x-b.txt", "x-a.txt", "x-c.txt"])
        self.assertEqual([i.status for i in items], ["rename"] * 3)
        for item in items:
            self.assertEqual(item.target.parent, item.source.parent)
            self.assertEqual(item.detail, "")

    def test_missing_source_fails_with_not_found(self) -> None:
        missing = self.root / "gone.txt"
        (item,) = plan_renames([missing], RenameOptions(prefix="x-"))
        self.assertEqual((item.status, item.detail), ("failed", "not found"))
        self.assertEqual(item.target, item.source)

    def test_folder_source_fails_and_points_at_dir(self) -> None:
        folder = self.root / "photos"
        folder.mkdir()
        (item,) = plan_renames([folder], RenameOptions(prefix="x-"))
        self.assertEqual(item.status, "failed")
        self.assertEqual(item.detail, "photos is a folder; use --dir photos")
        self.assertEqual(item.target, item.source)

    def test_unchanged_name(self) -> None:
        source = self.write("a.txt")
        (item,) = plan_renames([source], RenameOptions(replace=(("zzz", "y"),)))
        self.assertEqual(item.status, "unchanged")
        self.assertEqual(item.target, source)

    def test_a_name_problem_fails_that_file_only(self) -> None:
        bad = self.write("a.txt")
        good = self.write("b.txt")
        items = plan_renames([bad, good], RenameOptions(replace=(("a", "x/y"),)))
        self.assertEqual(items[0].status, "failed")
        self.assertIn("would leave the folder", items[0].detail)
        self.assertEqual(items[0].target, bad)
        self.assertEqual(items[1].status, "unchanged")

    def test_device_name_fails(self) -> None:
        source = self.write("x.txt")
        (item,) = plan_renames([source], RenameOptions(template="con"))
        self.assertEqual(item.status, "failed")
        self.assertIn("device name", item.detail)

    def test_counter_starts_at_start_with_one_number_per_source(self) -> None:
        sources = [self.write("a.txt"), self.root / "missing.txt", self.write("c.txt")]
        items = plan_renames(sources, RenameOptions(template="{n}", start=5))
        self.assertEqual(items[0].target.name, "5.txt")
        self.assertEqual(items[1].status, "failed")
        self.assertEqual(items[2].target.name, "7.txt")

    def test_counter_restarts_in_each_folder(self) -> None:
        sources = [
            self.write("one/a.txt"),
            self.write("two/b.txt"),
            self.write("one/c.txt"),
            self.write("two/d.txt"),
        ]
        items = plan_renames(sources, RenameOptions(template="{n:02}"))
        self.assertEqual(
            [(i.source.parent.name, i.target.name) for i in items],
            [("one", "01.txt"), ("two", "01.txt"), ("one", "02.txt"), ("two", "02.txt")],
        )

    def test_counter_counts_unchanged_sources_too(self) -> None:
        sources = [self.write("1.txt"), self.write("zzz.txt")]
        items = plan_renames(sources, RenameOptions(template="{n}"))
        self.assertEqual(items[0].status, "unchanged")
        self.assertEqual(items[1].target.name, "2.txt")

    def test_parent_field_is_the_folder_name(self) -> None:
        source = self.write("holiday/a.txt")
        (item,) = plan_renames([source], RenameOptions(template="{parent}-{name}"))
        self.assertEqual(item.target.name, "holiday-a.txt")

    def test_date_field_is_the_modified_date(self) -> None:
        source = self.write("a.txt")
        stamp = datetime(2020, 5, 17, 12, 0, 0).timestamp()
        os.utime(source, (stamp, stamp))
        (item,) = plan_renames([source], RenameOptions(template="{date}"))
        self.assertEqual(item.target.name, "2020-05-17.txt")

    def test_clash_inside_the_run_adds_counters_to_later_files(self) -> None:
        sources = [self.write("a1.txt"), self.write("a2.txt"), self.write("a3.txt")]
        items = plan_renames(sources, RenameOptions(regex=((r"\d", ""),)))
        self.assertEqual([i.target.name for i in items], ["a.txt", "a-2.txt", "a-3.txt"])
        self.assertEqual([i.status for i in items], ["rename"] * 3)
        self.assertEqual(items[0].detail, "")
        self.assertEqual(items[1].detail, "clash: -2 added")
        self.assertEqual(items[2].detail, "clash: -3 added")

    def test_clash_is_compared_without_case(self) -> None:
        sources = [self.write("A1.txt"), self.write("a2.txt")]
        items = plan_renames(sources, RenameOptions(regex=((r"\d", ""),)))
        self.assertEqual(items[0].target.name, "A.txt")
        self.assertEqual(items[1].target.name, "a-2.txt")
        self.assertEqual(items[1].detail, "clash: -2 added")

    def test_same_new_name_in_different_folders_is_not_a_clash(self) -> None:
        sources = [self.write("one/a1.txt"), self.write("two/a2.txt")]
        items = plan_renames(sources, RenameOptions(regex=((r"\d", ""),)))
        self.assertEqual([i.target.name for i in items], ["a.txt", "a.txt"])
        self.assertEqual([i.detail for i in items], ["", ""])

    def test_existing_file_outside_the_run_blocks_the_rename(self) -> None:
        source = self.write("a.txt")
        self.write("b.txt")
        (item,) = plan_renames([source], RenameOptions(replace=(("a", "b"),)))
        self.assertEqual((item.status, item.detail), ("skipped", "b.txt exists"))
        self.assertEqual(item.target, source)

    def test_chain_is_allowed(self) -> None:
        sources = [self.write("1.txt"), self.write("2.txt")]
        items = plan_renames(sources, RenameOptions(template="{n}", start=2))
        self.assertEqual([i.status for i in items], ["rename", "rename"])
        self.assertEqual([i.target.name for i in items], ["2.txt", "3.txt"])
        self.assertEqual([i.detail for i in items], ["", ""])

    def test_swap_is_allowed(self) -> None:
        sources = [self.write("2.txt"), self.write("1.txt")]
        items = plan_renames(sources, RenameOptions(template="{n}"))
        self.assertEqual([i.status for i in items], ["rename", "rename"])
        self.assertEqual([i.target.name for i in items], ["1.txt", "2.txt"])

    def test_chain_blocked_by_an_existing_file_skips_the_whole_chain(self) -> None:
        sources = [self.write("1.txt"), self.write("2.txt")]
        self.write("3.txt")
        items = plan_renames(sources, RenameOptions(template="{n}", start=2))
        self.assertEqual([i.status for i in items], ["skipped", "skipped"])
        self.assertEqual(items[0].detail, "2.txt exists")
        self.assertEqual(items[1].detail, "3.txt exists")

    def test_case_only_change_is_a_rename(self) -> None:
        source = self.write("IMG.JPG")
        (item,) = plan_renames([source], RenameOptions(case="lower", lower_ext=True))
        self.assertEqual(item.status, "rename")
        self.assertEqual(item.target.name, "img.jpg")
        self.assertEqual(item.detail, "")

    def test_unchanged_file_blocks_a_rename_onto_its_name(self) -> None:
        keeps = self.write("a.txt")
        wants = self.write("ab.txt")
        items = plan_renames([keeps, wants], RenameOptions(replace=(("b", ""),)))
        self.assertEqual(items[0].status, "unchanged")
        self.assertEqual((items[1].status, items[1].detail), ("skipped", "a.txt exists"))

    def test_failed_file_keeps_its_name_and_blocks_a_rename_onto_it(self) -> None:
        # "a.txt" fails (its new name leaves the folder) so it stays; "ab.txt" wants "a.txt".
        stays = self.write("a.txt")
        wants = self.write("ab.txt")
        options = RenameOptions(regex=((r"^a$", "x/y"), (r"^ab$", "a")))
        items = plan_renames([stays, wants], options)
        self.assertEqual(items[0].status, "failed")
        self.assertEqual((items[1].status, items[1].detail), ("skipped", "a.txt exists"))

    def test_clash_counter_skips_names_that_are_taken(self) -> None:
        sources = [self.write("a1.txt"), self.write("a2.txt")]
        self.write("a-2.txt")
        items = plan_renames(sources, RenameOptions(regex=((r"\d", ""),)))
        self.assertEqual(items[0].target.name, "a.txt")
        self.assertEqual(items[1].status, "rename")
        self.assertNotIn(items[1].target.name, ("a.txt", "a-2.txt"))
        self.assertTrue(items[1].detail.startswith("clash: -"))


class ApplyRenamesTests(TempFolderCase):
    def contents(self) -> dict[str, str]:
        return {
            name: (self.root / name).read_text(encoding="utf-8") for name in self.names()
        }

    def assertNoTempFiles(self) -> None:
        left = [n for n in self.names() if n.startswith("jzr-tmp-")]
        self.assertEqual(left, [])

    def test_plain_renames(self) -> None:
        sources = [self.write("a.txt", "A"), self.write("b.txt", "B")]
        result = apply_renames(plan_renames(sources, RenameOptions(prefix="x-")))
        self.assertEqual([i.status for i in result], ["renamed", "renamed"])
        self.assertEqual([i.source for i in result], sources)
        self.assertEqual([i.target.name for i in result], ["x-a.txt", "x-b.txt"])
        self.assertEqual(self.contents(), {"x-a.txt": "A", "x-b.txt": "B"})
        self.assertNoTempFiles()

    def test_clash_detail_survives_apply(self) -> None:
        sources = [self.write("a1.txt", "1"), self.write("a2.txt", "2")]
        result = apply_renames(plan_renames(sources, RenameOptions(regex=((r"\d", ""),))))
        self.assertEqual(result[1].detail, "clash: -2 added")
        self.assertEqual(self.contents(), {"a.txt": "1", "a-2.txt": "2"})

    def test_swap_ends_with_swapped_contents(self) -> None:
        sources = [self.write("2.txt", "was two"), self.write("1.txt", "was one")]
        items = plan_renames(sources, RenameOptions(template="{n}"))
        result = apply_renames(items)
        self.assertEqual([i.status for i in result], ["renamed", "renamed"])
        self.assertEqual(self.contents(), {"1.txt": "was two", "2.txt": "was one"})
        self.assertNoTempFiles()

    def test_chain_ends_with_shifted_contents(self) -> None:
        sources = [self.write("1.txt", "one"), self.write("2.txt", "two")]
        result = apply_renames(plan_renames(sources, RenameOptions(template="{n}", start=2)))
        self.assertEqual([i.status for i in result], ["renamed", "renamed"])
        self.assertEqual(self.contents(), {"2.txt": "one", "3.txt": "two"})
        self.assertNoTempFiles()

    def test_case_only_change_ends_with_the_exact_new_case(self) -> None:
        source = self.write("IMG.JPG", "pixels")
        result = apply_renames(plan_renames([source], RenameOptions(case="lower", lower_ext=True)))
        self.assertEqual(result[0].status, "renamed")
        self.assertEqual(os.listdir(self.root), ["img.jpg"])
        self.assertEqual((self.root / "img.jpg").read_text(encoding="utf-8"), "pixels")

    def test_other_items_pass_through_unchanged(self) -> None:
        moving = self.write("ab.txt", "m")
        keeps = self.write("a.txt", "k")
        missing = self.root / "gone.txt"
        planned = plan_renames([moving, keeps, missing], RenameOptions(replace=(("b", ""),)))
        self.assertEqual([i.status for i in planned], ["skipped", "unchanged", "failed"])
        self.assertEqual(apply_renames(planned), planned)
        self.assertEqual(self.contents(), {"a.txt": "k", "ab.txt": "m"})

    def test_target_that_appears_after_planning_is_skipped(self) -> None:
        a = self.write("a.txt", "A")
        b = self.write("b.txt", "B")
        planned = plan_renames([a, b], RenameOptions(prefix="x-"))
        self.write("x-a.txt", "newcomer")
        result = apply_renames(planned)
        self.assertEqual((result[0].status, result[0].detail), ("skipped", "x-a.txt exists"))
        self.assertEqual(result[1].status, "renamed")
        self.assertEqual(
            self.contents(), {"a.txt": "A", "x-a.txt": "newcomer", "x-b.txt": "B"}
        )
        self.assertNoTempFiles()

    def test_newcomer_blocking_a_chain_skips_the_chain(self) -> None:
        sources = [self.write("1.txt", "one"), self.write("2.txt", "two")]
        planned = plan_renames(sources, RenameOptions(template="{n}", start=2))
        self.write("3.txt", "newcomer")
        result = apply_renames(planned)
        self.assertEqual([i.status for i in result], ["skipped", "skipped"])
        self.assertEqual(self.contents(), {"1.txt": "one", "2.txt": "two", "3.txt": "newcomer"})

    def test_phase_one_failure_leaves_that_file_alone_and_fails_only_it(self) -> None:
        sources = [self.write("a.txt", "A"), self.write("b.txt", "B")]
        planned = plan_renames(sources, RenameOptions(prefix="x-"))

        def fake(src: Path, dst: Path) -> None:
            if Path(src).name == "a.txt":
                raise OSError(13, "Permission denied")
            os.rename(src, dst)

        result = apply_renames(planned, rename=fake)
        self.assertEqual(result[0].status, "failed")
        self.assertIn("could not rename", result[0].detail)
        self.assertIn("Permission denied", result[0].detail)
        self.assertEqual(result[1].status, "renamed")
        self.assertEqual(self.contents(), {"a.txt": "A", "x-b.txt": "B"})
        self.assertNoTempFiles()

    def test_phase_two_failure_puts_the_file_back(self) -> None:
        sources = [self.write("a.txt", "A"), self.write("b.txt", "B")]
        planned = plan_renames(sources, RenameOptions(prefix="x-"))

        def fake(src: Path, dst: Path) -> None:
            if Path(dst).name == "x-a.txt":
                raise OSError(13, "Permission denied")
            os.rename(src, dst)

        result = apply_renames(planned, rename=fake)
        self.assertEqual(result[0].status, "failed")
        self.assertIn("could not rename", result[0].detail)
        self.assertIn("Permission denied", result[0].detail)
        self.assertNotIn("jzr-tmp-", result[0].detail)
        self.assertEqual(result[1].status, "renamed")
        self.assertEqual(self.contents(), {"a.txt": "A", "x-b.txt": "B"})
        self.assertNoTempFiles()

    def test_failed_put_back_names_the_temp_file(self) -> None:
        source = self.write("a.txt", "A")
        planned = plan_renames([source], RenameOptions(prefix="x-"))

        def fake(src: Path, dst: Path) -> None:
            if Path(dst).name in ("x-a.txt", "a.txt") and Path(src).name.startswith("jzr-tmp-"):
                raise OSError(13, "Permission denied")
            os.rename(src, dst)

        (item,) = apply_renames(planned, rename=fake)
        self.assertEqual(item.status, "failed")
        self.assertIn("Permission denied", item.detail)
        left = [n for n in self.names() if n.startswith("jzr-tmp-")]
        self.assertEqual(len(left), 1)
        self.assertIn(left[0], item.detail)
        self.assertEqual((self.root / left[0]).read_text(encoding="utf-8"), "A")

    def test_temp_name_keeps_the_extension(self) -> None:
        source = self.write("a.txt", "A")
        planned = plan_renames([source], RenameOptions(prefix="x-"))
        seen: list[str] = []

        def fake(src: Path, dst: Path) -> None:
            seen.append(Path(dst).name)
            os.rename(src, dst)

        apply_renames(planned, rename=fake)
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[0].startswith("jzr-tmp-") and seen[0].endswith(".txt"))
        self.assertEqual(seen[1], "x-a.txt")

    def interrupting(self, at_call: int):
        calls = {"n": 0}

        def fake(src: Path, dst: Path) -> None:
            calls["n"] += 1
            if calls["n"] == at_call:
                raise KeyboardInterrupt
            os.rename(src, dst)

        return fake

    def assertEveryFileAtOldOrNewName(self, items: list[RenameItem], originals: dict[str, str]) -> None:
        self.assertNoTempFiles()
        for item in items:
            old, new = item.source.name, item.target.name
            if item.status == "renamed":
                self.assertTrue((self.root / new).exists(), new)
                self.assertFalse((self.root / old).exists(), old)
                where = new
            else:
                self.assertEqual(item.status, "skipped")
                self.assertEqual(item.detail, "interrupted")
                self.assertTrue((self.root / old).exists(), old)
                self.assertFalse((self.root / new).exists(), new)
                where = old
            self.assertEqual((self.root / where).read_text(encoding="utf-8"), originals[old])

    def test_interrupt_in_phase_one_restores_every_file(self) -> None:
        originals = {"a.txt": "A", "b.txt": "B", "c.txt": "C"}
        sources = [self.write(name, body) for name, body in originals.items()]
        planned = plan_renames(sources, RenameOptions(prefix="x-"))
        with self.assertRaises(RenameInterrupted) as caught:
            apply_renames(planned, rename=self.interrupting(at_call=2))
        items = caught.exception.items
        self.assertEqual([i.source for i in items], sources)
        self.assertEqual(self.contents(), originals)
        self.assertEveryFileAtOldOrNewName(items, originals)
        self.assertEqual({i.status for i in items}, {"skipped"})

    def test_interrupt_in_phase_two_leaves_no_temp_files(self) -> None:
        originals = {"a.txt": "A", "b.txt": "B", "c.txt": "C"}
        sources = [self.write(name, body) for name, body in originals.items()]
        planned = plan_renames(sources, RenameOptions(prefix="x-"))
        # Calls 1-3 are phase one; call 4 finishes "a.txt"; call 5 is interrupted.
        with self.assertRaises(RenameInterrupted) as caught:
            apply_renames(planned, rename=self.interrupting(at_call=5))
        items = caught.exception.items
        self.assertEqual([i.source for i in items], sources)
        self.assertEveryFileAtOldOrNewName(items, originals)
        self.assertEqual(items[0].status, "renamed")
        self.assertEqual(items[2].status, "skipped")
        self.assertEqual((self.root / "x-a.txt").read_text(encoding="utf-8"), "A")

    def test_interrupt_during_a_swap_leaves_both_files(self) -> None:
        originals = {"1.txt": "one", "2.txt": "two"}
        sources = [self.write("2.txt", "two"), self.write("1.txt", "one")]
        planned = plan_renames(sources, RenameOptions(template="{n}"))
        with self.assertRaises(RenameInterrupted) as caught:
            apply_renames(planned, rename=self.interrupting(at_call=3))
        self.assertNoTempFiles()
        self.assertEqual(sorted(self.names()), ["1.txt", "2.txt"])
        for item in caught.exception.items:
            where = item.target.name if item.status == "renamed" else item.source.name
            self.assertEqual((self.root / where).read_text(encoding="utf-8"), originals[item.source.name])


if __name__ == "__main__":
    unittest.main()
