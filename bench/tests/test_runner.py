"""Offline tests for jzr_bench.runner.run_one using fake `claude` executables.

The fake claude is a /bin/sh script that records how it was started (argv, cwd, environment, stdin,
files in its working folder) and prints a canned stream-json transcript. Nothing here starts the real
claude, touches the network or reads ~/.claude.
"""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import stream_fixtures as fx  # noqa: E402
from jzr_bench import arms as arms_module  # noqa: E402
from jzr_bench.arms import SYSTEM_PATH, ArmSetup, Auth, JzrSource, find_jzr_on, prepare_arm  # noqa: E402
from jzr_bench.cli import build_parser  # noqa: E402
from jzr_bench.plan import PlannedRun  # noqa: E402
from jzr_bench import runner as runner_module  # noqa: E402
from jzr_bench.runner import REPO, run_all, run_one  # noqa: E402
from jzr_bench.tasks import get_task  # noqa: E402
from jzr_bench.tasks.base import CheckResult, Task  # noqa: E402

SHELL_EXTRAS = {"PWD", "OLDPWD", "SHLVL", "_"}  # added by /bin/sh itself
HELLO_PROMPT = get_task("hello").prompt


def process_alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


class RunnerCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.rec = self.tmp / "rec"  # what the fake claude writes about itself
        self.rec.mkdir()

        jzr_bin = self.tmp / "repo" / "bin" / "jzr"
        jzr_bin.parent.mkdir(parents=True)
        jzr_bin.write_text("#!/bin/sh\nexit 0\n")
        jzr_bin.chmod(0o755)
        skills_dir = self.tmp / "repo" / "skill"
        for name in fx.JZR_SKILLS:
            (skills_dir / name).mkdir(parents=True)
            (skills_dir / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        self.source = JzrSource(
            jzr_bin=jzr_bin,
            skills_dir=skills_dir,
            mcp_command=[str(jzr_bin), "agent", "mcp"],
        )
        self.system_path = str(self.tmp / "syspath")
        (self.tmp / "syspath").mkdir()
        self.fixture_dir = self.tmp / "fixtures"
        self.fixture_dir.mkdir()
        self.transcript = self.tmp / "transcripts" / "run.jsonl"
        self.transcript.parent.mkdir()
        self.check_calls: list[tuple[Path, Path, str]] = []

    # -- helpers ------------------------------------------------------------------------------

    def arm_setup(self, arm: str, *, fresh: bool = False) -> ArmSetup:
        root = self.tmp / f"sandbox-{arm}"
        if fresh:  # a second run in the same test: start from an empty sandbox folder again
            shutil.rmtree(root, ignore_errors=True)
        root.mkdir()
        return prepare_arm(
            arm,
            root,
            self.source,
            Auth("CLAUDE_CODE_OAUTH_TOKEN", "tok-123"),
            model="sonnet",
            max_turns=12,
            system_path=self.system_path,
        )

    def canned(self, *events: dict[str, Any]) -> Path:
        path = self.tmp / "canned.jsonl"
        path.write_text("\n".join(fx.to_lines(*events)) + "\n")
        return path

    def fake_claude(self, body: str) -> str:
        """A fake claude. $REC is the record folder; the body runs after the standard recording."""
        script = self.tmp / "fake-claude"
        script.write_text(
            "#!/bin/sh\n"
            f"REC='{self.rec}'\n"
            '/usr/bin/env > "$REC/env.txt"\n'
            'pwd > "$REC/cwd.txt"\n'
            'printf \'%s\\n\' "$@" > "$REC/argv.txt"\n'
            '/usr/bin/ls -1 > "$REC/ls.txt"\n'
            '/usr/bin/readlink /proc/$$/fd/0 > "$REC/stdin_target.txt"\n'
            '/usr/bin/cat > "$REC/stdin.txt"\n'
            f"{body}\n"
        )
        script.chmod(0o755)
        return str(script)

    def printing_claude(self, *events: dict[str, Any]) -> str:
        return self.fake_claude(f"/usr/bin/cat '{self.canned(*events)}'\nexit 0")

    def task(self, name: str = "probe", *, passes: bool = True) -> Task:
        def prepare(fixture_dir: Path) -> None:
            (fixture_dir / "data.txt").write_text("fixture\n")

        def check(work: Path, fixtures: Path, text: str) -> CheckResult:
            self.check_calls.append((work, fixtures, text))
            return CheckResult(passes, ["data.txt ok"] if passes else ["data.txt wrong"])

        return Task(
            name=name,
            prompt="do the probe thing",
            prepare_fixtures=prepare,
            check=check,
        )

    def run_it(
        self,
        claude_bin: str,
        *,
        arm: str = "baseline",
        task: Task | None = None,
        timeout_s: float = 20,
        fresh: bool = False,
    ) -> tuple[dict[str, Any], ArmSetup]:
        task = task or self.task()
        setup = self.arm_setup(arm, fresh=fresh)
        planned = PlannedRun(run_id=f"r02-{task.name}-{arm}", round=2, task=task.name, arm=arm)
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

    def recorded(self, name: str) -> str:
        return (self.rec / name).read_text()

    def child_env(self) -> dict[str, str]:
        return dict(line.split("=", 1) for line in self.recorded("env.txt").splitlines() if "=" in line)


class SuccessfulRunTest(RunnerCase):
    def good_events(self, arm: str = "baseline", text: str = "ok") -> list[dict[str, Any]]:
        return [
            fx.init_for_arm(arm),
            fx.assistant_event(fx.bash("echo hi")),
            fx.user_tool_result("hi"),
            fx.result_event(
                text=text,
                cost=0.0421,
                input_tokens=12,
                cache_creation=3456,
                cache_read=78901,
                output_tokens=234,
                num_turns=4,
                duration_ms=9876,
            ),
        ]

    def test_record_of_a_passing_run(self) -> None:
        record, _ = self.run_it(self.printing_claude(*self.good_events()))
        self.assertEqual((record["task"], record["arm"], record["round"]), ("probe", "baseline", 2))
        self.assertIs(record["passed"], True)
        self.assertIs(record["isolation_ok"], True)
        self.assertIs(record["timed_out"], False)
        self.assertFalse(record.get("error"))
        self.assertAlmostEqual(record["total_cost_usd"], 0.0421)
        self.assertEqual(record["input_tokens"], 12)
        self.assertEqual(record["cache_creation_input_tokens"], 3456)
        self.assertEqual(record["cache_read_input_tokens"], 78901)
        self.assertEqual(record["output_tokens"], 234)
        self.assertEqual(record["num_turns"], 4)
        self.assertEqual(record["duration_ms"], 9876)
        self.assertIs(record["used_jzr_cli"], False)
        self.assertIs(record["used_jzr_skill"], False)
        self.assertIs(record["used_jzr_mcp"], False)

    def test_record_is_json_serialisable(self) -> None:
        record, _ = self.run_it(self.printing_claude(*self.good_events()))
        json.dumps(record)

    def test_transcript_is_the_raw_stdout(self) -> None:
        events = self.good_events()
        self.run_it(self.printing_claude(*events))
        written = [json.loads(line) for line in self.transcript.read_text().splitlines() if line.strip()]
        self.assertEqual(written, events)

    def test_the_check_runs_once_with_work_dir_fixture_dir_and_final_text(self) -> None:
        _, setup = self.run_it(self.printing_claude(*self.good_events(text="all done")))
        self.assertEqual(len(self.check_calls), 1)
        work, fixtures, text = self.check_calls[0]
        self.assertEqual(Path(work).resolve(), setup.sandbox.work.resolve())
        self.assertEqual(Path(fixtures).resolve(), self.fixture_dir.resolve())
        self.assertEqual(text, "all done")

    def test_real_hello_task_end_to_end(self) -> None:
        record, _ = self.run_it(self.printing_claude(*self.good_events(text="ok")), task=get_task("hello"))
        self.assertEqual(record["task"], "hello")
        self.assertIs(record["passed"], True)
        self.assertIs(record["isolation_ok"], True)

    def test_failing_check_gives_passed_false_but_isolation_ok(self) -> None:
        record, _ = self.run_it(self.printing_claude(*self.good_events()), task=self.task(passes=False))
        self.assertIs(record["passed"], False)
        self.assertIs(record["isolation_ok"], True)
        self.assertIs(record["timed_out"], False)
        self.assertEqual(len(self.check_calls), 1)
        self.assertAlmostEqual(record["total_cost_usd"], 0.0421)  # metrics are kept for failed runs

    def test_real_hello_task_fails_on_a_wrong_answer(self) -> None:
        record, _ = self.run_it(
            self.printing_claude(*self.good_events(text="no idea")),
            task=get_task("hello"),
        )
        self.assertIs(record["passed"], False)
        self.assertIs(record["isolation_ok"], True)

    def test_cli_use_is_detected_from_bash_commands(self) -> None:
        events = [
            fx.init_for_arm("skill"),
            fx.assistant_event(fx.tool_use("Skill", skill="jzr-media"), fx.bash("jzr video gif clip.mp4")),
            fx.result_event(),
        ]
        record, _ = self.run_it(self.printing_claude(*events), arm="skill")
        self.assertIs(record["used_jzr_cli"], True)
        self.assertIs(record["used_jzr_skill"], True)
        self.assertIs(record["used_jzr_mcp"], False)
        self.assertEqual(record["skills_invoked"], ["jzr-media"])
        self.assertIs(record["isolation_ok"], True)
        self.assertEqual(record["isolation_problems"], [])

    def test_mcp_use_is_detected(self) -> None:
        events = [
            fx.init_for_arm("mcp"),
            fx.assistant_event(fx.tool_use("mcp__jzr__video_gif", path="clip.mp4")),
            fx.result_event(),
        ]
        record, _ = self.run_it(self.printing_claude(*events), arm="mcp")
        self.assertIs(record["used_jzr_mcp"], True)
        self.assertIs(record["used_jzr_cli"], False)
        self.assertIs(record["isolation_ok"], True)

    def test_skill_arm_without_a_skill_call_does_not_count_skill_use(self) -> None:
        events = [
            fx.init_for_arm("skill"),
            fx.assistant_event(fx.bash("ffmpeg -i a.mp4 a.gif")),
            fx.result_event(),
        ]
        record, _ = self.run_it(self.printing_claude(*events), arm="skill")
        self.assertIs(record["isolation_ok"], True)
        self.assertIs(record["used_jzr_skill"], False)
        self.assertIs(record["used_jzr_cli"], False)

    def test_each_of_the_four_skills_counts_as_skill_use_in_the_record(self) -> None:
        for name in fx.JZR_SKILLS:
            with self.subTest(skill=name):
                events = [
                    fx.init_for_arm("skill"),
                    fx.assistant_event(fx.tool_use("Skill", skill=name)),
                    fx.result_event(),
                ]
                record, _ = self.run_it(self.printing_claude(*events), arm="skill", fresh=True)
                self.assertIs(record["used_jzr_skill"], True)
                self.assertEqual(record["skills_invoked"], [name])

    def test_skill_arm_run_with_the_old_single_skill_init_fails_isolation(self) -> None:
        init = fx.init_event(skills=["jzr", *fx.OTHER_SKILLS])
        record, _ = self.run_it(self.printing_claude(init, fx.result_event(cost=0.25)), arm="skill")
        self.assertIs(record["isolation_ok"], False)
        self.assertTrue(record["isolation_problems"])
        self.assertFalse(any("unknown arm" in p for p in record["isolation_problems"]))
        self.assertAlmostEqual(record["total_cost_usd"], 0.25)

    def test_skill_arm_run_missing_one_skill_fails_isolation_and_names_it(self) -> None:
        init = fx.init_event(skills=[*fx.JZR_SKILLS[:3], *fx.OTHER_SKILLS])
        record, _ = self.run_it(self.printing_claude(init, fx.result_event()), arm="skill")
        self.assertIs(record["isolation_ok"], False)
        self.assertTrue(any(fx.JZR_SKILLS[3] in p for p in record["isolation_problems"]), record["isolation_problems"])

    def test_skill_arm_run_with_the_baseline_init_fails_isolation(self) -> None:
        record, _ = self.run_it(self.printing_claude(fx.init_for_arm("baseline"), fx.result_event()), arm="skill")
        self.assertIs(record["isolation_ok"], False)

    def test_a_baseline_run_that_sees_one_of_the_four_skills_fails_isolation(self) -> None:
        init = fx.init_event(skills=["jzr-pdf", *fx.OTHER_SKILLS])
        record, _ = self.run_it(self.printing_claude(init, fx.result_event()), arm="baseline")
        self.assertIs(record["isolation_ok"], False)

    def test_an_mcp_run_that_sees_one_of_the_four_skills_fails_isolation(self) -> None:
        good = fx.init_for_arm("mcp")
        init = dict(good, skills=[*good["skills"], "jzr-data"])
        record, _ = self.run_it(self.printing_claude(init, fx.result_event()), arm="mcp")
        self.assertIs(record["isolation_ok"], False)


class ChildProcessContractTest(RunnerCase):
    def run_recording(self, **kwargs: Any) -> ArmSetup:
        _, setup = self.run_it(
            self.printing_claude(fx.init_for_arm(kwargs.get("arm", "baseline")), fx.result_event()),
            **kwargs,
        )
        return setup

    def test_command_line_is_claude_p_prompt_then_the_arm_arguments(self) -> None:
        setup = self.run_recording()
        argv = self.recorded("argv.txt").splitlines()
        self.assertEqual(argv, ["-p", "do the probe thing", *setup.claude_args])

    def test_mcp_arm_command_line_has_the_mcp_config(self) -> None:
        setup = self.run_recording(arm="mcp")
        argv = self.recorded("argv.txt").splitlines()
        self.assertEqual(argv[:2], ["-p", "do the probe thing"])
        self.assertEqual(argv[2:], setup.claude_args)
        self.assertIn("--mcp-config", argv)
        self.assertIn("--strict-mcp-config", argv)

    def test_skill_arm_command_line_has_no_mcp_config_and_the_child_has_the_shim_path(
        self,
    ) -> None:
        setup = self.run_recording(arm="skill")
        argv = self.recorded("argv.txt").splitlines()
        self.assertEqual(argv, ["-p", "do the probe thing", *setup.claude_args])
        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("--mcp-config", argv)
        self.assertEqual(self.child_env()["PATH"], f"{setup.sandbox.bin}:{self.system_path}")
        skills = setup.sandbox.config_dir / "skills"
        self.assertEqual({p.name for p in skills.iterdir()}, set(fx.JZR_SKILLS))

    def test_working_folder_is_the_sandbox_work_dir_with_the_fixtures(self) -> None:
        setup = self.run_recording()
        self.assertEqual(
            os.path.realpath(self.recorded("cwd.txt").strip()),
            str(setup.sandbox.work.resolve()),
        )
        self.assertEqual(self.recorded("ls.txt").split(), ["data.txt"])
        self.assertEqual((setup.sandbox.work / "data.txt").read_text(), "fixture\n")

    def test_child_gets_exactly_the_arm_environment(self) -> None:
        polluted = {
            "VIRTUAL_ENV": "/leak/venv",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "leak",
            "ANTHROPIC_API_KEY": "sk-leak",
            "BENCH_SENTINEL": "sentinel",
        }
        with mock.patch.dict(os.environ, polluted):
            setup = self.run_recording()
        env = self.child_env()
        for key in polluted:
            self.assertNotIn(key, env)
        self.assertEqual(set(env) - SHELL_EXTRAS, set(setup.env))
        for key, value in setup.env.items():
            self.assertEqual(env[key], value, key)

    @unittest.skipUnless(Path("/proc/self/fd/0").exists(), "needs /proc to inspect the child's stdin")
    def test_stdin_is_dev_null(self) -> None:
        self.run_recording()
        self.assertEqual(self.recorded("stdin_target.txt").strip(), os.devnull)
        self.assertEqual(self.recorded("stdin.txt"), "")


class FailedRunTest(RunnerCase):
    def test_error_result_is_not_a_pass_and_skips_the_check(self) -> None:
        events = [
            fx.init_for_arm("baseline"),
            fx.result_event(
                text="Invalid API key",
                is_error=True,
                cost=0,
                num_turns=1,
                duration_ms=455,
                model_usage={},
            ),
        ]
        record, _ = self.run_it(self.printing_claude(*events))
        self.assertIs(record["passed"], False)
        self.assertIs(record["isolation_ok"], True)
        self.assertIs(record["timed_out"], False)
        self.assertEqual(
            self.check_calls,
            [],
            "the check must only run when the agent finished without error",
        )

    def test_no_result_event_skips_the_check_and_keeps_the_stderr_tail(self) -> None:
        body = f"/usr/bin/cat '{self.canned(fx.init_for_arm('baseline'))}'\necho 'boom: something broke' >&2\nexit 3"
        record, _ = self.run_it(self.fake_claude(body))
        self.assertIs(record["passed"], False)
        self.assertIs(record["timed_out"], False)
        self.assertEqual(self.check_calls, [])
        self.assertIsNone(record["total_cost_usd"])
        self.assertIsNone(record["num_turns"])
        self.assertIn("boom: something broke", json.dumps(record, default=str))

    def test_only_the_last_2000_characters_of_stderr_are_kept(self) -> None:
        body = (
            "chunk=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
            "i=0; while [ $i -lt 100 ]; do printf '%s' $chunk >&2; i=$((i+1)); done\n"
            "printf 'THE-END' >&2\n"
            "exit 1"
        )
        record, _ = self.run_it(self.fake_claude(body))
        dumped = json.dumps(record, default=str)
        self.assertIn("THE-END", dumped)
        self.assertNotIn("x" * 2100, dumped)  # 5000 x were written; at most ~2000 kept

    def test_isolation_failure_is_recorded_and_the_run_still_reports_metrics(
        self,
    ) -> None:
        leaky = fx.init_event(skills=["jzr", "debug"])  # a baseline run that can see the jzr skill
        events = [leaky, fx.result_event(cost=0.5)]
        record, _ = self.run_it(self.printing_claude(*events), arm="baseline")
        self.assertIs(record["isolation_ok"], False)
        self.assertAlmostEqual(record["total_cost_usd"], 0.5)

    def test_missing_init_event_is_an_isolation_failure(self) -> None:
        record, _ = self.run_it(self.printing_claude(fx.result_event()))
        self.assertIs(record["isolation_ok"], False)


class TimeoutTest(RunnerCase):
    def test_timeout_kills_the_whole_process_group_and_records_it(self) -> None:
        pidfile = self.tmp / "sleeper.pid"
        body = (
            f"/usr/bin/cat '{self.canned(fx.init_for_arm('baseline'))}'\n"
            "/usr/bin/sleep 60 &\n"
            f"echo $! > '{pidfile}'\n"
            "wait"
        )
        started = time.monotonic()
        try:
            record, _ = self.run_it(self.fake_claude(body), timeout_s=1.5)
            elapsed = time.monotonic() - started
            self.assertLess(
                elapsed,
                30,
                "run_one must return soon after the timeout, not wait for the child",
            )
            self.assertIs(record["timed_out"], True)
            self.assertIs(record["passed"], False)
            self.assertEqual(self.check_calls, [])
            self.assertIn('"subtype": "init"', self.transcript.read_text())

            sleeper = int(pidfile.read_text().strip())
            for _ in range(40):
                if not process_alive(sleeper):
                    break
                time.sleep(0.1)
            self.assertFalse(process_alive(sleeper), "a grandchild of the agent survived the timeout")
        finally:
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text().strip()), 9)
                except (OSError, ValueError):
                    pass


