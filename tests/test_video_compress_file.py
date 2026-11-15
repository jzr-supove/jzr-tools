"""Tests of compressing one file, with ffprobe and ffmpeg replaced by fakes.

The fake ``run`` writes a chosen number of bytes to the job's target, so the
size checks, the retry and the clean-up can be driven without a real encode.
"""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from jzr_tools.core import ffmpeg
from jzr_tools.core.compress import (
    CompressOptions,
    CompressResult,
    compress_file,
    retry_bitrate,
)
from jzr_tools.core.ffmpeg import Tool, Tools
from jzr_tools.core.media import (
    AudioStream,
    MediaError,
    MediaInfo,
    VideoStream,
    format_size,
)

TOOLS = Tools(Tool("ffmpeg-bin", "environment"), Tool("ffprobe-bin", "environment"))
MP4_FAMILY = "mov,mp4,m4a,3gp,3g2,mj2"
SOURCE_BYTES = 1_200_000
LIMIT = 900_000
DURATION = 10.0


def make_info(
    source: Path,
    size: int,
    *,
    duration: float | None = DURATION,
    audio: bool = True,
    video: bool = True,
) -> MediaInfo:
    stream = VideoStream(0, "h264", 1920, 1080, 30.0, "yuv420p", 0, None, None)
    sound = AudioStream(1, "aac", 2, None, 48000, 128_000, None)
    return MediaInfo(
        path=source,
        container=MP4_FAMILY,
        container_name=None,
        duration=duration,
        size=size,
        bitrate=None,
        video=stream if video else None,
        audio=(sound,) if audio else (),
        extra=(),
        tags={},
        cover_art=False,
    )


class FakeRun:
    """Stands in for ``ffmpeg.run``.

    ``outcomes`` has one entry per call: a byte count to write to the job's
    target, or an exception to raise after writing a partial file.
    """

    def __init__(self, outcomes: list[int | BaseException]) -> None:
        self.outcomes = outcomes
        self.jobs: list[ffmpeg.Job] = []
        self.kwargs: list[dict[str, object]] = []

    def __call__(self, job: ffmpeg.Job, tools: Tools, **kwargs: object) -> None:
        outcome = self.outcomes[len(self.jobs)]
        self.jobs.append(job)
        self.kwargs.append(kwargs)
        job.target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(outcome, BaseException):
            job.target.write_bytes(b"partial")
            raise outcome
        job.target.write_bytes(b"x" * outcome)


def value_after(args: tuple[str, ...], flag: str) -> str:
    return args[args.index(flag) + 1]


class CompressFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.dir = Path(self._temp.name)

    def make_source(self, name: str = "clip.mov", size: int = SOURCE_BYTES) -> Path:
        source = self.dir / name
        source.write_bytes(b"s" * size)
        return source

    def target_for(self, source: Path) -> Path:
        return source.with_name(f"{source.stem}_compressed.mp4")

    def compress(
        self,
        source: Path,
        outcomes: list[int | BaseException],
        *,
        target: Path | None = None,
        options: CompressOptions | None = None,
        info: MediaInfo | MediaError | None = None,
        overwrite: bool = False,
        dry_run: bool = False,
        on_progress: object = None,
    ) -> tuple[CompressResult, FakeRun, mock.Mock]:
        run = FakeRun(outcomes)
        if info is None:
            info = make_info(source, source.stat().st_size)
        probe = mock.Mock(side_effect=info) if isinstance(info, MediaError) else mock.Mock(return_value=info)
        with mock.patch("jzr_tools.core.ffmpeg.probe", probe), mock.patch(
            "jzr_tools.core.ffmpeg.run", run
        ):
            result = compress_file(
                source,
                target or self.target_for(source),
                tools=TOOLS,
                options=options or CompressOptions(),
                overwrite=overwrite,
                dry_run=dry_run,
                on_progress=on_progress,  # type: ignore[arg-type]
            )
        return result, run, probe

    def names(self) -> list[str]:
        return sorted(os.listdir(self.dir))

    def assertNoTemporaryFiles(self) -> None:
        leftovers = [name for name in self.names() if name.startswith("jzr-tmp-")]
        self.assertEqual(leftovers, [])


