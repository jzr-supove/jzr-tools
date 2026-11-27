"""Offline tests for jzr_bench.arms: per-arm sandbox, environment and claude arguments."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
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
from jzr_bench.arms import ARMS, Auth, JzrSource, Sandbox, find_jzr_on, pick_auth, prepare_arm  # noqa: E402

OAUTH = "CLAUDE_CODE_OAUTH_TOKEN"
APIKEY = "ANTHROPIC_API_KEY"
SKILL_FOLDERS = ("jzr-media", "jzr-pdf", "jzr-data", "jzr-system")
ALLOWED_KEYS = {"HOME", "CLAUDE_CONFIG_DIR", "TMPDIR", "PATH", "LANG", "TERM"}
PRIVATE_TMP_KEYS = ALLOWED_KEYS - {"TMPDIR"}  # with a private /tmp the child gets no TMPDIR
FAKE_BWRAP = "/opt/fake bin/bwrap"  # never started here; a space checks nothing splits the path


def make_executable(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


class ArmTestCase(unittest.TestCase):
    """Builds a fake repo (jzr binary, a folder of four small skills) and an empty system PATH folder per test."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

        # The fake jzr prints one argument per line, so the shim test can see exact argv.
        self.jzr_bin = make_executable(
            self.tmp / "repo dir" / ".venv" / "bin" / "jzr",
            '#!/bin/sh\necho "fake-jzr"\nfor a in "$@"; do echo "arg:$a"; done\n',
        )
        # Like src/jzr_tools/data/skill: one folder per skill, each with its own files, next to things that are
        # not skills (a template file, an unrelated folder), which must never reach a sandbox.
        self.skills_dir = self.tmp / "repo dir" / "skill"
        for name in SKILL_FOLDERS:
            (self.skills_dir / name / "reference").mkdir(parents=True)
            (self.skills_dir / name / "SKILL.md").write_text(f"---\nname: {name}\n---\nBody of {name}.\n")
            (self.skills_dir / name / "reference" / "notes.md").write_text(f"notes of {name}\n")
        (self.skills_dir / "template.md").write_text("a template, not a skill\n")
        (self.skills_dir / "unrelated" / "deep").mkdir(parents=True)
        (self.skills_dir / "unrelated" / "SKILL.md").write_text("---\nname: unrelated\n---\n")
        self.source = JzrSource(
            jzr_bin=self.jzr_bin,
            skills_dir=self.skills_dir,
            mcp_command=[str(self.jzr_bin), "agent", "mcp"],
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
        bwrap: str | None = None,
    ):
        root = self.tmp / f"sandbox-{arm}"
        root.mkdir(exist_ok=True)
        extra = {"bwrap": bwrap} if bwrap is not None else {}  # older prepare_arm has no such parameter
        return prepare_arm(
            arm,
            root,
            self.source,
            auth or self.auth,
            model=model,
            max_turns=max_turns,
            system_path=self.system_path,
            **extra,
        )


class ConstantsTest(unittest.TestCase):
    def test_arms_and_system_path(self) -> None:
        self.assertEqual(tuple(ARMS), ("baseline", "skill", "mcp"))
        self.assertEqual(arms.SYSTEM_PATH, "/usr/local/bin:/usr/bin:/bin")

    def test_skills_are_the_four_names_as_a_frozenset(self) -> None:
        self.assertIsInstance(arms.SKILLS, frozenset)
        self.assertEqual(arms.SKILLS, frozenset(SKILL_FOLDERS))

    def test_skills_equal_the_names_jzr_ships(self) -> None:
        from jzr_tools.core.skill import SKILL_NAMES  # src is importable under `uv run`

        self.assertEqual(arms.SKILLS, set(SKILL_NAMES))


