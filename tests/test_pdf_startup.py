"""Start-up checks for the pdf group: help screens load no PDF library."""

from __future__ import annotations

import importlib
from pathlib import Path
import re
import sys
import tempfile
import unittest

import click

from tests import pdf_fixtures as fx
from tests import test_startup as startup

PDF_LIBRARIES = ["pypdf", "pypdfium2", "PIL"]
PDF_CORE = [
    "jzr_tools.core.pdfdoc",
    "jzr_tools.core.pdf_info",
    "jzr_tools.core.pdf_pages",
    "jzr_tools.core.pdf_compress",
    "jzr_tools.core.pdf_content",
    "jzr_tools.core.pdf_render",
    "jzr_tools.core.pdf_build",
    "jzr_tools.core.pdf_text",
    "jzr_tools.core.pdf_markdown",
]
GROUP_HELP = "Merge, split, compress, render and read PDF files."


def load_cli():  # noqa: ANN201
    sys.path.insert(0, str(startup.SRC_PATH))
    try:
        return importlib.import_module("jzr_tools.cli")
    finally:
        sys.path.remove(str(startup.SRC_PATH))


class PdfRegistryTests(unittest.TestCase):
    def test_the_registry_text_equals_the_groups_own_short_help(self) -> None:
        cli = load_cli()
        import_path, help_text = cli.app.lazy_commands["pdf"]
        self.assertEqual(import_path, "jzr_tools.commands.pdf:app")
        self.assertEqual(help_text, GROUP_HELP)
        command = cli.app.get_command(click.Context(cli.app), "pdf")
        self.assertIsInstance(command, click.Group)
        self.assertEqual(command.get_short_help_str(limit=300), help_text)


