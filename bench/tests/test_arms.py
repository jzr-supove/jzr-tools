"""Offline tests for jzr_bench.arms: per-arm sandbox, environment and claude arguments."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from jzr_bench import arms  # noqa: E402
from jzr_bench.arms import ARMS, Auth, JzrSource, find_jzr_on, pick_auth, prepare_arm  # noqa: E402

OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"
APIKEY = "ANTHROPIC_API_KEY"
SPLIT_NAMES = ("jzr-media", "jzr-pdf", "jzr-data", "jzr-system")
ALLOWED_KEYS = {"HOME", "CLAUDE_CONFIG_DIR", "TMPDIR", "PATH", "LANG", "TERM"}


def make_executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


class ArmTestCase(unittest.TestCase):
    """Builds a fake repo (jzr binary, skill folder) and an empty system PATH folder per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

        # The fake jzr prints one argument per line, so the shim test can see exact argv.
        self.jzr_bin = make_executable(
            self.tmp / "repo dir" / ".venv" / "bin" / "jzr",
            '#!/bin/sh\necho "fake-jzr"\nfor a in "$@"; do echo "arg:$a"; done\n',
        )
        self.skill_dir = self.tmp / "repo dir" / "skill" / "jzr"
        (self.skill_dir / "reference").mkdir(parents=True)
        (self.skill_dir / "SKILL.md").write_text("---\nname: jzr\n---\nUse jzr.\n")
        (self.skill_dir / "reference" / "video.md").write_text("video notes\n")
        # What write_split leaves behind: one folder per split skill, each with its own files.
        self.split_dir = self.tmp / "repo dir" / "split-skills"
        for name in SPLIT_NAMES:
            (self.split_dir / name / "reference").mkdir(parents=True)
            (self.split_dir / name / "SKILL.md").write_text(f"---\nname: {name}\n---\nBody of {name}.\n")
            (self.split_dir / name / "reference" / "notes.md").write_text(f"notes of {name}\n")
        self.source = JzrSource(
            jzr_bin=self.jzr_bin,
            skill_dir=self.skill_dir,
            mcp_command=[str(self.jzr_bin), "agent", "mcp"],
            split_dir=self.split_dir,
        )
        self.system_path = str(self.tmp / "syspath")
        (self.tmp / "syspath").mkdir()
        self.auth = Auth(var=OAUTH, value="oauth-secret-value")

    def prepare(
        self,
        arm: str,
        *,
        auth: Auth | None = None,
        model: str = "sonnet",
        max_turns: int = 30,
    ):
        root = self.tmp / f"sandbox-{arm}"
        root.mkdir(exist_ok=True)
        return prepare_arm(
            arm,
            root,
            self.source,
            auth or self.auth,
            model=model,
            max_turns=max_turns,
            system_path=self.system_path,
        )


class ConstantsTest(unittest.TestCase):
    def test_arms_and_system_path(self) -> None:
        self.assertEqual(tuple(ARMS), ("baseline", "skill", "mcp", "split"))
        self.assertEqual(arms.SYSTEM_PATH, "/usr/local/bin:/usr/bin:/bin")


class PickAuthTest(unittest.TestCase):
    def test_oauth_token_wins_over_api_key(self) -> None:
        auth = pick_auth({OAUTH: "tok", APIKEY: "key"})
        self.assertIsNotNone(auth)
        self.assertEqual((auth.var, auth.value), (OAUTH, "tok"))

    def test_api_key_when_no_oauth_token(self) -> None:
        auth = pick_auth({APIKEY: "key"})
        self.assertIsNotNone(auth)
        self.assertEqual((auth.var, auth.value), (APIKEY, "key"))

    def test_empty_oauth_token_is_ignored(self) -> None:
        auth = pick_auth({OAUTH: "", APIKEY: "key"})
        self.assertIsNotNone(auth)
        self.assertEqual((auth.var, auth.value), (APIKEY, "key"))

    def test_none_when_neither_is_usable(self) -> None:
        self.assertIsNone(pick_auth({}))
        self.assertIsNone(pick_auth({OAUTH: "", APIKEY: ""}))
        self.assertIsNone(pick_auth({"PATH": "/usr/bin", "HOME": "/home/x"}))

    def test_empty_api_key_is_ignored_when_oauth_set(self) -> None:
        auth = pick_auth({OAUTH: "tok", APIKEY: ""})
        self.assertIsNotNone(auth)
        self.assertEqual(auth.var, OAUTH)


class FindJzrOnTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_none_when_no_folder_has_jzr(self) -> None:
        (self.tmp / "a").mkdir()
        (self.tmp / "b").mkdir()
        self.assertIsNone(find_jzr_on(f"{self.tmp / 'a'}:{self.tmp / 'b'}"))

    def test_finds_executable_in_later_folder(self) -> None:
        (self.tmp / "a").mkdir()
        exe = make_executable(self.tmp / "b" / "jzr")
        self.assertEqual(find_jzr_on(f"{self.tmp / 'a'}:{self.tmp / 'b'}"), exe)

    def test_first_match_wins(self) -> None:
        first = make_executable(self.tmp / "a" / "jzr")
        make_executable(self.tmp / "b" / "jzr")
        self.assertEqual(find_jzr_on(f"{self.tmp / 'a'}:{self.tmp / 'b'}"), first)

    def test_non_executable_file_is_not_a_hit(self) -> None:
        (self.tmp / "a").mkdir()
        (self.tmp / "a" / "jzr").write_text("not executable")
        (self.tmp / "a" / "jzr").chmod(0o644)
        self.assertIsNone(find_jzr_on(str(self.tmp / "a")))

    def test_other_names_and_missing_folders_are_ignored(self) -> None:
        make_executable(self.tmp / "a" / "jzr-tools")
        make_executable(self.tmp / "a" / "myjzr")
        self.assertIsNone(find_jzr_on(f"{self.tmp / 'missing'}:{self.tmp / 'a'}"))

    def test_empty_path_string(self) -> None:
        self.assertIsNone(find_jzr_on(""))


