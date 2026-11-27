"""Offline tests for combining results folders: runner.combine_records and `report A B ...` in the CLI.

Results folders are tiny temporary ones with a hand-written runs.jsonl (and sometimes a meta.json).
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import usage_fixtures as uf  # noqa: E402
from jzr_bench.cli import main  # noqa: E402
from jzr_bench.runner import combine_records  # noqa: E402


def run(task: str, arm: str, cost: float, *, round: int = 1, src: str = "") -> dict[str, Any]:
    record = uf.usage_record({}, task=task, arm=arm, round=round, cost=cost)
    record["src"] = src  # which folder the record was written into
    return record


class FolderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def folder(self, name: str, records: list[dict[str, Any]] | None, meta: dict[str, Any] | None = None) -> Path:
        """A results folder; `records` None means no runs.jsonl at all."""
        path = self.root / name
        path.mkdir()
        if records is not None:
            path.joinpath("runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        if meta is not None:
            path.joinpath("meta.json").write_text(json.dumps(meta))
        return path


class CombineRecordsTest(FolderTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.folder(
            "20261126T131420Z",
            [
                run("hello", "baseline", 0.5, src="a"),
                run("hello", "skill", 0.6, src="a"),
                run("gif", "baseline", 1.0, src="a"),
                run("gif", "skill", 1.1, src="a"),
            ],
        )
        self.b = self.folder(
            "20261127T070331Z",
            [
                run("gif", "baseline", 2.0, src="b"),
                run("gif", "baseline", 2.5, round=2, src="b"),
                run("xlsx", "skill", 3.0, src="b"),
            ],
        )

    def ids(self, records: list[dict]) -> list[tuple[str, str, str]]:
        return sorted((r["task"], r["arm"], r["src"]) for r in records)

    def test_each_task_comes_from_the_last_folder_that_has_it(self) -> None:
        records, sources = combine_records([self.a, self.b])
        self.assertEqual(
            self.ids(records),
            [
                ("gif", "baseline", "b"),
                ("gif", "baseline", "b"),
                ("hello", "baseline", "a"),
                ("hello", "skill", "a"),
                ("xlsx", "skill", "b"),
            ],
        )
        self.assertEqual(sources, {"hello": self.a.name, "gif": self.b.name, "xlsx": self.b.name})

    def test_the_order_of_the_folders_decides(self) -> None:
        records, sources = combine_records([self.b, self.a])
        self.assertEqual(sources["gif"], self.a.name)
        self.assertEqual(sources["xlsx"], self.b.name)
        self.assertEqual(sorted(r["arm"] for r in records if r["task"] == "gif"), ["baseline", "skill"])
        self.assertTrue(all(r["src"] == "a" for r in records if r["task"] == "gif"))

    def test_all_runs_of_the_winning_folder_are_kept_and_none_of_the_others(self) -> None:
        records, _ = combine_records([self.a, self.b])
        gif = [r for r in records if r["task"] == "gif"]
        self.assertEqual(sorted(r["round"] for r in gif), [1, 2])
        self.assertEqual({r["src"] for r in gif}, {"b"})  # not a single gif run of folder a

    def test_a_task_in_an_earlier_folder_survives_a_later_folder_without_it(self) -> None:
        c = self.folder("20261128T000000Z", [run("pdf", "baseline", 4.0, src="c")])
        records, sources = combine_records([self.a, self.b, c])
        self.assertEqual(sources["hello"], self.a.name)
        self.assertEqual(sources["pdf"], c.name)
        self.assertEqual({r["task"] for r in records}, {"hello", "gif", "xlsx", "pdf"})

    def test_one_folder_gives_all_its_records(self) -> None:
        records, sources = combine_records([self.a])
        self.assertEqual(len(records), 4)
        self.assertEqual(sources, {"hello": self.a.name, "gif": self.a.name})

    def test_records_are_returned_as_written(self) -> None:
        records, _ = combine_records([self.a])
        written = [json.loads(line) for line in (self.a / "runs.jsonl").read_text().splitlines()]
        self.assertEqual(sorted(records, key=lambda r: r["run_id"]), sorted(written, key=lambda r: r["run_id"]))

    def test_blank_lines_are_skipped(self) -> None:
        path = self.a / "runs.jsonl"
        path.write_text("\n" + path.read_text() + "\n\n")
        records, _ = combine_records([self.a])
        self.assertEqual(len(records), 4)

    def test_sources_name_the_folder_not_its_path(self) -> None:
        _, sources = combine_records([self.a, self.b])
        for name in sources.values():
            self.assertNotIn("/", name)


class ReportCliTest(FolderTestCase):
    def report(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["report", *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def two_folders(self) -> tuple[Path, Path]:
        a = self.folder(
            "run-a",
            [
                run("hello", "baseline", 0.5),
                run("hello", "skill", 0.6),
                run("gif", "baseline", 1.0),
                run("gif", "skill", 1.1),
            ],
        )
        b = self.folder("run-b", [run("gif", "baseline", 2.0), run("gif", "skill", 1.5)])
        return a, b

    def listing(self, folder: Path) -> list[str]:
        return sorted(p.name for p in folder.iterdir())

    def test_several_folders_print_one_combined_summary(self) -> None:
        a, b = self.two_folders()
        code, out, err = self.report(str(a), str(b))
        self.assertEqual(code, 0, err)
        # gif comes from the last folder, hello from the first
        self.assertIn("$2.0000", out)
        self.assertIn("$1.5000", out)
        self.assertNotIn("$1.0000", out)
        self.assertNotIn("$1.1000", out)
        self.assertIn("$0.5000", out)  # hello baseline
        self.assertIn("run-a", out)
        self.assertIn("run-b", out)

    def test_the_combined_summary_has_the_all_tasks_section_across_folders(self) -> None:
        a, b = self.two_folders()
        _, out, _ = self.report(str(a), str(b))
        self.assertIn("All tasks", out)
        # gif of folder b minus hello of folder a: 2.0 - 0.5 and 1.5 - 0.6
        self.assertIn("$1.5000", out)
        self.assertIn("$0.9000", out)

    def test_several_folders_write_no_summary_md_into_any_folder(self) -> None:
        a, b = self.two_folders()
        before = (self.listing(a), self.listing(b))
        code, _, err = self.report(str(a), str(b))
        self.assertEqual(code, 0, err)
        self.assertEqual((self.listing(a), self.listing(b)), before)
        self.assertFalse((a / "summary.md").exists())
        self.assertFalse((b / "summary.md").exists())

    def test_out_writes_the_combined_summary_to_that_file(self) -> None:
        a, b = self.two_folders()
        target = self.root / "combined.md"
        code, out, err = self.report(str(a), str(b), "--out", str(target))
        self.assertEqual(code, 0, err)
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_text().strip(), out.strip())
        self.assertIn("run-a", target.read_text())
        self.assertIn("run-b", target.read_text())
        self.assertFalse((a / "summary.md").exists())
        self.assertFalse((b / "summary.md").exists())

    def test_out_can_come_before_the_folders(self) -> None:
        a, b = self.two_folders()
        target = self.root / "combined.md"
        code, _, err = self.report("--out", str(target), str(a), str(b))
        self.assertEqual(code, 0, err)
        self.assertIn("$2.0000", target.read_text())

    def test_one_folder_rewrites_its_summary_md_as_before(self) -> None:
        a, _ = self.two_folders()
        code, out, err = self.report(str(a))
        self.assertEqual(code, 0, err)
        summary = a / "summary.md"
        self.assertTrue(summary.is_file())
        self.assertEqual(summary.read_text().strip(), out.strip())
        self.assertIn("$1.0000", out)  # gif of its own runs
        self.assertIn("$0.5000", out)

    def test_one_folder_replaces_an_old_summary_md(self) -> None:
        a, _ = self.two_folders()
        (a / "summary.md").write_text("stale")
        self.report(str(a))
        self.assertNotIn("stale", (a / "summary.md").read_text())

    def test_the_single_folder_summary_shows_meta_such_as_private_tmp(self) -> None:
        a = self.folder("run-m", [run("gif", "baseline", 1.0)], meta={"model": "sonnet", "private_tmp": False})
        _, out, _ = self.report(str(a))
        self.assertIn("model: sonnet", out)
        self.assertRegex(out.lower(), r"private[ _]tmp.*false")

    def test_prices_fitted_from_the_runs_are_listed(self) -> None:
        records = uf.fit_records(task="gif", arm="baseline")
        a = self.folder("run-p", records)
        code, out, err = self.report(str(a))
        self.assertEqual(code, 0, err)
        self.assertIn(uf.MODEL, out)
        self.assertIn("million", out.lower())

    def test_prices_are_fitted_over_the_combined_runs(self) -> None:
        a, _ = self.two_folders()
        c = self.folder("run-c", uf.fit_records(task="xlsx", arm="baseline"))
        code, out, err = self.report(str(a), str(c))
        self.assertEqual(code, 0, err)
        self.assertIn(uf.MODEL, out)

    def test_a_folder_without_runs_jsonl_is_an_error(self) -> None:
        empty = self.folder("run-empty", None)
        code, out, err = self.report(str(empty))
        self.assertEqual(code, 2)
        self.assertIn("runs.jsonl", err)
        self.assertEqual(out, "")
        self.assertFalse((empty / "summary.md").exists())

    def test_one_missing_folder_among_several_is_an_error_and_nothing_is_written(self) -> None:
        a, b = self.two_folders()
        empty = self.folder("run-empty", None)
        target = self.root / "combined.md"
        code, out, err = self.report(str(a), str(empty), str(b), "--out", str(target))
        self.assertEqual(code, 2)
        self.assertIn(str(empty / "runs.jsonl"), err)
        self.assertEqual(out, "")
        self.assertFalse(target.exists())
        self.assertFalse((a / "summary.md").exists())
        self.assertFalse((b / "summary.md").exists())

    def test_a_folder_that_does_not_exist_is_an_error(self) -> None:
        a, _ = self.two_folders()
        code, _, err = self.report(str(a), str(self.root / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("runs.jsonl", err)


if __name__ == "__main__":
    unittest.main()
