"""CLI behaviour of `jzr ffmpeg install|status|remove` and the offer to install.

Nothing here reaches the network or the real per-user folder: ``resolve``,
``install`` and the real fetch are replaced, urllib's ``urlopen`` fails the
test if called, and every run points ``JZR_DATA_DIR`` at a temporary folder.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core import ffmpeg, media

from media_fixtures import make_fake_tool

WHICH = "jzr_tools.core.ffmpeg.shutil.which"
INSTALL_MODULE = "jzr_tools.core.ffmpeg_install"
INTERACTIVE = "jzr_tools.commands._batch._interactive"
HOST = "github.com/BtbN/FFmpeg-Builds"
runner = CliRunner()


def not_found(name: str) -> str:
    return f"{name} was not found. Run: jzr ffmpeg install"


def err(result: object) -> str:
    try:
        return result.stderr
    except (AttributeError, ValueError):
        return ""


def describe(result: object) -> str:
    return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={err(result)!r}"


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        from jzr_tools.core import ffmpeg_install

        self.ffmpeg_install = ffmpeg_install
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.dest = self.data / "ffmpeg"
        self.source = ffmpeg_install.pick_source("Windows", "AMD64")
        self.release = self.make_release("9.0", size=81_000_000)

        urlopen = mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used"))
        urlopen.start()
        self.addCleanup(urlopen.stop)

    def make_release(self, version: str, *, size: int | None, sha: str = "a" * 64):  # noqa: ANN201
        archive = self.ffmpeg_install.Archive(
            f"https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-n{version}.zip",
            f"ffmpeg-n{version}.zip",
            sha,
            size,
        )
        return self.ffmpeg_install.Release(self.source, version, (archive,))

    def write_record(self, release=None, version: str | None = None) -> None:  # noqa: ANN001
        release = release or self.release
        self.dest.mkdir(parents=True, exist_ok=True)
        record = {
            "version": version or release.version,
            "source": HOST,
            "archives": [{"name": a.name, "sha256": a.sha256} for a in release.archives],
            "installed": "2026-11-14",
        }
        (self.dest / "jzr-install.json").write_text(json.dumps(record), encoding="utf-8")

    def question(self, version: str = "9.0", size: str = "81.0 MB") -> str:
        return f"Download ffmpeg {version} ({size}) from {HOST} into {self.dest}? [Y/n]"

    def environment(self, **extra: str | None) -> mock._patch_dict:
        environ = {
            k: v
            for k, v in os.environ.items()
            if k not in ("JZR_FFMPEG", "JZR_FFPROBE", "JZR_DATA_DIR")
        }
        environ["JZR_DATA_DIR"] = str(self.data)
        for key, value in extra.items():
            if value is None:
                environ.pop(key, None)
            else:
                environ[key] = value
        return mock.patch.dict(os.environ, environ, clear=True)

    def invoke(self, args: list[str], *, input: str | None = None, path: dict[str, str] | None = None,  # noqa: A002
               **extra: str | None):  # noqa: ANN201
        found = path or {}
        with self.environment(**extra), mock.patch(
            WHICH, side_effect=lambda name, *a, **k: found.get(Path(name).stem)
        ):
            return runner.invoke(app, args, input=input)


class InstallCommandTests(CliTestCase):
    def patched(self, release=None):  # noqa: ANN001, ANN201
        resolve = mock.patch(f"{INSTALL_MODULE}.resolve", return_value=release or self.release)
        install = mock.patch(f"{INSTALL_MODULE}.install")
        pick = mock.patch(f"{INSTALL_MODULE}.pick_source", return_value=self.source)
        return resolve, install, pick

    def run_install(self, args: list[str], input: str | None = None):  # noqa: A002, ANN201
        resolve, install, pick = self.patched()
        with resolve as resolve_mock, install as install_mock, pick:
            result = self.invoke(["ffmpeg", "install", *args], input=input)
        return result, resolve_mock, install_mock

    def test_asks_before_downloading_and_yes_installs(self) -> None:
        result, resolve_mock, install_mock = self.run_install([], input="y\n")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(self.question(), err(result))
        resolve_mock.assert_called_once()
        install_mock.assert_called_once()
        self.assertIs(install_mock.call_args.args[0], self.release)
        self.assertIn(f"installed ffmpeg 9.0 into {self.dest}", result.stdout)
        self.assertNotIn("Traceback", describe(result))

    def test_an_empty_answer_means_yes(self) -> None:
        result, _, install_mock = self.run_install([], input="\n")
        self.assertEqual(result.exit_code, 0, describe(result))
        install_mock.assert_called_once()

    def test_no_installs_nothing_and_exits_1(self) -> None:
        result, _, install_mock = self.run_install([], input="n\n")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(self.question(), err(result))
        self.assertIn("Nothing was installed.", result.output)
        install_mock.assert_not_called()
        self.assertNotIn("installed ffmpeg", result.output)

    def test_yes_flags_skip_the_question(self) -> None:
        for flag in ("--yes", "-y"):
            with self.subTest(flag=flag):
                result, _, install_mock = self.run_install([flag])
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertNotIn("Download ffmpeg", result.output)
                install_mock.assert_called_once()
                self.assertIn(f"installed ffmpeg 9.0 into {self.dest}", result.stdout)

    def test_the_question_says_when_the_size_is_unknown(self) -> None:
        release = self.make_release("9.0", size=None)
        resolve, install, pick = self.patched(release)
        with resolve, install, pick:
            result = self.invoke(["ffmpeg", "install"], input="n\n")
        self.assertIn(self.question(size="size unknown"), err(result))

    def test_up_to_date_says_so_without_asking(self) -> None:
        self.write_record()
        result, _, install_mock = self.run_install([])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"ffmpeg 9.0 in {self.dest} is already up to date.", result.stdout)
        self.assertNotIn("Download ffmpeg", result.output)
        install_mock.assert_not_called()

    def test_a_different_build_is_offered_as_an_update(self) -> None:
        self.write_record(self.make_release("8.1", size=1, sha="b" * 64))
        result, _, install_mock = self.run_install([], input="y\n")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(self.question(), err(result))
        install_mock.assert_called_once()

    def test_a_machine_without_a_build_points_to_the_manual_route(self) -> None:
        cases = [
            ("FreeBSD", "amd64", "sudo apt install ffmpeg"),
            ("Windows", "x86", "winget install Gyan.FFmpeg"),
            ("Darwin", "i386", "brew install ffmpeg"),
        ]
        for system, machine, route in cases:
            with self.subTest(system=system):
                with mock.patch("platform.system", return_value=system), mock.patch(
                    "platform.machine", return_value=machine
                ), mock.patch(f"{INSTALL_MODULE}.resolve") as resolve_mock, mock.patch(
                    f"{INSTALL_MODULE}.install"
                ) as install_mock:
                    result = self.invoke(["ffmpeg", "install", "--yes"])
                self.assertEqual(result.exit_code, 1, describe(result))
                self.assertIn(route, result.output)
                self.assertNotIn("Traceback", describe(result))
                resolve_mock.assert_not_called()
                install_mock.assert_not_called()

    def test_a_failure_while_looking_up_the_release_is_one_line_on_stderr(self) -> None:
        text = f"could not download ffmpeg from {HOST}: connection refused. Install it yourself: winget install Gyan.FFmpeg"
        with mock.patch(
            f"{INSTALL_MODULE}.resolve", side_effect=self.ffmpeg_install.InstallError(text)
        ), mock.patch(f"{INSTALL_MODULE}.pick_source", return_value=self.source):
            result = self.invoke(["ffmpeg", "install", "--yes"])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(text, err(result))
        self.assertNotIn(text, result.stdout)
        self.assertNotIn("Traceback", describe(result))

    def test_a_failure_while_installing_is_one_line_on_stderr(self) -> None:
        text = "ffmpeg-n9.0.zip does not match its published checksum. Install it yourself: winget install Gyan.FFmpeg"
        resolve, install, pick = self.patched()
        with resolve, install as install_mock, pick:
            install_mock.side_effect = self.ffmpeg_install.InstallError(text)
            result = self.invoke(["ffmpeg", "install", "-y"])
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(text, err(result))
        self.assertNotIn("installed ffmpeg", result.output)
        self.assertNotIn("Traceback", describe(result))


class StatusCommandTests(CliTestCase):
    def status(self, **kwargs):  # noqa: ANN003, ANN201
        with mock.patch.object(ffmpeg, "version", return_value="1.2.3"), mock.patch(
            f"{INSTALL_MODULE}.resolve", side_effect=AssertionError("looked up a release")
        ), mock.patch(
            f"{INSTALL_MODULE}.urlopen_fetch", side_effect=AssertionError("fetched")
        ):
            return self.invoke(["ffmpeg", "status"], **kwargs)

    def test_tools_from_the_environment(self) -> None:
        folder = self.root / "tools"
        folder.mkdir()
        fake_ffmpeg = make_fake_tool(folder, "ffmpeg")
        fake_ffprobe = make_fake_tool(folder, "ffprobe")
        result = self.status(JZR_FFMPEG=fake_ffmpeg, JZR_FFPROBE=fake_ffprobe)
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn(f"ffmpeg: {fake_ffmpeg} (1.2.3, from environment)", lines)
        self.assertIn(f"ffprobe: {fake_ffprobe} (1.2.3, from environment)", lines)

    def test_tools_from_path(self) -> None:
        result = self.status(path={"ffmpeg": "C:/bin/ffmpeg", "ffprobe": "C:/bin/ffprobe"})
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn("ffmpeg: C:/bin/ffmpeg (1.2.3, from PATH)", lines)
        self.assertIn("ffprobe: C:/bin/ffprobe (1.2.3, from PATH)", lines)

    def test_tools_from_the_jzr_copy_win_over_path(self) -> None:
        paths = {}
        for name in ("ffmpeg", "ffprobe"):
            path = self.ffmpeg_install.tool_path(name, {"JZR_DATA_DIR": str(self.data)})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            paths[name] = path
        self.write_record()
        result = self.status(path={"ffmpeg": "C:/bin/ffmpeg", "ffprobe": "C:/bin/ffprobe"})
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn(f"ffmpeg: {paths['ffmpeg']} (1.2.3, from jzr)", lines)
        self.assertIn(f"ffprobe: {paths['ffprobe']} (1.2.3, from jzr)", lines)
        self.assertIn(f"jzr copy: ffmpeg 9.0 in {self.dest}", lines)

    def test_nothing_found(self) -> None:
        result = self.status()
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = result.stdout.splitlines()
        self.assertIn("ffmpeg: not found", lines)
        self.assertIn("ffprobe: not found", lines)
        self.assertIn(not_found("ffmpeg"), err(result))
        self.assertIn(not_found("ffprobe"), err(result))
        self.assertIn(f"jzr copy: none (installs into {self.dest})", lines)
        self.assertNotIn("Traceback", describe(result))

    def test_one_tool_found_and_one_not(self) -> None:
        result = self.status(path={"ffprobe": "C:/bin/ffprobe"})
        lines = result.stdout.splitlines()
        self.assertIn("ffmpeg: not found", lines)
        self.assertIn("ffprobe: C:/bin/ffprobe (1.2.3, from PATH)", lines)

    def test_a_variable_pointing_at_no_file_is_reported_on_stderr(self) -> None:
        missing = str(self.root / "nope")
        result = self.status(JZR_FFMPEG=missing)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn("ffmpeg: not found", result.stdout.splitlines())
        self.assertIn(f"JZR_FFMPEG is set to {missing}, which is not a file", err(result))

    def test_the_jzr_copy_line_is_there_without_any_tool(self) -> None:
        self.write_record(version="9.0")
        result = self.status()
        self.assertIn(f"jzr copy: ffmpeg 9.0 in {self.dest}", result.stdout.splitlines())


class RemoveCommandTests(CliTestCase):
    def test_removes_the_install_folder(self) -> None:
        self.write_record()
        (self.dest / "bin").mkdir()
        (self.dest / "bin" / "ffmpeg.exe").write_bytes(b"x")
        result = self.invoke(["ffmpeg", "remove"])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"removed {self.dest}", result.stdout)
        self.assertFalse(self.dest.exists())

    def test_nothing_to_remove(self) -> None:
        result = self.invoke(["ffmpeg", "remove"])
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"jzr has no ffmpeg in {self.dest}", result.stdout)


class OfferTests(CliTestCase):
    """What `video info` and `audio info` do when no ffprobe can be found."""

    def setUp(self) -> None:
        super().setUp()
        self.clip = self.root / "clip.mp4"
        self.clip.write_bytes(b"not really media")
        self.other = self.root / "other.mp4"
        self.other.write_bytes(b"not really media either")

        self.resolve = mock.patch(f"{INSTALL_MODULE}.resolve", return_value=self.release)
        self.pick = mock.patch(f"{INSTALL_MODULE}.pick_source", return_value=self.source)
        self.installer = mock.patch(f"{INSTALL_MODULE}.install", side_effect=self.drop_tools)
        self.fetch = mock.patch(f"{INSTALL_MODULE}.urlopen_fetch", side_effect=AssertionError("fetched"))
        self.resolve_mock = self.resolve.start()
        self.pick_mock = self.pick.start()
        self.install_mock = self.installer.start()
        self.fetch_mock = self.fetch.start()
        for patcher in (self.resolve, self.pick, self.installer, self.fetch):
            self.addCleanup(patcher.stop)

    def drop_tools(self, *args: object, **kwargs: object):  # noqa: ANN201
        """Stand-in for a real install: leave ffmpeg and ffprobe in jzr's folder."""
        for name in ("ffmpeg", "ffprobe"):
            path = self.ffmpeg_install.tool_path(name, {"JZR_DATA_DIR": str(self.data)})
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")

    def no_install(self) -> None:
        """Make the stand-in install do nothing, so the tools stay missing."""
        self.install_mock.side_effect = None

    def run_info(self, group: str, *files: Path, input: str | None = None,  # noqa: A002
                 interactive: bool = False, **extra: str | None):  # noqa: ANN201
        with mock.patch(INTERACTIVE, return_value=interactive), mock.patch.object(
            ffmpeg, "probe", side_effect=media.MediaError("not a readable media file")
        ) as probe:
            result = self.invoke([group, "info", *map(str, files)], input=input, **extra)
        return result, probe

    def test_without_a_terminal_it_only_says_how_to_install(self) -> None:
        for group in ("video", "audio"):
            with self.subTest(group=group):
                result, _ = self.run_info(group, self.clip)
                self.assertEqual(result.exit_code, 1, describe(result))
                self.assertEqual(err(result).strip(), not_found("ffprobe"), describe(result))
                self.assertEqual(result.stdout.strip(), "")
                self.assertNotIn("Download ffmpeg", result.output)
                self.resolve_mock.assert_not_called()
                self.install_mock.assert_not_called()
                self.fetch_mock.assert_not_called()

    def test_on_a_terminal_yes_installs_and_the_command_carries_on(self) -> None:
        for group in ("video", "audio"):
            with self.subTest(group=group):
                shutil_remove(self.dest)
                self.install_mock.reset_mock()
                result, probe = self.run_info(group, self.clip, input="y\n", interactive=True)
                self.assertIn(self.question(), err(result), describe(result))
                self.install_mock.assert_called_once()
                self.assertIs(self.install_mock.call_args.args[0], self.release)
                # The command went on to use the tool that was just installed.
                probe.assert_called_once()
                tool = probe.call_args.args[1]
                self.assertEqual(tool.origin, "jzr")
                self.assertEqual(
                    tool.path,
                    str(self.ffmpeg_install.tool_path("ffprobe", {"JZR_DATA_DIR": str(self.data)})),
                )
                self.assertIn(f"fail {self.clip}: not a readable media file", err(result))
                self.assertNotIn("was not found", err(result))
                self.assertNotIn("Traceback", describe(result))

    def test_on_a_terminal_no_prints_the_not_found_text_and_exits_1(self) -> None:
        result, probe = self.run_info("video", self.clip, input="n\n", interactive=True)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(self.question(), err(result))
        self.assertIn(not_found("ffprobe"), err(result))
        self.install_mock.assert_not_called()
        probe.assert_not_called()
        self.assertNotIn("Traceback", describe(result))

    def test_the_offer_is_made_once_per_run(self) -> None:
        self.no_install()
        result, _ = self.run_info(
            "video", self.clip, self.other, input="y\ny\ny\n", interactive=True
        )
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).count("Download ffmpeg"), 1, describe(result))
        self.install_mock.assert_called_once()
        self.assertIn(not_found("ffprobe"), err(result))

    def test_a_variable_pointing_at_no_file_never_asks(self) -> None:
        missing = str(self.root / "no-such-ffprobe")
        result, _ = self.run_info(
            "video", self.clip, input="y\n", interactive=True, JZR_FFPROBE=missing
        )
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"JZR_FFPROBE is set to {missing}, which is not a file", err(result))
        self.assertNotIn("Download ffmpeg", result.output)
        self.install_mock.assert_not_called()
        self.resolve_mock.assert_not_called()

    def run_convert(self, *, input: str | None = None, interactive: bool = False):  # noqa: A002, ANN201
        song = self.root / "song.wav"
        song.write_bytes(b"not really media")
        with mock.patch(INTERACTIVE, return_value=interactive), mock.patch.object(
            ffmpeg, "probe", side_effect=media.MediaError("not a readable media file")
        ) as probe:
            result = self.invoke(["audio", "convert", str(song), "--to", "mp3"], input=input)
        return result, probe, song

    def test_audio_convert_without_a_terminal_only_says_how_to_install(self) -> None:
        result, probe, _ = self.run_convert()
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).strip(), not_found("ffmpeg"), describe(result))
        self.assertNotIn("Download ffmpeg", result.output)
        self.install_mock.assert_not_called()
        probe.assert_not_called()

    def test_audio_convert_on_a_terminal_installs_and_carries_on(self) -> None:
        result, probe, song = self.run_convert(input="y\n", interactive=True)
        self.assertIn(self.question(), err(result), describe(result))
        self.install_mock.assert_called_once()
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args[1].origin, "jzr")
        self.assertIn(f"fail {song}: not a readable media file", err(result))
        self.assertNotIn("was not found", err(result))
        self.assertNotIn("Traceback", describe(result))

    def test_formats_never_asks(self) -> None:
        for group in ("video", "audio"):
            with self.subTest(group=group):
                with mock.patch(INTERACTIVE, return_value=True):
                    result = self.invoke([group, "formats"], input="y\n")
                self.assertEqual(result.exit_code, 0, describe(result))
                self.assertIn("ffmpeg: not found", result.stdout)
                self.assertNotIn("Download ffmpeg", result.output)
                self.install_mock.assert_not_called()
                self.resolve_mock.assert_not_called()

    def test_ffprobe_on_path_means_no_question(self) -> None:
        with mock.patch(INTERACTIVE, return_value=True), mock.patch.object(
            ffmpeg, "probe", side_effect=media.MediaError("not a readable media file")
        ):
            result = self.invoke(
                ["video", "info", str(self.clip)], input="y\n", path={"ffprobe": "C:/bin/ffprobe"}
            )
        self.assertNotIn("Download ffmpeg", result.output)
        self.install_mock.assert_not_called()
        self.assertIn(f"fail {self.clip}: not a readable media file", err(result))


def shutil_remove(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