def needs_the_real_source(cls: type) -> type:
    """run_all uses the repo's own jzr executable; skip where it is missing or a jzr is on the system PATH."""
    cls = unittest.skipIf(find_jzr_on(SYSTEM_PATH) is not None, "a jzr executable is on the system PATH")(cls)
    return unittest.skipUnless(os.access(REPO / ".venv" / "bin" / "jzr", os.X_OK), "needs the repo's .venv/bin/jzr")(
        cls
    )


class RunAllCase(RunnerCase):
    """run_all with the real default source and a fake claude (and, where a test asks, a fake bwrap on PATH)."""

    def setUp(self) -> None:
        super().setUp()
        # The shared pip cache and its warm-up are the runner's to touch, never the repo's or the network's:
        # every run_all test gets a cache folder of its own (not created: run_all makes it) and a warm-up that
        # does nothing. Tests of those two replace the mock. (create=True: they are new in round 4.)
        self.pip_cache = self.tmp / "pip-cache"
        for name, value in (("PIP_CACHE", self.pip_cache), ("warm_pip_cache", mock.Mock(return_value=None))):
            patcher = mock.patch.object(runner_module, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.warm_pip_cache = runner_module.warm_pip_cache

    @property
    def sandboxes(self) -> Path:
        return self.tmp / "sandboxes"

    def run_all_with(
        self,
        *argv: str,
        claude: str,
        path: str | None = None,
        sandbox_dir: bool = True,
        default_arms: bool = False,
    ) -> tuple[int, str, Path]:
        """Returns (exit code, stdout + stderr, results folder). `path` replaces PATH while run_all looks for bwrap.

        Runs the baseline arm unless argv says otherwise; `default_arms` leaves --arms out altogether.
        """
        code, stdout, stderr, out = self.run_all_streams(
            *argv, claude=claude, path=path, sandbox_dir=sandbox_dir, default_arms=default_arms
        )
        return code, stdout + stderr, out

    def run_all_streams(
        self,
        *argv: str,
        claude: str,
        path: str | None = None,
        sandbox_dir: bool = True,
        default_arms: bool = False,
    ) -> tuple[int, str, str, Path]:
        """Like run_all_with, but returns (exit code, stdout, stderr, results folder) with the streams apart."""
        out = self.tmp / "results"
        self.sandboxes.mkdir(exist_ok=True)
        options = ["--sandbox-dir", str(self.sandboxes)] if sandbox_dir else []
        arms = [] if default_arms else ["--arms", "baseline"]
        args = build_parser().parse_args(
            ["run", "--tasks", "hello", "--runs", "1", *arms, "--out", str(out), *options, "--claude", claude, *argv]
        )  # fmt: skip
        environ = {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}
        if path is not None:
            environ["PATH"] = path
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, environ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_all(args)
        return code, stdout.getvalue(), stderr.getvalue(), out

    def good_claude(self, arm: str = "baseline") -> str:
        return self.printing_claude(fx.init_for_arm(arm), fx.result_event(text="ok"))

    # -- a bwrap that records how it was called and then runs the command after `--` -------------

    @property
    def bwrap_log(self) -> Path:
        return self.tmp / "bwrap.log"

    def fake_bwrap_folder(self, *, working: bool = True) -> str:
        """A folder holding a `bwrap` stand-in. Working: logs its arguments, then runs what follows `--`
        (a call without `--`, like a start-up probe, just succeeds). Not working: prints an error, exits 1."""
        folder = self.tmp / ("bin-working-bwrap" if working else "bin-broken-bwrap")
        folder.mkdir()
        script = folder / "bwrap"
        if working:
            script.write_text(
                "#!/bin/sh\n"
                f"LOG='{self.bwrap_log}'\n"
                'printf \'%s\\n\' "$@" >> "$LOG"\n'
                "printf '%s\\n' '---end-of-call---' >> \"$LOG\"\n"
                'while [ $# -gt 0 ]; do\n  if [ "$1" = "--" ]; then shift; exec "$@"; fi\n  shift\ndone\n'
                "exit 0\n"
            )
        else:
            script.write_text("#!/bin/sh\necho 'bwrap: simulated start-up failure' >&2\nexit 1\n")
        script.chmod(0o755)
        return str(folder)

    def path_with(self, folder: str) -> str:
        return f"{folder}{os.pathsep}{os.environ['PATH']}"

    def path_without_bwrap(self) -> str:
        empty = self.tmp / "bin-empty"
        empty.mkdir(exist_ok=True)
        return str(empty)

    def bwrap_calls(self) -> list[list[str]]:
        """Every logged call; the ones that start a command contain `--`."""
        if not self.bwrap_log.exists():
            return []
        calls: list[list[str]] = [[]]
        for line in self.bwrap_log.read_text().splitlines():
            if line == "---end-of-call---":
                calls.append([])
            else:
                calls[-1].append(line)
        return [call for call in calls if call]

    def bwrap_commands(self) -> list[list[str]]:
        """The calls that started a run (with `--chdir` and `--`), not any start-up probe."""
        return [call for call in self.bwrap_calls() if "--" in call and "--chdir" in call]

    def records(self, out: Path) -> list[dict[str, Any]]:
        return [json.loads(line) for line in (out / "runs.jsonl").read_text().splitlines() if line.strip()]

    def meta(self, out: Path) -> dict[str, Any]:
        return json.loads((out / "meta.json").read_text())


class DefaultSourceTest(unittest.TestCase):
    """default_source() is about paths only; it starts and needs nothing."""

    def test_the_skills_folder_is_the_one_the_package_ships(self) -> None:
        source = runner_module.default_source()
        self.assertEqual(source.skills_dir, REPO / "src" / "jzr_tools" / "data" / "skill")
        for name in fx.JZR_SKILLS:
            with self.subTest(name=name):
                self.assertTrue((source.skills_dir / name / "SKILL.md").is_file())

    def test_the_old_single_skill_folder_is_not_pointed_at(self) -> None:
        source = runner_module.default_source()
        self.assertFalse((source.skills_dir / "SKILL.md").exists())
        self.assertFalse(hasattr(source, "skill_dir"))
        self.assertFalse(hasattr(source, "split_dir"))

    def test_the_binary_and_the_mcp_command_are_the_repos(self) -> None:
        source = runner_module.default_source()
        self.assertEqual(source.jzr_bin, REPO / ".venv" / "bin" / "jzr")
        self.assertEqual(source.mcp_command, [str(REPO / ".venv" / "bin" / "jzr"), "agent", "mcp"])


class SplitSkillGoneTest(unittest.TestCase):
    def test_the_module_that_cut_the_skill_is_gone(self) -> None:
        sys.modules.pop("jzr_bench.split_skill", None)
        with self.assertRaises(ImportError):
            importlib.import_module("jzr_bench.split_skill")

    def test_the_runner_no_longer_writes_split_skills(self) -> None:
        self.assertFalse(hasattr(runner_module, "write_split"))


@needs_the_real_source
class RunAllSkillsTest(RunAllCase):
    """run_all with the real default source and a fake claude: the skill arm gets the four packaged skills.

    These do not test /tmp, so they share it (--shared-tmp) and run on machines without bubblewrap.
    """

    def run_all_with(self, *argv: str, claude: str, **kwargs: Any) -> tuple[int, str, Path]:
        return super().run_all_with("--shared-tmp", *argv, claude=claude, **kwargs)

    def test_the_skill_run_is_recorded_with_isolation_ok(self) -> None:
        claude = self.printing_claude(fx.init_for_arm("skill"), fx.result_event(text="ok"))
        code, output, out = self.run_all_with("--arms", "skill", claude=claude)
        self.assertEqual(code, 0, output)
        self.assertEqual(
            [(r["arm"], r["isolation_ok"], r["passed"]) for r in self.records(out)],
            [("skill", True, True)],
        )

    def test_the_agent_in_a_skill_run_sees_exactly_the_four_packaged_skills(self) -> None:
        body = (
            f"/usr/bin/ls -1 \"$CLAUDE_CONFIG_DIR/skills\" > '{self.rec / 'skills.txt'}'\n"
            f"/usr/bin/cat '{self.canned(fx.init_for_arm('skill'), fx.result_event(text='ok'))}'\nexit 0"
        )
        code, output, _ = self.run_all_with("--arms", "skill", claude=self.fake_claude(body))
        self.assertEqual(code, 0, output)
        self.assertEqual((self.rec / "skills.txt").read_text().split(), sorted(fx.JZR_SKILLS))

    def test_each_installed_skill_is_the_packaged_one(self) -> None:
        body = (
            f"/usr/bin/cp -R \"$CLAUDE_CONFIG_DIR/skills\" '{self.rec / 'copied'}'\n"
            f"/usr/bin/cat '{self.canned(fx.init_for_arm('skill'), fx.result_event(text='ok'))}'\nexit 0"
        )
        code, output, _ = self.run_all_with("--arms", "skill", claude=self.fake_claude(body))
        self.assertEqual(code, 0, output)
        packaged = REPO / "src" / "jzr_tools" / "data" / "skill"
        for name in fx.JZR_SKILLS:
            with self.subTest(name=name):
                self.assertEqual(
                    (self.rec / "copied" / name / "SKILL.md").read_text(), (packaged / name / "SKILL.md").read_text()
                )

    def test_no_split_skills_folder_is_written_into_the_results_folder(self) -> None:
        for arm in ("baseline", "skill"):
            with self.subTest(arm=arm):
                shutil.rmtree(self.tmp / "results", ignore_errors=True)
                code, output, out = self.run_all_with("--arms", arm, claude=self.good_claude(arm))
                self.assertEqual(code, 0, output)
                self.assertFalse((out / "split-skills").exists())
                self.assertEqual({p.name for p in out.iterdir()} & {"split-skills", "skills"}, set())

    def test_split_is_rejected_with_exit_2_before_anything_runs(self) -> None:
        for arms in ("split", "baseline,skill,split"):
            with self.subTest(arms=arms):
                shutil.rmtree(self.tmp / "results", ignore_errors=True)
                code, output, out = self.run_all_with("--arms", arms, claude=self.good_claude())
                self.assertEqual(code, 2, output)
                self.assertIn("arms must be some of baseline, skill, mcp", output)
                self.assertFalse((self.rec / "env.txt").exists(), "claude must not have been started")
                self.assertFalse(out.exists(), "no results folder for a rejected command line")

    def test_split_is_rejected_for_a_dry_run_too(self) -> None:
        code, output, _ = self.run_all_with("--arms", "split", "--dry-run", claude="/bin/true")
        self.assertEqual(code, 2, output)

    def test_dry_run_plans_and_prepares_the_skill_arm(self) -> None:
        code, output, _ = self.run_all_with("--arms", "skill", "--dry-run", claude="/bin/true")
        self.assertEqual(code, 0, output)
        self.assertIn("r01-hello-skill", output)
        self.assertIn("1 runs", output)
        self.assertIn("first command:", output)
        self.assertNotIn("--mcp-config", output)

    def test_dry_run_of_two_arms_plans_two_runs(self) -> None:
        code, output, _ = self.run_all_with("--arms", "baseline,skill", "--dry-run", claude="/bin/true")
        self.assertEqual(code, 0, output)
        self.assertIn("2 runs", output)
        self.assertNotIn("-split", output)

    def test_the_default_arms_are_the_three_and_a_dry_run_plans_them_once_each(self) -> None:
        self.assertEqual(build_parser().parse_args(["run"]).arms, "baseline,skill,mcp")
        code, output, _ = self.run_all_with("--dry-run", claude="/bin/true", default_arms=True)
        if code == 2 and "mcp extra" in output:
            self.skipTest("the MCP arm needs the mcp extra here")
        self.assertEqual(code, 0, output)
        runs = [line for line in output.splitlines() if line.startswith("r01-hello-")]
        self.assertCountEqual(runs, ["r01-hello-baseline", "r01-hello-skill", "r01-hello-mcp"])
        self.assertIn("3 runs", output)


class CliOptionTest(unittest.TestCase):
    def test_shared_tmp_is_off_by_default(self) -> None:
        args = build_parser().parse_args(["run"])
        self.assertIs(args.shared_tmp, False)

    def test_the_flag_turns_it_on(self) -> None:
        args = build_parser().parse_args(["run", "--shared-tmp"])
        self.assertIs(args.shared_tmp, True)

    def test_the_other_options_keep_working_next_to_it(self) -> None:
        args = build_parser().parse_args(
            ["run", "--shared-tmp", "--keep-sandboxes", "--sandbox-dir", "/x", "--dry-run"]
        )
        self.assertEqual(
            (args.shared_tmp, args.keep_sandboxes, args.sandbox_dir, args.dry_run), (True, True, "/x", True)
        )


def refuse_var_tmp(real: Any) -> Any:
    """Wrap an os.access / Path.is_dir style function so that /var/tmp alone answers False."""

    def wrapper(path: Any, *rest: Any, **kwargs: Any) -> Any:
        if os.fspath(path) == "/var/tmp":
            return False
        return real(path, *rest, **kwargs)

    return wrapper


class DefaultSandboxParentTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.system_temp = Path(self._tmp.name).resolve() / "system temp"
        self.system_temp.mkdir()

    @unittest.skipUnless(
        os.path.isdir("/var/tmp") and os.access("/var/tmp", os.W_OK), "/var/tmp is not a writable folder here"
    )
    def test_var_tmp_when_it_is_a_writable_folder(self) -> None:
        with mock.patch("tempfile.gettempdir", return_value=str(self.system_temp)):
            parent = runner_module.default_sandbox_parent()
        self.assertEqual(parent, Path("/var/tmp"))
        self.assertIsInstance(parent, Path)

    def test_the_system_temp_folder_when_var_tmp_is_not_writable(self) -> None:
        with (
            mock.patch("tempfile.gettempdir", return_value=str(self.system_temp)),
            mock.patch("os.access", refuse_var_tmp(os.access)),
        ):
            parent = runner_module.default_sandbox_parent()
        self.assertEqual(parent, self.system_temp)
        self.assertIsInstance(parent, Path)

    def test_the_system_temp_folder_when_var_tmp_is_not_a_folder(self) -> None:
        with (
            mock.patch("tempfile.gettempdir", return_value=str(self.system_temp)),
            mock.patch("os.path.isdir", refuse_var_tmp(os.path.isdir)),
            mock.patch("pathlib.Path.is_dir", lambda self, **kw: str(self) != "/var/tmp" and os.path.isdir(self)),
            mock.patch("os.access", refuse_var_tmp(os.access)),
        ):
            parent = runner_module.default_sandbox_parent()
        self.assertEqual(parent, self.system_temp)

    def test_the_default_is_a_folder_that_exists(self) -> None:
        self.assertTrue(runner_module.default_sandbox_parent().is_dir())


@needs_the_real_source
class PrivateTmpRunAllTest(RunAllCase):
    """run_all and the private /tmp, with a stand-in for bwrap that logs how it was called."""

    def run_private(self, *argv: str, arms: str = "baseline") -> tuple[int, str, Path]:
        folder = self.fake_bwrap_folder()
        return self.run_all_with(
            "--arms", arms, "--keep-sandboxes", *argv, claude=self.good_claude(), path=self.path_with(folder)
        )

    # -- on by default -------------------------------------------------------------------------

    def test_by_default_the_run_goes_through_bwrap_and_meta_says_so(self) -> None:
        code, output, out = self.run_private()
        self.assertEqual(code, 0, output)
        self.assertIs(self.meta(out)["private_tmp"], True)
        [command] = self.bwrap_commands()
        [record] = self.records(out)
        sandbox = record["sandbox"]
        self.assertIn(["--bind", f"{sandbox}/tmp", "/tmp"], [command[i : i + 3] for i in range(len(command))])
        self.assertIn("--dev-bind", command)
        self.assertIn("--die-with-parent", command)
        self.assertEqual(command[command.index("--chdir") + 1], f"{sandbox}/work")
        after = command[command.index("--") + 1 :]
        self.assertEqual(after[:2], [str(self.tmp / "fake-claude"), "-p"])
        self.assertEqual(after[2], HELLO_PROMPT, "the prompt of the hello task")

    def test_the_child_started_under_bwrap_got_no_tmpdir_and_the_exact_allowlist(self) -> None:
        code, output, _ = self.run_private()
        self.assertEqual(code, 0, output)
        env = self.child_env()
        self.assertNotIn("TMPDIR", env)
        self.assertEqual(
            set(env) - SHELL_EXTRAS,
            {"HOME", "CLAUDE_CONFIG_DIR", "PATH", "LANG", "TERM", "CLAUDE_CODE_OAUTH_TOKEN"},
        )

    def test_the_command_after_the_wrapper_is_the_same_as_without_it(self) -> None:
        code, output, _ = self.run_private()
        self.assertEqual(code, 0, output)
        argv = self.recorded("argv.txt").splitlines()
        self.assertEqual(argv[:2], ["-p", HELLO_PROMPT])
        self.assertIn("--output-format", argv)

    def test_every_arm_gets_its_own_bwrap_command_and_its_own_tmp_folder(self) -> None:
        code, output, out = self.run_private(arms="baseline,skill,mcp")
        self.assertEqual(code, 0, output)
        records = self.records(out)
        self.assertEqual(len(records), 3)
        self.assertEqual(len(self.bwrap_commands()), 3)
        for record, command in zip(records, self.bwrap_commands(), strict=True):
            with self.subTest(arm=record["arm"]):
                self.assertTrue(Path(record["sandbox"], "tmp").is_dir())
                self.assertIn(f"{record['sandbox']}/tmp", command)

    # -- off with --shared-tmp -----------------------------------------------------------------

    def test_shared_tmp_does_not_start_bwrap_and_the_child_gets_tmpdir(self) -> None:
        folder = self.fake_bwrap_folder()
        code, output, out = self.run_all_with(
            "--shared-tmp", "--keep-sandboxes", claude=self.good_claude(), path=self.path_with(folder)
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(self.bwrap_calls(), [], "bwrap must not be started at all, not even to probe it")
        self.assertIs(self.meta(out)["private_tmp"], False)
        [record] = self.records(out)
        self.assertEqual(self.child_env()["TMPDIR"], f"{record['sandbox']}/tmp")
        self.assertEqual(self.recorded("argv.txt").splitlines()[:2], ["-p", HELLO_PROMPT])

    def test_shared_tmp_runs_on_a_machine_without_bwrap(self) -> None:
        code, output, out = self.run_all_with("--shared-tmp", claude=self.good_claude(), path=self.path_without_bwrap())
        self.assertEqual(code, 0, output)
        self.assertIs(self.meta(out)["private_tmp"], False)
        [record] = self.records(out)
        self.assertIs(record["passed"], True)

    # -- bwrap missing or broken ---------------------------------------------------------------

    def assert_refused_before_any_run(self, code: int, output: str, out: Path) -> None:
        self.assertEqual(code, 2, output)
        self.assertIn("bwrap", output)
        self.assertIn("--shared-tmp", output)
        self.assertFalse((self.rec / "env.txt").exists(), "claude must not have been started")
        self.assertEqual(list(self.sandboxes.iterdir()), [], "no sandbox may be created")
        runs = out / "runs.jsonl"
        self.assertTrue(not runs.exists() or runs.read_text().strip() == "", "no run may be recorded")

    def test_exit_2_before_any_run_when_bwrap_is_not_on_path(self) -> None:
        code, output, out = self.run_all_with(claude=self.good_claude(), path=self.path_without_bwrap())
        self.assert_refused_before_any_run(code, output, out)

    def test_exit_2_before_any_run_when_bwrap_cannot_start(self) -> None:
        folder = self.fake_bwrap_folder(working=False)
        code, output, out = self.run_all_with(claude=self.good_claude(), path=self.path_with(folder))
        self.assert_refused_before_any_run(code, output, out)

    def test_the_refusal_also_holds_with_keep_sandboxes_and_several_arms(self) -> None:
        code, output, out = self.run_all_with(
            "--arms",
            "baseline,skill,mcp",
            "--keep-sandboxes",
            claude=self.good_claude(),
            path=self.path_without_bwrap(),
        )
        self.assert_refused_before_any_run(code, output, out)

    # -- where the sandboxes go and what they are called ---------------------------------------

    def test_the_sandbox_folder_name_says_nothing_about_the_benchmark(self) -> None:
        folder = self.fake_bwrap_folder()
        for shared in (False, True):
            with self.subTest(shared_tmp=shared):
                shutil.rmtree(self.tmp / "results", ignore_errors=True)
                shutil.rmtree(self.sandboxes, ignore_errors=True)
                argv = ["--arms", "baseline,skill,mcp", "--keep-sandboxes", *(["--shared-tmp"] if shared else [])]
                code, output, out = self.run_all_with(*argv, claude=self.good_claude(), path=self.path_with(folder))
                self.assertEqual(code, 0, output)
                records = self.records(out)
                self.assertEqual(len(records), 3)
                for record in records:
                    name = Path(record["sandbox"]).name
                    self.assertTrue(name, record)
                    for word in ("jzr", "bench", record["task"], record["arm"], record["run_id"]):
                        self.assertNotIn(word.lower(), name.lower(), f"{name} (run {record['run_id']})")
                    self.assertNotIn(record["run_id"], record["sandbox"])

    def test_keep_sandboxes_prints_and_records_which_folder_belongs_to_which_run(self) -> None:
        code, output, out = self.run_private("--runs", "2", arms="baseline,skill")
        self.assertEqual(code, 0, output)
        records = self.records(out)
        self.assertEqual(len(records), 4)
        folders = [record["sandbox"] for record in records]
        self.assertEqual(len(set(folders)), 4, "one folder per run")
        for record in records:
            folder = Path(record["sandbox"])
            self.assertTrue(folder.is_dir(), record["run_id"])
            self.assertEqual(folder.parent, self.sandboxes)
            self.assertTrue((folder / "work").is_dir())
            self.assertIn(record["sandbox"], output, "the folder is printed too")

    def test_without_keep_sandboxes_the_folders_are_removed_and_not_recorded(self) -> None:
        folder = self.fake_bwrap_folder()
        code, output, out = self.run_all_with(claude=self.good_claude(), path=self.path_with(folder))
        self.assertEqual(code, 0, output)
        self.assertEqual(list(self.sandboxes.iterdir()), [])
        [record] = self.records(out)
        self.assertNotIn("sandbox", record)

    def test_the_default_sandbox_parent_is_where_the_sandboxes_go(self) -> None:
        default_parent = self.tmp / "default parent"
        default_parent.mkdir()
        folder = self.fake_bwrap_folder()
        with mock.patch("jzr_bench.runner.default_sandbox_parent", return_value=default_parent):
            code, output, out = self.run_all_with(
                "--keep-sandboxes", claude=self.good_claude(), path=self.path_with(folder), sandbox_dir=False
            )
        self.assertEqual(code, 0, output)
        [record] = self.records(out)
        self.assertEqual(Path(record["sandbox"]).parent, default_parent)

    def test_sandbox_dir_still_overrides_the_default_parent(self) -> None:
        default_parent = self.tmp / "default parent"
        default_parent.mkdir()
        folder = self.fake_bwrap_folder()
        with mock.patch("jzr_bench.runner.default_sandbox_parent", return_value=default_parent):
            code, output, out = self.run_all_with(
                "--keep-sandboxes", claude=self.good_claude(), path=self.path_with(folder)
            )
        self.assertEqual(code, 0, output)
        [record] = self.records(out)
        self.assertEqual(Path(record["sandbox"]).parent, self.sandboxes)
        self.assertEqual(list(default_parent.iterdir()), [])

    def test_shared_tmp_uses_the_same_default_parent(self) -> None:
        default_parent = self.tmp / "default parent"
        default_parent.mkdir()
        with mock.patch("jzr_bench.runner.default_sandbox_parent", return_value=default_parent):
            code, output, out = self.run_all_with(
                "--shared-tmp", "--keep-sandboxes", claude=self.good_claude(), sandbox_dir=False
            )
        self.assertEqual(code, 0, output)
        [record] = self.records(out)
        self.assertEqual(Path(record["sandbox"]).parent, default_parent)

    # -- --dry-run -----------------------------------------------------------------------------

    @staticmethod
    def line_starting(output: str, prefix: str) -> str:
        [line] = [line for line in output.splitlines() if line.startswith(prefix)]
        return line

    def test_dry_run_shows_the_first_command_with_the_wrapper_in_front(self) -> None:
        folder = self.fake_bwrap_folder()
        code, output, _ = self.run_all_with("--dry-run", claude="/bin/true", path=self.path_with(folder))
        self.assertEqual(code, 0, output)
        line = self.line_starting(output, "first command:")
        bwrap = f"{folder}/bwrap"
        order = [
            line.index(word)
            for word in (bwrap, "--dev-bind", "--die-with-parent", "--chdir", " -- ", "/bin/true", " -p ")
        ]
        self.assertEqual(order, sorted(order), line)
        self.assertEqual(self.bwrap_commands(), [], "a dry run must not run anything under bwrap")
        self.assertNotIn("TMPDIR", self.line_starting(output, "environment:"))

    def test_dry_run_with_shared_tmp_shows_the_command_without_it(self) -> None:
        folder = self.fake_bwrap_folder()
        code, output, _ = self.run_all_with(
            "--dry-run", "--shared-tmp", claude="/bin/true", path=self.path_with(folder)
        )
        self.assertEqual(code, 0, output)
        line = self.line_starting(output, "first command:")
        self.assertTrue(line.startswith("first command: /bin/true -p "), line)
        self.assertNotIn("bwrap", line)
        self.assertNotIn("--dev-bind", line)
        self.assertIn("TMPDIR", self.line_starting(output, "environment:"))


class BareCliOptionTest(unittest.TestCase):
    def test_no_bare_arms_by_default(self) -> None:
        self.assertFalse(build_parser().parse_args(["run"]).bare_arms)

    def test_the_option_takes_a_comma_separated_list(self) -> None:
        self.assertEqual(
            build_parser().parse_args(["run", "--bare-arms", "baseline,skill"]).bare_arms, "baseline,skill"
        )

    def test_the_other_options_keep_working_next_to_it(self) -> None:
        args = build_parser().parse_args(["run", "--bare-arms", "skill", "--arms", "baseline", "--runs", "2"])
        self.assertEqual((args.bare_arms, args.arms, args.runs), ("skill", "baseline", 2))


class RunRecordMachineTest(RunnerCase):
    def run_planned(self, planned: PlannedRun) -> dict[str, Any]:
        setup = self.arm_setup(planned.arm)
        return run_one(
            planned,
            self.task(),
            setup,
            claude_bin=self.printing_claude(fx.init_for_arm("baseline"), fx.result_event(text="ok")),
            fixture_dir=self.fixture_dir,
            transcript_path=self.transcript,
            timeout_s=20,
        )

    def test_the_record_carries_the_machine_of_the_planned_run(self) -> None:
        record = self.run_planned(PlannedRun("r01-probe-baseline-bare", 1, "probe", "baseline", "bare"))
        self.assertEqual(record["machine"], "bare")
        self.assertEqual(record["run_id"], "r01-probe-baseline-bare")
        json.dumps(record)

    def test_a_full_run_says_full(self) -> None:
        self.assertEqual(self.run_planned(PlannedRun("r01-probe-baseline", 1, "probe", "baseline"))["machine"], "full")


@needs_the_real_source
class BareRunAllTest(RunAllCase):
    """run_all with bare arms, a fake bwrap that logs its calls, and stand-ins for the two steps that need the
    real machine: the check before the first run (check_bare) and jzr's ffmpeg download (ensure_jzr_ffmpeg)."""

    ALL_NINE = "hello,gif,compress,videosize,tags,pdfsplit,batch,xlsxjson,rename"

    def setUp(self) -> None:
        super().setUp()
        self.bwrap_folder = self.fake_bwrap_folder()
        self.check_problems: list[str] = []
        self.checks: list[dict[str, Any]] = []
        self.ensure_calls: list[tuple[Any, Any]] = []
        self.shared_ffmpeg = self.tmp / "jzr-data" / "ffmpeg"
        (self.shared_ffmpeg / "bin").mkdir(parents=True)
        (self.shared_ffmpeg / "bin" / "ffmpeg").write_text("stand-in\n")
        for name, side_effect in (("check_bare", self.fake_check_bare), ("ensure_jzr_ffmpeg", self.fake_ensure)):
            patcher = mock.patch.object(runner_module, name, side_effect=side_effect)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake_check_bare(self, bwrap: str, bare: Any, parent: Path) -> list[str]:
        runs = self.tmp / "results" / "runs.jsonl"
        self.checks.append(
            {
                "bwrap": bwrap,
                "bare": bare,
                "parent": parent,
                "sandboxes": sorted(p.name for p in self.sandboxes.iterdir()) if self.sandboxes.exists() else [],
                "recorded": runs.read_text() if runs.exists() else "",
            }
        )
        return list(self.check_problems)

    def fake_ensure(self, jzr_bin: Any, cache: Any = None) -> Path:
        self.ensure_calls.append((jzr_bin, cache))
        return self.shared_ffmpeg

    def marking_claude(self) -> str:
        """A claude that leaves a file when it is started for a run (not for --version)."""
        answer = self.canned(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        return self.fake_claude(
            f'if [ "$1" = "-p" ]; then echo started >> "$REC/started.txt"; fi\n/usr/bin/cat \'{answer}\'\nexit 0'
        )

    def started(self) -> bool:
        return (self.rec / "started.txt").exists()

    def run_bare(
        self, *argv: str, arms: str = "baseline", bare: str = "baseline", **kwargs: Any
    ) -> tuple[int, str, Path]:
        return self.run_all_with(
            "--arms", arms, "--bare-arms", bare, "--keep-sandboxes", *argv,
            claude=kwargs.pop("claude", None) or self.marking_claude(),
            path=self.path_with(self.bwrap_folder),
            **kwargs,
        )  # fmt: skip

    def commands_by_run_id(self, out: Path) -> dict[str, list[str]]:
        sandboxes = {r["run_id"]: r["sandbox"] for r in self.records(out)}
        commands = {c[c.index("--chdir") + 1]: c for c in self.bwrap_commands()}
        return {run_id: commands[f"{sandbox}/work"] for run_id, sandbox in sandboxes.items()}

    @staticmethod
    def has_run(command: list[str], *words: str) -> bool:
        return any(command[i : i + len(words)] == list(words) for i in range(len(command)))

    # -- records and meta ------------------------------------------------------------------------

    def test_every_run_is_planned_recorded_and_labelled_with_its_machine(self) -> None:
        code, output, out = self.run_bare("--runs", "2")
        self.assertEqual(code, 0, output)
        records = self.records(out)
        self.assertEqual(
            sorted((r["run_id"], r["machine"]) for r in records),
            [
                ("r01-hello-baseline", "full"),
                ("r01-hello-baseline-bare", "bare"),
                ("r02-hello-baseline", "full"),
                ("r02-hello-baseline-bare", "bare"),
            ],
        )
        for record in records:
            self.assertIs(record["passed"], True, record)
            self.assertEqual(record["arm"], "baseline")
        self.assertTrue((out / "transcripts" / "r01-hello-baseline-bare.jsonl").is_file())
        self.assertTrue((out / "transcripts" / "r01-hello-baseline.jsonl").is_file())

    def test_meta_records_the_bare_arms(self) -> None:
        code, output, out = self.run_bare(bare="baseline,skill")
        self.assertEqual(code, 0, output)
        meta = self.meta(out)
        self.assertEqual(meta["bare_arms"], ["baseline", "skill"])
        self.assertEqual(meta["arms"], ["baseline"])

    def test_meta_has_an_empty_list_when_there_are_none(self) -> None:
        code, output, out = self.run_all_with(
            "--keep-sandboxes", claude=self.good_claude(), path=self.path_with(self.bwrap_folder)
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(self.meta(out)["bare_arms"], [])
        self.assertEqual([r["machine"] for r in self.records(out)], ["full"])

    def test_the_summary_shows_both_machines(self) -> None:
        code, output, out = self.run_bare()
        self.assertEqual(code, 0, output)
        summary = (out / "summary.md").read_text()
        self.assertIn("## hello", summary)
        self.assertIn("## hello, bare machine", summary)
        self.assertIn("bare arms: baseline", summary)

    def test_arms_may_be_empty_when_there_are_bare_arms(self) -> None:
        code, output, out = self.run_bare("--runs", "2", arms="")
        self.assertEqual(code, 0, output)
        records = self.records(out)
        self.assertEqual({r["machine"] for r in records}, {"bare"})
        self.assertEqual(sorted(r["run_id"] for r in records), ["r01-hello-baseline-bare", "r02-hello-baseline-bare"])
        self.assertEqual(self.meta(out)["arms"], [])

    # -- what bubblewrap is asked to do ------------------------------------------------------------

    def test_only_the_bare_run_gets_overlays_and_the_site_packages_replaced(self) -> None:
        code, output, out = self.run_bare()
        self.assertEqual(code, 0, output)
        commands = self.commands_by_run_id(out)
        self.assertEqual(set(commands), {"r01-hello-baseline", "r01-hello-baseline-bare"})
        self.assertNotIn("--overlay-src", commands["r01-hello-baseline"])
        self.assertNotIn("--overlay", commands["r01-hello-baseline"])
        self.assertNotIn("--tmpfs", commands["r01-hello-baseline"])
        bare = commands["r01-hello-baseline-bare"]
        usr_bin = os.path.realpath("/usr/bin")
        self.assertTrue(self.has_run(bare, "--overlay-src", usr_bin))
        self.assertLess(bare.index("--overlay-src"), bare.index("--die-with-parent"))

    def test_every_bare_run_gets_its_own_overlay_folders(self) -> None:
        code, output, out = self.run_bare("--runs", "2", arms="")
        self.assertEqual(code, 0, output)
        sandboxes = [r["sandbox"] for r in self.records(out)]
        self.assertEqual(len(set(sandboxes)), 2)
        for sandbox in sandboxes:
            self.assertTrue(any(Path(sandbox, "overlay").glob("*/upper")), sandbox)

    def test_the_child_environment_is_still_the_allowlist(self) -> None:
        code, output, _ = self.run_bare(arms="")
        self.assertEqual(code, 0, output)
        env = self.child_env()
        self.assertNotIn("TMPDIR", env)
        self.assertEqual(
            set(env) - SHELL_EXTRAS, {"HOME", "CLAUDE_CONFIG_DIR", "PATH", "LANG", "TERM", "CLAUDE_CODE_OAUTH_TOKEN"}
        )

    # -- the check before the first run ------------------------------------------------------------

    def test_the_check_runs_once_before_any_run_with_bwrap_and_the_bare_machine(self) -> None:
        code, output, _ = self.run_bare("--runs", "2")
        self.assertEqual(code, 0, output)
        [check] = self.checks
        self.assertEqual(check["bwrap"], f"{self.bwrap_folder}/bwrap")
        self.assertIsInstance(check["bare"], arms_module.BareMachine)
        self.assertIn(Path(os.path.realpath("/usr/bin")), check["bare"].bin_dirs)
        self.assertEqual(check["recorded"], "", "a run was recorded before the check")
        self.assertEqual(check["sandboxes"], [], "a run's sandbox existed before the check")
        self.assertIsInstance(check["parent"], Path)

    def test_the_check_is_not_made_without_bare_arms(self) -> None:
        code, output, _ = self.run_all_with(claude=self.good_claude(), path=self.path_with(self.bwrap_folder))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.checks, [])

    def test_a_failed_check_stops_everything_with_exit_2_and_says_why(self) -> None:
        self.check_problems = ["ffmpeg is still found at /usr/bin/ffmpeg", "import PIL still works"]
        code, output, out = self.run_bare("--runs", "2")
        self.assertEqual(code, 2, output)
        for problem in self.check_problems:
            self.assertIn(problem, output)
        self.assertFalse(self.started(), "claude must not have been started for a run")
        self.assertEqual(list(self.sandboxes.iterdir()), [], "no sandbox may be left")
        runs = out / "runs.jsonl"
        self.assertTrue(not runs.exists() or runs.read_text().strip() == "", "no run may be recorded")
        self.assertEqual(len(self.checks), 1)

    def test_a_failed_check_also_stops_the_full_runs_of_the_same_invocation(self) -> None:
        self.check_problems = ["python3 -m pip does not work"]
        code, _, out = self.run_bare(arms="baseline,skill", bare="baseline")
        self.assertEqual(code, 2)
        self.assertFalse(self.started())

    # -- refused command lines ---------------------------------------------------------------------

    def assert_refused(self, code: int, output: str, out: Path) -> None:
        self.assertEqual(code, 2, output)
        self.assertFalse(self.started(), "claude must not have been started")
        self.assertEqual(self.checks, [])
        self.assertFalse(out.exists(), "no results folder for a rejected command line")

    def test_an_unknown_bare_arm_is_exit_2(self) -> None:
        for bare in ("split", "baseline,split", "nonsense"):
            with self.subTest(bare=bare):
                shutil.rmtree(self.tmp / "results", ignore_errors=True)
                code, output, out = self.run_bare(bare=bare)
                self.assert_refused(code, output, out)
                self.assertIn("bare", output.lower())

    def test_bare_arms_with_shared_tmp_is_exit_2_with_a_message(self) -> None:
        code, output, out = self.run_bare("--shared-tmp")
        self.assert_refused(code, output, out)
        self.assertIn("--shared-tmp", output)
        self.assertIn("bare", output.lower())
        self.assertEqual(self.bwrap_calls(), [], "nothing may be started under bwrap")

    def test_shared_tmp_alone_is_still_fine(self) -> None:
        code, output, out = self.run_all_with(
            "--shared-tmp", claude=self.good_claude(), path=self.path_with(self.bwrap_folder)
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(self.checks, [])

    def test_no_arms_at_all_is_still_exit_2(self) -> None:
        for argv in (["--arms", ""], ["--arms", "", "--bare-arms", ""]):
            with self.subTest(argv=argv):
                shutil.rmtree(self.tmp / "results", ignore_errors=True)
                code, output, out = self.run_all_with(
                    *argv, claude=self.marking_claude(), path=self.path_with(self.bwrap_folder)
                )
                self.assert_refused(code, output, out)

    def test_unknown_full_arms_are_still_refused_next_to_bare_arms(self) -> None:
        code, output, out = self.run_bare(arms="split")
        self.assert_refused(code, output, out)

    def test_no_tasks_is_still_exit_2_with_bare_arms(self) -> None:
        code, output, out = self.run_bare("--tasks", "")
        self.assert_refused(code, output, out)

    # -- jzr's ffmpeg ------------------------------------------------------------------------------

    def test_the_cache_is_in_the_bench_results_folder(self) -> None:
        self.assertEqual(runner_module.JZR_FFMPEG_CACHE, REPO / "bench" / "results" / ".cache" / "jzr-data")

    def test_a_bare_jzr_arm_installs_once_however_many_runs(self) -> None:
        code, output, out = self.run_bare("--runs", "3", bare="baseline,skill")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.ensure_calls), 1)
        jzr_bin, cache = self.ensure_calls[0]
        self.assertEqual(Path(jzr_bin), REPO / ".venv" / "bin" / "jzr")
        self.assertIn(cache, (None, runner_module.JZR_FFMPEG_CACHE))

    def test_it_is_done_before_the_first_run(self) -> None:
        seen: list[bool] = []

        def ensure(jzr_bin: Any, cache: Any = None) -> Path:
            seen.append(self.started())
            return self.shared_ffmpeg

        with mock.patch.object(runner_module, "ensure_jzr_ffmpeg", side_effect=ensure):
            code, output, _ = self.run_bare(bare="skill")
        self.assertEqual(code, 0, output)
        self.assertEqual(seen, [False])

    def test_a_bare_baseline_alone_never_installs_it(self) -> None:
        code, output, _ = self.run_bare(bare="baseline")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.ensure_calls, [])

    def test_a_jzr_arm_on_the_full_machine_alone_never_installs_it(self) -> None:
        code, output, _ = self.run_bare(arms="baseline,skill", bare="baseline")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.ensure_calls, [])

    def test_a_dry_run_downloads_nothing(self) -> None:
        code, output, _ = self.run_bare("--dry-run", bare="baseline,skill")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.ensure_calls, [])

    def test_the_skill_run_gets_the_folder_read_only_under_its_home_and_the_baseline_does_not(self) -> None:
        code, output, out = self.run_bare(arms="skill", bare="baseline,skill")
        self.assertEqual(code, 0, output)
        records = {r["run_id"]: r for r in self.records(out)}
        self.assertEqual(set(records), {"r01-hello-skill", "r01-hello-baseline-bare", "r01-hello-skill-bare"})
        commands = self.commands_by_run_id(out)
        home = records["r01-hello-skill-bare"]["sandbox"] + "/home"
        self.assertTrue(
            self.has_run(
                commands["r01-hello-skill-bare"],
                "--ro-bind",
                str(self.shared_ffmpeg),
                f"{home}/.local/share/jzr/ffmpeg",
            )
        )
        self.assertNotIn(str(self.shared_ffmpeg), commands["r01-hello-baseline-bare"])
        self.assertNotIn(str(self.shared_ffmpeg), commands["r01-hello-skill"], "the full machine has its own ffmpeg")

    def test_the_mcp_run_gets_the_same_folder(self) -> None:
        code, output, out = self.run_bare(arms="", bare="mcp")
        if code == 2 and "mcp extra" in output:
            self.skipTest("the MCP arm needs the mcp extra here")
        self.assertEqual(code, 0, output)
        [record] = self.records(out)
        command = self.commands_by_run_id(out)["r01-hello-mcp-bare"]
        self.assertTrue(
            self.has_run(
                command, "--ro-bind", str(self.shared_ffmpeg), f"{record['sandbox']}/home/.local/share/jzr/ffmpeg"
            )
        )
        self.assertEqual(len(self.ensure_calls), 1)

    # -- --dry-run ---------------------------------------------------------------------------------

    def dry(self, *argv: str, **kwargs: Any) -> tuple[int, str]:
        code, output, _ = self.run_all_with(
            "--dry-run", *argv, claude="/bin/true", path=self.path_with(self.bwrap_folder), **kwargs
        )
        return code, output

    def test_the_dry_run_of_the_acceptance_plans_135_runs(self) -> None:
        code, output = self.dry(
            "--tasks", self.ALL_NINE, "--arms", "baseline,skill,mcp", "--bare-arms", "baseline,skill", "--runs", "3"
        )
        if code == 2 and "mcp extra" in output:
            self.skipTest("the MCP arm needs the mcp extra here")
        self.assertEqual(code, 0, output)
        self.assertIn("135 runs", output)
        ids = [line for line in output.splitlines() if re.fullmatch(r"r\d\d-\w+-(baseline|skill|mcp)(-bare)?", line)]
        self.assertEqual(len(ids), 135)
        self.assertEqual(len([i for i in ids if i.endswith("-bare")]), 54)
        self.assertIn("r03-rename-skill-bare", ids)
        self.assertIn("r01-videosize-mcp", ids)
        self.assertNotIn("r01-videosize-mcp-bare", ids)

    def test_the_same_arithmetic_with_the_tasks_there_have_always_been(self) -> None:
        code, output = self.dry(
            "--tasks", "hello,gif", "--arms", "baseline,skill,mcp", "--bare-arms", "baseline,skill", "--runs", "3"
        )
        if code == 2 and "mcp extra" in output:
            self.skipTest("the MCP arm needs the mcp extra here")
        self.assertEqual(code, 0, output)
        self.assertIn("30 runs", output)
        ids = [line for line in output.splitlines() if re.fullmatch(r"r\d\d-\w+-(baseline|skill|mcp)(-bare)?", line)]
        self.assertEqual(len(ids), 30)
        self.assertEqual(len([i for i in ids if i.endswith("-bare")]), 12)

    def test_the_dry_run_lists_bare_ids_and_starts_nothing(self) -> None:
        code, output = self.dry("--arms", "baseline", "--bare-arms", "baseline")
        self.assertEqual(code, 0, output)
        self.assertIn("2 runs", output)
        self.assertIn("r01-hello-baseline-bare", output.splitlines())
        self.assertIn("r01-hello-baseline", output.splitlines())
        self.assertEqual(self.bwrap_commands(), [])
        self.assertFalse(self.started())

    def test_the_dry_run_shows_the_bare_arguments_when_the_first_run_is_bare(self) -> None:
        code, output = self.dry("--arms", "", "--bare-arms", "baseline")
        self.assertEqual(code, 0, output)
        self.assertIn("1 runs", output)
        [line] = [ln for ln in output.splitlines() if ln.startswith("first command:")]
        self.assertIn("--overlay-src", line)
        self.assertLess(line.index("--overlay-src"), line.index("--die-with-parent"))
        self.assertLess(line.index("--die-with-parent"), line.index(" -- "))

    def test_the_dry_run_shows_no_bare_arguments_when_the_first_run_is_full(self) -> None:
        code, output = self.dry("--arms", "baseline")
        self.assertEqual(code, 0, output)
        [line] = [ln for ln in output.splitlines() if ln.startswith("first command:")]
        self.assertNotIn("--overlay", line)