class PrepareArmEnvironmentTest(ArmTestCase):
    def test_env_has_exactly_the_allowlisted_keys_in_every_arm(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm)
                self.assertEqual(set(setup.env), ALLOWED_KEYS | {OAUTH})

    def test_nothing_from_the_runner_environment_leaks(self) -> None:
        polluted = {
            "VIRTUAL_ENV": "/home/someone/.venv",
            "CLAUDECODE": "claudecode-leak-flag",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "CLAUDE_CODE_SESSION_ID": "leak-session",
            "ANTHROPIC_API_KEY": "sk-should-not-leak",
            "BENCH_SENTINEL": "sentinel-value-123",
            "PYTHONPATH": "/leak/pythonpath",
            "XDG_CONFIG_HOME": "/leak/xdg",
        }
        with mock.patch.dict(os.environ, polluted):
            for arm in ARMS:
                with self.subTest(arm=arm):
                    setup = self.prepare(arm)  # auth is the OAuth token
                    for key in polluted:
                        self.assertNotIn(key, setup.env)
                    joined = "\n".join(setup.env.values())
                    for leaked in polluted.values():
                        self.assertNotIn(leaked, joined)
                    self.assertNotIn(APIKEY, setup.env)

    def test_only_the_chosen_auth_variable_is_passed(self) -> None:
        setup = self.prepare("baseline", auth=Auth(var=APIKEY, value="sk-abc"))
        self.assertEqual(setup.env[APIKEY], "sk-abc")
        self.assertNotIn(OAUTH, setup.env)
        self.assertEqual(set(setup.env), ALLOWED_KEYS | {APIKEY})

    def test_oauth_value_is_passed_verbatim(self) -> None:
        setup = self.prepare("baseline")
        self.assertEqual(setup.env[OAUTH], "oauth-secret-value")

    def test_fixed_values_and_sandbox_locations(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm)
                sb = setup.sandbox
                self.assertEqual(setup.env["LANG"], "C.UTF-8")
                self.assertEqual(setup.env["TERM"], "dumb")
                self.assertEqual(setup.env["HOME"], str(sb.home))
                self.assertEqual(setup.env["CLAUDE_CONFIG_DIR"], str(sb.config_dir))
                self.assertEqual(setup.env["TMPDIR"], str(sb.tmp))
                self.assertEqual(sb.home, sb.root / "home")
                self.assertEqual(sb.config_dir, sb.root / "home" / ".claude")
                self.assertEqual(sb.work, sb.root / "work")
                self.assertEqual(sb.tmp, sb.root / "tmp")
                self.assertEqual(sb.bin, sb.root / "bin")
                self.assertEqual(sb.mcp_config, sb.root / "mcp.json")
                self.assertEqual(setup.arm, arm)

    def test_sandbox_never_points_at_the_real_home(self) -> None:
        real_home = Path(os.path.expanduser("~")).resolve()
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm)
                self.assertNotEqual(Path(setup.env["HOME"]).resolve(), real_home)
                self.assertNotEqual(
                    Path(setup.env["CLAUDE_CONFIG_DIR"]).resolve(),
                    real_home / ".claude",
                )

    def test_core_directories_are_created(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                sb = self.prepare(arm).sandbox
                for d in (sb.home, sb.config_dir, sb.work, sb.tmp):
                    self.assertTrue(d.is_dir(), d)

    def test_path_per_arm(self) -> None:
        self.assertEqual(self.prepare("baseline").env["PATH"], self.system_path)
        self.assertEqual(self.prepare("mcp").env["PATH"], self.system_path)
        for arm in ("skill", "split"):
            with self.subTest(arm=arm):
                setup = self.prepare(arm)
                self.assertEqual(setup.env["PATH"], f"{setup.sandbox.bin}:{self.system_path}")

    def test_two_sandboxes_do_not_share_state(self) -> None:
        a = self.prepare("skill")
        root_b = self.tmp / "sandbox-other"
        root_b.mkdir()
        b = prepare_arm(
            "skill",
            root_b,
            self.source,
            self.auth,
            model="sonnet",
            max_turns=30,
            system_path=self.system_path,
        )
        self.assertNotEqual(a.env["HOME"], b.env["HOME"])
        self.assertNotEqual(a.env["TMPDIR"], b.env["TMPDIR"])
        self.assertNotEqual(a.sandbox.work, b.sandbox.work)


class PrepareArmFilesTest(ArmTestCase):
    def test_skill_copied_only_in_skill_arm(self) -> None:
        skill = self.prepare("skill")
        copied = skill.sandbox.config_dir / "skills" / "jzr"
        self.assertEqual((copied / "SKILL.md").read_text(), (self.skill_dir / "SKILL.md").read_text())
        self.assertEqual((copied / "reference" / "video.md").read_text(), "video notes\n")
        self.assertEqual({p.name for p in (skill.sandbox.config_dir / "skills").iterdir()}, {"jzr"})
        for arm in ("baseline", "mcp"):
            with self.subTest(arm=arm):
                sb = self.prepare(arm).sandbox
                self.assertFalse((sb.config_dir / "skills").exists())
        # split has skills too, but never the jzr one
        split = self.prepare("split").sandbox
        self.assertFalse((split.config_dir / "skills" / "jzr").exists())

    def test_skill_copy_is_a_copy_not_a_link_to_the_repo(self) -> None:
        skill = self.prepare("skill")
        copied = skill.sandbox.config_dir / "skills" / "jzr" / "SKILL.md"
        self.assertFalse(copied.is_symlink())
        copied.write_text("changed in the sandbox")
        self.assertIn("Use jzr.", (self.skill_dir / "SKILL.md").read_text())

    def test_bin_shim_only_in_skill_and_split_arms(self) -> None:
        for arm in ("skill", "split"):
            with self.subTest(arm=arm):
                shim = self.prepare(arm).sandbox.bin / "jzr"
                self.assertTrue(shim.is_file())
                self.assertTrue(os.access(shim, os.X_OK))
                self.assertEqual(shim.stat().st_mode & 0o777, 0o755)
                self.assertTrue(shim.read_text().startswith("#!/bin/sh"))
        for arm in ("baseline", "mcp"):
            with self.subTest(arm=arm):
                self.assertFalse(self.prepare(arm).sandbox.bin.exists())

    def test_shim_runs_the_given_jzr_bin_with_the_arguments(self) -> None:
        # The fake jzr lives in a folder with a space, which also checks the shim quotes the path.
        skill = self.prepare("skill")
        shim = skill.sandbox.bin / "jzr"
        proc = subprocess.run(
            [str(shim), "video", "gif", "a file.mp4", "--start", "0:02"],
            capture_output=True,
            text=True,
            env=skill.env,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout.splitlines(),
            [
                "fake-jzr",
                "arg:video",
                "arg:gif",
                "arg:a file.mp4",
                "arg:--start",
                "arg:0:02",
            ],
        )

    def test_shim_is_found_by_name_through_the_arm_path(self) -> None:
        skill = self.prepare("skill")
        proc = subprocess.run(
            ["/bin/sh", "-c", "jzr hash x"],
            capture_output=True,
            text=True,
            env=skill.env,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), ["fake-jzr", "arg:hash", "arg:x"])

    def test_shim_propagates_the_exit_code(self) -> None:
        make_executable(self.jzr_bin, "#!/bin/sh\nexit 7\n")
        skill = self.prepare("skill")
        proc = subprocess.run(
            [str(skill.sandbox.bin / "jzr")],
            env=skill.env,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 7)

    def test_mcp_json_only_in_mcp_arm_with_the_right_shape(self) -> None:
        mcp = self.prepare("mcp")
        config = json.loads(mcp.sandbox.mcp_config.read_text())
        self.assertEqual(
            config,
            {"mcpServers": {"jzr": {"command": str(self.jzr_bin), "args": ["agent", "mcp"]}}},
        )
        for arm in ("baseline", "skill", "split"):
            with self.subTest(arm=arm):
                self.assertFalse(self.prepare(arm).sandbox.mcp_config.exists())

    def test_mcp_json_follows_the_given_command(self) -> None:
        self.source = JzrSource(
            jzr_bin=self.jzr_bin,
            skill_dir=self.skill_dir,
            mcp_command=["/opt/py", "-m", "jzr_tools", "agent", "mcp"],
            split_dir=self.split_dir,
        )
        config = json.loads(self.prepare("mcp").sandbox.mcp_config.read_text())
        self.assertEqual(
            config["mcpServers"]["jzr"],
            {"command": "/opt/py", "args": ["-m", "jzr_tools", "agent", "mcp"]},
        )


