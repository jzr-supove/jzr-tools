"""Offline tests for jzr_bench.stream: parsing stream-json, jzr-use detection, isolation check."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import stream_fixtures as fx  # noqa: E402
from jzr_bench.stream import check_isolation, is_jzr_skill, mentions_jzr_command, parse_stream  # noqa: E402


class ParseCompleteRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.lines = fx.to_lines(
            fx.init_for_arm("skill"),
            fx.assistant_event(fx.text_block("Let me look."), fx.tool_use("Skill", skill="jzr")),
            fx.user_tool_result("loaded"),
            fx.assistant_event(
                fx.bash("jzr video gif clip.mp4 --start 0:02"),
                fx.tool_use("Read", file_path="a"),
            ),
            fx.user_tool_result("ok"),
            fx.assistant_event(fx.bash("ls -la"), fx.bash("cd d && ffprobe clip.gif")),
            fx.result_event(
                text="Done: clip.gif is ready.",
                cost=0.0421,
                input_tokens=12,
                cache_creation=3456,
                cache_read=78901,
                output_tokens=234,
                num_turns=5,
                duration_ms=23456,
            ),
        )
        self.m = parse_stream(self.lines)

    def test_result_metrics(self) -> None:
        self.assertTrue(self.m.has_result)
        self.assertFalse(self.m.is_error)
        self.assertEqual(self.m.final_text, "Done: clip.gif is ready.")
        self.assertAlmostEqual(self.m.cost_usd, 0.0421)
        self.assertEqual(self.m.input_tokens, 12)
        self.assertEqual(self.m.cache_creation_input_tokens, 3456)
        self.assertEqual(self.m.cache_read_input_tokens, 78901)
        self.assertEqual(self.m.output_tokens, 234)
        self.assertEqual(self.m.num_turns, 5)
        self.assertEqual(self.m.duration_ms, 23456)

    def test_model_usage_is_the_result_model_usage(self) -> None:
        self.assertEqual(list(self.m.model_usage), ["claude-sonnet-5-5"])
        self.assertAlmostEqual(self.m.model_usage["claude-sonnet-5-5"]["costUSD"], 0.0421)

    def test_init_event_is_kept(self) -> None:
        self.assertIsNotNone(self.m.init)
        self.assertIn("jzr", self.m.init["skills"])
        self.assertEqual(self.m.init["model"], "claude-sonnet-5-5")
        self.assertEqual(self.m.init["claude_code_version"], "2.1.332")

    def test_tool_calls_counted_by_name_over_all_assistant_events(self) -> None:
        self.assertEqual(self.m.tool_calls, {"Skill": 1, "Bash": 3, "Read": 1})

    def test_bash_commands_in_order(self) -> None:
        self.assertEqual(
            self.m.bash_commands,
            [
                "jzr video gif clip.mp4 --start 0:02",
                "ls -la",
                "cd d && ffprobe clip.gif",
            ],
        )

    def test_skills_invoked(self) -> None:
        self.assertEqual(self.m.skills_invoked, ["jzr"])

    def test_usage_flags(self) -> None:
        self.assertTrue(self.m.used_jzr_cli)
        self.assertTrue(self.m.used_jzr_skill)
        self.assertFalse(self.m.used_jzr_mcp)

    def test_accepts_a_generator_and_lines_with_trailing_newlines(self) -> None:
        m = parse_stream(line + "\n" for line in self.lines)
        self.assertTrue(m.has_result)
        self.assertEqual(m.num_turns, 5)
        self.assertEqual(m.bash_commands[0], "jzr video gif clip.mp4 --start 0:02")


class ParseErrorResultTest(unittest.TestCase):
    """Shape of a real probe run: auth failure, zero usage, empty modelUsage."""

    def test_error_result_with_zero_usage_keeps_zeros(self) -> None:
        init = fx.init_event(
            skills=["jzr", "debug"],
            mcp_servers=[{"name": "jzr", "status": "connected"}],
        )
        failed = fx.result_event(
            text="Invalid API key · Fix external API key",
            is_error=True,
            cost=0,
            input_tokens=0,
            cache_creation=0,
            cache_read=0,
            output_tokens=0,
            num_turns=1,
            duration_ms=455,
            model_usage={},
        )
        m = parse_stream(fx.to_lines(init, failed))
        self.assertTrue(m.has_result)
        self.assertTrue(m.is_error)
        self.assertEqual(m.final_text, "Invalid API key · Fix external API key")
        # zero is a real value, not "missing"
        self.assertEqual(m.cost_usd, 0)
        self.assertIsNotNone(m.cost_usd)
        self.assertEqual(m.input_tokens, 0)
        self.assertIsNotNone(m.input_tokens)
        self.assertEqual(m.output_tokens, 0)
        self.assertEqual(m.num_turns, 1)
        self.assertEqual(m.duration_ms, 455)
        self.assertEqual(m.model_usage, {})
        self.assertEqual(m.tool_calls, {})
        self.assertEqual(m.bash_commands, [])


class AuthFailureTest(unittest.TestCase):
    """A rejected credential (shape of a real 401 result) is flagged so the runner can stop."""

    def test_401_result_is_an_auth_failure(self) -> None:
        failed = fx.result_event(
            text="Invalid API key · Fix external API key",
            is_error=True,
            cost=0,
            model_usage={},
        )
        failed.update(api_error_status=401, api_error_type="authentication_error")
        self.assertTrue(parse_stream(fx.to_lines(fx.init_for_arm("baseline"), failed)).auth_failed)

    def test_other_results_are_not(self) -> None:
        self.assertFalse(parse_stream(fx.to_lines(fx.result_event())).auth_failed)
        overloaded = fx.result_event(is_error=True)
        overloaded.update(api_error_status=529, api_error_type="overloaded_error")
        self.assertFalse(parse_stream(fx.to_lines(overloaded)).auth_failed)
        self.assertFalse(parse_stream([]).auth_failed)


class ParseRobustnessTest(unittest.TestCase):
    def test_blank_and_garbage_lines_are_skipped(self) -> None:
        lines = [
            "",
            "   ",
            "\n",
            "not json at all",
            "{broken json",
            *fx.to_lines(fx.init_for_arm("baseline")),
            "Warning: something on stdout",
            *fx.to_lines(fx.assistant_event(fx.bash("echo hi"))),
            '{"type": "assistant", "message": ',  # truncated line
            *fx.to_lines(fx.result_event(text="ok", num_turns=2)),
            "",
        ]
        m = parse_stream(lines)
        self.assertTrue(m.has_result)
        self.assertEqual(m.num_turns, 2)
        self.assertEqual(m.bash_commands, ["echo hi"])
        self.assertIsNotNone(m.init)

    def test_last_result_event_wins(self) -> None:
        m = parse_stream(
            fx.to_lines(
                fx.result_event(text="first", is_error=True, cost=1.0, num_turns=1),
                fx.result_event(text="second", is_error=False, cost=2.5, num_turns=9),
            )
        )
        self.assertEqual(m.final_text, "second")
        self.assertFalse(m.is_error)
        self.assertEqual(m.cost_usd, 2.5)
        self.assertEqual(m.num_turns, 9)

    def test_no_result_event(self) -> None:
        m = parse_stream(
            fx.to_lines(
                fx.init_for_arm("baseline"),
                fx.assistant_event(fx.bash("sleep 1")),
            )
        )
        self.assertFalse(m.has_result)
        self.assertTrue(m.is_error)
        self.assertEqual(m.final_text, "")
        self.assertIsNone(m.cost_usd)
        self.assertIsNone(m.input_tokens)
        self.assertIsNone(m.cache_creation_input_tokens)
        self.assertIsNone(m.cache_read_input_tokens)
        self.assertIsNone(m.output_tokens)
        self.assertIsNone(m.num_turns)
        self.assertIsNone(m.duration_ms)
        self.assertEqual(m.model_usage, {})
        # what was seen before the cut-off is still reported
        self.assertEqual(m.bash_commands, ["sleep 1"])
        self.assertEqual(m.tool_calls, {"Bash": 1})
        self.assertIsNotNone(m.init)

    def test_empty_stream(self) -> None:
        m = parse_stream([])
        self.assertIsNone(m.init)
        self.assertFalse(m.has_result)
        self.assertTrue(m.is_error)
        self.assertEqual(m.tool_calls, {})
        self.assertEqual(m.bash_commands, [])
        self.assertEqual(m.skills_invoked, [])
        self.assertFalse(m.used_jzr_cli)
        self.assertFalse(m.used_jzr_skill)
        self.assertFalse(m.used_jzr_mcp)

    def test_text_only_assistant_events_count_no_tools(self) -> None:
        m = parse_stream(fx.to_lines(fx.assistant_event(fx.text_block("thinking")), fx.result_event()))
        self.assertEqual(m.tool_calls, {})

    def test_tool_results_in_user_events_are_not_tool_calls(self) -> None:
        m = parse_stream(fx.to_lines(fx.user_tool_result("jzr video gif"), fx.result_event()))
        self.assertEqual(m.tool_calls, {})
        self.assertFalse(m.used_jzr_cli)

    def test_bash_command_is_recorded_verbatim(self) -> None:
        command = "ffmpeg -ss 2 -t 3 -i clip.mp4 \\\n  -vf 'fps=10' clip.gif"
        m = parse_stream(fx.to_lines(fx.assistant_event(fx.bash(command))))
        self.assertEqual(m.bash_commands, [command])


class JzrUseDetectionTest(unittest.TestCase):
    @staticmethod
    def parse(*blocks: dict) -> object:
        return parse_stream(fx.to_lines(fx.assistant_event(*blocks), fx.result_event()))

    def test_skill_flag_for_jzr_and_namespaced_jzr(self) -> None:
        for name in ("jzr", "jzr-tools:jzr", "plugin:jzr"):
            with self.subTest(skill=name):
                m = self.parse(fx.tool_use("Skill", skill=name))
                self.assertTrue(m.used_jzr_skill)
                self.assertEqual(m.skills_invoked, [name])

    def test_skill_flag_is_false_for_other_skills(self) -> None:
        for name in ("debug", "jzr-extra", "my-jzr", "jzr:other", "jzrx"):
            with self.subTest(skill=name):
                m = self.parse(fx.tool_use("Skill", skill=name))
                self.assertFalse(m.used_jzr_skill)
                self.assertEqual(m.skills_invoked, [name])

    def test_skill_invocations_all_listed_in_order(self) -> None:
        m = self.parse(fx.tool_use("Skill", skill="debug"), fx.tool_use("Skill", skill="jzr"))
        self.assertEqual(m.skills_invoked, ["debug", "jzr"])
        self.assertTrue(m.used_jzr_skill)

    def test_mcp_flag_for_jzr_server_tools(self) -> None:
        m = self.parse(fx.tool_use("mcp__jzr__video_gif", path="clip.mp4"))
        self.assertTrue(m.used_jzr_mcp)
        self.assertEqual(m.tool_calls, {"mcp__jzr__video_gif": 1})
        self.assertFalse(m.used_jzr_cli)
        self.assertFalse(m.used_jzr_skill)

    def test_mcp_flag_is_false_for_other_servers_and_lookalikes(self) -> None:
        for name in ("mcp__other__jzr", "mcp__jzrx__tool", "jzr", "Read"):
            with self.subTest(tool=name):
                self.assertFalse(self.parse(fx.tool_use(name)).used_jzr_mcp)

    def test_cli_flag_from_bash_commands(self) -> None:
        self.assertTrue(self.parse(fx.bash("jzr image info a.png")).used_jzr_cli)
        self.assertTrue(self.parse(fx.bash("ls && /tmp/s/bin/jzr -h")).used_jzr_cli)

    def test_cli_flag_false_when_jzr_is_only_a_word_in_the_command(self) -> None:
        self.assertFalse(self.parse(fx.bash("echo jzr-tools")).used_jzr_cli)
        self.assertFalse(self.parse(fx.bash("cat jzr.txt")).used_jzr_cli)

    def test_cli_flag_true_if_any_command_uses_jzr(self) -> None:
        m = self.parse(fx.bash("ls"), fx.bash("jzr hash f"), fx.bash("pwd"))
        self.assertTrue(m.used_jzr_cli)

    def test_cli_flag_ignores_non_bash_tools(self) -> None:
        m = self.parse(
            fx.tool_use("Read", file_path="jzr"),
            fx.tool_use("Write", file_path="x", content="jzr hash f"),
        )
        self.assertFalse(m.used_jzr_cli)


class MentionsJzrCommandTest(unittest.TestCase):
    def test_runs_as_a_command_word(self) -> None:
        for command in (
            "jzr video gif x",
            "cd d && jzr image info a",
            "/tmp/s/bin/jzr -h",
            "uvx --from jzr-tools jzr hash f",
            "jzr",
            "jzr --help",
            "  jzr video info clip.mp4  ",
            "echo start; jzr hash f",
            "ls | jzr text slug",
            "false || jzr hash f",
            "echo a\njzr hash f",
            "/home/me/project/.venv/bin/jzr video gif clip.mp4",
        ):
            with self.subTest(command=command):
                self.assertTrue(mentions_jzr_command(command))

    def test_not_a_command(self) -> None:
        for command in (
            "echo jzr-tools",
            "cat jzr.txt",
            "ls ./jzrdir",
            "grep -r myjzr .",
            "",
            "ffmpeg -i clip.mp4 clip.gif",
            "python3 -c 'print(1)'",
            "ls /tmp/jzr-bench",
            # another program whose name merely contains jzr
            "jzr-tools --help",
            "myjzr hash f",
            "jzr.py hash f",
            "./jzrdir/run.sh",
            "/opt/jzrx/bin/tool a",
        ):
            with self.subTest(command=command):
                self.assertFalse(mentions_jzr_command(command))

    def test_jzr_as_a_plain_argument_is_not_a_command_word(self) -> None:
        # "jzr runs as a command word": naming it as an argument does not run it.
        for command in ("echo jzr", "which jzr", "cat jzr"):
            with self.subTest(command=command):
                self.assertFalse(mentions_jzr_command(command))


class CheckIsolationTest(unittest.TestCase):
    def mcp_init(self, **overrides):
        base = {
            "tools": fx.BASE_TOOLS + fx.JZR_TOOLS,
            "mcp_servers": [{"name": "jzr", "status": "connected"}],
            "skills": list(fx.OTHER_SKILLS),
        }
        base.update(overrides)
        return fx.init_event(**base)

    def test_good_inits_pass_for_their_arm(self) -> None:
        for arm in ("baseline", "skill", "mcp", "split"):
            with self.subTest(arm=arm):
                self.assertEqual(check_isolation(arm, fx.init_for_arm(arm)), [])

    def test_no_init_event_is_a_problem_in_every_arm(self) -> None:
        for arm in ("baseline", "skill", "mcp", "split"):
            with self.subTest(arm=arm):
                problems = check_isolation(arm, None)
                self.assertTrue(problems)
                self.assertTrue(all(isinstance(p, str) and p for p in problems))

    # baseline: no jzr skill, no jzr MCP server, no mcp__jzr__* tool

    def test_baseline_rejects_jzr_skill(self) -> None:
        self.assertTrue(check_isolation("baseline", fx.init_event(skills=["jzr", "debug"])))

    def test_baseline_rejects_namespaced_jzr_skill(self) -> None:
        self.assertTrue(check_isolation("baseline", fx.init_event(skills=["debug", "jzr-tools:jzr"])))

    def test_baseline_rejects_jzr_mcp_server(self) -> None:
        init = fx.init_event(mcp_servers=[{"name": "jzr", "status": "connected"}])
        self.assertTrue(check_isolation("baseline", init))
        init = fx.init_event(mcp_servers=[{"name": "jzr", "status": "failed"}])
        self.assertTrue(check_isolation("baseline", init))

    def test_baseline_rejects_jzr_mcp_tool(self) -> None:
        self.assertTrue(check_isolation("baseline", fx.init_event(tools=fx.BASE_TOOLS + ["mcp__jzr__hash"])))

    def test_baseline_ignores_lookalike_names(self) -> None:
        init = fx.init_event(
            skills=["jzr-extra", "myjzr", "other:jzr2"],
            mcp_servers=[{"name": "jzr2", "status": "connected"}],
            tools=fx.BASE_TOOLS + ["mcp__jzr2__x", "mcp__other__jzr"],
        )
        self.assertEqual(check_isolation("baseline", init), [])

    # skill: shows the jzr skill, no jzr MCP tool

    def test_skill_arm_accepts_a_namespaced_jzr_skill(self) -> None:
        init = fx.init_event(skills=["jzr-tools:jzr", "debug"])
        self.assertEqual(check_isolation("skill", init), [])

    def test_skill_arm_requires_the_jzr_skill(self) -> None:
        self.assertTrue(check_isolation("skill", fx.init_event()))
        self.assertTrue(check_isolation("skill", fx.init_event(skills=["jzr-extra"])))

    def test_skill_arm_rejects_jzr_mcp_tool(self) -> None:
        init = fx.init_event(skills=["jzr", "debug"], tools=fx.BASE_TOOLS + ["mcp__jzr__video_gif"])
        self.assertTrue(check_isolation("skill", init))

    # mcp: server jzr connected, at least one mcp__jzr__ tool, no jzr skill

    def test_mcp_arm_rejects_missing_server(self) -> None:
        self.assertTrue(check_isolation("mcp", self.mcp_init(mcp_servers=[])))

    def test_mcp_arm_rejects_server_that_is_not_connected(self) -> None:
        for status in ("failed", "pending", "needs-auth"):
            with self.subTest(status=status):
                init = self.mcp_init(mcp_servers=[{"name": "jzr", "status": status}])
                self.assertTrue(check_isolation("mcp", init))

    def test_mcp_arm_rejects_a_connected_server_with_another_name(self) -> None:
        init = self.mcp_init(mcp_servers=[{"name": "jzr2", "status": "connected"}])
        self.assertTrue(check_isolation("mcp", init))

    def test_mcp_arm_requires_at_least_one_jzr_tool(self) -> None:
        self.assertTrue(check_isolation("mcp", self.mcp_init(tools=list(fx.BASE_TOOLS))))
        self.assertTrue(check_isolation("mcp", self.mcp_init(tools=fx.BASE_TOOLS + ["mcp__other__hash"])))

    def test_mcp_arm_accepts_a_single_jzr_tool(self) -> None:
        self.assertEqual(
            check_isolation("mcp", self.mcp_init(tools=fx.BASE_TOOLS + ["mcp__jzr__hash"])),
            [],
        )

    def test_mcp_arm_rejects_jzr_skill(self) -> None:
        self.assertTrue(check_isolation("mcp", self.mcp_init(skills=["jzr", "debug"])))
        self.assertTrue(check_isolation("mcp", self.mcp_init(skills=["jzr-tools:jzr"])))

    def test_mcp_arm_accepts_other_connected_servers_alongside(self) -> None:
        init = self.mcp_init(
            mcp_servers=[
                {"name": "other", "status": "failed"},
                {"name": "jzr", "status": "connected"},
            ]
        )
        self.assertEqual(check_isolation("mcp", init), [])

    def test_a_multi_rule_violation_reports_every_rule(self) -> None:
        # baseline with skill + server + tool all present: three separate violations.
        init = fx.init_event(
            skills=["jzr"],
            mcp_servers=[{"name": "jzr", "status": "connected"}],
            tools=fx.BASE_TOOLS + ["mcp__jzr__hash"],
        )
        self.assertGreaterEqual(len(check_isolation("baseline", init)), 3)

    def test_init_from_parse_stream_round_trips(self) -> None:
        from jzr_bench.stream import parse_stream

        for arm in ("baseline", "skill", "mcp", "split"):
            with self.subTest(arm=arm):
                m = parse_stream([json.dumps(fx.init_for_arm(arm))])
                self.assertEqual(check_isolation(arm, m.init), [])


SPLIT = fx.SPLIT_SKILLS


class IsJzrSkillTest(unittest.TestCase):
    def test_the_jzr_skill_with_or_without_a_plugin_prefix(self) -> None:
        for name in ("jzr", "jzr-tools:jzr", "plugin:jzr"):
            with self.subTest(name=name):
                self.assertTrue(is_jzr_skill(name))

    def test_the_four_split_skills(self) -> None:
        for name in SPLIT:
            with self.subTest(name=name):
                self.assertTrue(is_jzr_skill(name))

    def test_a_split_skill_with_a_plugin_prefix(self) -> None:
        self.assertTrue(is_jzr_skill("plugin:jzr-pdf"))
        for name in SPLIT:
            with self.subTest(name=name):
                self.assertTrue(is_jzr_skill(f"jzr-tools:{name}"))

    def test_lookalikes_are_not_jzr_skills(self) -> None:
        for name in (
            "jzr-extra",
            "jzr-pdfx",
            "xjzr-pdf",
            "jzr-medias",
            "jzr-",
            "plugin:jzr-extra",
            "plugin:jzr-pdfx",
            "jzr-pdf:extra",
            "myjzr",
            "jzr2",
            "debug",
            "",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_jzr_skill(name))


class SplitSkillUseTest(unittest.TestCase):
    def test_a_skill_call_to_a_split_skill_is_jzr_skill_use(self) -> None:
        lines = fx.to_lines(
            fx.init_for_arm("split"),
            fx.assistant_event(fx.tool_use("Skill", skill="jzr-media")),
            fx.result_event(),
        )
        m = parse_stream(lines)
        self.assertEqual(m.skills_invoked, ["jzr-media"])
        self.assertTrue(m.used_jzr_skill)
        self.assertFalse(m.used_jzr_cli)
        self.assertFalse(m.used_jzr_mcp)

    def test_each_split_skill_counts(self) -> None:
        for name in SPLIT:
            with self.subTest(name=name):
                m = parse_stream(fx.to_lines(fx.assistant_event(fx.tool_use("Skill", skill=name))))
                self.assertTrue(m.used_jzr_skill)

    def test_a_skill_call_to_a_lookalike_or_other_skill_does_not_count(self) -> None:
        for name in ("jzr-extra", "jzr-pdfx", "debug"):
            with self.subTest(name=name):
                m = parse_stream(fx.to_lines(fx.assistant_event(fx.tool_use("Skill", skill=name))))
                self.assertEqual(m.skills_invoked, [name])
                self.assertFalse(m.used_jzr_skill)

    def test_no_skill_call_means_no_skill_use_even_when_the_skills_are_listed(
        self,
    ) -> None:
        m = parse_stream(
            fx.to_lines(
                fx.init_for_arm("split"),
                fx.assistant_event(fx.bash("ls")),
                fx.result_event(),
            )
        )
        self.assertFalse(m.used_jzr_skill)


class CheckIsolationSplitTest(unittest.TestCase):
    """split: the four split skills, no jzr skill, no jzr MCP tool or server. Others: none of the four."""

    def assertProblem(self, arm: str, init: dict) -> None:
        problems = check_isolation(arm, init)
        self.assertTrue(problems)
        self.assertTrue(all(isinstance(p, str) and p for p in problems))
        # split is a known arm: the problem must be about the init, not about the arm name
        self.assertFalse(any("unknown arm" in p for p in problems), problems)

    def split_init(self, **overrides):
        base = {"skills": [*SPLIT, *fx.OTHER_SKILLS]}
        base.update(overrides)
        return fx.init_event(**base)

    def test_a_good_init_passes(self) -> None:
        self.assertEqual(check_isolation("split", self.split_init()), [])

    def test_the_order_of_the_skills_does_not_matter(self) -> None:
        self.assertEqual(
            check_isolation("split", self.split_init(skills=[*fx.OTHER_SKILLS, *reversed(SPLIT)])),
            [],
        )

    def test_each_split_skill_is_required(self) -> None:
        for missing in SPLIT:
            with self.subTest(missing=missing):
                skills = [s for s in SPLIT if s != missing] + list(fx.OTHER_SKILLS)
                self.assertProblem("split", self.split_init(skills=skills))

    def test_no_split_skills_at_all_is_a_problem(self) -> None:
        self.assertProblem("split", self.split_init(skills=list(fx.OTHER_SKILLS)))

    def test_lookalike_skills_do_not_stand_in_for_a_split_skill(self) -> None:
        skills = ["jzr-media", "jzr-pdf", "jzr-data", "jzr-systemx", "jzr-extra"]
        self.assertProblem("split", self.split_init(skills=skills))

    def test_lookalike_skills_next_to_the_four_are_fine(self) -> None:
        skills = [*SPLIT, "jzr-extra", "jzr-pdfx", "debug"]
        self.assertEqual(check_isolation("split", self.split_init(skills=skills)), [])

    def test_the_jzr_skill_is_a_problem(self) -> None:
        self.assertProblem("split", self.split_init(skills=["jzr", *SPLIT]))
        self.assertProblem("split", self.split_init(skills=["jzr-tools:jzr", *SPLIT]))

    def test_a_jzr_mcp_tool_is_a_problem(self) -> None:
        self.assertProblem("split", self.split_init(tools=fx.BASE_TOOLS + ["mcp__jzr__hash"]))

    def test_other_mcp_tools_are_not(self) -> None:
        self.assertEqual(
            check_isolation("split", self.split_init(tools=fx.BASE_TOOLS + ["mcp__other__hash"])),
            [],
        )

    def test_a_jzr_mcp_server_is_a_problem_whatever_its_status(self) -> None:
        for status in ("connected", "failed", "pending"):
            with self.subTest(status=status):
                self.assertProblem(
                    "split",
                    self.split_init(mcp_servers=[{"name": "jzr", "status": status}]),
                )

    def test_another_mcp_server_is_not(self) -> None:
        init = self.split_init(mcp_servers=[{"name": "other", "status": "connected"}])
        self.assertEqual(check_isolation("split", init), [])

    def test_every_violated_rule_is_reported(self) -> None:
        init = self.split_init(
            skills=["jzr", *SPLIT[:2]],  # two split skills missing, jzr present
            tools=fx.BASE_TOOLS + ["mcp__jzr__hash"],
            mcp_servers=[{"name": "jzr", "status": "connected"}],
        )
        problems = check_isolation("split", init)
        self.assertGreaterEqual(len(problems), 3)
        self.assertFalse(any("unknown arm" in p for p in problems), problems)

    def test_other_arms_report_each_split_skill_as_a_problem(self) -> None:
        for arm in ("baseline", "skill", "mcp"):
            for name in SPLIT:
                with self.subTest(arm=arm, skill=name):
                    good = fx.init_for_arm(arm)
                    init = dict(good, skills=[*good["skills"], name])
                    self.assertProblem(arm, init)

    def test_other_arms_report_a_prefixed_split_skill(self) -> None:
        for arm in ("baseline", "skill", "mcp"):
            with self.subTest(arm=arm):
                good = fx.init_for_arm(arm)
                self.assertProblem(arm, dict(good, skills=[*good["skills"], "jzr-tools:jzr-pdf"]))

    def test_the_skill_arm_still_requires_the_jzr_skill(self) -> None:
        self.assertProblem("skill", fx.init_event(skills=[*SPLIT, *fx.OTHER_SKILLS]))
        self.assertEqual(check_isolation("skill", fx.init_for_arm("skill")), [])

    def test_the_other_arms_ignore_lookalikes_of_the_split_names(self) -> None:
        for arm in ("baseline", "skill", "mcp"):
            with self.subTest(arm=arm):
                good = fx.init_for_arm(arm)
                self.assertEqual(
                    check_isolation(
                        arm,
                        dict(good, skills=[*good["skills"], "jzr-extra", "jzr-pdfx"]),
                    ),
                    [],
                )

    def test_a_split_init_is_a_problem_for_the_baseline_skill_and_mcp_arms(
        self,
    ) -> None:
        init = fx.init_for_arm("split")
        for arm in ("baseline", "skill", "mcp"):
            with self.subTest(arm=arm):
                self.assertProblem(arm, init)

    def test_the_other_arms_inits_are_a_problem_for_the_split_arm(self) -> None:
        for arm in ("baseline", "skill", "mcp"):
            with self.subTest(arm=arm):
                self.assertProblem("split", fx.init_for_arm(arm))


if __name__ == "__main__":
    unittest.main()