SIZE = CompressOptions(size=LIMIT)


class SkipTests(CompressFileTestCase):
    def test_a_source_that_is_its_own_target_is_skipped_without_probing(self) -> None:
        source = self.make_source("clip.mp4")
        result, run, probe = self.compress(source, [], target=source)
        self.assertIsInstance(result, CompressResult)
        self.assertEqual(result.result.status, "skipped")
        self.assertEqual(result.result.detail, "source is the target")
        probe.assert_not_called()
        self.assertEqual(run.jobs, [])
        self.assertEqual(source.read_bytes(), b"s" * SOURCE_BYTES)

    def test_an_existing_target_is_skipped_without_probing_and_left_alone(self) -> None:
        source = self.make_source()
        target = self.target_for(source)
        target.write_bytes(b"old")
        result, run, probe = self.compress(source, [])
        self.assertEqual(result.result.status, "skipped")
        self.assertEqual(result.result.detail, "target exists (use --overwrite)")
        probe.assert_not_called()
        self.assertEqual(run.jobs, [])
        self.assertEqual(target.read_bytes(), b"old")

    def test_a_skipped_file_reports_no_commands(self) -> None:
        source = self.make_source()
        self.target_for(source).write_bytes(b"old")
        result, _, _ = self.compress(source, [])
        self.assertEqual(result.commands, ())

    def test_the_result_names_the_source_and_the_target(self) -> None:
        source = self.make_source()
        result, _, _ = self.compress(source, [500_000])
        self.assertEqual(result.result.source, source)
        self.assertEqual(result.result.target, self.target_for(source))


