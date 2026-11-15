"""CLI behaviour of `jzr video compress` against stand-in tools.

The stand-in ffprobe answers every call with the same JSON, and the stand-in
ffmpeg writes whatever output path it is given (``output_bytes`` chooses how
many bytes), so each test describes the source it needs and checks the
commands that were started.
"""

from __future__ import annotations

import json
from pathlib import Path
import shlex
import unittest

from test_audio_convert_cli import source_of
from test_media_cli import describe, err, run_without_tools
from test_video_convert_cli import FakeToolsCase

MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
BIG = 30_000_000
VIDEO_EXTENSIONS = "mp4 m4v mov mkv webm avi wmv flv mpg mpeg ts mts m2ts 3gp ogv".split()


def compress_probe(
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    duration: float = 60.0,
    audio: str | None = "aac",
    size: int = BIG,
) -> str:
    streams: list[dict[str, object]] = [
        {
            "index": 0, "codec_type": "video", "codec_name": "h264",
            "width": width, "height": height, "avg_frame_rate": f"{fps}/1", "pix_fmt": "yuv420p",
        }
    ]
    if audio:
        streams.append(
            {
                "index": 1, "codec_type": "audio", "codec_name": audio,
                "channels": 2, "channel_layout": "stereo", "sample_rate": "44100",
            }
        )
    return json.dumps(
        {
            "streams": streams,
            "format": {"format_name": MP4_FAMILY, "duration": str(duration), "size": str(size)},
        }
    )


class CompressCase(FakeToolsCase):
    PROBE = compress_probe()

    def touch(self, *names: str, size: int = 1000) -> list[Path]:
        paths = []
        for name in names:
            path = self.dir / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\0" * size)
            paths.append(path)
        return paths

    def touch_big(self, *names: str) -> list[Path]:
        return self.touch(*names, size=BIG)

    def command_lines(self, result: object, source: Path) -> list[list[str]]:
        self.assertEqual(result.exit_code, 0, describe(result))  # type: ignore[attr-defined]
        return [
            shlex.split(line)
            for line in result.stdout.splitlines()  # type: ignore[attr-defined]
            if str(source) in line and " -i " in line
        ]

    def dry_sources(self, result: object) -> set[str]:
        sources = set()
        for line in result.stdout.splitlines():  # type: ignore[attr-defined]
            words = shlex.split(line)
            if "-i" in words:
                sources.add(Path(words[words.index("-i") + 1]).name)
        return sources

    @staticmethod
    def value_of(words: list[str], flag: str) -> str:
        return words[words.index(flag) + 1]


class UsageErrorTests(CompressCase):
    def assert_usage_error(self, result: object, *needles: str) -> None:
        self.assertEqual(result.exit_code, 2, describe(result))
        self.assertNotIn("Traceback", describe(result))
        self.assertNotIn("No such command", err(result))
        for needle in needles:
            self.assertIn(needle, err(result), describe(result))
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(self.ffprobe_calls(), [])

    def test_size_and_for_cannot_be_combined(self) -> None:
        (clip,) = self.touch("clip.mov")
        self.assert_usage_error(self.invoke("compress", clip, "--size", "5MB", "--for", "email"))

    def test_quality_cannot_be_combined_with_a_size_or_a_named_size(self) -> None:
        (clip,) = self.touch("clip.mov")
        for args in (["--quality", "high", "--size", "5MB"], ["-q", "small", "--for", "discord"]):
            with self.subTest(args=args):
                self.assert_usage_error(self.invoke("compress", clip, *args))

    def test_a_size_that_is_not_a_size_is_refused(self) -> None:
        (clip,) = self.touch("clip.mov")
        for value in ("lots", "12XB", "MB"):
            with self.subTest(value=value):
                self.assert_usage_error(self.invoke("compress", clip, "--size", value), "--size")

    def test_an_unknown_named_size_or_quality_is_refused(self) -> None:
        (clip,) = self.touch("clip.mov")
        self.assert_usage_error(self.invoke("compress", clip, "--for", "telegram"))
        self.assert_usage_error(self.invoke("compress", clip, "--quality", "best"))

    def test_the_output_must_be_an_mp4(self) -> None:
        (clip,) = self.touch("clip.mov")
        self.assert_usage_error(self.invoke("compress", clip, "-o", self.dir / "x.mkv"))
        self.assertNotIn("x.mkv", self.names())

    def test_output_takes_one_source_only(self) -> None:
        a, b = self.touch("a.mov", "b.mov")
        self.assert_usage_error(self.invoke("compress", a, b, "-o", self.dir / "x.mp4"))

    def test_usage_errors_do_not_need_ffmpeg(self) -> None:
        (clip,) = self.touch("clip.mov")
        cases = [
            ["--size", "5MB", "--for", "email"],
            ["--quality", "high", "--size", "5MB"],
            ["--quality", "high", "--for", "discord"],
            ["--size", "lots"],
            ["-o", str(self.dir / "x.mkv")],
        ]
        for args in cases:
            with self.subTest(args=args):
                result = run_without_tools(["video", "compress", str(clip), *args])
                self.assertEqual(result.exit_code, 2, describe(result))
                self.assertNotIn("No such command", err(result))
                self.assertNotIn("was not found", err(result))