class PrepareSplitArmTest(ArmTestCase):
    def test_split_dir_defaults_to_none(self) -> None:
        source = JzrSource(jzr_bin=self.jzr_bin, skill_dir=self.skill_dir, mcp_command=["x"])
        self.assertIsNone(source.split_dir)

    def test_every_subfolder_is_copied_to_skills_under_its_own_name(self) -> None:
        sb = self.prepare("split").sandbox
        skills = sb.config_dir / "skills"
        self.assertEqual({p.name for p in skills.iterdir()}, set(SPLIT_NAMES))
        for name in SPLIT_NAMES:
            with self.subTest(name=name):
                self.assertEqual(
                    (skills / name / "SKILL.md").read_text(),
                    f"---\nname: {name}\n---\nBody of {name}.\n",
                )
                self.assertEqual(
                    (skills / name / "reference" / "notes.md").read_text(),
                    f"notes of {name}\n",
                )

    def test_the_subfolders_of_split_dir_decide_what_is_copied(self) -> None:
        other = self.tmp / "other-split"
        for name in ("jzr-alpha", "jzr-beta"):
            (other / name).mkdir(parents=True)
            (other / name / "SKILL.md").write_text(f"{name}\n")
        self.source = JzrSource(
            jzr_bin=self.jzr_bin,
            skill_dir=self.skill_dir,
            mcp_command=["x"],
            split_dir=other,
        )
        skills = self.prepare("split").sandbox.config_dir / "skills"
        self.assertEqual({p.name for p in skills.iterdir()}, {"jzr-alpha", "jzr-beta"})
        self.assertEqual((skills / "jzr-beta" / "SKILL.md").read_text(), "jzr-beta\n")

    def test_no_jzr_skill_in_the_split_arm_and_no_split_skill_in_the_others(
        self,
    ) -> None:
        self.assertFalse((self.prepare("split").sandbox.config_dir / "skills" / "jzr").exists())
        for arm in ("baseline", "skill", "mcp"):
            with self.subTest(arm=arm):
                skills = self.prepare(arm).sandbox.config_dir / "skills"
                names = {p.name for p in skills.iterdir()} if skills.exists() else set()
                self.assertFalse(names & set(SPLIT_NAMES), names)

    def test_the_copy_is_a_copy_not_a_link_to_split_dir(self) -> None:
        skills = self.prepare("split").sandbox.config_dir / "skills"
        copied = skills / "jzr-pdf" / "SKILL.md"
        self.assertFalse(copied.is_symlink())
        self.assertFalse((skills / "jzr-pdf").is_symlink())
        copied.write_text("changed in the sandbox")
        self.assertIn("Body of jzr-pdf.", (self.split_dir / "jzr-pdf" / "SKILL.md").read_text())

    def test_the_shim_runs_the_given_jzr_bin_with_the_arguments(self) -> None:
        setup = self.prepare("split")
        proc = subprocess.run(
            [
                str(setup.sandbox.bin / "jzr"),
                "pdf",
                "merge",
                "a file.pdf",
                "--overwrite",
            ],
            capture_output=True,
            text=True,
            env=setup.env,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            proc.stdout.splitlines(),
            ["fake-jzr", "arg:pdf", "arg:merge", "arg:a file.pdf", "arg:--overwrite"],
        )

    def test_the_shim_is_found_by_name_through_the_arm_path(self) -> None:
        setup = self.prepare("split")
        proc = subprocess.run(
            ["/bin/sh", "-c", "jzr hash x"],
            capture_output=True,
            text=True,
            env=setup.env,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines(), ["fake-jzr", "arg:hash", "arg:x"])

    def test_no_mcp_config_and_strict_mcp_config_stays(self) -> None:
        setup = self.prepare("split")
        self.assertFalse(setup.sandbox.mcp_config.exists())
        self.assertNotIn("--mcp-config", setup.claude_args)
        self.assertIn("--strict-mcp-config", setup.claude_args)

    def test_the_environment_is_the_same_allowlist_as_the_skill_arm(self) -> None:
        split, skill = self.prepare("split"), self.prepare("skill")
        self.assertEqual(set(split.env), ALLOWED_KEYS | {OAUTH})
        self.assertEqual(set(split.env), set(skill.env))
        self.assertEqual(split.env["PATH"], f"{split.sandbox.bin}:{self.system_path}")
        self.assertEqual(split.env["CLAUDE_CONFIG_DIR"], str(split.sandbox.config_dir))

    def test_claude_args_equal_the_skill_arms(self) -> None:
        self.assertEqual(self.prepare("split").claude_args, self.prepare("skill").claude_args)

    def test_without_split_dir_the_split_arm_is_a_value_error_about_it(self) -> None:
        self.source = JzrSource(jzr_bin=self.jzr_bin, skill_dir=self.skill_dir, mcp_command=["x"])
        with self.assertRaises(ValueError) as caught:
            self.prepare("split")
        # not the "unknown arm" error: split is a known arm that lacks its input
        self.assertNotIn("unknown arm", str(caught.exception))

    def test_the_other_arms_do_not_need_split_dir(self) -> None:
        self.source = JzrSource(jzr_bin=self.jzr_bin, skill_dir=self.skill_dir, mcp_command=["x"])
        for arm in ("baseline", "skill", "mcp"):
            with self.subTest(arm=arm):
                self.assertEqual(self.prepare(arm).arm, arm)