class JzrSourceTest(unittest.TestCase):
    def test_the_fields_are_the_binary_the_skills_folder_and_the_mcp_command(self) -> None:
        self.assertEqual([f.name for f in dataclasses.fields(JzrSource)], ["jzr_bin", "skills_dir", "mcp_command"])

    def test_no_single_skill_or_split_folder_any_more(self) -> None:
        source = JzrSource(jzr_bin=Path("/x/jzr"), skills_dir=Path("/x/skills"), mcp_command=["x"])
        self.assertEqual(source.skills_dir, Path("/x/skills"))
        for old in ("skill_dir", "split_dir"):
            with self.subTest(field=old):
                self.assertFalse(hasattr(source, old))
        with self.assertRaises(TypeError):
            JzrSource(jzr_bin=Path("/x/jzr"), skill_dir=Path("/x/skill"), mcp_command=["x"])  # type: ignore[call-arg]


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
        setup = self.prepare("skill")
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


def bind_pairs(wrapper: list[str]) -> list[tuple[str, str]]:
    """The (source, destination) of every `--bind` in the wrapper, in the order they appear."""
    return [(wrapper[i + 1], wrapper[i + 2]) for i, word in enumerate(wrapper) if word == "--bind"]


class PrivateTmpWrapperTest(unittest.TestCase):
    """private_tmp_wrapper: the bwrap words put in front of claude."""

    def wrapper(self, root: str) -> tuple[list[str], Sandbox]:
        sandbox = Sandbox.at(Path(root))
        return arms.private_tmp_wrapper(FAKE_BWRAP, sandbox), sandbox

    def test_starts_with_bwrap_and_ends_with_the_double_dash(self) -> None:
        wrapper, _ = self.wrapper("/var/tmp/wab12cd34")
        self.assertIsInstance(wrapper, list)
        self.assertTrue(all(isinstance(word, str) for word in wrapper))
        self.assertEqual(wrapper[0], FAKE_BWRAP)
        self.assertEqual(wrapper[-1], "--")
        self.assertEqual(wrapper.count("--"), 1, "claude's own arguments come after the wrapper, not inside it")

    def test_the_whole_file_system_is_the_machines_own_and_comes_before_the_binds(self) -> None:
        wrapper, _ = self.wrapper("/var/tmp/wab12cd34")
        self.assertEqual(wrapper[1:4], ["--dev-bind", "/", "/"])
        self.assertEqual(wrapper.count("--dev-bind"), 1)

    def test_the_sandbox_tmp_is_bound_over_tmp_and_nothing_else_is_for_a_sandbox_outside_tmp(self) -> None:
        wrapper, sandbox = self.wrapper("/var/tmp/wab12cd34")
        self.assertEqual(bind_pairs(wrapper), [("/var/tmp/wab12cd34/tmp", "/tmp")])
        self.assertEqual(bind_pairs(wrapper), [(str(sandbox.tmp), "/tmp")])

    def test_it_dies_with_its_parent_and_starts_in_the_work_folder(self) -> None:
        wrapper, sandbox = self.wrapper("/var/tmp/wab12cd34")
        self.assertEqual(wrapper.count("--die-with-parent"), 1)
        self.assertEqual(wrapper.count("--chdir"), 1)
        self.assertEqual(wrapper[wrapper.index("--chdir") + 1], str(sandbox.work))
        self.assertEqual(wrapper[wrapper.index("--chdir") + 1], "/var/tmp/wab12cd34/work")

    def test_a_sandbox_inside_tmp_is_bound_again_at_its_own_path_after_the_tmp_bind(self) -> None:
        wrapper, sandbox = self.wrapper("/tmp/wab12cd34")
        self.assertEqual(
            bind_pairs(wrapper),
            [(str(sandbox.tmp), "/tmp"), ("/tmp/wab12cd34", "/tmp/wab12cd34")],
        )
        self.assertEqual(wrapper[-1], "--")
        self.assertEqual(wrapper[wrapper.index("--chdir") + 1], "/tmp/wab12cd34/work")

    def test_a_sandbox_deeper_inside_tmp_is_bound_at_the_sandbox_root_not_at_its_parent(self) -> None:
        wrapper, sandbox = self.wrapper("/tmp/parent/wab12cd34")
        self.assertEqual(
            bind_pairs(wrapper),
            [(str(sandbox.tmp), "/tmp"), ("/tmp/parent/wab12cd34", "/tmp/parent/wab12cd34")],
        )

    def test_folders_that_only_start_with_tmp_are_not_inside_tmp(self) -> None:
        for root in ("/tmpfoo/wab12cd34", "/var/tmp/wab12cd34", "/home/user/tmp/wab12cd34", "/mnt/tmp"):
            with self.subTest(root=root):
                wrapper, sandbox = self.wrapper(root)
                self.assertEqual(bind_pairs(wrapper), [(str(sandbox.tmp), "/tmp")])


