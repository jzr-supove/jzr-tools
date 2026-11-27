"""Tests of the private /tmp against the real bubblewrap, with fake `claude` scripts.

Each fake claude writes a marker file to the literal path /tmp/<unique name> and records what it can see. The
tests then look on the machine's /tmp and in the sandbox's own `tmp` folder. They are skipped, with the reason
printed, where bubblewrap is missing or cannot start (no user namespaces, for instance).

The fake claude, its record folder and the canned transcript live outside /tmp (in /var/tmp or the home
folder's cache): inside the run /tmp is somebody else's, so a script there would not be reachable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import process_support as ps  # noqa: E402
import stream_fixtures as fx  # noqa: E402
import test_runner as tr  # noqa: E402
from jzr_bench.arms import ArmSetup, Auth, prepare_arm  # noqa: E402
from jzr_bench.plan import PlannedRun  # noqa: E402
from jzr_bench.runner import run_one  # noqa: E402

BWRAP = shutil.which("bwrap")


def bwrap_problem() -> str | None:
    """Why the real bubblewrap cannot be used here, or None when it can."""
    if BWRAP is None:
        return "bwrap (bubblewrap) is not on PATH"
    try:
        done = subprocess.run(
            [BWRAP, "--dev-bind", "/", "/", "true"],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"bwrap cannot be started: {error}"
    if done.returncode != 0:
        return f"`bwrap --dev-bind / / true` fails here: {done.stderr.strip()[:200]}"
    return None


def folder_outside_tmp() -> Path | None:
    for candidate in (Path("/var/tmp"), Path.home() / ".cache"):
        if candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK):
            return candidate.resolve()
    return None


BWRAP_PROBLEM = bwrap_problem()
OUTSIDE_TMP = folder_outside_tmp()
SKIP_REASON = (
    f"private /tmp needs bubblewrap: {BWRAP_PROBLEM}"
    if BWRAP_PROBLEM
    else None
    if OUTSIDE_TMP is not None
    else "needs a writable folder outside /tmp (/var/tmp or ~/.cache) for the fake claude"
)
needs_bwrap = unittest.skipIf(SKIP_REASON is not None, SKIP_REASON or "")

PROMPT_OF_TASK = "do the probe thing"  # the prompt of RunnerCase.task()


class RealBwrapCase(tr.RunnerCase):
    """RunnerCase with its folders outside /tmp, so a fake claude can still be reached once /tmp is replaced."""

    def setUp(self) -> None:
        with mock.patch.object(tempfile, "tempdir", str(OUTSIDE_TMP)):
            super().setUp()
        assert not str(self.tmp).startswith("/tmp/"), self.tmp

    def new_marker(self, label: str = "marker") -> str:
        """A file name nothing else uses; whatever a failing test leaves at /tmp/<name> is removed afterwards."""
        name = f"jzr-private-tmp-test-{label}-{uuid.uuid4().hex}"
        self.addCleanup(self.remove_from_machine_tmp, name)
        return name

    @staticmethod
    def remove_from_machine_tmp(name: str) -> None:
        path = Path("/tmp") / name
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)

    def sandbox_root(self, parent: Path) -> Path:
        root = Path(tempfile.mkdtemp(prefix="w", dir=parent))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def private_setup(self, parent: Path, arm: str = "baseline", *, pip_cache: Path | None = None) -> ArmSetup:
        assert BWRAP is not None
        extra: dict[str, Any] = {"pip_cache": pip_cache} if pip_cache is not None else {}  # a round 4 parameter
        return prepare_arm(
            arm,
            self.sandbox_root(parent),
            self.source,
            Auth("CLAUDE_CODE_OAUTH_TOKEN", "tok-123"),
            model="sonnet",
            max_turns=12,
            system_path=self.system_path,
            bwrap=BWRAP,
            **extra,
        )

    def run_private(
        self, claude_bin: str, parent: Path, *, timeout_s: float = 30, pip_cache: Path | None = None
    ) -> tuple[dict[str, Any], ArmSetup]:
        """One run_one in a fresh sandbox under `parent`, wrapped by the real bwrap (with the pip cache if given)."""
        setup = self.private_setup(parent, pip_cache=pip_cache)
        task = self.task()
        planned = PlannedRun(run_id="r01-probe-baseline", round=1, task=task.name, arm="baseline")
        record = run_one(
            planned,
            task,
            setup,
            claude_bin=claude_bin,
            fixture_dir=self.fixture_dir,
            transcript_path=self.transcript,
            timeout_s=timeout_s,
        )
        return record, setup

    def probing_claude(self, marker: str) -> str:
        """Lists /tmp, makes a temp file the way a user's shell does, writes the marker, then answers."""
        answer = self.canned(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        return self.fake_claude(
            '/usr/bin/ls -A /tmp > "$REC/tmp_listing.txt"\n'
            '/usr/bin/mktemp > "$REC/mktemp.txt"\n'
            f"echo probe > '/tmp/{marker}'\n"
            'echo written-by-the-run > "$HOME/home_probe.txt"\n'
            "echo written-by-the-run > ./work_probe.txt\n"
            f"/usr/bin/cat '{answer}'\nexit 0"
        )

    def listing(self) -> list[str]:
        return self.recorded("tmp_listing.txt").split()


if TYPE_CHECKING:
    _Base = RealBwrapCase
else:
    _Base = object


class PrivateTmpBehaviour(_Base):
    """The tests every sandbox location must pass (a mixin: the classes below add the TestCase). `parent` is
    where the sandbox folder is made."""

    parent: Path

    def test_the_marker_is_not_left_on_the_machine_and_is_in_the_sandbox_tmp(self) -> None:
        marker = self.new_marker()
        record, setup = self.run_private(self.probing_claude(marker), self.parent)
        self.assertIs(record["passed"], True)  # the run really happened (a bwrap failure would give no result)
        self.assertFalse((Path("/tmp") / marker).exists(), "the run wrote to the machine's /tmp")
        self.assertEqual((setup.sandbox.tmp / marker).read_text(), "probe\n")

    def test_a_file_already_in_the_machines_tmp_is_not_seen_by_the_run(self) -> None:
        left_by_someone = self.new_marker("left-behind")
        (Path("/tmp") / left_by_someone).write_text("an earlier run left this\n")
        marker = self.new_marker()
        record, _ = self.run_private(self.probing_claude(marker), self.parent)
        self.assertIs(record["passed"], True)
        self.assertTrue((Path("/tmp") / left_by_someone).exists(), "the test's own file must still be there")
        self.assertNotIn(left_by_someone, self.listing())

    def test_a_second_run_does_not_see_what_the_first_left_in_tmp(self) -> None:
        first, second = self.new_marker("first"), self.new_marker("second")
        record, first_setup = self.run_private(self.probing_claude(first), self.parent)
        self.assertIs(record["passed"], True)
        self.assertEqual((first_setup.sandbox.tmp / first).read_text(), "probe\n")

        record, second_setup = self.run_private(self.probing_claude(second), self.parent)
        self.assertIs(record["passed"], True)
        self.assertNotIn(first, self.listing(), "the second run saw the first run's file")
        self.assertTrue((second_setup.sandbox.tmp / second).exists())
        self.assertFalse((second_setup.sandbox.tmp / first).exists())
        self.assertFalse((first_setup.sandbox.tmp / second).exists())

    def test_the_working_folder_is_the_sandbox_work_folder_with_the_fixtures(self) -> None:
        record, setup = self.run_private(self.probing_claude(self.new_marker()), self.parent)
        self.assertIs(record["passed"], True)
        self.assertEqual(os.path.realpath(self.recorded("cwd.txt").strip()), str(setup.sandbox.work.resolve()))
        self.assertEqual(self.recorded("ls.txt").split(), ["data.txt"])
        self.assertEqual((setup.sandbox.work / "data.txt").read_text(), "fixture\n")

    def test_what_the_run_writes_to_its_work_folder_and_home_reaches_the_sandbox(self) -> None:
        record, setup = self.run_private(self.probing_claude(self.new_marker()), self.parent)
        self.assertIs(record["passed"], True)
        self.assertEqual((setup.sandbox.work / "work_probe.txt").read_text(), "written-by-the-run\n")
        self.assertEqual((setup.sandbox.home / "home_probe.txt").read_text(), "written-by-the-run\n")

    def test_the_child_has_no_tmpdir_and_mktemp_uses_the_private_tmp(self) -> None:
        record, setup = self.run_private(self.probing_claude(self.new_marker()), self.parent)
        self.assertIs(record["passed"], True)
        self.assertNotIn("TMPDIR", self.child_env())
        made = Path(self.recorded("mktemp.txt").strip())
        self.assertEqual(made.parent, Path("/tmp"))
        self.assertTrue(made.name.startswith("tmp."), made)
        self.assertTrue((setup.sandbox.tmp / made.name).exists(), "mktemp's file is in the sandbox tmp folder")
        self.assertFalse(made.exists(), "mktemp's file must not be on the machine's /tmp")

    def test_the_child_env_is_the_arm_env_and_the_wrapper_is_not_in_the_command_line(self) -> None:
        record, setup = self.run_private(self.probing_claude(self.new_marker()), self.parent)
        self.assertIs(record["passed"], True)
        self.assertEqual(set(self.child_env()) - tr.SHELL_EXTRAS, set(setup.env))
        self.assertEqual(self.recorded("argv.txt").splitlines(), ["-p", PROMPT_OF_TASK, *setup.claude_args])


@needs_bwrap
class PrivateTmpUnderVarTmpTest(PrivateTmpBehaviour, RealBwrapCase):
    """A sandbox outside /tmp: only the sandbox's tmp folder is mounted."""

    parent = OUTSIDE_TMP  # type: ignore[assignment]


@needs_bwrap
class PrivateTmpNestedInTmpTest(PrivateTmpBehaviour, RealBwrapCase):
    """A sandbox inside /tmp itself: its folder is hidden by the mount, so it is mounted again at its own path."""

    parent = Path("/tmp")

    def test_the_sandbox_is_inside_tmp_for_this_class(self) -> None:
        setup = self.private_setup(self.parent)
        self.assertTrue(str(setup.sandbox.root).startswith("/tmp/"))


@needs_bwrap
class PrivateTmpTimeoutTest(RealBwrapCase):
    def test_the_timeout_still_kills_bwrap_and_everything_it_started(self) -> None:
        # The run has its own pid namespace now, so a pid it notes down means nothing out here: the sleeper is
        # found on the machine by its (unique) argument instead.
        token = ps.unique_sleep()
        self.addCleanup(ps.kill_with_token, token)
        answer = self.canned(fx.init_for_arm("baseline"))
        body = f"/usr/bin/cat '{answer}'\n/usr/bin/sleep {token} &\nwait"
        started = time.monotonic()
        record, _ = self.run_private(self.fake_claude(body), OUTSIDE_TMP, timeout_s=2)
        self.assertLess(time.monotonic() - started, 30, "run_one must return soon after the timeout")
        self.assertIs(record["timed_out"], True)
        self.assertTrue(
            ps.wait_until(lambda: not ps.host_pids_with(token), seconds=5),
            "the agent's background process survived the timeout",
        )


@tr.needs_the_real_source
@needs_bwrap
class PrivateTmpRunAllTest(RealBwrapCase, tr.RunAllCase):
    """run_all as a user starts it: private /tmp is the default, nothing is passed to turn it on."""

    def new_parent(self, under: Path) -> Path:
        parent = Path(tempfile.mkdtemp(prefix="p", dir=under))
        self.addCleanup(shutil.rmtree, parent, ignore_errors=True)
        return parent

    def run_default(self, marker: str, *argv: str, parent: Path | None = None, **kwargs: Any) -> Any:
        answer = self.canned(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        # run_all also starts `claude --version` outside the wrapper; only a real run (-p) leaves the marker
        claude = self.fake_claude(
            'if [ "$1" != "-p" ]; then echo "9.9.9 (fake claude)"; exit 0; fi\n'
            f"/usr/bin/ls -A /tmp > \"$REC/tmp_listing.txt\"\necho probe > '/tmp/{marker}'\n"
            f"/usr/bin/cat '{answer}'\nexit 0"
        )
        extra = ["--sandbox-dir", str(parent)] if parent else []
        return self.run_all_with("--keep-sandboxes", *extra, *argv, claude=claude, sandbox_dir=False, **kwargs)

    def test_default_run_keeps_the_marker_out_of_the_machines_tmp(self) -> None:
        marker = self.new_marker()
        code, output, out = self.run_default(marker, parent=self.new_parent(OUTSIDE_TMP or Path("/var/tmp")))
        self.assertEqual(code, 0, output)
        [record] = self.records(out)
        self.assertIs(record["passed"], True)
        self.assertIs(self.meta(out)["private_tmp"], True)
        self.assertFalse((Path("/tmp") / marker).exists(), "the run wrote to the machine's /tmp")
        self.assertEqual((Path(record["sandbox"]) / "tmp" / marker).read_text(), "probe\n")
        self.assertNotIn("TMPDIR", self.child_env())

    def test_default_run_with_the_sandbox_dir_inside_tmp(self) -> None:
        marker = self.new_marker()
        code, output, out = self.run_default(marker, parent=self.new_parent(Path("/tmp")))
        self.assertEqual(code, 0, output)
        [record] = self.records(out)
        self.assertTrue(record["sandbox"].startswith("/tmp/"), record["sandbox"])
        self.assertIs(record["passed"], True)
        self.assertFalse((Path("/tmp") / marker).exists(), "the run wrote to the machine's /tmp")
        self.assertEqual((Path(record["sandbox"]) / "tmp" / marker).read_text(), "probe\n")

    def test_two_runs_in_one_invocation_do_not_share_tmp(self) -> None:
        marker = self.new_marker()
        code, output, out = self.run_default(marker, "--arms", "baseline,skill", parent=self.new_parent(Path("/tmp")))
        self.assertEqual(code, 0, output)
        first, second = self.records(out)
        for record in (first, second):
            self.assertEqual((Path(record["sandbox"]) / "tmp" / marker).read_text(), "probe\n")
        self.assertNotEqual(first["sandbox"], second["sandbox"])
        self.assertNotIn(marker, self.listing(), "the second run saw the first run's file")

    def test_shared_tmp_is_how_a_run_leaves_files_on_the_machine(self) -> None:
        # the contrast: with --shared-tmp the very same fake claude does write to the machine's /tmp
        marker = self.new_marker()
        code, output, out = self.run_default(
            marker, "--shared-tmp", parent=self.new_parent(OUTSIDE_TMP or Path("/var/tmp"))
        )
        self.assertEqual(code, 0, output)
        self.assertIs(self.meta(out)["private_tmp"], False)
        self.assertEqual((Path("/tmp") / marker).read_text(), "probe\n")


if __name__ == "__main__":
    unittest.main()
