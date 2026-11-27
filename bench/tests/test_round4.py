"""Acceptance tests for round 4 of the harness (spec: "Round 4 of the harness: what the first bare run showed").

1. Nothing outlives a run: the private /tmp wrapper gives a run its own process-id namespace.
2. Downloads are cached across runs: one pip cache, mounted at ~/.cache/pip, filled before the first bare run.
3. A timed-out run still counts what it spent: partial usage from the stream, an estimated cost, the ceiling.
4. `report` with several folders combines per machine and task.

The real-bubblewrap tests are skipped, with the reason, where bubblewrap cannot be used. Their fake claude scripts
and cache folders live outside /tmp (see test_private_tmp), and every process they start is looked for by a
unique `sleep` argument on the machine and killed again when a test ends.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import process_support as ps  # noqa: E402
import stream_fixtures as fx  # noqa: E402
import test_arms as ta  # noqa: E402
import test_private_tmp as tpt  # noqa: E402
import test_report_cli as trc  # noqa: E402
import test_report_round3 as r3  # noqa: E402
import test_runner as tr  # noqa: E402
import usage_fixtures as uf  # noqa: E402
from jzr_bench import arms  # noqa: E402
from jzr_bench import runner as runner_module  # noqa: E402
from jzr_bench.arms import ARMS, Sandbox, prepare_arm  # noqa: E402
from jzr_bench.cli import main  # noqa: E402
from jzr_bench.report import overall, render_markdown, summarize  # noqa: E402
from jzr_bench.runner import REPO, combine_records, combined_summary, write_summary  # noqa: E402
from jzr_bench.stream import StreamMetrics, parse_stream  # noqa: E402

PARTIAL_KEYS = {
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "cacheCreationEphemeral5mInputTokens",
    "cacheCreationEphemeral1hInputTokens",
}
PLAIN_PARTIAL_KEYS = PARTIAL_KEYS - {"cacheCreationEphemeral5mInputTokens", "cacheCreationEphemeral1hInputTokens"}
MODEL = uf.MODEL  # "claude-sonnet-5-5", the model of the fixtures' init event and assistant events


def partial_entry(tokens: dict[str, int]) -> dict[str, int]:
    """What `partial_model_usage` holds for a model: a `modelUsage` entry with only the token counts."""
    full = uf.entry(tokens, None)
    return {key: value for key, value in full.items() if key in PARTIAL_KEYS}


def assistant_for(message_id: str, tokens: dict[str, int], **kwargs: Any) -> dict[str, Any]:
    """An assistant event whose usage is `tokens` (kinds as in usage_fixtures, with a 1 hour cache write)."""
    return fx.assistant_usage_event(
        message_id,
        input_tokens=tokens["input"],
        cache_creation=tokens["cache_write_1h"],
        cache_read=tokens["cache_read"],
        output_tokens=tokens["output"],
        ephemeral=(tokens["cache_write_5m"], tokens["cache_write_1h"]),
        **kwargs,
    )


# -- 1. the wrapper -------------------------------------------------------------------------------


def words_at(wrapper: list[str], *words: str) -> list[int]:
    """The positions where the words occur next to each other, in this order."""
    return [i for i in range(len(wrapper) - len(words) + 1) if wrapper[i : i + len(words)] == list(words)]


class PidNamespaceWrapperTest(unittest.TestCase):
    EXTRA = ["--overlay-src", "/usr/bin", "--overlay", "/s/u", "/s/w", "/usr/bin", "--tmpfs", "/x"]
    ROOTS = ("/var/tmp/wab12cd34", "/tmp/wab12cd34")  # a sandbox beside /tmp and one inside it

    def wrapper(self, root: str, extra: list[str] | None = None) -> list[str]:
        return arms.private_tmp_wrapper(ta.FAKE_BWRAP, Sandbox.at(Path(root)), extra or ())

    def test_a_pid_namespace_and_a_fresh_proc_are_in_every_wrapper(self) -> None:
        for root in self.ROOTS:
            for extra in (None, self.EXTRA):
                with self.subTest(root=root, extra=bool(extra)):
                    wrapper = self.wrapper(root, extra)
                    self.assertEqual(wrapper.count("--unshare-pid"), 1)
                    self.assertEqual(len(words_at(wrapper, "--proc", "/proc")), 1)
                    self.assertEqual(wrapper.count("--proc"), 1)

    def test_they_come_after_the_machines_file_system_and_before_die_with_parent(self) -> None:
        for root in self.ROOTS:
            for extra in (None, self.EXTRA):
                with self.subTest(root=root, extra=bool(extra)):
                    wrapper = self.wrapper(root, extra)
                    machine = wrapper.index("--dev-bind")
                    done = wrapper.index("--die-with-parent")
                    for word in ("--unshare-pid", "--proc"):
                        self.assertGreater(wrapper.index(word), machine, f"{word} must follow the --dev-bind of /")
                        self.assertLess(wrapper.index(word), done, f"{word} must come before --die-with-parent")

    def test_the_rest_of_the_wrapper_is_what_it_was(self) -> None:
        wrapper = self.wrapper("/var/tmp/wab12cd34")
        self.assertEqual(wrapper[:4], [ta.FAKE_BWRAP, "--dev-bind", "/", "/"])
        self.assertEqual(wrapper[-1], "--")
        self.assertEqual(wrapper.count("--"), 1)
        self.assertEqual(ta.bind_pairs(wrapper), [("/var/tmp/wab12cd34/tmp", "/tmp")])
        self.assertEqual(wrapper.count("--die-with-parent"), 1)
        self.assertEqual(wrapper[wrapper.index("--chdir") + 1], "/var/tmp/wab12cd34/work")


class PipCacheWrapperTest(unittest.TestCase):
    EXTRA = PidNamespaceWrapperTest.EXTRA
    CACHE = Path("/var/tmp/shared cache/pip")  # a space: the path is one word of the wrapper

    def wrapper(self, root: str, extra: list[str] | None = None, cache: Path | None = None) -> list[str]:
        sandbox = Sandbox.at(Path(root))
        return arms.private_tmp_wrapper(ta.FAKE_BWRAP, sandbox, extra or (), pip_cache=cache)

    def test_the_cache_is_bound_read_write_at_the_pip_cache_of_the_sandbox_home(self) -> None:
        for root in ("/var/tmp/wab12cd34", "/tmp/wab12cd34"):
            with self.subTest(root=root):
                wrapper = self.wrapper(root, cache=self.CACHE)
                home = Sandbox.at(Path(root)).home
                self.assertIn((str(self.CACHE), f"{home}/.cache/pip"), ta.bind_pairs(wrapper))
                self.assertEqual(len(words_at(wrapper, "--bind", str(self.CACHE), f"{home}/.cache/pip")), 1)
                self.assertNotIn("--ro-bind", wrapper)

    def test_it_comes_before_the_extra_arguments_and_before_die_with_parent(self) -> None:
        for root in ("/var/tmp/wab12cd34", "/tmp/wab12cd34"):
            with self.subTest(root=root):
                wrapper = self.wrapper(root, self.EXTRA, self.CACHE)
                home = Sandbox.at(Path(root)).home
                [at] = words_at(wrapper, "--bind", str(self.CACHE), f"{home}/.cache/pip")
                self.assertLess(at + 2, wrapper.index("--overlay-src"))
                self.assertLess(at + 2, wrapper.index("--die-with-parent"))
                self.assertGreater(at, wrapper.index("--dev-bind"))
                start = wrapper.index("--overlay-src")
                self.assertEqual(wrapper[start : start + len(self.EXTRA)], self.EXTRA, "extra comes as it is")

    def test_the_other_binds_are_still_there(self) -> None:
        wrapper = self.wrapper("/tmp/wab12cd34", cache=self.CACHE)
        pairs = ta.bind_pairs(wrapper)
        self.assertIn(("/tmp/wab12cd34/tmp", "/tmp"), pairs)
        self.assertIn(("/tmp/wab12cd34", "/tmp/wab12cd34"), pairs)
        self.assertEqual(len(pairs), 3)

    def test_without_a_cache_there_is_no_such_bind(self) -> None:
        for root in ("/var/tmp/wab12cd34", "/tmp/wab12cd34"):
            for extra in (None, self.EXTRA):
                with self.subTest(root=root, extra=bool(extra)):
                    wrapper = self.wrapper(root, extra)
                    self.assertFalse([d for _, d in ta.bind_pairs(wrapper) if d.endswith(".cache/pip")])
                    self.assertFalse([w for w in wrapper if "shared cache" in w or w.endswith("/.cache")])
                    self.assertEqual(wrapper, self.wrapper(root, extra, None))
                    self.assertEqual(
                        wrapper, arms.private_tmp_wrapper(ta.FAKE_BWRAP, Sandbox.at(Path(root)), extra or ())
                    )

    def test_only_the_pip_bind_and_nothing_else_differs_from_the_wrapper_without_it(self) -> None:
        plain = self.wrapper("/var/tmp/wab12cd34", self.EXTRA)
        cached = self.wrapper("/var/tmp/wab12cd34", self.EXTRA, self.CACHE)
        home = Sandbox.at(Path("/var/tmp/wab12cd34")).home
        added = ["--bind", str(self.CACHE), f"{home}/.cache/pip"]
        [at] = words_at(cached, *added)
        self.assertEqual(cached[:at] + cached[at + 3 :], plain)


class PrepareArmPipCacheTest(ta.ArmTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.cache = self.tmp / "bench results" / ".cache" / "pip"
        self.bin_dirs = (self.tmp / "machine" / "bin", self.tmp / "machine" / "local-bin")
        for folder in self.bin_dirs:
            folder.mkdir(parents=True)
        site = self.tmp / "machine" / "site-packages"
        (site / "pip").mkdir(parents=True)
        self.machine = arms.BareMachine(self.bin_dirs, (site,), None)

    def prepare_with(
        self,
        arm: str,
        *,
        bwrap: str | None = ta.FAKE_BWRAP,
        bare: bool = False,
        cache: Path | None = ...,  # type: ignore[assignment]
    ) -> arms.ArmSetup:
        root = self.tmp / f"sandbox-{arm}-{'bare' if bare else 'full'}-{bwrap is not None}"
        root.mkdir()
        extra: dict[str, Any] = {"pip_cache": self.cache if cache is ... else cache}
        if bwrap is not None:
            extra["bwrap"] = bwrap
        if bare:
            extra["bare"] = self.machine
        return prepare_arm(
            arm, root, self.source, self.auth, model="sonnet", max_turns=30, system_path=self.system_path, **extra
        )

    def bind_of(self, setup: arms.ArmSetup) -> list[str]:
        return ["--bind", str(self.cache), f"{setup.sandbox.home}/.cache/pip"]

    def test_every_arm_on_the_full_machine_gets_the_cache(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare_with(arm)
                self.assertEqual(len(words_at(setup.wrapper, *self.bind_of(setup))), 1)
                self.assertEqual(
                    setup.wrapper,
                    arms.private_tmp_wrapper(ta.FAKE_BWRAP, setup.sandbox, pip_cache=self.cache),
                )
                self.assertEqual(setup.machine, "full")

    def test_every_arm_on_the_bare_machine_gets_the_cache_before_the_bare_arguments(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare_with(arm, bare=True)
                [at] = words_at(setup.wrapper, *self.bind_of(setup))
                self.assertLess(at, setup.wrapper.index("--overlay-src"))
                self.assertLess(at, setup.wrapper.index("--die-with-parent"))
                self.assertEqual(setup.machine, "bare")
                self.assertIn(
                    ["--tmpfs", str(self.machine.site_dirs[0])],
                    [setup.wrapper[i : i + 2] for i in range(len(setup.wrapper))],
                )

    def test_without_bwrap_the_cache_is_ignored_and_the_setup_is_the_old_one(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare_with(arm, bwrap=None)
                self.assertEqual(setup.wrapper, [])
                self.assertEqual(setup.env["TMPDIR"], str(setup.sandbox.tmp))
                self.assertEqual(set(setup.env), ta.ALLOWED_KEYS | {ta.OAUTH})
                self.assertNotIn(str(self.cache), json.dumps([setup.env, setup.claude_args]))
                shutil.rmtree(setup.sandbox.root)
                plain = self.prepare_with(arm, bwrap=None, cache=None)
                self.assertEqual(setup.claude_args, plain.claude_args)
                self.assertEqual(setup.env, plain.env)

    def test_no_cache_is_the_wrapper_there_has_always_been(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare_with(arm, cache=None)
                self.assertEqual(setup.wrapper, arms.private_tmp_wrapper(ta.FAKE_BWRAP, setup.sandbox))
                self.assertFalse([d for _, d in ta.bind_pairs(setup.wrapper) if d.endswith(".cache/pip")])

    def test_the_environment_and_the_claude_arguments_do_not_depend_on_the_cache(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                with_cache = self.prepare_with(arm)
                shutil.rmtree(with_cache.sandbox.root)
                without = self.prepare_with(arm, cache=None)
                self.assertEqual(with_cache.env, without.env)
                self.assertEqual(with_cache.claude_args, without.claude_args)
                self.assertNotIn("PIP_CACHE_DIR", with_cache.env)


# -- 2. the real bubblewrap -----------------------------------------------------------------------


@tpt.needs_bwrap
class RealPidNamespaceTest(tpt.RealBwrapCase):
    """Nothing a run started is alive after run_one returns; inside the wrapper only the run's own processes exist."""

    def setUp(self) -> None:
        super().setUp()
        self.tokens: list[str] = []
        self.addCleanup(self.kill_stragglers)

    def kill_stragglers(self) -> None:
        for token in self.tokens:
            ps.kill_with_token(token)

    def token(self) -> str:
        self.tokens.append(ps.unique_sleep())
        return self.tokens[-1]

    def spawning_claude(self, setsid: str, nohup: str, job_control: str, *, finish: bool) -> str:
        """Starts three long sleeps that each escape in their own way, notes their (namespace) pids, then ends or hangs.

        setsid: a new session. nohup: a background job immune to the hangup. job_control: a background job in a
        process group of its own (`set -m`), which a kill of the run's group does not reach.
        """
        events = [fx.init_for_arm("baseline"), *([fx.result_event(text="ok")] if finish else [])]
        answer = self.canned(*events)
        return self.fake_claude(
            f"/usr/bin/cat '{answer}'\n"
            f"/usr/bin/setsid /usr/bin/sleep {setsid} </dev/null >/dev/null 2>&1 &\n"
            "echo $! >> ./pids.txt\n"
            f"/usr/bin/nohup /usr/bin/sleep {nohup} </dev/null >/dev/null 2>&1 &\n"
            "echo $! >> ./pids.txt\n"
            "set -m\n"
            f"/usr/bin/sleep {job_control} </dev/null >/dev/null 2>&1 &\n"
            "echo $! >> ./pids.txt\n" + ("exit 0" if finish else "wait")
        )

    def check_nothing_survives(self, *, finish: bool) -> None:
        tokens = [self.token(), self.token(), self.token()]
        claude = self.spawning_claude(*tokens, finish=finish)
        started = time.monotonic()
        record, setup = self.run_private(claude, tpt.OUTSIDE_TMP, timeout_s=2)
        self.assertLess(time.monotonic() - started, 30, "run_one must return soon")
        self.assertIs(record["timed_out"], not finish)
        self.assertIs(record["passed"], finish, record)
        pids = (setup.sandbox.work / "pids.txt").read_text().split()
        self.assertEqual(len(pids), 3, "the fake claude must have started its three sleeps")
        for token in tokens:
            self.assertTrue(
                ps.wait_until(lambda t=token: not ps.host_pids_with(t)),
                f"`sleep {token}` is still alive after the run ({'ended' if finish else 'timed out'})",
            )

    def test_nothing_started_by_a_run_that_ends_normally_is_alive_afterwards(self) -> None:
        self.check_nothing_survives(finish=True)

    def test_nothing_started_by_a_run_that_hangs_until_the_timeout_is_alive_afterwards(self) -> None:
        self.check_nothing_survives(finish=False)

    def test_the_sleeps_really_are_alive_while_the_run_lasts(self) -> None:
        # the fixture can fail: while the fake claude waits, the machine can see (and so could miss) its sleeps
        tokens = [self.token(), self.token(), self.token()]
        claude = self.spawning_claude(*tokens, finish=False)
        seen: dict[str, list[int]] = {}

        def look() -> None:
            end = time.monotonic() + 1.5
            while time.monotonic() < end:
                found = {t: ps.host_pids_with(t) for t in tokens}
                if all(found.values()):
                    seen.update(found)
                    return
                time.sleep(0.05)

        watcher = threading.Thread(target=look)
        watcher.start()
        try:
            record, _ = self.run_private(claude, tpt.OUTSIDE_TMP, timeout_s=2.5)
        finally:
            watcher.join()
        self.assertIs(record["timed_out"], True)
        self.assertEqual(set(seen), set(tokens), "the three sleeps were never all alive on the machine at once")

    def test_inside_the_wrapper_only_the_runs_own_processes_exist(self) -> None:
        outsider = subprocess.Popen(["/usr/bin/sleep", self.token()], stdin=subprocess.DEVNULL)
        self.addCleanup(lambda: (outsider.kill(), outsider.wait()))
        answer = self.canned(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        claude = self.fake_claude(
            '/usr/bin/ls /proc > "$REC/proc_listing.txt"\n'
            "/usr/bin/tr '\\0' ' ' < /proc/1/cmdline > \"$REC/pid1.txt\"\n"
            'if [ -x /usr/bin/ps ]; then /usr/bin/ps -e -o pid= > "$REC/ps.txt"; fi\n'
            f"/usr/bin/cat '{answer}'\nexit 0"
        )
        record, _ = self.run_private(claude, tpt.OUTSIDE_TMP)
        self.assertIs(record["passed"], True, record)

        inside = sorted(int(name) for name in self.recorded("proc_listing.txt").split() if name.isdigit())
        self.assertIn(1, inside)
        self.assertLess(len(inside), 30, f"the run sees {len(inside)} processes: {inside}")
        self.assertLess(max(inside), 200, f"pids of the machine are visible: {inside}")
        for name, pid in (
            ("the test itself", os.getpid()),
            ("the test's parent", os.getppid()),
            ("a host process", outsider.pid),
        ):
            self.assertNotIn(pid, inside, f"{name} (pid {pid}) is visible inside the run")
        self.assertIn(
            "bwrap", self.recorded("pid1.txt"), "pid 1 of the run must be the wrapper's, not the machine's init"
        )
        if (self.rec / "ps.txt").exists():
            listed = sorted(int(word) for word in self.recorded("ps.txt").split())
            self.assertNotIn(outsider.pid, listed)
            self.assertNotIn(os.getpid(), listed)
            self.assertLess(len(listed), 30)


@tpt.needs_bwrap
class RealPipCacheTest(tpt.RealBwrapCase):
    """The shared cache folder is writable at $HOME/.cache/pip of a run, and a later run sees what an earlier left."""

    def new_cache(self) -> Path:
        cache = self.tmp / "shared" / "pip"
        cache.mkdir(parents=True)
        return cache

    def writing_claude(self, marker: str, name: str) -> str:
        answer = self.canned(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        return self.fake_claude(
            'if [ -d "$HOME/.cache/pip" ]; then echo dir > "$REC/is_dir.txt"; fi\n'
            'if [ -w "$HOME/.cache/pip" ]; then echo writable > "$REC/writable.txt"; fi\n'
            '/usr/bin/ls -A "$HOME/.cache/pip" > "$REC/cache_listing.txt"\n'
            '/usr/bin/cat "$HOME/.cache/pip/from-first" > "$REC/from_first.txt" 2>/dev/null\n'
            '/usr/bin/ls -A /tmp > "$REC/tmp_listing.txt"\n'
            f"echo '{name}' > \"$HOME/.cache/pip/{name}\"\n"
            f"echo probe > '/tmp/{marker}'\n"
            f"/usr/bin/cat '{answer}'\nexit 0"
        )

    def test_the_folder_is_writable_inside_and_what_is_written_is_on_the_machine(self) -> None:
        cache = self.new_cache()
        marker = self.new_marker()
        record, _ = self.run_private(self.writing_claude(marker, "from-first"), tpt.OUTSIDE_TMP, pip_cache=cache)
        self.assertIs(record["passed"], True, record)
        self.assertEqual(self.recorded("is_dir.txt").strip(), "dir")
        self.assertEqual(self.recorded("writable.txt").strip(), "writable")
        self.assertEqual((cache / "from-first").read_text(), "from-first\n")

    def test_the_next_run_sees_the_file_while_the_tmp_folders_stay_apart(self) -> None:
        cache = self.new_cache()
        first_marker, second_marker = self.new_marker("first"), self.new_marker("second")
        record, first = self.run_private(
            self.writing_claude(first_marker, "from-first"), tpt.OUTSIDE_TMP, pip_cache=cache
        )
        self.assertIs(record["passed"], True, record)
        record, second = self.run_private(
            self.writing_claude(second_marker, "from-second"), tpt.OUTSIDE_TMP, pip_cache=cache
        )
        self.assertIs(record["passed"], True, record)

        self.assertEqual(self.recorded("from_first.txt"), "from-first\n", "the second run did not see the first's file")
        self.assertIn("from-first", self.recorded("cache_listing.txt").split())
        self.assertEqual(sorted(p.name for p in cache.iterdir()), ["from-first", "from-second"])
        self.assertNotIn(first_marker, self.recorded("tmp_listing.txt").split(), "/tmp is not shared, the cache is")
        self.assertTrue((first.sandbox.tmp / first_marker).exists())
        self.assertFalse((first.sandbox.tmp / second_marker).exists())
        self.assertTrue((second.sandbox.tmp / second_marker).exists())
        self.assertFalse((second.sandbox.tmp / first_marker).exists())
        self.assertNotEqual(first.sandbox.home, second.sandbox.home)

    def test_a_sandbox_inside_tmp_gets_the_cache_too(self) -> None:
        cache = self.new_cache()
        record, _ = self.run_private(
            self.writing_claude(self.new_marker(), "from-first"), Path("/tmp"), pip_cache=cache
        )
        self.assertIs(record["passed"], True, record)
        self.assertEqual(self.recorded("writable.txt").strip(), "writable")
        self.assertEqual((cache / "from-first").read_text(), "from-first\n")


@tr.needs_the_real_source
@tpt.needs_bwrap
class RealPipCacheRunAllTest(tpt.RealBwrapCase, tr.RunAllCase):
    """run_all with the real bubblewrap: the cache folder is made when missing and shared by the runs."""

    def test_two_runs_share_the_cache_folder_that_run_all_made(self) -> None:
        self.assertFalse(self.pip_cache.exists())
        answer = self.canned(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        claude = self.fake_claude(
            'if [ "$1" != "-p" ]; then echo "9.9.9 (fake claude)"; exit 0; fi\n'
            'n=$(/usr/bin/ls -A "$HOME/.cache/pip" | /usr/bin/wc -l)\n'
            'echo "$n" >> "$REC/entries_seen.txt"\n'
            'echo data > "$HOME/.cache/pip/entry-$n"\n'
            f"/usr/bin/cat '{answer}'\nexit 0"
        )
        code, output, out = self.run_all_with("--runs", "2", claude=claude)
        self.assertEqual(code, 0, output)
        self.assertEqual([r["passed"] for r in self.records(out)], [True, True])
        self.assertTrue(self.pip_cache.is_dir())
        self.assertEqual(self.recorded("entries_seen.txt").split(), ["0", "1"])
        self.assertEqual(sorted(p.name for p in self.pip_cache.iterdir()), ["entry-0", "entry-1"])
        self.assertEqual(list(self.sandboxes.iterdir()), [], "the sandboxes are removed; the cache is not theirs")


# -- 3. run_all: the cache and its warm-up ----------------------------------------------------------


@tr.needs_the_real_source
class RunAllPipCacheTest(tr.RunAllCase):
    """run_all with a fake bwrap that logs how it was called: every run gets the shared cache."""

    def run_private(
        self, *argv: str, arm_list: str = "baseline,skill", claude: str | None = None
    ) -> tuple[int, str, Path]:
        folder = self.fake_bwrap_folder()
        return self.run_all_with(
            "--arms",
            arm_list,
            "--keep-sandboxes",
            *argv,
            claude=claude or self.good_claude(),
            path=self.path_with(folder),
        )

    def commands_by_run_id(self, out: Path) -> dict[str, list[str]]:
        sandboxes = {r["run_id"]: r["sandbox"] for r in self.records(out)}
        commands = {c[c.index("--chdir") + 1]: c for c in self.bwrap_commands()}
        return {run_id: commands[f"{sandbox}/work"] for run_id, sandbox in sandboxes.items()}

    def test_every_run_has_the_cache_bound_at_the_pip_cache_of_its_own_home(self) -> None:
        code, output, out = self.run_private("--runs", "2")
        self.assertEqual(code, 0, output)
        records = self.records(out)
        self.assertEqual(len(records), 4)
        commands = self.commands_by_run_id(out)
        for record in records:
            with self.subTest(run=record["run_id"]):
                command = commands[record["run_id"]]
                bind = ["--bind", str(self.pip_cache), f"{record['sandbox']}/home/.cache/pip"]
                self.assertEqual(len(words_at(command, *bind)), 1)
                self.assertLess(command.index("--unshare-pid"), command.index("--die-with-parent"))
                self.assertEqual(len(words_at(command, "--proc", "/proc")), 1)

    def test_the_folder_is_made_when_it_is_missing_and_exists_when_the_first_run_starts(self) -> None:
        claude = self.fake_claude(
            'if [ "$1" != "-p" ]; then echo "9.9.9 (fake claude)"; exit 0; fi\n'
            f"if [ -d '{self.pip_cache}' ]; then echo yes > \"$REC/cache_existed.txt\"; fi\n"
            f"/usr/bin/cat '{self.canned(fx.init_for_arm('baseline'), fx.result_event(text='ok'))}'\nexit 0"
        )
        self.assertFalse(self.pip_cache.exists())
        code, output, _ = self.run_private(arm_list="baseline", claude=claude)
        self.assertEqual(code, 0, output)
        self.assertTrue(self.pip_cache.is_dir())
        self.assertEqual(self.recorded("cache_existed.txt").strip(), "yes")

    def test_a_cache_that_has_files_keeps_them(self) -> None:
        self.pip_cache.mkdir(parents=True)
        (self.pip_cache / "http-v2").write_text("a package an earlier invocation fetched\n")
        code, output, _ = self.run_private(arm_list="baseline")
        self.assertEqual(code, 0, output)
        self.assertEqual((self.pip_cache / "http-v2").read_text(), "a package an earlier invocation fetched\n")

    def test_the_cache_is_also_given_to_the_mcp_arm(self) -> None:
        code, output, out = self.run_private(arm_list="mcp")
        if code == 2 and "mcp extra" in output:
            self.skipTest("the MCP arm needs the mcp extra here")
        self.assertEqual(code, 0, output)
        [record] = self.records(out)
        [command] = self.bwrap_commands()
        self.assertEqual(
            len(words_at(command, "--bind", str(self.pip_cache), f"{record['sandbox']}/home/.cache/pip")), 1
        )

    def test_shared_tmp_has_no_wrapper_and_no_cache_bind(self) -> None:
        folder = self.fake_bwrap_folder()
        code, output, out = self.run_all_with(
            "--shared-tmp", "--keep-sandboxes", claude=self.good_claude(), path=self.path_with(folder)
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(self.bwrap_calls(), [])
        [record] = self.records(out)
        self.assertEqual(self.child_env()["TMPDIR"], f"{record['sandbox']}/tmp")
        self.assertNotIn("PIP_CACHE_DIR", self.child_env())


@tr.needs_the_real_source
class RunAllWarmUpTest(tr.RunAllCase):
    """When run_all warms the cache. The warm-up itself and check_bare are stand-ins; the fake bwrap logs calls."""

    def setUp(self) -> None:
        super().setUp()
        self.bwrap_folder = self.fake_bwrap_folder()
        self.started_when_warmed: list[bool] = []
        self.checks = 0
        patcher = mock.patch.object(runner_module, "check_bare", side_effect=self.fake_check_bare)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_check_bare(self, bwrap: str, bare: Any, parent: Path) -> list[str]:
        self.checks += 1
        return []

    def marking_claude(self) -> str:
        answer = self.canned(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        return self.fake_claude(
            f'if [ "$1" = "-p" ]; then echo started >> "$REC/started.txt"; fi\n/usr/bin/cat \'{answer}\'\nexit 0'
        )

    def started(self) -> bool:
        return (self.rec / "started.txt").exists()

    def warm_with(self, result: str | None = None) -> mock.Mock:
        def warm(*args: Any, **kwargs: Any) -> str | None:
            self.started_when_warmed.append(self.started())
            return result

        mocked = mock.Mock(side_effect=warm)
        patcher = mock.patch.object(runner_module, "warm_pip_cache", mocked, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def run_bare(self, *argv: str, arm_list: str = "baseline", bare: str = "baseline") -> tuple[int, str, str, Path]:
        return self.run_all_streams(
            "--arms", arm_list, "--bare-arms", bare, "--keep-sandboxes", *argv,
            claude=self.marking_claude(), path=self.path_with(self.bwrap_folder),
        )  # fmt: skip

    @staticmethod
    def cache_given(call: Any) -> Any:
        return call.args[0] if call.args else call.kwargs.get("cache")

    def test_it_is_called_once_before_the_first_run_however_many_runs_there_are(self) -> None:
        warm = self.warm_with()
        code, stdout, stderr, out = self.run_bare("--runs", "3")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(len(self.records(out)), 6)
        self.assertEqual(warm.call_count, 1)
        self.assertEqual(self.started_when_warmed, [False], "the first run had started before the warm-up")

    def test_it_is_given_the_cache_the_runs_use(self) -> None:
        warm = self.warm_with()
        code, stdout, stderr, out = self.run_bare()
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(self.cache_given(warm.call_args), self.pip_cache)
        sandboxes = {r["run_id"]: r["sandbox"] for r in self.records(out)}
        commands = {c[c.index("--chdir") + 1]: c for c in self.bwrap_commands()}
        self.assertEqual(set(sandboxes), {"r01-hello-baseline", "r01-hello-baseline-bare"})
        for run_id, sandbox in sandboxes.items():
            with self.subTest(run=run_id):
                bind = ["--bind", str(self.pip_cache), f"{sandbox}/home/.cache/pip"]
                self.assertEqual(len(words_at(commands[f"{sandbox}/work"], *bind)), 1)

    def test_it_is_called_when_only_bare_arms_run_and_for_a_bare_jzr_arm(self) -> None:
        for arm_list, bare in (("", "baseline"), ("baseline", "skill")):
            with self.subTest(arms=arm_list, bare=bare):
                warm = self.warm_with()
                with mock.patch.object(runner_module, "ensure_jzr_ffmpeg", return_value=self.tmp / "ffmpeg"):
                    code, stdout, stderr, _ = self.run_bare(arm_list=arm_list, bare=bare)
                self.assertEqual(code, 0, stdout + stderr)
                self.assertEqual(warm.call_count, 1)
                shutil.rmtree(self.tmp / "results")

    def test_it_is_not_called_when_no_arm_runs_on_the_bare_machine(self) -> None:
        warm = self.warm_with()
        code, output, _ = self.run_all_with(
            "--arms", "baseline,skill", claude=self.marking_claude(), path=self.path_with(self.bwrap_folder)
        )
        self.assertEqual(code, 0, output)
        warm.assert_not_called()
        self.assertTrue(self.started())

    def test_it_is_not_called_in_a_dry_run(self) -> None:
        warm = self.warm_with()
        code, stdout, stderr, _ = self.run_bare("--dry-run")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertIn("2 runs", stdout)
        warm.assert_not_called()
        self.assertFalse(self.started())

    def test_it_is_not_called_with_shared_tmp_which_the_bare_machine_refuses_anyway(self) -> None:
        warm = self.warm_with()
        code, stdout, stderr, _ = self.run_bare("--shared-tmp")
        self.assertEqual(code, 2, stdout + stderr)
        warm.assert_not_called()
        self.assertFalse(self.started())

    def test_a_problem_is_a_note_on_stderr_and_the_runs_go_on(self) -> None:
        problem = "pip download exited with 1: no network to reach pypi.example"
        warm = self.warm_with(problem)
        code, stdout, stderr, out = self.run_bare("--runs", "2")
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual(warm.call_count, 1)
        self.assertEqual(len(self.records(out)), 4)
        self.assertTrue(all(r["passed"] for r in self.records(out)))
        self.assertIn(problem, stderr)
        self.assertNotIn(problem, stdout)
        [line] = [ln for ln in stderr.splitlines() if problem in ln]
        self.assertNotIn("error", line.lower(), "a failed warm-up is a note, not an error")
        self.assertIsNone(self.meta(out)["stopped"])

    def test_no_problem_prints_no_note_about_it(self) -> None:
        self.warm_with(None)
        code, stdout, stderr, _ = self.run_bare()
        self.assertEqual(code, 0, stdout + stderr)
        self.assertEqual([ln for ln in stderr.splitlines() if ln.startswith("note")], [])


class WarmConstantsTest(unittest.TestCase):
    def test_the_cache_is_bench_results_cache_pip_next_to_the_jzr_data_cache(self) -> None:
        self.assertEqual(runner_module.PIP_CACHE, REPO / "bench" / "results" / ".cache" / "pip")
        self.assertEqual(runner_module.PIP_CACHE.parent, runner_module.JZR_FFMPEG_CACHE.parent)

    def test_the_bare_baseline_packages_are_the_six_it_was_seen_to_install(self) -> None:
        packages = runner_module.BARE_WARM_PACKAGES
        self.assertIsInstance(packages, tuple)
        self.assertCountEqual(packages, ["imageio-ffmpeg", "pillow", "pillow-heif", "pypdf", "openpyxl", "mutagen"])
        self.assertEqual(len(set(packages)), len(packages))


class WarmPipCacheTest(unittest.TestCase):
    """warm_pip_cache with a fake `python3` on a fake system path: it records its calls and fakes what pip does."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(dir="/var/tmp" if os.access("/var/tmp", os.W_OK) else None)
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.rec = self.tmp / "rec"
        self.rec.mkdir()
        self.cache = self.tmp / "cache" / "pip"
        self.cache.mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def fake_python(self, *, exit_code: int = 0, stderr: str = "", extra: str = "") -> str:
        """Writes bin/python3; returns the system path that holds it."""
        script = self.bin / "python3"
        script.write_text(
            "#!/bin/sh\n"
            f"REC='{self.rec}'\n"
            'printf "%s\\n" "$@" > "$REC/args-$$.txt"\n'
            'printf "%s\\n" "$PIP_CACHE_DIR" > "$REC/cache-$$.txt"\n'
            'pwd > "$REC/cwd-$$.txt"\n'
            "if [ -z \"$PIP_CACHE_DIR\" ]; then echo 'PIP_CACHE_DIR is not set' >&2; exit 96; fi\n"
            'dest=""; prev=""\n'
            'for a in "$@"; do\n'
            '  case "$prev" in -d|--dest) dest=$a;; esac\n'
            '  case "$a" in --dest=*) dest=${a#--dest=};; esac\n'
            "  prev=$a\n"
            "done\n"
            'printf "%s\\n" "$dest" > "$REC/dest-$$.txt"\n'
            f"if [ -z \"$dest\" ] && [ \"$PWD\" = '{os.getcwd()}' ]; then echo 'no download destination' >&2; exit 97; fi\n"
            'target="${dest:-$PWD}"\n'
            '/usr/bin/mkdir -p "$target"\n'
            ': > "$target/package-0.1-py3-none-any.whl"\n'
            '/usr/bin/mkdir -p "$PIP_CACHE_DIR/http"\n'
            ': > "$PIP_CACHE_DIR/http/entry"\n'
            f"{extra}" + (f"echo '{stderr}' >&2\n" if stderr else "") + f"exit {exit_code}\n"
        )
        script.chmod(0o755)
        return str(self.bin)

    def calls(self) -> list[dict[str, Any]]:
        found = []
        for args_file in sorted(self.rec.glob("args-*.txt")):
            pid = args_file.stem.split("-", 1)[1]
            found.append(
                {
                    "args": args_file.read_text().splitlines(),
                    "cache": (self.rec / f"cache-{pid}.txt").read_text().strip(),
                    "cwd": (self.rec / f"cwd-{pid}.txt").read_text().strip(),
                    "dest": (self.rec / f"dest-{pid}.txt").read_text().strip(),
                }
            )
        return found

    def warm(self, system_path: str, **kwargs: Any) -> str | None:
        return runner_module.warm_pip_cache(self.cache, system_path=system_path, **kwargs)

    def test_it_runs_pip_download_of_every_package_through_the_system_python(self) -> None:
        self.assertIsNone(self.warm(self.fake_python()))
        calls = self.calls()
        self.assertTrue(calls, "python3 was never started")
        for call in calls:
            self.assertEqual(call["args"][:3], ["-m", "pip", "download"], call["args"])
        asked = {arg for call in calls for arg in call["args"]}
        for package in runner_module.BARE_WARM_PACKAGES:
            self.assertIn(package, asked)

    def test_it_asks_for_the_packages_it_is_given(self) -> None:
        self.assertIsNone(self.warm(self.fake_python(), packages=("alpha-pkg", "beta")))
        asked = {arg for call in self.calls() for arg in call["args"]}
        self.assertTrue({"alpha-pkg", "beta"} <= asked)
        self.assertNotIn("pillow", asked)

    def test_pip_is_told_to_use_the_shared_cache(self) -> None:
        self.assertIsNone(self.warm(self.fake_python()))
        for call in self.calls():
            self.assertEqual(call["cache"], str(self.cache))
        self.assertTrue((self.cache / "http" / "entry").exists(), "what pip cached must stay in the cache")

    def test_the_downloads_go_to_a_throwaway_folder_that_is_gone_afterwards(self) -> None:
        self.assertIsNone(self.warm(self.fake_python()))
        for call in self.calls():
            target = Path(call["dest"] or call["cwd"])
            self.assertNotEqual(target, Path.cwd(), "pip must not download into the caller's folder")
            self.assertFalse(target == self.cache or self.cache in target.parents, "not into the cache itself")
            self.assertFalse(target.exists(), f"{target} was left behind")
        self.assertEqual([p.name for p in self.cache.rglob("*.whl")], [], "the packages themselves are not kept")
        self.assertEqual([p.name for p in Path.cwd().glob("package-*.whl")], [])

    def test_a_failing_pip_is_a_problem_text_and_the_folder_is_still_gone(self) -> None:
        problem = self.warm(self.fake_python(exit_code=1, stderr="ERROR: no matching distribution"))
        self.assertIsInstance(problem, str)
        self.assertTrue(problem)
        for call in self.calls():
            self.assertFalse(Path(call["dest"] or call["cwd"]).exists())

    def test_any_non_zero_exit_is_a_problem(self) -> None:
        for code in (2, 127):
            with self.subTest(code=code):
                shutil.rmtree(self.rec)
                self.rec.mkdir()
                problem = self.warm(self.fake_python(exit_code=code))
                self.assertIsInstance(problem, str)
                self.assertTrue(problem)

    def test_a_system_path_without_python3_is_a_problem_not_an_exception(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        problem = self.warm(str(empty))
        self.assertIsInstance(problem, str)
        self.assertTrue(problem)
        self.assertEqual(self.calls(), [])

    def test_a_timeout_is_a_problem_text_and_the_wait_is_at_most_30_minutes(self) -> None:
        system_path = self.fake_python()
        real_run = subprocess.run
        with mock.patch.object(runner_module.subprocess, "run", wraps=real_run) as watched:
            self.assertIsNone(self.warm(system_path))
        timeouts = [call.kwargs.get("timeout") for call in watched.call_args_list]
        self.assertTrue(timeouts, "pip was not started through subprocess.run")
        for timeout in timeouts:
            self.assertIsNotNone(timeout, "pip download must have a timeout")
            self.assertLessEqual(timeout, 1800)
            self.assertGreater(timeout, 0)

        def hang(*args: Any, **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(args[0] if args else "pip", kwargs.get("timeout") or 1800)

        with mock.patch.object(runner_module.subprocess, "run", side_effect=hang):
            problem = self.warm(system_path)
        self.assertIsInstance(problem, str)
        self.assertTrue(problem)


# -- 4. the stream -------------------------------------------------------------------------------


class PartialUsageTest(unittest.TestCase):
    def usage(self, *events: dict[str, Any]) -> dict[str, dict]:
        return parse_stream(fx.to_lines(*events)).partial_usage

    def test_the_field_exists_and_is_empty_for_a_fresh_metrics_object(self) -> None:
        self.assertEqual(StreamMetrics().partial_usage, {})
        first, second = StreamMetrics(), StreamMetrics()
        first.partial_usage["m"] = {}
        self.assertEqual(second.partial_usage, {})

    def test_one_message_gives_a_model_usage_shaped_entry_for_its_model(self) -> None:
        got = self.usage(fx.init_for_arm("baseline"), fx.assistant_usage_event("msg_1"))
        self.assertEqual(
            got,
            {
                "claude-sonnet-5-5": {
                    "inputTokens": 2,
                    "outputTokens": 16,
                    "cacheReadInputTokens": 11837,
                    "cacheCreationInputTokens": 5777,
                    "cacheCreationEphemeral5mInputTokens": 0,
                    "cacheCreationEphemeral1hInputTokens": 5777,
                }
            },
        )

    def test_there_is_no_cost_in_an_entry(self) -> None:
        [entry] = self.usage(fx.assistant_usage_event("msg_1")).values()
        self.assertEqual(set(entry), PARTIAL_KEYS)
        self.assertNotIn("costUSD", entry)

    def test_the_ephemeral_keys_only_when_the_usage_has_a_cache_creation_object_with_them(self) -> None:
        plain = self.usage(fx.assistant_usage_event("msg_1", ephemeral=None))
        self.assertEqual(set(plain[MODEL]), PLAIN_PARTIAL_KEYS)
        self.assertEqual(plain[MODEL]["cacheCreationInputTokens"], 5777)
        event = fx.assistant_usage_event("msg_2")
        event["message"]["usage"]["cache_creation"] = {}
        self.assertEqual(set(self.usage(event)[MODEL]), PLAIN_PARTIAL_KEYS)

    def test_the_ephemeral_split_is_the_streams_not_derived(self) -> None:
        got = self.usage(fx.assistant_usage_event("msg_1", cache_creation=900, ephemeral=(400, 500)))
        self.assertEqual(got[MODEL]["cacheCreationEphemeral5mInputTokens"], 400)
        self.assertEqual(got[MODEL]["cacheCreationEphemeral1hInputTokens"], 500)
        self.assertEqual(got[MODEL]["cacheCreationInputTokens"], 900)

    def test_a_message_in_several_events_is_counted_once_with_its_last_usage(self) -> None:
        got = self.usage(
            fx.assistant_usage_event("msg_1", output_tokens=16, blocks=(fx.text_block("a"),)),
            fx.assistant_usage_event("msg_1", output_tokens=57, blocks=(fx.tool_use("Read", file_path="a"),)),
            fx.assistant_usage_event("msg_1", output_tokens=120, blocks=(fx.bash("ls"),)),
        )
        self.assertEqual(got[MODEL]["outputTokens"], 120)
        self.assertEqual(got[MODEL]["inputTokens"], 2)
        self.assertEqual(got[MODEL]["cacheReadInputTokens"], 11837)
        self.assertEqual(got[MODEL]["cacheCreationInputTokens"], 5777)

    def test_the_last_usage_wins_even_when_it_is_the_smaller_one(self) -> None:
        got = self.usage(
            fx.assistant_usage_event("msg_1", output_tokens=120, cache_read=20000),
            fx.assistant_usage_event("msg_1", output_tokens=90, cache_read=11837),
        )
        self.assertEqual(got[MODEL]["outputTokens"], 90)
        self.assertEqual(got[MODEL]["cacheReadInputTokens"], 11837)

    def test_different_messages_add_up(self) -> None:
        got = self.usage(
            fx.assistant_usage_event(
                "msg_1", input_tokens=2, cache_creation=5777, cache_read=11837, output_tokens=16, ephemeral=(0, 5777)
            ),
            fx.user_tool_result("done"),
            fx.assistant_usage_event(
                "msg_2", input_tokens=1, cache_creation=300, cache_read=17614, output_tokens=40, ephemeral=(100, 200)
            ),
        )
        self.assertEqual(
            got[MODEL],
            {
                "inputTokens": 3,
                "outputTokens": 56,
                "cacheReadInputTokens": 29451,
                "cacheCreationInputTokens": 6077,
                "cacheCreationEphemeral5mInputTokens": 100,
                "cacheCreationEphemeral1hInputTokens": 5977,
            },
        )

    def test_a_message_updated_after_another_one_started_is_still_counted_once(self) -> None:
        got = self.usage(
            fx.assistant_usage_event("msg_1", output_tokens=10),
            fx.assistant_usage_event("msg_2", output_tokens=20),
            fx.assistant_usage_event("msg_1", output_tokens=100),
        )
        self.assertEqual(got[MODEL]["outputTokens"], 120)
        self.assertEqual(got[MODEL]["inputTokens"], 4)

    def test_each_model_has_its_own_entry(self) -> None:
        got = self.usage(
            fx.assistant_usage_event("msg_1", model="claude-sonnet-5-5", output_tokens=16),
            fx.assistant_usage_event("msg_2", model="claude-haiku-4-5", output_tokens=7, input_tokens=9),
            fx.assistant_usage_event("msg_3", model="claude-sonnet-5-5", output_tokens=4),
        )
        self.assertEqual(set(got), {"claude-sonnet-5-5", "claude-haiku-4-5"})
        self.assertEqual(got["claude-sonnet-5-5"]["outputTokens"], 20)
        self.assertEqual(got["claude-haiku-4-5"]["outputTokens"], 7)
        self.assertEqual(got["claude-haiku-4-5"]["inputTokens"], 9)

    def test_events_without_a_usage_are_ignored(self) -> None:
        got = self.usage(
            fx.assistant_event(fx.bash("echo hi")),  # an id, a model, no usage
            fx.assistant_usage_event("msg_1", output_tokens=16),
            fx.assistant_event(fx.bash("echo there")),
        )
        self.assertEqual(got[MODEL]["outputTokens"], 16)
        self.assertEqual(got[MODEL]["inputTokens"], 2)

    def test_a_later_event_of_a_message_without_usage_does_not_erase_what_was_known(self) -> None:
        silent = fx.assistant_usage_event("msg_1")
        del silent["message"]["usage"]
        got = self.usage(fx.assistant_usage_event("msg_1", output_tokens=16), silent)
        self.assertEqual(got[MODEL]["outputTokens"], 16)

    def test_events_without_an_id_are_ignored(self) -> None:
        anonymous = fx.assistant_usage_event("x", output_tokens=999)
        del anonymous["message"]["id"]
        got = self.usage(anonymous, fx.assistant_usage_event("msg_1", output_tokens=16))
        self.assertEqual(got[MODEL]["outputTokens"], 16)
        self.assertEqual(self.usage(anonymous), {})

    def test_without_assistant_events_it_is_an_empty_dict(self) -> None:
        for events in ([], [fx.init_for_arm("baseline")], [fx.init_for_arm("baseline"), fx.user_tool_result("x")]):
            with self.subTest(events=len(events)):
                self.assertEqual(self.usage(*events), {})
        self.assertEqual(parse_stream([]).partial_usage, {})
        self.assertEqual(parse_stream(["", "not json", "[1, 2]"]).partial_usage, {})

    def test_the_results_usage_is_not_counted_as_a_message(self) -> None:
        got = self.usage(
            fx.init_for_arm("baseline"),
            fx.assistant_usage_event("msg_1", output_tokens=16),
            fx.result_event(text="ok", input_tokens=9000, cache_creation=9000, cache_read=9000, output_tokens=9000),
        )
        self.assertEqual(got[MODEL]["outputTokens"], 16)
        self.assertEqual(got[MODEL]["inputTokens"], 2)

    def test_the_other_metrics_of_such_events_are_still_read(self) -> None:
        m = parse_stream(
            fx.to_lines(
                fx.init_for_arm("skill"),
                fx.assistant_usage_event(
                    "msg_1", blocks=(fx.tool_use("Skill", skill="jzr-media"), fx.bash("jzr video gif a.mp4"))
                ),
            )
        )
        self.assertEqual(m.tool_calls, {"Skill": 1, "Bash": 1})
        self.assertEqual(m.bash_commands, ["jzr video gif a.mp4"])
        self.assertTrue(m.used_jzr_cli and m.used_jzr_skill)
        self.assertFalse(m.has_result)
        self.assertEqual(m.model_usage, {})


# -- 5. records and cost --------------------------------------------------------------------------


ROW_FIRST = uf.ROWS_4[0]


class PartialUsageInRecordsTest(tr.RunnerCase):
    """run_one: a run without a result event carries partial_model_usage; one with a result event does not."""

    def events(self, *, finish: bool) -> list[dict[str, Any]]:
        return [
            fx.init_for_arm("baseline"),
            fx.assistant_usage_event("msg_1", output_tokens=10, blocks=(fx.bash("echo one"),)),
            fx.assistant_usage_event("msg_1", output_tokens=16, blocks=(fx.bash("echo two"),)),
            fx.user_tool_result("one"),
            fx.assistant_usage_event(
                "msg_2", input_tokens=1, cache_creation=100, cache_read=20000, output_tokens=1000, ephemeral=(100, 0)
            ),
            *([fx.result_event(text="ok")] if finish else []),
        ]

    def hanging_claude(self, *events: dict[str, Any]) -> str:
        return self.fake_claude(f"/usr/bin/cat '{self.canned(*events)}'\nexec /usr/bin/sleep 60")

    def test_a_timed_out_run_has_the_usage_of_the_messages_seen_so_far(self) -> None:
        record, _ = self.run_it(self.hanging_claude(*self.events(finish=False)), timeout_s=1.5)
        self.assertIs(record["timed_out"], True)
        self.assertEqual(
            record["partial_model_usage"],
            {
                MODEL: {
                    "inputTokens": 3,
                    "outputTokens": 16 + 1000,
                    "cacheReadInputTokens": 11837 + 20000,
                    "cacheCreationInputTokens": 5777 + 100,
                    "cacheCreationEphemeral5mInputTokens": 100,
                    "cacheCreationEphemeral1hInputTokens": 5777,
                }
            },
        )
        self.assertIsNone(record["total_cost_usd"])
        self.assertIsNone(record["num_turns"])
        json.dumps(record)

    def test_a_run_whose_child_died_without_a_result_has_it_too(self) -> None:
        body = f"/usr/bin/cat '{self.canned(*self.events(finish=False))}'\necho 'killed' >&2\nexit 137"
        record, _ = self.run_it(self.fake_claude(body))
        self.assertIs(record["timed_out"], False)
        self.assertIn("no result event", record["error"])
        self.assertEqual(record["partial_model_usage"][MODEL]["outputTokens"], 1016)
        self.assertEqual(record["partial_model_usage"][MODEL]["inputTokens"], 3)

    def test_a_run_that_made_no_assistant_event_has_an_empty_dict_not_a_missing_key(self) -> None:
        record, _ = self.run_it(self.hanging_claude(fx.init_for_arm("baseline")), timeout_s=1.5)
        self.assertIs(record["timed_out"], True)
        self.assertIn("partial_model_usage", record)
        self.assertEqual(record["partial_model_usage"], {})

    def test_a_run_with_a_result_event_does_not_carry_the_key_at_all(self) -> None:
        # pinned as absent (the spec: "Runs with a result event do not carry it"), not as null
        record, _ = self.run_it(self.printing_claude(*self.events(finish=True)))
        self.assertIs(record["passed"], True)
        self.assertNotIn("partial_model_usage", record)

    def test_an_error_result_is_a_result_event_too(self) -> None:
        events = [*self.events(finish=False), fx.result_event(text="Invalid API key", is_error=True, cost=0)]
        record, _ = self.run_it(self.printing_claude(*events))
        self.assertIs(record["passed"], False)
        self.assertNotIn("partial_model_usage", record)

    def test_the_results_own_numbers_are_unchanged_by_it(self) -> None:
        record, _ = self.run_it(self.printing_claude(*self.events(finish=True)))
        self.assertAlmostEqual(record["total_cost_usd"], 0.0123)
        self.assertEqual(record["model_usage"][MODEL]["inputTokens"], 10)


class EstimateCostTest(unittest.TestCase):
    PRICES_1H = {MODEL: uf.PRICES_1H}

    def record(self, partial: Any = ..., **extra: Any) -> dict[str, Any]:
        base: dict[str, Any] = {"run_id": "r01-gif-baseline", "total_cost_usd": None, "model_usage": {}}
        if partial is not ...:
            base["partial_model_usage"] = partial
        return {**base, **extra}

    def test_it_is_the_partial_usage_priced_with_the_prices(self) -> None:
        for row in uf.ROWS_4:
            with self.subTest(row=row):
                tokens = uf.tokens_1h(row)
                record = self.record({MODEL: partial_entry(tokens)})
                self.assertAlmostEqual(
                    runner_module.estimate_cost(record, self.PRICES_1H), uf.cost_of(tokens, uf.PRICES_1H), places=9
                )

    def test_a_zero_count_of_a_kind_without_a_price_costs_nothing(self) -> None:
        # the fitted prices of real runs have no 5 minute cache write (no run ever wrote any); the partial usage has 0 of it
        tokens = uf.tokens_1h((10, 3000, 20000, 100))
        self.assertEqual(tokens["cache_write_5m"], 0)
        self.assertNotIn("cache_write_5m", uf.PRICES_1H)
        estimate = runner_module.estimate_cost(self.record({MODEL: partial_entry(tokens)}), self.PRICES_1H)
        self.assertIsNotNone(estimate)

    def test_the_five_minute_kind_is_priced_when_there_are_tokens_of_it(self) -> None:
        tokens = {"input": 5, "cache_write_5m": 1000, "cache_write_1h": 2000, "cache_read": 10, "output": 7}
        prices = {MODEL: uf.PRICES_5M_1H}
        estimate = runner_module.estimate_cost(self.record({MODEL: partial_entry(tokens)}), prices)
        self.assertAlmostEqual(estimate, uf.cost_of(tokens, uf.PRICES_5M_1H), places=12)

    def test_without_the_split_the_cache_write_is_one_kind(self) -> None:
        tokens = uf.tokens_plain(uf.ROWS_4[1])
        entry = partial_entry(tokens)
        self.assertEqual(set(entry), PLAIN_PARTIAL_KEYS)
        estimate = runner_module.estimate_cost(self.record({MODEL: entry}), {MODEL: uf.PRICES_PLAIN})
        self.assertAlmostEqual(estimate, uf.cost_of(tokens, uf.PRICES_PLAIN), places=12)

    def test_models_add_up_each_at_its_own_prices(self) -> None:
        sonnet, haiku = uf.tokens_1h(uf.ROWS_4[0]), uf.tokens_plain(uf.ROWS_4[2])
        record = self.record({MODEL: partial_entry(sonnet), uf.OTHER_MODEL: partial_entry(haiku)})
        prices = {MODEL: uf.PRICES_1H, uf.OTHER_MODEL: uf.PRICES_PLAIN}
        expected = uf.cost_of(sonnet, uf.PRICES_1H) + uf.cost_of(haiku, uf.PRICES_PLAIN)
        self.assertAlmostEqual(runner_module.estimate_cost(record, prices), expected, places=12)

    def test_no_usage_is_none(self) -> None:
        self.assertIsNone(runner_module.estimate_cost(self.record(), self.PRICES_1H))  # an old record: no key
        self.assertIsNone(runner_module.estimate_cost(self.record({}), self.PRICES_1H))
        self.assertIsNone(runner_module.estimate_cost(self.record(None), self.PRICES_1H))

    def test_no_prices_is_none(self) -> None:
        record = self.record({MODEL: partial_entry(uf.tokens_1h(ROW_FIRST))})
        self.assertIsNone(runner_module.estimate_cost(record, {}))

    def test_no_prices_for_a_model_is_none_not_a_partial_sum(self) -> None:
        record = self.record(
            {MODEL: partial_entry(uf.tokens_1h(ROW_FIRST)), uf.OTHER_MODEL: partial_entry(uf.tokens_1h(ROW_FIRST))}
        )
        self.assertIsNone(runner_module.estimate_cost(record, self.PRICES_1H))
        self.assertIsNone(runner_module.estimate_cost(record, {uf.OTHER_MODEL: uf.PRICES_1H}))

    def test_a_kind_with_tokens_and_no_price_is_none(self) -> None:
        tokens = {"input": 5, "cache_write_5m": 1000, "cache_write_1h": 2000, "cache_read": 10, "output": 7}
        self.assertIsNone(runner_module.estimate_cost(self.record({MODEL: partial_entry(tokens)}), self.PRICES_1H))

    def test_it_does_not_change_the_record(self) -> None:
        record = self.record({MODEL: partial_entry(uf.tokens_1h(ROW_FIRST))})
        before = json.dumps(record, sort_keys=True)
        runner_module.estimate_cost(record, self.PRICES_1H)
        self.assertEqual(json.dumps(record, sort_keys=True), before)


@tr.needs_the_real_source
class RunAllTimeoutCostTest(tr.RunAllCase):
    """run_all with a fake claude that plays one canned stream per run, the hanging ones until the timeout.

    Four finished runs (the token rows of usage_fixtures at PRICES_1H) are enough for the fit; the hanging run
    then used HANG tokens, which the fitted prices price at HANG_COST. No bubblewrap is needed: --shared-tmp.
    """

    HANG = {"input": 3, "cache_write_5m": 0, "cache_write_1h": 20000, "cache_read": 50000, "output": 2000}
    HANG_COST = uf.cost_of(HANG, uf.PRICES_1H)  # 0.105006
    FINISHED = [uf.tokens_1h(row) for row in uf.ROWS_4[:4]]
    FINISHED_COSTS = [uf.cost_of(tokens, uf.PRICES_1H) for tokens in FINISHED]

    def finished(self, number: int, tokens: dict[str, int]) -> list[dict[str, Any]]:
        cost = uf.cost_of(tokens, uf.PRICES_1H)
        return [
            fx.init_for_arm("baseline"),
            assistant_for(f"msg_f{number}", tokens),
            fx.result_event(text="ok", cost=cost, model_usage={MODEL: uf.entry(tokens, cost)}),
        ]

    def hanging(self) -> list[dict[str, Any]]:
        """Two messages, the first in two events, adding up to HANG."""
        first = {"input": 2, "cache_write_5m": 0, "cache_write_1h": 12000, "cache_read": 20000, "output": 1000}
        second = {"input": 1, "cache_write_5m": 0, "cache_write_1h": 8000, "cache_read": 30000, "output": 1000}
        early = dict(first, output=10)
        assert {k: first[k] + second[k] for k in first} == self.HANG
        return [
            fx.init_for_arm("baseline"),
            assistant_for("msg_h1", early),
            assistant_for("msg_h1", first),
            fx.user_tool_result("done"),
            assistant_for("msg_h2", second),
        ]

    def scripted(self, runs: list[tuple[list[dict[str, Any]], bool]]) -> str:
        """A claude that plays runs[0], runs[1], ... for its first, second, ... start; True means: then hang."""
        for number, (events, hang) in enumerate(runs, 1):
            (self.rec / f"canned-{number}.jsonl").write_text("\n".join(fx.to_lines(*events)) + "\n")
            if hang:
                (self.rec / f"hang-{number}").write_text("")
        return self.fake_claude(
            'if [ "$1" != "-p" ]; then echo "9.9.9 (fake claude)"; exit 0; fi\n'
            'n=$(/usr/bin/cat "$REC/count" 2>/dev/null || echo 0)\n'
            "n=$((n + 1))\n"
            'echo "$n" > "$REC/count"\n'
            '/usr/bin/cat "$REC/canned-$n.jsonl"\n'
            'if [ -f "$REC/hang-$n" ]; then exec /usr/bin/sleep 300; fi\n'
            "exit 0"
        )

    def starts(self) -> int:
        return int((self.rec / "count").read_text())

    def run_scripted(self, runs: list[tuple[list[dict[str, Any]], bool]], *argv: str) -> tuple[int, str, Path]:
        claude = self.scripted(runs)
        return self.run_all_with("--shared-tmp", "--runs", str(len(runs)), "--timeout", "2", *argv, claude=claude)

    def four_then_a_hang(self, *, then: int = 0) -> list[tuple[list[dict[str, Any]], bool]]:
        runs: list[tuple[list[dict[str, Any]], bool]] = [
            (self.finished(i, t), False) for i, t in enumerate(self.FINISHED, 1)
        ]
        runs.append((self.hanging(), True))
        for number in range(then):
            runs.append((self.finished(10 + number, uf.tokens_1h(uf.ROWS_4[4 + number])), False))
        return runs

    # -- the record --------------------------------------------------------------------------------

    def test_a_timed_out_run_after_four_finished_ones_gets_an_estimate_and_no_total(self) -> None:
        code, output, out = self.run_scripted(self.four_then_a_hang())
        self.assertEqual(code, 0, output)
        records = self.records(out)
        self.assertEqual(len(records), 5)
        hung = records[4]
        self.assertIs(hung["timed_out"], True)
        self.assertIsNone(hung["total_cost_usd"], "total_cost_usd is Claude Code's figure only")
        self.assertAlmostEqual(hung["estimated_cost_usd"], self.HANG_COST, places=9)
        self.assertEqual(
            hung["partial_model_usage"][MODEL],
            {
                "inputTokens": 3,
                "outputTokens": 2000,
                "cacheReadInputTokens": 50000,
                "cacheCreationInputTokens": 20000,
                "cacheCreationEphemeral5mInputTokens": 0,
                "cacheCreationEphemeral1hInputTokens": 20000,
            },
            "msg_h1 counted once (its last usage), msg_h2 added",
        )

    def test_finished_runs_keep_their_own_cost_and_carry_no_partial_usage(self) -> None:
        code, output, out = self.run_scripted(self.four_then_a_hang())
        self.assertEqual(code, 0, output)
        for record, cost in zip(self.records(out)[:4], self.FINISHED_COSTS, strict=True):
            self.assertAlmostEqual(record["total_cost_usd"], cost, places=9)
            self.assertNotIn("partial_model_usage", record)
            self.assertIsNone(record.get("estimated_cost_usd"))

    # -- spent, the ceiling and meta ------------------------------------------------------------------

    def test_the_estimate_counts_in_spent_usd_and_is_reported_apart(self) -> None:
        code, output, out = self.run_scripted(self.four_then_a_hang())
        self.assertEqual(code, 0, output)
        meta = self.meta(out)
        self.assertAlmostEqual(meta["spent_usd"], sum(self.FINISHED_COSTS) + self.HANG_COST, places=5)
        self.assertAlmostEqual(meta["spent_estimated_usd"], self.HANG_COST, places=5)
        self.assertIsNone(meta["stopped"])

    def test_a_run_without_estimates_has_a_spent_estimated_usd_of_zero(self) -> None:
        runs = [(self.finished(i, t), False) for i, t in enumerate(self.FINISHED, 1)]
        code, output, out = self.run_scripted(runs)
        self.assertEqual(code, 0, output)
        meta = self.meta(out)
        self.assertEqual(meta["spent_estimated_usd"], 0)
        self.assertAlmostEqual(meta["spent_usd"], sum(self.FINISHED_COSTS), places=5)

    def test_the_cost_ceiling_counts_the_estimate_so_the_next_run_is_not_started(self) -> None:
        # 4 finished runs cost 0.0817; the hung one is estimated at 0.1050: 0.1867 is over the ceiling of 0.15
        self.assertLess(sum(self.FINISHED_COSTS), 0.15)
        self.assertGreater(sum(self.FINISHED_COSTS) + self.HANG_COST, 0.15)
        code, output, out = self.run_scripted(self.four_then_a_hang(then=2), "--max-cost-usd", "0.15")
        self.assertEqual(code, 2, output)
        self.assertEqual(len(self.records(out)), 5, "the sixth run must not be started")
        self.assertEqual(self.starts(), 5)
        meta = self.meta(out)
        self.assertIn("cost ceiling", meta["stopped"])
        self.assertIn("5 runs", meta["stopped"])
        self.assertAlmostEqual(meta["spent_usd"], sum(self.FINISHED_COSTS) + self.HANG_COST, places=5)

    def test_without_the_ceiling_being_reached_the_runs_after_it_go_on(self) -> None:
        code, output, out = self.run_scripted(self.four_then_a_hang(then=2), "--max-cost-usd", "0.25")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.records(out)), 7)
        extra = [uf.cost_of(uf.tokens_1h(row), uf.PRICES_1H) for row in uf.ROWS_4[4:6]]
        self.assertAlmostEqual(
            self.meta(out)["spent_usd"], sum(self.FINISHED_COSTS) + self.HANG_COST + sum(extra), places=5
        )
        self.assertAlmostEqual(self.meta(out)["spent_estimated_usd"], self.HANG_COST, places=5)

    def test_the_progress_line_of_the_hung_run_still_says_timeout(self) -> None:
        code, output, _ = self.run_scripted(self.four_then_a_hang())
        self.assertEqual(code, 0, output)
        [line] = [ln for ln in output.splitlines() if ln.startswith("[5/5]")]
        self.assertIn("timeout", line)

    # -- no prices yet ----------------------------------------------------------------------------------

    def test_with_no_earlier_run_there_is_no_estimate_and_nothing_breaks(self) -> None:
        runs = [(self.hanging(), True), (self.finished(2, self.FINISHED[0]), False)]
        code, output, out = self.run_scripted(runs)
        self.assertEqual(code, 0, output)
        first, second = self.records(out)
        self.assertIs(first["timed_out"], True)
        self.assertIsNone(first["total_cost_usd"])
        self.assertIsNone(first.get("estimated_cost_usd"))
        self.assertEqual(first["partial_model_usage"][MODEL]["outputTokens"], 2000, "the usage is kept for the report")
        self.assertEqual(second["passed"], True)
        meta = self.meta(out)
        self.assertAlmostEqual(meta["spent_usd"], self.FINISHED_COSTS[0], places=6)
        self.assertEqual(meta["spent_estimated_usd"], 0)

    def test_with_too_few_earlier_runs_to_fit_prices_there_is_no_estimate_either(self) -> None:
        runs = [(self.finished(i, t), False) for i, t in enumerate(self.FINISHED[:3], 1)]
        runs.append((self.hanging(), True))
        code, output, out = self.run_scripted(runs)
        self.assertEqual(code, 0, output)
        hung = self.records(out)[3]
        self.assertIsNone(hung.get("estimated_cost_usd"))
        self.assertEqual(self.meta(out)["spent_estimated_usd"], 0)
        self.assertAlmostEqual(self.meta(out)["spent_usd"], sum(self.FINISHED_COSTS[:3]), places=6)

    # -- the summary written at the end --------------------------------------------------------------------

    def test_the_summary_counts_the_estimate_in_cost_all_only(self) -> None:
        code, output, out = self.run_scripted(self.four_then_a_hang())
        self.assertEqual(code, 0, output)
        text = (out / "summary.md").read_text()
        self.assertEqual(
            r3.cell(text, "## hello", "baseline", "cost (all)"),
            f"${statistics.median([*self.FINISHED_COSTS, self.HANG_COST]):.4f}",
        )
        self.assertEqual(
            r3.cell(text, "## hello", "baseline", "cost (passed)"), f"${statistics.median(self.FINISHED_COSTS):.4f}"
        )
        self.assertEqual(r3.cell(text, "## hello", "baseline", "passed / runs"), "4 / 5")
        self.assertRegex(text.split("##")[0], r"(?i)lower bound")


# -- 6. the report ----------------------------------------------------------------------------------------


SIMPLE_PRICES = r3.SIMPLE_PRICES


def finished(
    task: str, arm: str, cost: float, *, round: int = 1, k: int | None = None, passed: bool = True
) -> dict[str, Any]:
    return r3.run(task, arm, cost, k=k, round=round, passed=passed)


def unfinished(
    task: str,
    arm: str,
    *,
    round: int = 9,
    estimated: float | None = None,
    partial_k: int | None = None,
    isolation_ok: bool = True,
) -> dict[str, Any]:
    """A run that timed out: no cost, no tokens, and what round 4 records for it."""
    record = uf.usage_record({}, task=task, arm=arm, round=round, passed=False, isolation_ok=isolation_ok, cost=None)
    for key in (
        "input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
        "num_turns",
        "duration_ms",
    ):
        record[key] = None
    record.update(timed_out=True, error="timed out after 600 s", model_usage={})
    if partial_k is not None:
        tokens = {"input": partial_k, "cache_write": 2 * partial_k, "cache_read": 4 * partial_k, "output": partial_k}
        record["partial_model_usage"] = {MODEL: partial_entry(tokens)}
    if estimated is not None:
        record["estimated_cost_usd"] = estimated
    return record


class CostAllWithEstimatesTest(unittest.TestCase):
    def row(
        self, records: list[dict[str, Any]], arm: str = "baseline", task: str = "gif", prices: Any = ...
    ) -> dict[str, Any]:
        rows = summarize(records, SIMPLE_PRICES if prices is ... else prices)
        [found] = [r for r in rows if r["task"] == task and r["arm"] == arm]
        return found

    def test_an_unfinished_run_counts_at_its_estimated_cost(self) -> None:
        records = [
            finished("gif", "baseline", 1.0),
            finished("gif", "baseline", 3.0, round=2),
            unfinished("gif", "baseline", estimated=10.0),
        ]
        row = self.row(records)
        self.assertEqual(row["median_cost_usd"], 3.0, "median of 1, 3 and 10; without the estimate it would be 2")
        self.assertEqual(row["runs"], 3)

    def test_without_an_estimate_it_is_estimated_from_the_partial_usage_with_the_prices_of_the_summary(self) -> None:
        # k = 2 tokens-units cost 22 at SIMPLE prices (see report_round2)
        records = [
            finished("gif", "baseline", 1.0),
            finished("gif", "baseline", 5.0, round=2),
            unfinished("gif", "baseline", partial_k=2),
        ]
        self.assertEqual(self.row(records)["median_cost_usd"], 5.0, "median of 1, 5 and 22")
        self.assertEqual(self.row(records, prices={})["median_cost_usd"], 3.0, "no prices: nothing to estimate with")

    def test_the_fitted_prices_are_used_when_the_summary_is_given_none(self) -> None:
        fitted = uf.fit_records(task="gif", arm="baseline")
        row_tokens = uf.tokens_1h(uf.ROWS_4[0])
        partial = unfinished("gif", "baseline")
        partial["partial_model_usage"] = {MODEL: partial_entry({**row_tokens, "output": 5000})}
        expected = uf.cost_of({**row_tokens, "output": 5000}, uf.PRICES_1H)
        costs = [r["total_cost_usd"] for r in fitted]
        [row] = [r for r in summarize([*fitted, partial]) if r["arm"] == "baseline"]
        self.assertAlmostEqual(row["median_cost_usd"], statistics.median([*costs, expected]), places=9)
        self.assertNotAlmostEqual(row["median_cost_usd"], statistics.median(costs), places=6)

    def test_an_estimated_cost_wins_over_the_partial_usage(self) -> None:
        records = [
            finished("gif", "baseline", 1.0),
            finished("gif", "baseline", 9.0, round=2),
            unfinished("gif", "baseline", estimated=7.0, partial_k=2),
        ]
        self.assertEqual(self.row(records)["median_cost_usd"], 7.0, "not 9 (which 22 would give)")

    def test_a_run_with_nothing_to_go_by_stays_out_of_cost_all_as_before(self) -> None:
        old_timeout = unfinished("gif", "baseline")  # no partial usage, no estimate: a record of an earlier round
        self.assertNotIn("partial_model_usage", old_timeout)
        records = [finished("gif", "baseline", 1.0), finished("gif", "baseline", 3.0, round=2), old_timeout]
        row = self.row(records)
        self.assertEqual(row["median_cost_usd"], 2.0)
        self.assertEqual(row["runs"], 3)

    def test_a_run_that_failed_isolation_is_still_left_out(self) -> None:
        records = [
            finished("gif", "baseline", 1.0),
            finished("gif", "baseline", 3.0, round=2),
            unfinished("gif", "baseline", estimated=10.0, isolation_ok=False),
        ]
        self.assertEqual(self.row(records)["median_cost_usd"], 2.0)

    def test_the_total_of_a_finished_run_is_used_as_before_whatever_else_it_has(self) -> None:
        record = finished("gif", "baseline", 4.0)
        record["estimated_cost_usd"] = 99.0
        self.assertEqual(self.row([record])["median_cost_usd"], 4.0)

    def test_the_table_shows_it_in_the_cost_all_column(self) -> None:
        records = [
            finished("gif", "baseline", 1.0),
            finished("gif", "baseline", 3.0, round=2),
            unfinished("gif", "baseline", estimated=10.0),
        ]
        text = render_markdown(summarize(records, SIMPLE_PRICES), None, SIMPLE_PRICES)
        self.assertEqual(r3.cell(text, "## gif", "baseline", "cost (all)"), "$3.0000")
        self.assertEqual(r3.cell(text, "## gif", "baseline", "cost (passed)"), "$2.0000")
        self.assertEqual(r3.cell(text, "## gif", "baseline", "passed / runs"), "2 / 3")


class CostPassedIsUntouchedTest(unittest.TestCase):
    """Estimates change "cost (all)" and nothing derived from "cost (passed)"."""

    def base(self) -> list[dict[str, Any]]:
        return [
            finished("hello", "baseline", 1.0, k=1),
            finished("hello", "skill", 1.5, k=1),
            finished("gif", "baseline", 4.0, k=1),
            finished("gif", "baseline", 5.0, k=1, round=2),
            finished("gif", "skill", 3.0, k=2),
            finished("gif", "skill", 9.0, k=2, round=2, passed=False),
        ]

    def with_estimates(self) -> list[dict[str, Any]]:
        return [
            *self.base(),
            unfinished("gif", "skill", estimated=20.0, round=3),
            unfinished("gif", "baseline", partial_k=3, round=3),
            unfinished("hello", "baseline", estimated=9.0, round=3),
        ]

    # the figures of a row that round 3 wrote; a row may gain keys of its own in round 4, these may not change
    OLD_KEYS = (
        "machine task arm passed isolation_failures used_jzr median_cost_passed_usd min_cost_passed_usd "
        "max_cost_passed_usd cost_vs_baseline cost_minus_hello_usd minus_hello_vs_baseline median_duration_s "
        "median_input_tokens median_cache_creation_input_tokens median_cache_read_input_tokens median_output_tokens "
        "median_turns median_written_usd median_reread_usd median_output_usd"
    ).split()

    def test_every_figure_but_the_runs_and_cost_all_is_the_same(self) -> None:
        plain = summarize(self.base(), SIMPLE_PRICES)
        counted = summarize(self.with_estimates(), SIMPLE_PRICES)
        self.assertEqual([(r["task"], r["arm"]) for r in plain], [(r["task"], r["arm"]) for r in counted])
        self.assertGreater(sum(r["runs"] for r in counted), sum(r["runs"] for r in plain))
        for before, after in zip(plain, counted, strict=True):
            with self.subTest(task=after["task"], arm=after["arm"]):
                self.assertEqual(set(self.OLD_KEYS) - set(before), set(), "OLD_KEYS must be keys of a row")
                self.assertEqual({k: before[k] for k in self.OLD_KEYS}, {k: after[k] for k in self.OLD_KEYS})

    def test_cost_all_does_change_where_there_is_an_estimate(self) -> None:
        plain = {(r["task"], r["arm"]): r for r in summarize(self.base(), SIMPLE_PRICES)}
        counted = {(r["task"], r["arm"]): r for r in summarize(self.with_estimates(), SIMPLE_PRICES)}
        for key in (("gif", "skill"), ("gif", "baseline"), ("hello", "baseline")):
            self.assertNotEqual(plain[key]["median_cost_usd"], counted[key]["median_cost_usd"], key)
        self.assertEqual(plain[("hello", "skill")]["median_cost_usd"], counted[("hello", "skill")]["median_cost_usd"])

    def test_the_all_tasks_sums_are_the_same(self) -> None:
        plain = overall(summarize(self.base(), SIMPLE_PRICES))
        counted = overall(summarize(self.with_estimates(), SIMPLE_PRICES))
        self.assertTrue(plain)
        self.assertEqual(plain, counted)

    def test_the_rendered_all_tasks_table_and_the_passed_columns_are_the_same(self) -> None:
        plain = render_markdown(summarize(self.base(), SIMPLE_PRICES), None, SIMPLE_PRICES)
        counted = render_markdown(summarize(self.with_estimates(), SIMPLE_PRICES), None, SIMPLE_PRICES)
        self.assertEqual(r3.section(plain, "## All tasks"), r3.section(counted, "## All tasks"))
        for heading in ("## hello", "## gif"):
            for arm in ("baseline", "skill"):
                for column in (
                    "cost (passed)",
                    "range",
                    "vs baseline",
                    "minus hello",
                    "minus hello vs baseline",
                    "written $",
                    "re-read $",
                    "output $",
                ):
                    with self.subTest(heading=heading, arm=arm, column=column):
                        self.assertEqual(r3.cell(plain, heading, arm, column), r3.cell(counted, heading, arm, column))

    def test_a_passed_run_never_borrows_an_estimate(self) -> None:
        records = [*self.base(), unfinished("gif", "skill", estimated=1000.0, round=3)]
        [row] = [r for r in summarize(records, SIMPLE_PRICES) if r["task"] == "gif" and r["arm"] == "skill"]
        self.assertEqual(row["median_cost_passed_usd"], 3.0)
        self.assertEqual((row["min_cost_passed_usd"], row["max_cost_passed_usd"]), (3.0, 3.0))
        self.assertEqual(row["passed"], 1)


class LowerBoundNoteTest(unittest.TestCase):
    def notes(self, text: str) -> str:
        """Everything before the first heading under the title: the meta lines and the explanation."""
        return text.split("\n## ")[0]

    def test_the_notes_say_that_cost_all_counts_unfinished_runs_at_a_lower_bound(self) -> None:
        records = [finished("gif", "baseline", 1.0), unfinished("gif", "baseline", estimated=10.0)]
        text = render_markdown(summarize(records, SIMPLE_PRICES), None, SIMPLE_PRICES)
        notes = self.notes(text)
        self.assertRegex(notes, r"(?i)lower bound")
        [paragraph] = [p for p in notes.split("\n\n") if "lower bound" in p.lower()]
        self.assertIn("cost (all)", paragraph)

    def test_it_is_also_said_when_the_estimate_comes_from_the_partial_usage(self) -> None:
        records = [finished("gif", "baseline", 1.0), unfinished("gif", "baseline", partial_k=2)]
        text = render_markdown(summarize(records, SIMPLE_PRICES), None, SIMPLE_PRICES)
        self.assertRegex(self.notes(text), r"(?i)lower bound")

    def test_old_records_get_the_old_text_without_it(self) -> None:
        records = [
            finished("gif", "baseline", 1.0),
            finished("gif", "baseline", 2.0, round=2),
            unfinished("gif", "baseline"),
        ]
        text = render_markdown(summarize(records, SIMPLE_PRICES), {"model": "sonnet"}, SIMPLE_PRICES)
        self.assertNotRegex(text, r"(?i)lower bound")

    def test_it_is_not_in_the_summary_of_a_folder_without_unfinished_runs(self) -> None:
        text = render_markdown(summarize([finished("gif", "baseline", 1.0)], SIMPLE_PRICES), None, SIMPLE_PRICES)
        self.assertNotRegex(text, r"(?i)lower bound")


class FolderSummaryWithEstimatesTest(trc.FolderTestCase):
    def test_write_summary_prices_a_partial_usage_with_the_prices_fitted_from_the_folder(self) -> None:
        fitted = uf.fit_records(task="gif", arm="baseline")
        tokens = uf.tokens_1h((4, 9000, 40000, 3000))
        hung = unfinished("gif", "baseline")
        hung["partial_model_usage"] = {MODEL: partial_entry(tokens)}
        folder = self.folder("run-a", [*fitted, hung], meta={"model": "sonnet"})
        text = write_summary(folder).read_text()
        costs = [r["total_cost_usd"] for r in fitted]
        expected = statistics.median([*costs, uf.cost_of(tokens, uf.PRICES_1H)])
        self.assertEqual(r3.cell(text, "## gif", "baseline", "cost (all)"), f"${expected:.4f}")
        self.assertNotEqual(
            f"${expected:.4f}", f"${statistics.median(costs):.4f}", "the fixture must make a difference"
        )
        self.assertRegex(text, r"(?i)lower bound")


# -- 7. several folders, per machine -----------------------------------------------------------------------


def machine_run(task: str, arm: str, cost: float, machine: Any, *, round: int = 1, src: str = "") -> dict[str, Any]:
    record = trc.run(task, arm, cost, round=round, src=src)
    if machine is not None:  # None: an old record, without the key
        record["machine"] = machine
        if machine == "bare":
            record["run_id"] += "-bare"
    return record


class CombinePerMachineTest(trc.FolderTestCase):
    def ids(self, records: list[dict]) -> list[tuple[str, str, str, str]]:
        return sorted((r.get("machine", "full"), r["task"], r["arm"], r["src"]) for r in records)

    def test_the_full_runs_of_a_task_and_its_bare_runs_may_come_from_different_folders(self) -> None:
        a = self.folder(
            "20261126T131420Z",
            [machine_run("gif", "baseline", 1.0, "full", src="a"), machine_run("gif", "skill", 1.1, "full", src="a")],
        )
        b = self.folder(
            "20261127T151425Z",
            [machine_run("gif", "baseline", 2.0, "bare", src="b"), machine_run("gif", "skill", 2.1, "bare", src="b")],
        )
        records, sources = combine_records([a, b])
        self.assertEqual(
            self.ids(records),
            [
                ("bare", "gif", "baseline", "b"),
                ("bare", "gif", "skill", "b"),
                ("full", "gif", "baseline", "a"),
                ("full", "gif", "skill", "a"),
            ],
        )
        self.assertEqual(sources, {"gif": a.name, "gif, bare machine": b.name})

    def test_a_later_folder_with_only_the_bare_runs_of_a_task_does_not_drop_the_earlier_full_runs(self) -> None:
        a = self.folder(
            "20261126T131420Z",
            [
                machine_run("hello", "baseline", 0.5, "full", src="a"),
                machine_run("gif", "baseline", 1.0, "full", src="a"),
                machine_run("gif", "baseline", 1.2, "full", round=2, src="a"),
                machine_run("gif", "baseline", 3.0, "bare", src="a"),
            ],
        )
        b = self.folder("20261127T151425Z", [machine_run("gif", "baseline", 4.0, "bare", src="b")])
        records, sources = combine_records([a, b])
        self.assertEqual(
            self.ids(records),
            [
                ("bare", "gif", "baseline", "b"),  # replaces the bare gif of a
                ("full", "gif", "baseline", "a"),
                ("full", "gif", "baseline", "a"),
                ("full", "hello", "baseline", "a"),
            ],
        )
        self.assertEqual(sources, {"hello": a.name, "gif": a.name, "gif, bare machine": b.name})

    def test_the_last_folder_that_has_a_machine_and_task_gives_all_of_it_and_the_earlier_gives_none(self) -> None:
        a = self.folder(
            "a1",
            [machine_run("gif", "baseline", 1.0, "bare", src="a"), machine_run("gif", "skill", 1.1, "bare", src="a")],
        )
        b = self.folder(
            "b1",
            [
                machine_run("gif", "baseline", 2.0, "bare", src="b"),
                machine_run("gif", "baseline", 2.5, "bare", round=2, src="b"),
            ],
        )
        records, sources = combine_records([a, b])
        self.assertEqual(self.ids(records), [("bare", "gif", "baseline", "b"), ("bare", "gif", "baseline", "b")])
        self.assertEqual(sources, {"gif, bare machine": b.name})

    def test_the_order_of_the_folders_decides_per_machine(self) -> None:
        a = self.folder(
            "a2",
            [
                machine_run("gif", "baseline", 1.0, "full", src="a"),
                machine_run("gif", "baseline", 3.0, "bare", src="a"),
            ],
        )
        b = self.folder("b2", [machine_run("gif", "baseline", 2.0, "full", src="b")])
        records, sources = combine_records([b, a])
        self.assertEqual(self.ids(records), [("bare", "gif", "baseline", "a"), ("full", "gif", "baseline", "a")])
        self.assertEqual(sources, {"gif": a.name, "gif, bare machine": a.name})
        records, sources = combine_records([a, b])
        self.assertEqual(self.ids(records), [("bare", "gif", "baseline", "a"), ("full", "gif", "baseline", "b")])
        self.assertEqual(sources, {"gif": b.name, "gif, bare machine": a.name})

    def test_old_records_without_a_machine_are_full_ones(self) -> None:
        a = self.folder(
            "old1",
            [machine_run("gif", "baseline", 1.0, None, src="a"), machine_run("hello", "baseline", 0.5, None, src="a")],
        )
        b = self.folder("new1", [machine_run("gif", "baseline", 2.0, "bare", src="b")])
        records, sources = combine_records([a, b])
        self.assertEqual(
            sorted((r["task"], r.get("machine") or "", r["src"]) for r in records),
            [("gif", "", "a"), ("gif", "bare", "b"), ("hello", "", "a")],
        )
        self.assertEqual(sources, {"hello": a.name, "gif": a.name, "gif, bare machine": b.name})

    def test_a_later_full_folder_replaces_an_old_record_without_a_machine(self) -> None:
        a = self.folder("old2", [machine_run("gif", "baseline", 1.0, None, src="a")])
        b = self.folder("new2", [machine_run("gif", "baseline", 2.0, "full", src="b")])
        records, sources = combine_records([a, b])
        self.assertEqual([r["src"] for r in records], ["b"])
        self.assertEqual(sources, {"gif": b.name})

    def test_a_folder_of_old_records_only_keeps_the_keys_there_have_always_been(self) -> None:
        a = self.folder(
            "old3",
            [machine_run("gif", "baseline", 1.0, None, src="a"), machine_run("hello", "skill", 0.5, None, src="a")],
        )
        b = self.folder("old4", [machine_run("gif", "baseline", 2.0, None, src="b")])
        records, sources = combine_records([a, b])
        self.assertEqual(sources, {"hello": a.name, "gif": b.name})
        self.assertFalse([k for r in records for k in r if k == "machine"], "records are returned as written")
        self.assertFalse([k for k in sources if "bare" in k])

    def test_both_machines_of_one_folder_stay_together(self) -> None:
        a = self.folder(
            "both",
            [
                machine_run("gif", "baseline", 1.0, "full", src="a"),
                machine_run("gif", "baseline", 2.0, "bare", src="a"),
            ],
        )
        records, sources = combine_records([a])
        self.assertEqual(len(records), 2)
        self.assertEqual(sources, {"gif": a.name, "gif, bare machine": a.name})

    def test_sources_name_the_folder_not_its_path(self) -> None:
        a = self.folder("p1", [machine_run("gif", "baseline", 1.0, "bare", src="a")])
        _, sources = combine_records([a])
        self.assertEqual(sources, {"gif, bare machine": "p1"})


class ReportSeveralFoldersPerMachineTest(trc.FolderTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = self.folder(
            "20261126T131420Z",
            [
                machine_run("hello", "baseline", 0.5, "full"),
                machine_run("hello", "skill", 0.6, "full"),
                machine_run("gif", "baseline", 1.0, "full"),
                machine_run("gif", "skill", 1.1, "full"),
            ],
        )
        self.b = self.folder(
            "20261127T151425Z",
            [machine_run("gif", "baseline", 2.0, "bare"), machine_run("gif", "skill", 1.5, "bare")],
        )

    def report(self, *args: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(["report", *args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_the_source_lines_are_the_ones_the_spec_gives(self) -> None:
        code, out, err = self.report(str(self.a), str(self.b))
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertIn(f"- gif: runs from {self.a.name}", lines)
        self.assertIn(f"- gif, bare machine: runs from {self.b.name}", lines)
        self.assertIn(f"- hello: runs from {self.a.name}", lines)
        self.assertEqual(len([ln for ln in lines if "runs from" in ln]), 3)

    def test_both_machines_of_gif_are_in_the_summary_with_their_own_costs(self) -> None:
        code, out, err = self.report(str(self.a), str(self.b))
        self.assertEqual(code, 0, err)
        self.assertEqual(r3.cell(out, "## gif", "baseline", "cost (passed)"), "$1.0000")
        self.assertEqual(r3.cell(out, "## gif", "skill", "cost (passed)"), "$1.1000")
        self.assertEqual(r3.cell(out, "## gif, bare machine", "baseline", "cost (passed)"), "$2.0000")
        self.assertEqual(r3.cell(out, "## gif, bare machine", "skill", "cost (passed)"), "$1.5000")
        self.assertNotIn("## hello, bare machine", r3.headings(out))

    def test_combined_summary_gives_the_same_text(self) -> None:
        _, out, _ = self.report(str(self.a), str(self.b))
        self.assertEqual(combined_summary([self.a, self.b]).strip(), out.strip())

    def test_the_folders_are_not_written_into(self) -> None:
        before = (sorted(p.name for p in self.a.iterdir()), sorted(p.name for p in self.b.iterdir()))
        self.report(str(self.a), str(self.b))
        self.assertEqual((sorted(p.name for p in self.a.iterdir()), sorted(p.name for p in self.b.iterdir())), before)

    def test_folders_without_a_machine_are_reported_as_before(self) -> None:
        old_a = self.folder(
            "old-a", [machine_run("hello", "baseline", 0.5, None), machine_run("gif", "baseline", 1.0, None)]
        )
        old_b = self.folder("old-b", [machine_run("gif", "baseline", 2.0, None)])
        code, out, err = self.report(str(old_a), str(old_b))
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertIn("- hello: runs from old-a", lines)
        self.assertIn("- gif: runs from old-b", lines)
        self.assertNotIn("bare", out.lower())
        self.assertEqual(r3.cell(out, "## gif", "baseline", "cost (passed)"), "$2.0000")


if __name__ == "__main__":
    unittest.main()