class PrepareArmArgsTest(ArmTestCase):
    @staticmethod
    def value_of(args: list[str], flag: str) -> str:
        return args[args.index(flag) + 1]

    def test_common_flags_in_every_arm(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                args = self.prepare(arm).claude_args
                self.assertEqual(self.value_of(args, "--output-format"), "stream-json")
                self.assertIn("--verbose", args)
                self.assertIn("--no-session-persistence", args)
                self.assertEqual(self.value_of(args, "--permission-mode"), "bypassPermissions")
                self.assertIn("--strict-mcp-config", args)
                self.assertNotIn("--bare", args)
                self.assertNotIn("-p", args)  # the runner adds -p and the prompt itself

    def test_mcp_config_flag_only_for_mcp_arm(self) -> None:
        mcp = self.prepare("mcp")
        self.assertEqual(self.value_of(mcp.claude_args, "--mcp-config"), str(mcp.sandbox.mcp_config))
        for arm in ("baseline", "skill", "split"):
            with self.subTest(arm=arm):
                self.assertNotIn("--mcp-config", self.prepare(arm).claude_args)

    def test_model_and_max_turns_values(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                args = self.prepare(arm, model="opus", max_turns=7).claude_args
                self.assertEqual(self.value_of(args, "--model"), "opus")
                self.assertEqual(self.value_of(args, "--max-turns"), "7")
        args = self.prepare("baseline", model="claude-sonnet-5-5", max_turns=30).claude_args
        self.assertEqual(self.value_of(args, "--model"), "claude-sonnet-5-5")
        self.assertEqual(self.value_of(args, "--max-turns"), "30")

    def test_all_args_are_strings(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.assertTrue(all(isinstance(a, str) for a in self.prepare(arm).claude_args))


class PrepareArmErrorsTest(ArmTestCase):
    def test_unknown_arm_is_a_value_error(self) -> None:
        with self.assertRaises(ValueError):
            self.prepare("everything")

    def test_refuses_when_system_path_has_a_jzr_executable(self) -> None:
        make_executable(Path(self.system_path) / "jzr")
        for arm in ARMS:
            with self.subTest(arm=arm):
                with self.assertRaises(RuntimeError):
                    self.prepare(arm)

    def test_non_executable_jzr_in_system_path_does_not_block(self) -> None:
        (Path(self.system_path) / "jzr").write_text("just a file")
        (Path(self.system_path) / "jzr").chmod(0o644)
        self.assertEqual(self.prepare("baseline").arm, "baseline")


if __name__ == "__main__":
    unittest.main()