class PrepareArmPrivateTmpTest(ArmTestCase):
    def test_the_wrapper_is_the_private_tmp_wrapper_of_the_arms_sandbox(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm, bwrap=FAKE_BWRAP)
                self.assertEqual(setup.wrapper, arms.private_tmp_wrapper(FAKE_BWRAP, setup.sandbox))
                self.assertEqual(setup.wrapper[0], FAKE_BWRAP)
                self.assertEqual(bind_pairs(setup.wrapper)[0], (str(setup.sandbox.tmp), "/tmp"))
                self.assertEqual(setup.wrapper[setup.wrapper.index("--chdir") + 1], str(setup.sandbox.work))

    def test_env_has_exactly_the_allowlist_without_tmpdir_in_every_arm(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm, bwrap=FAKE_BWRAP)
                self.assertEqual(set(setup.env), PRIVATE_TMP_KEYS | {OAUTH})
                self.assertNotIn("TMPDIR", setup.env)

    def test_env_with_an_api_key_has_exactly_the_allowlist_plus_that_key(self) -> None:
        setup = self.prepare("skill", auth=Auth(var=APIKEY, value="sk-abc"), bwrap=FAKE_BWRAP)
        self.assertEqual(set(setup.env), PRIVATE_TMP_KEYS | {APIKEY})
        self.assertEqual(setup.env[APIKEY], "sk-abc")

    def test_the_other_env_values_are_what_they_were(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm, bwrap=FAKE_BWRAP)
                sb = setup.sandbox
                self.assertEqual(setup.env["HOME"], str(sb.home))
                self.assertEqual(setup.env["CLAUDE_CONFIG_DIR"], str(sb.config_dir))
                self.assertEqual(setup.env["LANG"], "C.UTF-8")
                self.assertEqual(setup.env["TERM"], "dumb")
                self.assertEqual(setup.env[OAUTH], "oauth-secret-value")
                expected_path = self.system_path if arm in ("baseline", "mcp") else f"{sb.bin}:{self.system_path}"
                self.assertEqual(setup.env["PATH"], expected_path)

    def test_the_sandbox_tmp_folder_exists_because_it_is_what_gets_mounted(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm, bwrap=FAKE_BWRAP)
                self.assertTrue(setup.sandbox.tmp.is_dir())
                self.assertEqual(list(setup.sandbox.tmp.iterdir()), [], "a run starts with an empty /tmp")
                self.assertTrue(setup.sandbox.work.is_dir())

    def test_the_claude_arguments_do_not_change(self) -> None:
        plain = self.prepare("baseline").claude_args
        root = self.tmp / "sandbox-private"
        root.mkdir()
        private = prepare_arm(
            "baseline",
            root,
            self.source,
            self.auth,
            model="sonnet",
            max_turns=30,
            system_path=self.system_path,
            bwrap=FAKE_BWRAP,
        )
        self.assertEqual(private.claude_args, plain)
        self.assertNotIn(FAKE_BWRAP, private.claude_args)