class EnsureJzrFfmpegTest(unittest.TestCase):
    """ensure_jzr_ffmpeg with a fake jzr that logs how it was called and makes `<JZR_DATA_DIR>/ffmpeg/bin/ffmpeg`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.log = self.tmp / "jzr-calls.log"
        self.cache = self.tmp / "cache" / "jzr-data"
        self.jzr = self.tmp / "bin" / "jzr"
        self.write_jzr(install=True)

    def write_jzr(self, *, install: bool, exit_code: int = 0) -> None:
        self.jzr.parent.mkdir(parents=True, exist_ok=True)
        make = (
            'if [ "$1" = ffmpeg ] && [ "$2" = install ]; then\n'
            '  mkdir -p "$JZR_DATA_DIR/ffmpeg/bin"\n'
            '  printf "#!/bin/sh\\n" > "$JZR_DATA_DIR/ffmpeg/bin/ffmpeg"\n'
            '  chmod +x "$JZR_DATA_DIR/ffmpeg/bin/ffmpeg"\n'
            "fi\n"
            if install
            else ""
        )
        self.jzr.write_text(
            f"#!/bin/sh\nprintf '%s|%s\\n' \"$*\" \"$JZR_DATA_DIR\" >> '{self.log}'\n{make}exit {exit_code}\n"
        )
        self.jzr.chmod(0o755)

    def assert_refuses(self, jzr: Path, cache: Path) -> None:
        """ensure_jzr_ffmpeg raises some error of its own: a missing function or a wrong call does not count."""
        ensure = runner_module.ensure_jzr_ffmpeg
        try:
            ensure(jzr, cache)
        except (AttributeError, TypeError, NameError):
            raise
        except Exception:
            return
        self.fail("ensure_jzr_ffmpeg returned a folder although jzr left no ffmpeg in it")

    def calls(self) -> list[tuple[str, str]]:
        if not self.log.exists():
            return []
        return [tuple(line.split("|", 1)) for line in self.log.read_text().splitlines()]  # type: ignore[misc]

    def test_it_installs_into_the_cache_and_returns_the_ffmpeg_folder(self) -> None:
        folder = runner_module.ensure_jzr_ffmpeg(self.jzr, self.cache)
        self.assertEqual(Path(folder), self.cache / "ffmpeg")
        self.assertTrue((self.cache / "ffmpeg" / "bin" / "ffmpeg").is_file())

    def test_it_asks_jzr_to_install_without_a_prompt_with_the_cache_as_its_data_folder(self) -> None:
        runner_module.ensure_jzr_ffmpeg(self.jzr, self.cache)
        [(args, data_dir)] = self.calls()
        words = args.split()
        self.assertEqual(words[:2], ["ffmpeg", "install"])
        self.assertIn("--yes", words)
        self.assertEqual(data_dir, str(self.cache))

    def test_the_users_own_data_folder_is_not_what_jzr_is_told_to_use(self) -> None:
        with mock.patch.dict(os.environ, {"JZR_DATA_DIR": str(self.tmp / "users-own")}):
            runner_module.ensure_jzr_ffmpeg(self.jzr, self.cache)
        [(_, data_dir)] = self.calls()
        self.assertEqual(data_dir, str(self.cache))
        self.assertFalse((self.tmp / "users-own").exists())

    def test_the_second_call_does_not_install_again(self) -> None:
        first = runner_module.ensure_jzr_ffmpeg(self.jzr, self.cache)
        second = runner_module.ensure_jzr_ffmpeg(self.jzr, self.cache)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(first, second)

    def test_a_cache_that_already_holds_it_is_not_touched_at_all(self) -> None:
        (self.cache / "ffmpeg" / "bin").mkdir(parents=True)
        (self.cache / "ffmpeg" / "bin" / "ffmpeg").write_text("downloaded earlier\n")
        folder = runner_module.ensure_jzr_ffmpeg(self.jzr, self.cache)
        self.assertEqual(self.calls(), [])
        self.assertEqual(Path(folder), self.cache / "ffmpeg")
        self.assertEqual((self.cache / "ffmpeg" / "bin" / "ffmpeg").read_text(), "downloaded earlier\n")

    def test_a_failing_install_is_an_error_not_a_path_to_nothing(self) -> None:
        self.write_jzr(install=False, exit_code=1)
        self.assert_refuses(self.jzr, self.cache)

    def test_an_install_that_leaves_no_ffmpeg_is_an_error_too(self) -> None:
        self.write_jzr(install=False, exit_code=0)
        self.assert_refuses(self.jzr, self.cache)


if __name__ == "__main__":
    unittest.main()