class FailureBeforeEncodingTests(CompressFileTestCase):
    def test_an_unreadable_source_fails_with_the_reason(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(
            source, [], info=MediaError("not a readable media file")
        )
        self.assertEqual(result.result.status, "failed")
        self.assertEqual(result.result.detail, "not a readable media file")
        self.assertEqual(run.jobs, [])
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_source_without_video_fails_and_runs_nothing(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(
            source, [], info=make_info(source, SOURCE_BYTES, video=False)
        )
        self.assertEqual(result.result.status, "failed")
        self.assertEqual(result.result.detail, "no video track")
        self.assertEqual(run.jobs, [])

    def test_a_size_that_cannot_be_reached_fails_before_encoding(self) -> None:
        source = self.make_source(size=150_000)
        info = make_info(source, 150_000, duration=600.0)
        result, run, _ = self.compress(
            source, [], options=CompressOptions(size=100_000), info=info
        )
        self.assertEqual(result.result.status, "failed")
        self.assertTrue(result.result.detail.startswith("too small for a 10:00 video"))
        self.assertEqual(run.jobs, [])
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_size_without_a_duration_fails_and_runs_nothing(self) -> None:
        source = self.make_source()
        info = make_info(source, SOURCE_BYTES, duration=None)
        result, run, _ = self.compress(source, [], options=SIZE, info=info)
        self.assertEqual(result.result.status, "failed")
        self.assertIn("duration", result.result.detail)
        self.assertEqual(run.jobs, [])

    def test_a_source_already_under_the_size_is_skipped_and_nothing_runs(self) -> None:
        source = self.make_source(size=800_000)
        result, run, probe = self.compress(source, [], options=SIZE)
        self.assertEqual(result.result.status, "skipped")
        self.assertTrue(result.result.detail.startswith("already"))
        probe.assert_called_once()
        self.assertEqual(run.jobs, [])
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_source_exactly_at_the_size_is_skipped(self) -> None:
        source = self.make_source(size=LIMIT)
        result, run, _ = self.compress(source, [], options=SIZE)
        self.assertEqual(result.result.status, "skipped")
        self.assertTrue(result.result.detail.startswith("already"))
        self.assertEqual(run.jobs, [])

    def test_a_source_under_the_size_is_not_skipped_in_quality_mode(self) -> None:
        source = self.make_source(size=800_000)
        result, run, _ = self.compress(source, [500_000])
        self.assertEqual(result.result.status, "converted")
        self.assertEqual(len(run.jobs), 1)


class DryRunTests(CompressFileTestCase):
    def test_a_size_dry_run_plans_two_commands_and_writes_nothing(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(source, [], options=SIZE, dry_run=True)
        self.assertEqual(result.result.status, "planned")
        self.assertEqual(run.jobs, [])
        self.assertEqual(len(result.commands), 2)
        first, second = result.commands
        self.assertEqual(first[0], TOOLS.ffmpeg.path)
        self.assertEqual(second[0], TOOLS.ffmpeg.path)
        self.assertEqual(value_after(first, "-pass"), "1")
        self.assertEqual(value_after(second, "-pass"), "2")
        self.assertIn(os.path.abspath(source), first)
        self.assertEqual(second[-1], os.path.abspath(self.target_for(source)))
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_quality_dry_run_plans_one_command_and_writes_nothing(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(source, [], dry_run=True)
        self.assertEqual(result.result.status, "planned")
        self.assertEqual(run.jobs, [])
        self.assertEqual(len(result.commands), 1)
        self.assertIn("-crf", result.commands[0])
        self.assertEqual(result.commands[0][-1], os.path.abspath(self.target_for(source)))
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_dry_run_reports_what_would_be_done(self) -> None:
        source = self.make_source()
        result, _, _ = self.compress(source, [], dry_run=True)
        self.assertIn("video encoded to h264", result.result.detail)

    def test_a_dry_run_of_an_impossible_size_still_fails(self) -> None:
        source = self.make_source(size=150_000)
        info = make_info(source, 150_000, duration=600.0)
        result, _, _ = self.compress(
            source, [], options=CompressOptions(size=100_000), info=info, dry_run=True
        )
        self.assertEqual(result.result.status, "failed")


class QualityModeTests(CompressFileTestCase):
    def test_a_smaller_result_replaces_nothing_and_is_kept_at_the_target(self) -> None:
        source = self.make_source(size=2_000_000)
        result, run, _ = self.compress(source, [1_234_567])
        self.assertEqual(result.result.status, "converted")
        self.assertEqual(len(run.jobs), 1)
        target = self.target_for(source)
        self.assertEqual(target.read_bytes(), b"x" * 1_234_567)
        self.assertEqual(source.read_bytes(), b"s" * 2_000_000)
        self.assertEqual(self.names(), ["clip.mov", "clip_compressed.mp4"])
        self.assertNoTemporaryFiles()

    def test_the_converted_detail_includes_the_output_size(self) -> None:
        source = self.make_source(size=2_000_000)
        result, _, _ = self.compress(source, [1_234_567])
        self.assertIn(format_size(1_234_567), result.result.detail)
        self.assertEqual(len(result.commands), 1)

    def test_the_encode_is_staged_beside_the_target_under_a_temporary_name(self) -> None:
        source = self.make_source()
        target = self.target_for(source)
        _, run, _ = self.compress(source, [500_000])
        (job,) = run.jobs
        self.assertEqual(job.target.parent, target.parent)
        self.assertTrue(job.target.name.startswith("jzr-tmp-"))
        self.assertEqual(job.target.suffix, ".mp4")
        self.assertNotEqual(job.target, target)

    def test_the_job_has_one_step_reading_the_source_and_the_source_duration(self) -> None:
        source = self.make_source()
        _, run, _ = self.compress(source, [500_000])
        (job,) = run.jobs
        self.assertEqual(len(job.steps), 1)
        step = job.steps[0]
        self.assertEqual(value_after(step, "-i"), os.path.abspath(source))
        self.assertEqual(step[-1], ffmpeg.OUTPUT)
        self.assertEqual(job.duration, DURATION)

    def test_progress_is_handed_to_the_runner(self) -> None:
        source = self.make_source()
        callback = mock.Mock()
        _, run, _ = self.compress(source, [500_000], on_progress=callback)
        self.assertIs(run.kwargs[0].get("on_progress"), callback)

    def test_a_result_the_same_size_as_the_source_is_discarded(self) -> None:
        source = self.make_source(size=2_000_000)
        result, _, _ = self.compress(source, [2_000_000])
        self.assertEqual(result.result.status, "skipped")
        self.assertIn("already smaller than the result", result.result.detail)
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_result_larger_than_the_source_is_discarded(self) -> None:
        source = self.make_source(size=2_000_000)
        result, _, _ = self.compress(source, [3_000_000])
        self.assertEqual(result.result.status, "skipped")
        self.assertIn("already smaller than the result", result.result.detail)
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_discarded_result_leaves_an_existing_target_untouched(self) -> None:
        source = self.make_source(size=2_000_000)
        target = self.target_for(source)
        target.write_bytes(b"old target")
        result, _, _ = self.compress(source, [3_000_000], overwrite=True)
        self.assertEqual(result.result.status, "skipped")
        self.assertEqual(target.read_bytes(), b"old target")
        self.assertNoTemporaryFiles()

    def test_a_kept_result_replaces_an_existing_target_when_overwriting(self) -> None:
        source = self.make_source(size=2_000_000)
        target = self.target_for(source)
        target.write_bytes(b"old target")
        result, _, _ = self.compress(source, [1_000_000], overwrite=True)
        self.assertEqual(result.result.status, "converted")
        self.assertEqual(target.read_bytes(), b"x" * 1_000_000)
        self.assertNoTemporaryFiles()

    def test_a_failed_encode_is_reported_and_leaves_nothing_behind(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(source, [MediaError("Invalid data found")])
        self.assertEqual(result.result.status, "failed")
        self.assertEqual(result.result.detail, "Invalid data found")
        self.assertEqual(len(run.jobs), 1)
        self.assertEqual(self.names(), ["clip.mov"])

    def test_an_interrupt_leaves_no_temporary_file_and_is_raised_again(self) -> None:
        source = self.make_source()
        with self.assertRaises(KeyboardInterrupt):
            self.compress(source, [KeyboardInterrupt()])
        self.assertEqual(self.names(), ["clip.mov"])

    def test_an_interrupt_leaves_an_existing_target_as_it_was(self) -> None:
        source = self.make_source()
        target = self.target_for(source)
        target.write_bytes(b"old target")
        with self.assertRaises(KeyboardInterrupt):
            self.compress(source, [KeyboardInterrupt()], overwrite=True)
        self.assertEqual(target.read_bytes(), b"old target")
        self.assertNoTemporaryFiles()


class SizeModeTests(CompressFileTestCase):
    def test_a_result_under_the_size_is_kept_after_one_encode(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(source, [880_000], options=SIZE)
        self.assertEqual(result.result.status, "converted")
        self.assertEqual(len(run.jobs), 1)
        self.assertEqual(len(run.jobs[0].steps), 2)
        self.assertEqual(self.target_for(source).read_bytes(), b"x" * 880_000)
        self.assertIn(format_size(880_000), result.result.detail)
        self.assertEqual(len(result.commands), 2)
        self.assertNoTemporaryFiles()

    def test_a_result_exactly_at_the_size_is_kept(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(source, [LIMIT], options=SIZE)
        self.assertEqual(result.result.status, "converted")
        self.assertEqual(len(run.jobs), 1)

    def test_the_two_passes_read_the_source_and_the_last_writes_the_output(self) -> None:
        source = self.make_source()
        _, run, _ = self.compress(source, [880_000], options=SIZE)
        first, second = run.jobs[0].steps
        for step in (first, second):
            self.assertEqual(value_after(step, "-i"), os.path.abspath(source))
        self.assertEqual(first[-3:], ("-f", "null", "-"))
        self.assertEqual(second[-1], ffmpeg.OUTPUT)

    def test_a_result_over_the_size_is_encoded_again_at_a_lower_bitrate(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(source, [950_000, 880_000], options=SIZE)
        self.assertEqual(len(run.jobs), 2)
        first_bitrate = int(value_after(run.jobs[0].steps[1], "-b:v"))
        second_steps = run.jobs[1].steps
        expected = retry_bitrate(first_bitrate, LIMIT, 950_000)
        self.assertLess(expected, first_bitrate)
        self.assertEqual(len(second_steps), 2)
        for step in second_steps:
            self.assertEqual(value_after(step, "-b:v"), str(expected))
        self.assertEqual(result.result.status, "converted")
        self.assertEqual(self.target_for(source).read_bytes(), b"x" * 880_000)
        self.assertNoTemporaryFiles()

    def test_the_retry_changes_only_the_bitrate(self) -> None:
        source = self.make_source()
        _, run, _ = self.compress(source, [950_000, 880_000], options=SIZE)
        first, second = run.jobs[0].steps, run.jobs[1].steps
        old = value_after(first[0], "-b:v")
        new = value_after(second[0], "-b:v")
        self.assertNotEqual(old, new)
        replaced = tuple(new if arg == old else arg for arg in first[0])
        self.assertEqual(replaced, second[0])

    def test_a_result_over_the_size_twice_fails_and_is_not_kept(self) -> None:
        source = self.make_source()
        result, run, _ = self.compress(source, [950_000, 940_000], options=SIZE)
        self.assertEqual(result.result.status, "failed")
        self.assertIn("over", result.result.detail)
        self.assertEqual(len(run.jobs), 2)
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_failure_for_size_leaves_an_existing_target_byte_identical(self) -> None:
        source = self.make_source()
        target = self.target_for(source)
        target.write_bytes(b"previous result")
        result, _, _ = self.compress(source, [950_000, 940_000], options=SIZE, overwrite=True)
        self.assertEqual(result.result.status, "failed")
        self.assertEqual(target.read_bytes(), b"previous result")
        self.assertEqual(self.names(), ["clip.mov", "clip_compressed.mp4"])

    def test_a_kept_retry_replaces_an_existing_target_when_overwriting(self) -> None:
        source = self.make_source()
        target = self.target_for(source)
        target.write_bytes(b"previous result")
        result, _, _ = self.compress(source, [950_000, 880_000], options=SIZE, overwrite=True)
        self.assertEqual(result.result.status, "converted")
        self.assertEqual(target.read_bytes(), b"x" * 880_000)
        self.assertNoTemporaryFiles()

    def test_an_interrupt_during_the_retry_leaves_no_temporary_file(self) -> None:
        source = self.make_source()
        with self.assertRaises(KeyboardInterrupt):
            self.compress(source, [950_000, KeyboardInterrupt()], options=SIZE)
        self.assertEqual(self.names(), ["clip.mov"])

    def test_a_failed_retry_encode_fails_the_file_without_a_target(self) -> None:
        source = self.make_source()
        result, _, _ = self.compress(
            source, [950_000, MediaError("encoder crashed")], options=SIZE
        )
        self.assertEqual(result.result.status, "failed")
        self.assertEqual(result.result.detail, "encoder crashed")
        self.assertEqual(self.names(), ["clip.mov"])


class FileNameTests(CompressFileTestCase):
    def test_cyrillic_and_percent_names_work(self) -> None:
        for name in ("видео 100%.mov", "%s %d.mkv", "-dash.mov"):
            with self.subTest(name=name):
                source = self.make_source(name)
                result, run, _ = self.compress(source, [500_000])
                self.assertEqual(result.result.status, "converted")
                target = self.dir / f"{Path(name).stem}_compressed.mp4"
                self.assertEqual(result.result.target, target)
                self.assertTrue(target.is_file())
                self.assertEqual(value_after(run.jobs[0].steps[0], "-i"), os.path.abspath(source))
                self.assertNoTemporaryFiles()

    def test_a_target_in_a_new_folder_is_created(self) -> None:
        source = self.make_source()
        target = self.dir / "out" / "deeper" / "clip_compressed.mp4"
        result, _, _ = self.compress(source, [500_000], target=target)
        self.assertEqual(result.result.status, "converted")
        self.assertTrue(target.is_file())
        self.assertEqual(os.listdir(target.parent), ["clip_compressed.mp4"])


if __name__ == "__main__":
    unittest.main()