class PrepareArmSharedTmpTest(ArmTestCase):
    def test_without_bwrap_there_is_no_wrapper_in_any_arm(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.assertEqual(self.prepare(arm).wrapper, [])

    def test_without_bwrap_the_child_still_gets_the_sandbox_tmp_as_tmpdir(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare(arm)
                self.assertEqual(setup.env["TMPDIR"], str(setup.sandbox.tmp))
                self.assertEqual(set(setup.env), ALLOWED_KEYS | {OAUTH})


class PrepareArmFilesTest(ArmTestCase):
    def test_the_four_skills_are_copied_only_in_the_skill_arm(self) -> None:
        skills = self.prepare("skill").sandbox.config_dir / "skills"
        self.assertEqual({p.name for p in skills.iterdir()}, set(SKILL_FOLDERS))
        for arm in ("baseline", "mcp"):
            with self.subTest(arm=arm):
                sb = self.prepare(arm).sandbox
                self.assertFalse((sb.config_dir / "skills").exists())

    def test_each_skill_folder_arrives_under_its_own_name_with_its_files(self) -> None:
        skills = self.prepare("skill").sandbox.config_dir / "skills"
        for name in SKILL_FOLDERS:
            with self.subTest(name=name):
                self.assertEqual((skills / name / "SKILL.md").read_text(), f"---\nname: {name}\n---\nBody of {name}.\n")
                self.assertEqual((skills / name / "reference" / "notes.md").read_text(), f"notes of {name}\n")

    def test_no_old_jzr_folder_is_made(self) -> None:
        self.assertFalse((self.prepare("skill").sandbox.config_dir / "skills" / "jzr").exists())

    def test_nothing_but_the_four_is_copied_from_the_skills_folder(self) -> None:
        config_dir = self.prepare("skill").sandbox.config_dir
        skills = config_dir / "skills"
        self.assertFalse((skills / "template.md").exists())
        self.assertFalse((skills / "unrelated").exists())
        self.assertEqual(sorted(p.name for p in skills.iterdir()), sorted(SKILL_FOLDERS))
        self.assertEqual({p.name for p in config_dir.iterdir()}, {"skills"})  # nothing next to the skills

    def test_the_copy_is_a_copy_not_a_link_to_the_repo(self) -> None:
        skills = self.prepare("skill").sandbox.config_dir / "skills"
        copied = skills / "jzr-pdf" / "SKILL.md"
        self.assertFalse((skills / "jzr-pdf").is_symlink())
        self.assertFalse(copied.is_symlink())
        copied.write_text("changed in the sandbox")
        self.assertIn("Body of jzr-pdf.", (self.skills_dir / "jzr-pdf" / "SKILL.md").read_text())

    def test_what_is_in_the_source_folder_at_run_time_is_what_is_copied(self) -> None:
        (self.skills_dir / "jzr-data" / "SKILL.md").write_text("changed in the repo\n")
        (self.skills_dir / "jzr-data" / "extra.md").write_text("one more file\n")
        folder = self.prepare("skill").sandbox.config_dir / "skills" / "jzr-data"
        self.assertEqual((folder / "SKILL.md").read_text(), "changed in the repo\n")
        self.assertEqual((folder / "extra.md").read_text(), "one more file\n")

    def test_bin_shim_only_in_the_skill_arm(self) -> None:
        shim = self.prepare("skill").sandbox.bin / "jzr"
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
        for arm in ("baseline", "skill"):
            with self.subTest(arm=arm):
                self.assertFalse(self.prepare(arm).sandbox.mcp_config.exists())

    def test_mcp_json_follows_the_given_command(self) -> None:
        self.source = JzrSource(
            jzr_bin=self.jzr_bin,
            skills_dir=self.skills_dir,
            mcp_command=["/opt/py", "-m", "jzr_tools", "agent", "mcp"],
        )
        config = json.loads(self.prepare("mcp").sandbox.mcp_config.read_text())
        self.assertEqual(
            config["mcpServers"]["jzr"],
            {"command": "/opt/py", "args": ["-m", "jzr_tools", "agent", "mcp"]},
        )


class PrepareSkillArmTest(ArmTestCase):
    def test_a_missing_skill_is_a_value_error_naming_it(self) -> None:
        for missing in SKILL_FOLDERS:
            with self.subTest(missing=missing):
                shutil.rmtree(self.skills_dir / missing)
                with self.assertRaises(ValueError) as caught:
                    self.prepare("skill")
                self.assertIn(missing, str(caught.exception))
                self.assertNotIn("unknown arm", str(caught.exception))
                (self.skills_dir / missing).mkdir()  # put it back as a bare folder for the next round
                (self.skills_dir / missing / "SKILL.md").write_text("x\n")

    def test_several_missing_skills_are_all_named(self) -> None:
        shutil.rmtree(self.skills_dir / "jzr-media")
        shutil.rmtree(self.skills_dir / "jzr-system")
        with self.assertRaises(ValueError) as caught:
            self.prepare("skill")
        self.assertIn("jzr-media", str(caught.exception))
        self.assertIn("jzr-system", str(caught.exception))

    def test_an_empty_skills_folder_is_a_value_error(self) -> None:
        shutil.rmtree(self.skills_dir)
        self.skills_dir.mkdir()
        with self.assertRaises(ValueError):
            self.prepare("skill")

    def test_a_missing_skills_folder_is_an_error_too(self) -> None:
        shutil.rmtree(self.skills_dir)
        with self.assertRaises((ValueError, OSError)):
            self.prepare("skill")

    def test_three_of_four_is_not_enough_even_with_other_folders_there(self) -> None:
        shutil.rmtree(self.skills_dir / "jzr-pdf")
        (self.skills_dir / "jzr-extra").mkdir()
        (self.skills_dir / "jzr-extra" / "SKILL.md").write_text("x\n")
        with self.assertRaises(ValueError):
            self.prepare("skill")

    def test_the_other_arms_do_not_need_the_skills(self) -> None:
        shutil.rmtree(self.skills_dir)
        self.skills_dir.mkdir()
        for arm in ("baseline", "mcp"):
            with self.subTest(arm=arm):
                self.assertEqual(self.prepare(arm).arm, arm)

    def test_the_shim_runs_the_given_jzr_bin_with_the_arguments(self) -> None:
        setup = self.prepare("skill")
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

    def test_no_mcp_config_and_strict_mcp_config_stays(self) -> None:
        setup = self.prepare("skill")
        self.assertFalse(setup.sandbox.mcp_config.exists())
        self.assertNotIn("--mcp-config", setup.claude_args)
        self.assertIn("--strict-mcp-config", setup.claude_args)

    def test_the_environment_is_the_allowlist_with_the_shim_path(self) -> None:
        setup = self.prepare("skill")
        self.assertEqual(set(setup.env), ALLOWED_KEYS | {OAUTH})
        self.assertEqual(setup.env["PATH"], f"{setup.sandbox.bin}:{self.system_path}")
        self.assertEqual(setup.env["CLAUDE_CONFIG_DIR"], str(setup.sandbox.config_dir))

    def test_the_skills_are_found_under_the_config_dir_the_child_gets(self) -> None:
        setup = self.prepare("skill")
        skills = Path(setup.env["CLAUDE_CONFIG_DIR"]) / "skills"
        self.assertEqual({p.name for p in skills.iterdir()}, set(SKILL_FOLDERS))


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
        for arm in ("baseline", "skill"):
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

    def test_split_is_an_unknown_arm(self) -> None:
        with self.assertRaises(ValueError) as caught:
            self.prepare("split")
        self.assertIn("unknown arm", str(caught.exception))
        self.assertFalse((self.tmp / "sandbox-split" / "home").exists())  # nothing was set up for it

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