class DryRunTests(CompressCase):
    def test_a_size_plans_two_passes_in_order(self) -> None:
        (clip,) = self.touch_big("clip.mov")
        result = self.invoke("compress", clip, "--size", "5MB", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = self.command_lines(result, clip)
        self.assertEqual(len(lines), 2, describe(result))
        first, second = lines
        self.assertEqual(self.value_of(first, "-pass"), "1")
        self.assertEqual(self.value_of(second, "-pass"), "2")
        self.assertEqual(self.value_of(first, "-b:v"), self.value_of(second, "-b:v"))
        self.assertEqual(second[-1], str(self.dir / "clip_compressed.mp4"))
        self.assertEqual(self.names(), {"clip.mov"})
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_without_a_size_one_command_uses_a_constant_quality(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("compress", clip, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        (words,) = self.command_lines(result, clip)
        self.assertEqual(self.value_of(words, "-crf"), "23")
        self.assertNotIn("-pass", words)
        self.assertNotIn("-b:v", words)
        self.assertEqual(self.names(), {"clip.mov"})
        self.assertEqual(self.ffmpeg_calls(), [])

    def test_quality_levels_choose_the_crf(self) -> None:
        (clip,) = self.touch("clip.mov")
        for level, crf in (("high", "18"), ("balanced", "23"), ("small", "28")):
            with self.subTest(level=level):
                result = self.invoke("compress", clip, "-q", level, "--dry-run")
                self.assertEqual(result.exit_code, 0, describe(result))
                (words,) = self.command_lines(result, clip)
                self.assertEqual(self.value_of(words, "-crf"), crf)

    def test_for_discord_plans_twenty_million_bytes(self) -> None:
        self.install(compress_probe(duration=100.0))
        (clip,) = self.touch_big("clip.mov")
        result = self.invoke("compress", clip, "--for", "discord", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        lines = self.command_lines(result, clip)
        self.assertEqual(len(lines), 2, describe(result))
        total = 20_000_000 * 8 * 0.96 / 100.0
        audio = 128_000
        for words in lines:
            self.assertAlmostEqual(int(self.value_of(words, "-b:v")), total - audio, delta=3)

    def test_the_target_is_named_after_the_source_with_compressed_before_mp4(self) -> None:
        (clip,) = self.touch("My Clip.mov")
        result = self.invoke("compress", clip, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        (words,) = self.command_lines(result, clip)
        self.assertEqual(words[-1], str(self.dir / "My Clip_compressed.mp4"))

    def test_strip_removes_the_metadata_and_the_default_keeps_it(self) -> None:
        (clip,) = self.touch("clip.mov")
        kept = self.invoke("compress", clip, "--dry-run")
        stripped = self.invoke("compress", clip, "--strip", "--dry-run")
        (kept_words,) = self.command_lines(kept, clip)
        (strip_words,) = self.command_lines(stripped, clip)
        self.assertNotIn("-map_metadata", kept_words)
        self.assertEqual(self.value_of(strip_words, "-map_metadata"), "-1")

    def test_no_audio_leaves_the_audio_track_out(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("compress", clip, "--no-audio", "--dry-run")
        (words,) = self.command_lines(result, clip)
        self.assertNotIn("0:1", words)
        self.assertNotIn("-c:a", words)
        with_audio = self.invoke("compress", clip, "--dry-run")
        (audio_words,) = self.command_lines(with_audio, clip)
        self.assertIn("0:1", audio_words)

    def test_no_audio_with_a_size_drops_it_from_the_second_pass(self) -> None:
        (clip,) = self.touch_big("clip.mov")
        result = self.invoke("compress", clip, "--size", "5MB", "--no-audio", "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        for words in self.command_lines(result, clip):
            self.assertNotIn("0:1", words)
            self.assertNotIn("-c:a", words)


class ScanTests(CompressCase):
    def test_files_that_are_already_compressed_are_left_out(self) -> None:
        self.touch("clip.mov", "other_compressed.mp4", "notes.txt", "song.wav")
        result = self.invoke("compress", "--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.converted_sources(), {"clip.mov"})

    def test_every_video_extension_is_picked_up(self) -> None:
        names = [f"{i}-v.{ext}" for i, ext in enumerate(VIDEO_EXTENSIONS)]
        self.touch(*names, "song.mp3", "cover.jpg")
        result = self.invoke("compress", "--dir", self.dir, "--dry-run")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertEqual(self.dry_sources(result), set(names))

    def test_pattern_and_recursive_select_files(self) -> None:
        self.touch("take1.mov", "other.mov", "sub/take2.mov")
        self.invoke("compress", "--dir", self.dir, "--pattern", r"^take")
        self.assertEqual(self.converted_sources(), {"take1.mov"})
        self.invoke("compress", "--dir", self.dir, "--pattern", "take", "--recursive", "--overwrite")
        self.assertIn("take2.mov", self.converted_sources())

    def test_nothing_to_compress_says_so_and_exits_1(self) -> None:
        self.touch("only_compressed.mp4", "song.wav", "notes.txt")
        result = self.invoke("compress", "--dir", self.dir)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("No matching files.", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])


class CompressTests(CompressCase):
    def test_success_prints_source_and_target_and_writes_the_target(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("compress", clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        target = self.dir / "clip_compressed.mp4"
        self.assertTrue(
            any(line.startswith(f"{clip} -> {target} (") for line in result.stdout.splitlines()),
            describe(result),
        )
        self.assertIn("ffmpeg: 1 converted", result.stdout.splitlines())
        self.assertTrue(target.is_file())
        (call,) = self.ffmpeg_calls()
        self.assertEqual(source_of(call), str(clip))
        self.assertTrue(Path(call[-1]).name.startswith("jzr-tmp-"))
        self.assertEqual(Path(call[-1]).parent, self.dir)

    def test_output_names_the_one_target(self) -> None:
        (clip,) = self.touch("clip.mov")
        target = self.dir / "made" / "small.mp4"
        result = self.invoke("compress", clip, "-o", target)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue(target.is_file())

    def test_outdir_collects_the_outputs_and_mirrors_subfolders(self) -> None:
        self.touch("a.mov", "sub/b.mov")
        out = self.dir / "out"
        result = self.invoke("compress", "--dir", self.dir, "--recursive", "--outdir", out)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertTrue((out / "a_compressed.mp4").is_file())
        self.assertTrue((out / "sub" / "b_compressed.mp4").is_file())
        self.assertFalse((self.dir / "a_compressed.mp4").exists())

    def test_two_sources_never_share_an_output(self) -> None:
        self.touch("clip.mov", "clip.mkv")
        result = self.invoke("compress", "--dir", self.dir)
        self.assertEqual(result.exit_code, 0, describe(result))
        made = {n for n in self.names() if n.endswith(".mp4")}
        self.assertEqual(len(made), 2, made)
        self.assertIn("clip_compressed.mp4", made)

    def test_an_existing_target_is_skipped_without_overwrite(self) -> None:
        (clip,) = self.touch("clip.mov")
        (existing,) = self.touch("clip_compressed.mp4", size=77)
        result = self.invoke("compress", clip)
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: target exists (use --overwrite)", err(result))
        self.assertEqual(existing.stat().st_size, 77)
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertIn("ffmpeg: 1 skipped", result.stdout)

    def test_overwrite_replaces_the_existing_target(self) -> None:
        (clip,) = self.touch("clip.mov")
        (existing,) = self.touch("clip_compressed.mp4", size=77)
        result = self.invoke("compress", clip, "--overwrite")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertNotEqual(existing.stat().st_size, 77)

    def test_one_bad_file_fails_alone_and_the_batch_continues(self) -> None:
        self.install(ffprobe={"fail_marker": "broken", "fail_code": 1})
        good, bad = self.touch("a.mov", "broken.mov")
        result = self.invoke("compress", bad, good)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertEqual(err(result).count("fail "), 1, describe(result))
        self.assertIn(f"fail {bad}:", err(result))
        self.assertIn("ffmpeg: 1 converted, 1 failed", result.stdout.splitlines())
        self.assertTrue((self.dir / "a_compressed.mp4").is_file())
        self.assertFalse((self.dir / "broken_compressed.mp4").exists())

    def test_an_ffmpeg_failure_leaves_nothing_behind(self) -> None:
        self.install(ffmpeg={"code": 1, "stderr": "Error while encoding: disk is full\n"})
        (clip,) = self.touch("clip.mov")
        result = self.invoke("compress", clip)
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("disk is full", err(result))
        self.assertEqual(self.names(), {"clip.mov"})

    def test_no_audio_and_strip_reach_the_encode(self) -> None:
        (clip,) = self.touch("clip.mov")
        result = self.invoke("compress", clip, "--no-audio", "--strip")
        self.assertEqual(result.exit_code, 0, describe(result))
        (call,) = self.ffmpeg_calls()
        self.assertEqual(self.value_of(call, "-map_metadata"), "-1")
        self.assertNotIn("0:1", call)
        self.assertNotIn("-c:a", call)

    def test_a_size_runs_two_encodes_and_keeps_a_result_within_it(self) -> None:
        self.install(ffmpeg={"output_bytes": "x" * 4_000_000})
        (clip,) = self.touch_big("clip.mov")
        result = self.invoke("compress", clip, "--size", "5MB")
        self.assertEqual(result.exit_code, 0, describe(result))
        calls = self.ffmpeg_calls()
        self.assertEqual([self.value_of(c, "-pass") for c in calls], ["1", "2"])
        self.assertEqual((self.dir / "clip_compressed.mp4").stat().st_size, 4_000_000)
        self.assertEqual({n for n in self.names() if n.startswith("jzr-tmp-")}, set())

    def test_a_result_over_the_size_is_never_kept(self) -> None:
        self.install(ffmpeg={"output_bytes": "x" * 5_500_000})
        (clip,) = self.touch_big("clip.mov")
        result = self.invoke("compress", clip, "--size", "5MB")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn(f"fail {clip}:", err(result))
        self.assertIn("over", err(result))
        self.assertFalse((self.dir / "clip_compressed.mp4").exists())
        self.assertEqual({n for n in self.names() if n.startswith("jzr-tmp-")}, set())

    def test_an_impossible_size_fails_before_any_encode(self) -> None:
        self.install(compress_probe(duration=600.0))
        (clip,) = self.touch_big("clip.mov")
        result = self.invoke("compress", clip, "--size", "100KB")
        self.assertEqual(result.exit_code, 1, describe(result))
        self.assertIn("too small for a", err(result))
        self.assertIn("10:00", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])
        self.assertEqual(self.names(), {"clip.mov"})

    def test_a_source_already_within_the_size_is_skipped(self) -> None:
        self.install(compress_probe(size=1000))
        (clip,) = self.touch("clip.mov", size=1000)
        result = self.invoke("compress", clip, "--size", "5MB")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertIn(f"skip {clip}: already", err(result))
        self.assertEqual(self.ffmpeg_calls(), [])


class HelpTests(CompressCase):
    def test_the_video_group_lists_compress(self) -> None:
        result = self.invoke("--help")
        self.assertEqual(result.exit_code, 0, describe(result))
        self.assertRegex(result.stdout, r"(?m)^  compress\s+\S")

    def test_compress_help_lists_the_options(self) -> None:
        result = self.invoke("compress", "--help")
        self.assertEqual(result.exit_code, 0, describe(result))
        for option in (
            "--dir", "--pattern", "--recursive", "--output", "--outdir", "--quality",
            "--size", "--for", "--resize", "--no-audio", "--strip", "--overwrite", "--dry-run",
        ):
            self.assertIn(option, result.stdout)


if __name__ == "__main__":
    unittest.main()
