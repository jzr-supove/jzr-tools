"""Offline tests for jzr_bench.runner.run_one using fake `claude` executables.

The fake claude is a /bin/sh script that records how it was started (argv, cwd, environment, stdin,
files in its working folder) and prints a canned stream-json transcript. Nothing here starts the real
claude, touches the network or reads ~/.claude.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
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
from jzr_bench.arms import SYSTEM_PATH, ArmSetup, Auth, JzrSource, find_jzr_on, prepare_arm  # noqa: E402
from jzr_bench.cli import build_parser  # noqa: E402
from jzr_bench.plan import PlannedRun  # noqa: E402
from jzr_bench.runner import REPO, run_all, run_one  # noqa: E402
from jzr_bench.tasks import get_task  # noqa: E402
from jzr_bench.tasks.base import CheckResult, Task  # noqa: E402

SHELL_EXTRAS = {"PWD", "OLDPWD", "SHLVL", "_"}  # added by /bin/sh itself


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
        skill_dir = self.tmp / "repo" / "skill" / "jzr"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: jzr\n---\n")
        split_dir = self.tmp / "repo" / "split-skills"
        for name in fx.SPLIT_SKILLS:
            (split_dir / name).mkdir(parents=True)
            (split_dir / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        self.source = JzrSource(
            jzr_bin=jzr_bin,
            skill_dir=skill_dir,
            mcp_command=[str(jzr_bin), "agent", "mcp"],
            split_dir=split_dir,
        )
        self.system_path = str(self.tmp / "syspath")
        (self.tmp / "syspath").mkdir()
        self.fixture_dir = self.tmp / "fixtures"
        self.fixture_dir.mkdir()
        self.transcript = self.tmp / "transcripts" / "run.jsonl"
        self.transcript.parent.mkdir()
        self.check_calls: list[tuple[Path, Path, str]] = []

    # -- helpers ------------------------------------------------------------------------------

    def arm_setup(self, arm: str) -> ArmSetup:
        root = self.tmp / f"sandbox-{arm}"
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
    ) -> tuple[dict[str, Any], ArmSetup]:
        task = task or self.task()
        setup = self.arm_setup(arm)
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
            fx.assistant_event(fx.tool_use("Skill", skill="jzr"), fx.bash("jzr video gif clip.mp4")),
            fx.result_event(),
        ]
        record, _ = self.run_it(self.printing_claude(*events), arm="skill")
        self.assertIs(record["used_jzr_cli"], True)
        self.assertIs(record["used_jzr_skill"], True)
        self.assertIs(record["used_jzr_mcp"], False)
        self.assertIs(record["isolation_ok"], True)

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

    def test_split_arm_records_skill_and_cli_use_and_isolation(self) -> None:
        events = [
            fx.init_for_arm("split"),
            fx.assistant_event(
                fx.tool_use("Skill", skill="jzr-media"),
                fx.bash("jzr image resize a.png --width 100"),
            ),
            fx.result_event(),
        ]
        record, _ = self.run_it(self.printing_claude(*events), arm="split")
        self.assertEqual(record["arm"], "split")
        self.assertIs(record["isolation_ok"], True)
        self.assertEqual(record["isolation_problems"], [])
        self.assertIs(record["used_jzr_skill"], True)
        self.assertIs(record["used_jzr_cli"], True)
        self.assertIs(record["used_jzr_mcp"], False)
        self.assertEqual(record["skills_invoked"], ["jzr-media"])
        self.assertIs(record["passed"], True)

    def test_split_arm_without_a_skill_call_does_not_count_skill_use(self) -> None:
        events = [
            fx.init_for_arm("split"),
            fx.assistant_event(fx.bash("ffmpeg -i a.mp4 a.gif")),
            fx.result_event(),
        ]
        record, _ = self.run_it(self.printing_claude(*events), arm="split")
        self.assertIs(record["isolation_ok"], True)
        self.assertIs(record["used_jzr_skill"], False)
        self.assertIs(record["used_jzr_cli"], False)

    def test_split_arm_run_with_the_skill_arms_init_fails_isolation(self) -> None:
        # the sandbox was set up for split, but Claude Code shows the single jzr skill: not a valid split run
        events = [fx.init_for_arm("skill"), fx.result_event(cost=0.25)]
        record, _ = self.run_it(self.printing_claude(*events), arm="split")
        self.assertIs(record["isolation_ok"], False)
        self.assertTrue(record["isolation_problems"])
        self.assertFalse(any("unknown arm" in p for p in record["isolation_problems"]))
        self.assertAlmostEqual(record["total_cost_usd"], 0.25)

    def test_split_arm_run_missing_one_split_skill_fails_isolation(self) -> None:
        init = fx.init_event(skills=[*fx.SPLIT_SKILLS[:3], *fx.OTHER_SKILLS])
        record, _ = self.run_it(self.printing_claude(init, fx.result_event()), arm="split")
        self.assertIs(record["isolation_ok"], False)
        self.assertFalse(any("unknown arm" in p for p in record["isolation_problems"]))

    def test_a_baseline_run_that_sees_a_split_skill_fails_isolation(self) -> None:
        init = fx.init_event(skills=["jzr-pdf", *fx.OTHER_SKILLS])
        record, _ = self.run_it(self.printing_claude(init, fx.result_event()), arm="baseline")
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

    def test_split_arm_command_line_has_no_mcp_config_and_the_child_has_the_shim_path(
        self,
    ) -> None:
        setup = self.run_recording(arm="split")
        argv = self.recorded("argv.txt").splitlines()
        self.assertEqual(argv, ["-p", "do the probe thing", *setup.claude_args])
        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("--mcp-config", argv)
        self.assertEqual(self.child_env()["PATH"], f"{setup.sandbox.bin}:{self.system_path}")
        skills = setup.sandbox.config_dir / "skills"
        self.assertEqual({p.name for p in skills.iterdir()}, set(fx.SPLIT_SKILLS))

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


@unittest.skipUnless(os.access(REPO / ".venv" / "bin" / "jzr", os.X_OK), "needs the repo's .venv/bin/jzr")
@unittest.skipIf(find_jzr_on(SYSTEM_PATH) is not None, "a jzr executable is on the system PATH")
class RunAllSplitTest(RunnerCase):
    """run_all with the real default source and a fake claude: the split skills are written once per results folder."""

    def run_all_with(self, *argv: str, claude: str) -> tuple[int, str, Path]:
        out = self.tmp / "results"
        sandboxes = self.tmp / "sandboxes"
        sandboxes.mkdir(exist_ok=True)
        args = build_parser().parse_args(
            ["run", "--tasks", "hello", "--runs", "1", "--out", str(out), "--sandbox-dir", str(sandboxes),
             "--claude", claude, *argv]
        )  # fmt: skip
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok-123"}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = run_all(args)
        return code, stdout.getvalue() + stderr.getvalue(), out

    def test_a_split_run_writes_the_split_skills_into_the_results_folder(self) -> None:
        claude = self.printing_claude(fx.init_for_arm("split"), fx.result_event(text="ok"))
        code, output, out = self.run_all_with("--arms", "split", claude=claude)
        self.assertEqual(code, 0, output)
        folder = out / "split-skills"
        self.assertEqual({p.name for p in folder.iterdir()}, set(fx.SPLIT_SKILLS))
        for name in fx.SPLIT_SKILLS:
            with self.subTest(name=name):
                self.assertIn(f"name: {name}\n", (folder / name / "SKILL.md").read_text())

    def test_the_split_run_is_recorded_with_isolation_ok(self) -> None:
        claude = self.printing_claude(fx.init_for_arm("split"), fx.result_event(text="ok"))
        code, output, out = self.run_all_with("--arms", "split", claude=claude)
        self.assertEqual(code, 0, output)
        records = [json.loads(line) for line in (out / "runs.jsonl").read_text().splitlines() if line.strip()]
        self.assertEqual(
            [(r["arm"], r["isolation_ok"], r["passed"]) for r in records],
            [("split", True, True)],
        )

    def test_the_agent_in_a_split_run_sees_the_four_skills_in_its_config_folder(
        self,
    ) -> None:
        body = (
            f"/usr/bin/ls -1 \"$CLAUDE_CONFIG_DIR/skills\" > '{self.rec / 'skills.txt'}'\n"
            f"/usr/bin/cat '{self.canned(fx.init_for_arm('split'), fx.result_event(text='ok'))}'\nexit 0"
        )
        code, output, _ = self.run_all_with("--arms", "split", claude=self.fake_claude(body))
        self.assertEqual(code, 0, output)
        self.assertEqual(set((self.rec / "skills.txt").read_text().split()), set(fx.SPLIT_SKILLS))

    def test_no_split_skills_are_written_when_split_is_not_among_the_arms(self) -> None:
        claude = self.printing_claude(fx.init_for_arm("baseline"), fx.result_event(text="ok"))
        code, output, out = self.run_all_with("--arms", "baseline", claude=claude)
        self.assertEqual(code, 0, output)
        self.assertFalse((out / "split-skills").exists())

    def test_dry_run_plans_and_prepares_the_split_arm(self) -> None:
        code, output, _ = self.run_all_with("--arms", "split", "--dry-run", claude="/bin/true")
        self.assertEqual(code, 0, output)
        self.assertIn("r01-hello-split", output)
        self.assertIn("first command:", output)
        self.assertNotIn("--mcp-config", output)

    def test_arms_option_accepts_split_alongside_the_others(self) -> None:
        code, output, _ = self.run_all_with("--arms", "baseline,skill,split", "--dry-run", claude="/bin/true")
        self.assertEqual(code, 0, output)
        self.assertIn("3 runs", output)


if __name__ == "__main__":
    unittest.main()