class PdfStartupTests(startup._ObservedTestCase):
    def test_root_help_lists_pdf_without_loading_its_code(self) -> None:
        obs = self.run_cli("--help")
        self.assert_ok(obs)
        self.assertRegex(obs.stdout, rf"(?m)^  pdf\s+{re.escape(GROUP_HELP)}")
        self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE + ["jzr_tools.commands.pdf"])

    def test_group_help_lists_the_commands_and_loads_no_pdf_library(self) -> None:
        obs = self.run_cli("pdf", "-h")
        self.assert_ok(obs)
        self.assertIn("Usage:", obs.stdout)
        listed = re.findall(r"^  (\S+)", obs.stdout.split("Commands:", 1)[1], flags=re.MULTILINE)
        for name in ("info", "merge", "split", "organize", "compress", "images", "from-images", "text"):
            self.assertIn(name, listed)
        self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_each_command_help_loads_no_pdf_library(self) -> None:
        for command in ("info", "merge", "split", "organize", "compress", "images", "from-images", "text"):
            with self.subTest(command=command):
                obs = self.run_cli("pdf", command, "-h")
                self.assert_ok(obs)
                self.assertIn("Usage:", obs.stdout)
                self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_compress_help_loads_none_of_the_pdf_libraries(self) -> None:
        obs = self.run_cli("pdf", "compress", "-h")
        self.assert_ok(obs)
        self.assertIn("Make PDF files smaller, mostly by shrinking their images.", obs.stdout)
        self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_compress_argument_errors_load_none_of_the_pdf_libraries(self) -> None:
        for args in (
            ["--level", "extreme", "a.pdf"],
            ["-o", "result.txt", "a.pdf"],
            ["a.pdf", "-o", "one.pdf", "--outdir", "out"],
        ):
            with self.subTest(args=args):
                obs = self.run_cli("pdf", "compress", *args)
                self.assertEqual(obs.exit_code, "2", obs.describe())
                self.assertNotIn("No such command", obs.stderr + obs.stdout)
                self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_images_and_from_images_help_load_none_of_the_pdf_libraries(self) -> None:
        for command in ("images", "from-images"):
            with self.subTest(command=command):
                obs = self.run_cli("pdf", command, "-h")
                self.assert_ok(obs)
                self.assertIn("Usage:", obs.stdout)
                self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_images_argument_errors_load_none_of_the_pdf_libraries(self) -> None:
        for args in (
            ["--to", "gif", "a.pdf"],
            ["--dpi", "35", "a.pdf"],
            ["--dpi", "601", "a.pdf"],
            ["-q", "0", "a.pdf"],
            ["--to", "png", "-q", "75", "a.pdf"],
        ):
            with self.subTest(args=args):
                obs = self.run_cli("pdf", "images", *args)
                self.assertEqual(obs.exit_code, "2", obs.describe())
                self.assertNotIn("No such command", obs.stderr + obs.stdout)
                self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_from_images_argument_errors_load_none_of_the_pdf_libraries(self) -> None:
        for args in (
            ["--paper", "a5", "a.png"],
            ["--orientation", "sideways", "a.png"],
            ["--margin", "31", "a.png"],
            ["-o", "result.txt", "a.png"],
            [],
        ):
            with self.subTest(args=args):
                obs = self.run_cli("pdf", "from-images", *args)
                self.assertEqual(obs.exit_code, "2", obs.describe())
                self.assertNotIn("No such command", obs.stderr + obs.stdout)
                self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_text_help_loads_none_of_the_pdf_libraries(self) -> None:
        obs = self.run_cli("pdf", "text", "-h")
        self.assert_ok(obs)
        self.assertIn("Print the text of a PDF, or convert it to Markdown.", obs.stdout)
        self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_text_argument_errors_load_none_of_the_pdf_libraries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jzr-startup-pdf-") as folder:
            source = Path(folder) / "a.pdf"
            source.write_bytes(fx.marked(1))
            for args, message in (
                ([str(source), "--page-breaks"], "--page-breaks only applies to --markdown."),
                ([str(source), "--bogus"], "No such option"),
                ([], "Missing argument"),
            ):
                with self.subTest(args=args[1:]):
                    obs = self.run_cli("pdf", "text", *args)
                    self.assertEqual(obs.exit_code, "2", obs.describe())
                    self.assertIn(message, " ".join(obs.stderr.split()), obs.describe())
                    self.assertNotIn("No such command", obs.stderr + obs.stdout)
                    self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE)

    def test_a_compress_dry_run_loads_pypdf_but_not_pdfium(self) -> None:
        with tempfile.TemporaryDirectory(prefix="jzr-startup-pdf-") as folder:
            source = Path(folder) / "a.pdf"
            source.write_bytes(fx.marked(1))
            obs = self.run_cli("pdf", "compress", "--dry-run", str(source))
            created = sorted(entry.name for entry in Path(folder).iterdir())
        self.assert_ok(obs)
        self.assertIn("pdf: 1 planned", obs.stdout)
        self.assertEqual(created, ["a.pdf"])
        self.assertTrue(obs.loaded("pypdf"), obs.describe())
        self.assert_not_loaded(obs, ["pypdfium2"])

    def test_other_calls_never_load_the_pdf_code(self) -> None:
        for args in (["--version"], ["text", "slug", "x"], ["image", "formats"]):
            with self.subTest(args=args):
                obs = self.run_cli(*args)
                self.assert_ok(obs)
                self.assert_not_loaded(obs, PDF_LIBRARIES + PDF_CORE + ["jzr_tools.commands.pdf"])

    def test_a_merge_loads_pypdf_but_not_pdfium(self) -> None:
        # pypdf imports Pillow itself when it is installed, so only pdfium is checked.
        with tempfile.TemporaryDirectory(prefix="jzr-startup-pdf-") as folder:
            first, second = Path(folder) / "a.pdf", Path(folder) / "b.pdf"
            first.write_bytes(fx.marked(1))
            second.write_bytes(fx.marked(2))
            obs = self.run_cli("pdf", "merge", "--dry-run", str(first), str(second))
            created = sorted(entry.name for entry in Path(folder).iterdir())
        self.assert_ok(obs)
        self.assertIn("pdf: 1 planned", obs.stdout)
        self.assertEqual(created, ["a.pdf", "b.pdf"])
        self.assertTrue(obs.loaded("pypdf"), obs.describe())
        self.assert_not_loaded(obs, ["pypdfium2"])


if __name__ == "__main__":
    unittest.main()
