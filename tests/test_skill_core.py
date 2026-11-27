"""Tests for the skill installer's core: the packaged folders, the file listing
and digest, where the four skills go for each agent, and install, remove and
the removal of the old single skill.

Everything lives in temporary folders (home, project, a small fake source), so
nothing touches the real ``~/.claude``, ``~/.agents`` or ``~/.codex``. Renames
are made to fail with patches that do not depend on which rename function the
code calls. Tests that need a symlink or a Windows junction skip themselves
when the platform cannot create one.
"""

from __future__ import annotations

import contextlib
import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import jzr_tools
from jzr_tools.core import skill
from jzr_tools.core.skill import (
    MARKER,
    SkillError,
    Target,
    digest,
    files,
    install,
    remove,
    source_dir,
    targets,
)

# Names that do not exist yet in the core are reached through ``skill.`` so that
# each test fails on its own instead of the whole module failing to import.
NAMES = ("jzr-media", "jzr-pdf", "jzr-data", "jzr-system")
OLD_NAME = "jzr"

NO_AGENT = (
    "no AI agent found (looked for ~/.claude, ~/.codex and ~/.cursor); "
    "name one with --agent, or use --project"
)
NOT_OURS_INSTALL = "not installed by jzr; use --overwrite to replace it"
NOT_OURS_REMOVE = "not installed by jzr; remove it yourself"
EDITED_INSTALL = "changed since jzr installed it; use --overwrite to replace it"
EDITED_REMOVE = "changed since jzr installed it; use --overwrite to remove it"
IS_LINK = "is a link; remove it first"
IS_FILE = "is a file; remove it first"

SOURCE = {
    "SKILL.md": b"---\nname: jzr\n---\nUse jzr.\n",
    "reference/image.md": b"# jzr image\n",
    "reference/deep/more.md": b"more\n",
}
SOURCE_V2 = {
    "SKILL.md": b"---\nname: jzr\n---\nUse jzr, version two.\n",
    "reference/image.md": b"# jzr image\n",
}


def write_tree(root: Path, mapping: dict[str, bytes]) -> Path:
    for rel, data in mapping.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under root: bytes for files, None for folders."""
    result: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        result[rel] = path.read_bytes() if path.is_file() else None
    return result


def leftovers(folder: Path) -> list[str]:
    if not folder.is_dir():
        return []
    return [p.name for p in folder.iterdir() if p.name.startswith("jzr-tmp-")]


def remove_link(path: Path) -> None:
    try:
        os.rmdir(path)
    except FileNotFoundError:
        pass
    except OSError:
        os.unlink(path)


@contextlib.contextmanager
def failing_rename(fail_at: int):
    """Make the Nth rename (counting every way of renaming) raise OSError.

    The counter ignores calls made from inside another wrapped call, so
    Path.rename -> os.rename counts once. Later renames work, so a restore
    after the failure can succeed.
    """
    state = {"calls": 0, "depth": 0}

    def guard(original):
        def wrapper(*args, **kwargs):
            if state["depth"]:
                return original(*args, **kwargs)
            state["depth"] += 1
            try:
                state["calls"] += 1
                if state["calls"] == fail_at:
                    raise OSError(errno.EACCES, "simulated rename failure")
                return original(*args, **kwargs)
            finally:
                state["depth"] -= 1

        return wrapper

    with (
        mock.patch.object(os, "replace", guard(os.replace)),
        mock.patch.object(os, "rename", guard(os.rename)),
        mock.patch.object(Path, "rename", guard(Path.rename)),
        mock.patch.object(Path, "replace", guard(Path.replace)),
        mock.patch.object(shutil, "move", guard(shutil.move)),
    ):
        yield state


class SkillCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.src = write_tree(self.root / "src", SOURCE)
        self.target = Target(("claude",), self.home / ".claude" / "skills" / "jzr-media")

    def source_v2(self) -> Path:
        return write_tree(self.root / "src2", SOURCE_V2)

    def installed(self, version: str = "1.0.0", source: Path | None = None) -> None:
        outcome = install(self.target, version, source=source or self.src)
        self.assertEqual(outcome.status, "installed", outcome)

    def edit_installed(self) -> None:
        (self.target.path / "SKILL.md").write_bytes(b"edited by the user\n")

    def make_link(self, kind: str) -> Path:
        """Make target.path a link to a real jzr install elsewhere; return that folder."""
        real = Target(("claude",), self.root / "elsewhere" / self.target.path.name)
        self.assertEqual(install(real, "1.0.0", source=self.src).status, "installed")
        self.target.path.parent.mkdir(parents=True)
        if kind == "symlink":
            try:
                os.symlink(real.path, self.target.path, target_is_directory=True)
            except (OSError, NotImplementedError, AttributeError) as exc:
                raise unittest.SkipTest(f"cannot create a symlink here: {exc}")
        else:
            if sys.platform != "win32":
                raise unittest.SkipTest("junctions exist on Windows only")
            run = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(self.target.path), str(real.path)],
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
            )
            if run.returncode != 0:
                raise unittest.SkipTest(f"cannot create a junction here: {run.stdout}{run.stderr}")
        self.addCleanup(remove_link, self.target.path)
        return real.path


class NamesTests(unittest.TestCase):
    def test_the_four_skills_in_order(self):
        self.assertEqual(skill.SKILL_NAMES, NAMES)

    def test_the_old_single_skill(self):
        self.assertEqual(skill.OLD_SKILL_NAMES, (OLD_NAME,))

    def test_the_old_name_is_not_one_of_the_four(self):
        self.assertFalse(set(skill.SKILL_NAMES) & set(skill.OLD_SKILL_NAMES))


class SkillsDirTests(unittest.TestCase):
    def test_points_at_the_folder_that_holds_the_packaged_skills(self):
        expected = Path(jzr_tools.__file__).resolve().parent / "data" / "skill"
        self.assertEqual(Path(skill.skills_dir()).resolve(), expected)


class SourceDirTests(unittest.TestCase):
    def test_each_skill_is_a_folder_of_the_skills_dir(self):
        base = Path(jzr_tools.__file__).resolve().parent / "data" / "skill"
        for name in NAMES:
            with self.subTest(name=name):
                self.assertEqual(Path(source_dir(name)).resolve(), base / name)

    def test_follows_skills_dir(self):
        with mock.patch.object(
            skill, "skills_dir", return_value=Path("/somewhere/else"), create=True
        ):
            self.assertEqual(Path(source_dir("jzr-pdf")), Path("/somewhere/else") / "jzr-pdf")

    def test_a_name_that_is_not_one_of_the_four_is_a_skill_error(self):
        # The old skill is not packaged any more; the others are not skills at all.
        for name in ("jzr", "bogus", "", "JZR-MEDIA", "jzr-media/", "../jzr-media", "template.md"):
            with self.subTest(name=name):
                with self.assertRaises(SkillError):
                    source_dir(name)

    def test_an_unknown_name_is_refused_even_when_a_folder_of_that_name_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "jzr").mkdir()
            (Path(tmp) / "extra").mkdir()
            with mock.patch.object(skill, "skills_dir", return_value=Path(tmp), create=True):
                for name in ("jzr", "extra"):
                    with self.subTest(name=name):
                        with self.assertRaises(SkillError):
                            source_dir(name)


class ImportTests(unittest.TestCase):
    def test_importing_the_module_does_not_import_click(self):
        code = "import sys; import jzr_tools.core.skill; sys.exit(1 if 'click' in sys.modules else 0)"
        run = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


class FilesTests(SkillCase):
    def test_keys_are_posix_paths_including_nested_folders(self):
        self.assertEqual(sorted(files(self.src)), sorted(SOURCE))
        self.assertTrue(all("\\" not in key for key in files(self.src)))

    def test_contents_are_bytes(self):
        self.assertEqual(files(self.src), SOURCE)

    def test_crlf_becomes_lf_and_a_lone_cr_stays(self):
        folder = write_tree(
            self.root / "crlf", {"a.md": b"one\r\ntwo\r\n", "sub/b.md": b"x\ry\r\nz"}
        )
        self.assertEqual(files(folder), {"a.md": b"one\ntwo\n", "sub/b.md": b"x\ry\nz"})

    def test_marker_is_left_out(self):
        folder = write_tree(self.root / "marked", {**SOURCE, MARKER: b"1.0.0\nabc\n"})
        self.assertEqual(files(folder), SOURCE)

    def test_empty_folders_are_not_listed(self):
        folder = write_tree(self.root / "withdir", {"a.md": b"a\n"})
        (folder / "empty" / "deeper").mkdir(parents=True)
        self.assertEqual(sorted(files(folder)), ["a.md"])


class DigestTests(SkillCase):
    def test_is_sha256_hex(self):
        self.assertRegex(digest(SOURCE), r"^[0-9a-f]{64}$")

    def test_same_for_any_order(self):
        reordered = dict(reversed(list(SOURCE.items())))
        self.assertEqual(digest(SOURCE), digest(reordered))

    def test_same_for_crlf_and_lf_copies_of_a_folder(self):
        lf = write_tree(self.root / "lf", {"a.md": b"one\ntwo\n"})
        crlf = write_tree(self.root / "crlf", {"a.md": b"one\r\ntwo\r\n"})
        self.assertEqual(digest(files(lf)), digest(files(crlf)))

    def test_changes_with_content(self):
        changed = {**SOURCE, "SKILL.md": SOURCE["SKILL.md"] + b"more\n"}
        self.assertNotEqual(digest(SOURCE), digest(changed))

    def test_changes_with_path(self):
        renamed = dict(SOURCE)
        renamed["SKILL2.md"] = renamed.pop("SKILL.md")
        self.assertNotEqual(digest(SOURCE), digest(renamed))

    def test_changes_when_contents_swap_between_paths(self):
        one = {"a": b"x", "b": b"y"}
        two = {"a": b"y", "b": b"x"}
        self.assertNotEqual(digest(one), digest(two))

    def test_path_and_content_boundary_matters(self):
        self.assertNotEqual(digest({"a": b"bc"}), digest({"ab": b"c"}))

    def test_a_missing_file_changes_it(self):
        fewer = dict(SOURCE)
        del fewer["reference/deep/more.md"]
        self.assertNotEqual(digest(SOURCE), digest(fewer))


def skill_folder(base: Path, agent: str) -> Path:
    """The skills folder an agent reads under ``base``."""
    return base / (".claude" if agent == "claude" else ".agents") / "skills"


class TargetNameTests(unittest.TestCase):
    def test_name_is_the_last_part_of_the_path(self):
        for name in (*NAMES, OLD_NAME):
            with self.subTest(name=name):
                self.assertEqual(Target(("claude",), Path("/h/.claude/skills") / name).name, name)

    def test_the_name_does_not_depend_on_the_agents(self):
        path = Path("/h/.agents/skills/jzr-data")
        self.assertEqual(Target(("codex", "cursor"), path).name, "jzr-data")


class TargetsTests(SkillCase):
    def home_dirs(self, *names: str, file_names: tuple[str, ...] = ()) -> None:
        for name in names:
            (self.home / name).mkdir()
        for name in file_names:
            (self.home / name).write_text("not a folder")

    def claude_targets(self, base: Path) -> list[Target]:
        return [Target(("claude",), skill_folder(base, "claude") / name) for name in NAMES]

    def agents_targets(self, base: Path, *agents: str) -> list[Target]:
        return [Target(tuple(agents), skill_folder(base, "codex") / name) for name in NAMES]

    def test_there_are_four_targets_per_folder_in_the_order_of_the_names(self):
        result = targets(self.home, agent="claude")
        self.assertEqual([t.name for t in result], list(NAMES))
        self.assertEqual({t.path.parent for t in result}, {self.home / ".claude" / "skills"})

    def test_project_alone_means_claude_without_detection(self):
        self.home_dirs(".codex", ".cursor")
        self.assertEqual(
            targets(self.home, project=self.project), self.claude_targets(self.project)
        )

    def test_project_with_claude(self):
        self.assertEqual(
            targets(self.home, agent="claude", project=self.project),
            self.claude_targets(self.project),
        )

    def test_project_with_codex_uses_the_agents_folder(self):
        self.assertEqual(
            targets(self.home, agent="codex", project=self.project),
            self.agents_targets(self.project, "codex"),
        )

    def test_project_targets_need_no_existing_folders(self):
        result = targets(self.home, agent="codex", project=self.project)
        self.assertEqual(len(result), 4)
        self.assertFalse(any(t.path.parent.exists() for t in result))
        self.assertEqual(os.listdir(self.project), [])

    def test_agent_claude_without_its_home_folder(self):
        self.assertFalse((self.home / ".claude").exists())
        self.assertEqual(targets(self.home, agent="claude"), self.claude_targets(self.home))

    def test_agent_codex_without_its_home_folder(self):
        self.assertEqual(targets(self.home, agent="codex"), self.agents_targets(self.home, "codex"))

    def test_agent_named_ignores_other_agents_found(self):
        self.home_dirs(".claude", ".codex")
        self.assertEqual(targets(self.home, agent="claude"), self.claude_targets(self.home))

    def test_detection_in_every_combination(self):
        claude = self.claude_targets(self.home)
        cases = {
            (".claude",): claude,
            (".codex",): self.agents_targets(self.home, "codex"),
            (".cursor",): self.agents_targets(self.home, "cursor"),
            (".claude", ".codex"): [*claude, *self.agents_targets(self.home, "codex")],
            (".claude", ".cursor"): claude,
            (".codex", ".cursor"): self.agents_targets(self.home, "codex", "cursor"),
            (".claude", ".codex", ".cursor"): [
                *claude,
                *self.agents_targets(self.home, "codex", "cursor"),
            ],
        }
        for names, expected in cases.items():
            with self.subTest(found=names):
                home = self.root / ("home-" + "-".join(n.strip(".") for n in names))
                home.mkdir()
                for name in names:
                    (home / name).mkdir()
                want = [Target(t.agents, home / t.path.relative_to(self.home)) for t in expected]
                self.assertEqual(targets(home), want)

    def test_nothing_found_is_an_error_with_the_exact_text(self):
        with self.assertRaises(SkillError) as caught:
            targets(self.home)
        self.assertEqual(str(caught.exception), NO_AGENT)

    def test_a_file_named_like_an_agent_folder_does_not_count(self):
        for name in (".claude", ".codex", ".cursor"):
            with self.subTest(name=name):
                home = self.root / ("file-home" + name)
                home.mkdir()
                (home / name).write_text("not a folder")
                with self.assertRaises(SkillError) as caught:
                    targets(home)
                self.assertEqual(str(caught.exception), NO_AGENT)

    def test_a_claude_file_is_skipped_while_a_codex_folder_counts(self):
        self.home_dirs(".codex", file_names=(".claude",))
        self.assertEqual(targets(self.home), self.agents_targets(self.home, "codex"))

    def test_a_cursor_file_adds_nothing_next_to_codex(self):
        self.home_dirs(".codex", file_names=(".cursor",))
        self.assertEqual(targets(self.home), self.agents_targets(self.home, "codex"))

    def test_does_not_create_anything(self):
        self.home_dirs(".claude")
        before = snapshot(self.home)
        targets(self.home)
        self.assertEqual(snapshot(self.home), before)


class OldTargetsTests(SkillCase):
    """``old_targets`` names the old skill in the very folders ``targets`` uses."""

    def old(self, agents: tuple[str, ...], base: Path) -> Target:
        return Target(
            agents, skill_folder(base, "claude" if agents == ("claude",) else "codex") / OLD_NAME
        )

    def home_dirs(self, *names: str) -> None:
        for name in names:
            (self.home / name).mkdir()

    def test_one_target_called_jzr_for_a_folder(self):
        self.assertEqual(
            skill.old_targets(self.home, agent="claude"), [self.old(("claude",), self.home)]
        )

    def test_project_alone_means_claude_without_detection(self):
        self.home_dirs(".codex", ".cursor")
        self.assertEqual(
            skill.old_targets(self.home, project=self.project),
            [self.old(("claude",), self.project)],
        )

    def test_project_with_codex_uses_the_agents_folder(self):
        self.assertEqual(
            skill.old_targets(self.home, agent="codex", project=self.project),
            [self.old(("codex",), self.project)],
        )

    def test_agent_codex_without_its_home_folder(self):
        self.assertEqual(
            skill.old_targets(self.home, agent="codex"), [self.old(("codex",), self.home)]
        )

    def test_agent_named_ignores_other_agents_found(self):
        self.home_dirs(".claude", ".codex")
        self.assertEqual(
            skill.old_targets(self.home, agent="claude"), [self.old(("claude",), self.home)]
        )

    def test_detection_in_every_combination(self):
        claude = ("claude",)
        cases = {
            (".claude",): [claude],
            (".codex",): [("codex",)],
            (".cursor",): [("cursor",)],
            (".claude", ".codex"): [claude, ("codex",)],
            (".claude", ".cursor"): [claude],
            (".codex", ".cursor"): [("codex", "cursor")],
            (".claude", ".codex", ".cursor"): [claude, ("codex", "cursor")],
        }
        for names, expected in cases.items():
            with self.subTest(found=names):
                home = self.root / ("home-" + "-".join(n.strip(".") for n in names))
                home.mkdir()
                for name in names:
                    (home / name).mkdir()
                want = [
                    Target(
                        agents,
                        skill_folder(home, "claude" if agents == claude else "codex") / OLD_NAME,
                    )
                    for agents in expected
                ]
                self.assertEqual(skill.old_targets(home), want)

    def test_they_sit_in_the_same_folders_as_the_four_skills(self):
        self.home_dirs(".claude", ".codex", ".cursor")
        new = targets(self.home)
        old = skill.old_targets(self.home)
        self.assertEqual(
            [(t.agents, t.path.parent) for t in old],
            [(t.agents, t.path.parent) for t in new if t.name == "jzr-media"],
        )

    def test_nothing_found_is_the_same_error(self):
        with self.assertRaises(SkillError) as caught:
            skill.old_targets(self.home)
        self.assertEqual(str(caught.exception), NO_AGENT)

    def test_does_not_create_anything(self):
        self.home_dirs(".claude")
        before = snapshot(self.home)
        skill.old_targets(self.home)
        skill.old_targets(self.home, agent="codex", project=self.project)
        self.assertEqual(snapshot(self.home), before)
        self.assertEqual(os.listdir(self.project), [])


class InstallFreshTests(SkillCase):
    def test_missing_target_is_installed_with_all_files(self):
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("installed", ""))
        self.assertEqual(outcome.target, self.target)
        self.assertEqual(files(self.target.path), SOURCE)

    def test_marker_holds_version_then_digest_on_lf_lines(self):
        install(self.target, "1.0.0", source=self.src)
        raw = (self.target.path / MARKER).read_bytes()
        self.assertNotIn(b"\r", raw)
        lines = raw.decode("utf-8").split("\n")
        self.assertEqual(lines[0], "1.0.0")
        self.assertEqual(lines[1], digest(SOURCE))
        self.assertIn(lines[2:], ([], [""]))

    def test_marker_digest_matches_the_installed_files(self):
        install(self.target, "1.0.0", source=self.src)
        marker = (self.target.path / MARKER).read_text(encoding="utf-8").split("\n")
        self.assertEqual(marker[1], digest(files(self.target.path)))

    def test_crlf_source_is_installed_as_lf(self):
        crlf = write_tree(self.root / "crlf", {"SKILL.md": b"a\r\nb\r\n", "r/x.md": b"x\r\n"})
        install(self.target, "1.0.0", source=crlf)
        self.assertEqual((self.target.path / "SKILL.md").read_bytes(), b"a\nb\n")
        self.assertEqual((self.target.path / "r" / "x.md").read_bytes(), b"x\n")

    def test_a_marker_in_the_source_is_replaced_by_the_real_one(self):
        src = write_tree(self.root / "withmarker", {**SOURCE, MARKER: b"9.9.9\njunk\n"})
        install(self.target, "1.0.0", source=src)
        first = (self.target.path / MARKER).read_text(encoding="utf-8").split("\n")[0]
        self.assertEqual(first, "1.0.0")
        self.assertEqual(files(self.target.path), SOURCE)

    def test_parents_are_created(self):
        self.assertFalse((self.home / ".claude").exists())
        install(self.target, "1.0.0", source=self.src)
        self.assertTrue(self.target.path.is_dir())

    def test_default_source_is_the_packaged_folder_of_that_skill(self):
        outcome = install(self.target, "1.0.0")
        self.assertEqual(outcome.status, "installed", outcome)
        packaged = files(source_dir("jzr-media"))
        self.assertIn("SKILL.md", packaged)
        self.assertEqual(files(self.target.path), packaged)

    def test_no_temp_folder_is_left_beside_the_target(self):
        install(self.target, "1.0.0", source=self.src)
        self.assertEqual(os.listdir(self.target.path.parent), ["jzr-media"])


class InstallDefaultSourceTests(SkillCase):
    """Without ``source=``, a target gets the packaged folder of its own name."""

    def setUp(self) -> None:
        super().setUp()
        self.packaged = self.root / "packaged"
        for name in NAMES:
            write_tree(
                self.packaged / name,
                {
                    "SKILL.md": f"---\nname: {name}\n---\nOnly {name}.\n".encode(),
                    f"reference/{name}.md": f"# {name}\n".encode(),
                },
            )

    def target_named(self, name: str) -> Target:
        return Target(("claude",), self.home / ".claude" / "skills" / name)

    def test_each_target_gets_the_folder_of_its_own_name(self):
        with mock.patch.object(skill, "skills_dir", return_value=self.packaged, create=True):
            for name in NAMES:
                with self.subTest(name=name):
                    target = self.target_named(name)
                    outcome = install(target, "1.0.0")
                    self.assertEqual(outcome.status, "installed", outcome)
                    self.assertEqual(files(target.path), files(self.packaged / name))
                    self.assertIn(f"Only {name}.".encode(), files(target.path)["SKILL.md"])

    def test_the_four_installs_are_independent_folders_with_their_own_marker(self):
        with mock.patch.object(skill, "skills_dir", return_value=self.packaged, create=True):
            for name in NAMES:
                install(self.target_named(name), "1.0.0")
        for name in NAMES:
            with self.subTest(name=name):
                marker = (
                    (self.target_named(name).path / MARKER).read_text(encoding="utf-8").split("\n")
                )
                self.assertEqual(marker[0], "1.0.0")
                self.assertEqual(marker[1], digest(files(self.packaged / name)))
        self.assertEqual(sorted(os.listdir(self.home / ".claude" / "skills")), sorted(NAMES))

    def test_the_source_is_asked_for_by_the_name_of_the_target(self):
        asked: list[str] = []

        def fake(name: str) -> Path:
            asked.append(name)
            return self.packaged / name

        with mock.patch.object(skill, "source_dir", side_effect=fake):
            install(self.target_named("jzr-pdf"), "1.0.0")
        self.assertEqual(asked, ["jzr-pdf"])
        self.assertEqual(files(self.target_named("jzr-pdf").path), files(self.packaged / "jzr-pdf"))

    def test_a_second_install_of_the_same_skill_is_current(self):
        with mock.patch.object(skill, "skills_dir", return_value=self.packaged, create=True):
            self.assertEqual(install(self.target_named("jzr-data"), "1.0.0").status, "installed")
            self.assertEqual(install(self.target_named("jzr-data"), "1.0.0").status, "current")

    def test_an_explicit_source_wins_over_the_packaged_folder(self):
        with mock.patch.object(skill, "skills_dir", return_value=self.packaged, create=True):
            install(self.target_named("jzr-pdf"), "1.0.0", source=self.src)
        self.assertEqual(files(self.target_named("jzr-pdf").path), SOURCE)


class InstallDecisionTests(SkillCase):
    def test_same_version_and_same_files_is_current(self):
        self.installed("1.0.0")
        before = snapshot(self.target.path)
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual(outcome.status, "current")
        self.assertEqual(snapshot(self.target.path), before)

    def test_current_stays_current_with_overwrite(self):
        self.installed("1.0.0")
        self.assertEqual(
            install(self.target, "1.0.0", source=self.src, overwrite=True).status, "current"
        )

    def test_older_version_is_updated_and_detail_is_the_old_version(self):
        self.installed("1.0.0")
        outcome = install(self.target, "1.1.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("updated", "1.0.0"))
        first = (self.target.path / MARKER).read_text(encoding="utf-8").split("\n")[0]
        self.assertEqual(first, "1.1.0")

    def test_same_version_with_a_different_source_is_updated(self):
        self.installed("1.0.0")
        outcome = install(self.target, "1.0.0", source=self.source_v2())
        self.assertEqual((outcome.status, outcome.detail), ("updated", "1.0.0"))
        self.assertEqual(files(self.target.path), SOURCE_V2)

    def test_stale_files_from_the_old_version_are_gone_after_update(self):
        self.installed("1.0.0")
        self.assertTrue((self.target.path / "reference" / "deep" / "more.md").exists())
        install(self.target, "1.1.0", source=self.source_v2())
        self.assertEqual(files(self.target.path), SOURCE_V2)
        self.assertFalse((self.target.path / "reference" / "deep").exists())

    def test_update_leaves_no_temp_folder(self):
        self.installed("1.0.0")
        install(self.target, "1.1.0", source=self.source_v2())
        self.assertEqual(os.listdir(self.target.path.parent), ["jzr-media"])

    def test_newer_installed_version_is_refused(self):
        self.installed("2.0.0")
        before = snapshot(self.target.path)
        outcome = install(self.target, "1.0.0", source=self.source_v2())
        self.assertEqual(outcome.status, "refused")
        self.assertEqual(
            outcome.detail,
            "installed by a newer jzr (2.0.0); use --overwrite to replace it",
        )
        self.assertEqual(snapshot(self.target.path), before)

    def test_newer_version_with_overwrite_is_updated(self):
        self.installed("2.0.0")
        outcome = install(self.target, "1.0.0", source=self.source_v2(), overwrite=True)
        self.assertEqual((outcome.status, outcome.detail), ("updated", "2.0.0"))
        self.assertEqual(files(self.target.path), SOURCE_V2)

    def test_versions_compare_as_numbers_not_text(self):
        self.installed("0.10.0")
        newer = install(self.target, "0.9.0", source=self.src)
        self.assertEqual(newer.status, "refused")
        self.assertIn("newer jzr (0.10.0)", newer.detail)
        # And the other way round: 0.9.0 installed, 0.10.0 running.
        other = Target(("claude",), self.root / "other" / "jzr-media")
        install(other, "0.9.0", source=self.src)
        self.assertEqual(install(other, "0.10.0", source=self.source_v2()).status, "updated")

    def test_an_unparsable_installed_version_counts_as_older(self):
        self.installed("banana")
        outcome = install(self.target, "0.1.0", source=self.source_v2())
        self.assertEqual((outcome.status, outcome.detail), ("updated", "banana"))

    def test_an_edited_copy_is_refused(self):
        self.installed("1.0.0")
        self.edit_installed()
        outcome = install(self.target, "1.1.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("refused", EDITED_INSTALL))
        self.assertEqual((self.target.path / "SKILL.md").read_bytes(), b"edited by the user\n")

    def test_an_added_file_counts_as_edited(self):
        self.installed("1.0.0")
        (self.target.path / "notes.md").write_text("mine")
        outcome = install(self.target, "1.1.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("refused", EDITED_INSTALL))

    def test_a_deleted_file_counts_as_edited(self):
        self.installed("1.0.0")
        (self.target.path / "reference" / "image.md").unlink()
        outcome = install(self.target, "1.1.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("refused", EDITED_INSTALL))

    def test_a_crlf_copy_of_an_untouched_install_is_not_edited(self):
        self.installed("1.0.0")
        path = self.target.path / "SKILL.md"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.assertEqual(install(self.target, "1.0.0", source=self.src).status, "current")

    def test_edited_wins_over_newer(self):
        self.installed("2.0.0")
        self.edit_installed()
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual(outcome.detail, EDITED_INSTALL)

    def test_edited_wins_over_current(self):
        self.installed("1.0.0")
        self.edit_installed()
        self.assertEqual(install(self.target, "1.0.0", source=self.src).status, "refused")

    def test_overwrite_replaces_an_edited_copy(self):
        self.installed("1.0.0")
        self.edit_installed()
        outcome = install(self.target, "1.1.0", source=self.src, overwrite=True)
        self.assertEqual((outcome.status, outcome.detail), ("updated", "1.0.0"))
        self.assertEqual(files(self.target.path), SOURCE)

    def test_folder_without_marker_is_refused_and_untouched(self):
        write_tree(self.target.path, {"SKILL.md": b"someone else's skill\n"})
        before = snapshot(self.target.path)
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("refused", NOT_OURS_INSTALL))
        self.assertEqual(snapshot(self.target.path), before)

    def test_folder_with_an_empty_marker_counts_as_unmarked(self):
        write_tree(self.target.path, {"SKILL.md": b"x\n", MARKER: b""})
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("refused", NOT_OURS_INSTALL))

    def test_folder_with_a_binary_marker_counts_as_unmarked(self):
        write_tree(self.target.path, {"SKILL.md": b"x\n", MARKER: b"\xff\xfe\x00\x01"})
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("refused", NOT_OURS_INSTALL))

    def test_overwrite_replaces_an_unmarked_folder_with_no_old_version(self):
        write_tree(self.target.path, {"old.md": b"old\n"})
        outcome = install(self.target, "1.0.0", source=self.src, overwrite=True)
        self.assertEqual((outcome.status, outcome.detail), ("updated", ""))
        self.assertEqual(files(self.target.path), SOURCE)

    def test_a_plain_file_is_refused(self):
        self.target.path.parent.mkdir(parents=True)
        self.target.path.write_text("a file")
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual((outcome.status, outcome.detail), ("refused", IS_FILE))

    def test_a_plain_file_is_refused_even_with_overwrite(self):
        self.target.path.parent.mkdir(parents=True)
        self.target.path.write_text("a file")
        outcome = install(self.target, "1.0.0", source=self.src, overwrite=True)
        self.assertEqual((outcome.status, outcome.detail), ("refused", IS_FILE))
        self.assertEqual(self.target.path.read_text(), "a file")


class InstallLinkTests(SkillCase):
    def check_link_is_never_followed(self, kind: str) -> None:
        real = self.make_link(kind)
        before = snapshot(real)

        plain = install(self.target, "1.0.0", source=self.source_v2())
        self.assertEqual((plain.status, plain.detail), ("refused", IS_LINK))

        forced = install(self.target, "1.0.0", source=self.source_v2(), overwrite=True)
        self.assertEqual((forced.status, forced.detail), ("refused", IS_LINK))

        self.assertEqual(snapshot(real), before)
        self.assertTrue(os.path.lexists(self.target.path))
        self.assertEqual(files(real), SOURCE)

    def test_symlink_is_refused_and_its_target_survives(self):
        self.check_link_is_never_followed("symlink")

    def test_junction_is_refused_and_its_target_survives(self):
        self.check_link_is_never_followed("junction")


class InstallDryRunTests(SkillCase):
    def test_missing_target_reports_installed_and_writes_nothing(self):
        outcome = install(self.target, "1.0.0", source=self.src, dry_run=True)
        self.assertEqual(outcome.status, "installed")
        self.assertEqual(snapshot(self.home), {})

    def test_update_reports_updated_and_changes_nothing(self):
        self.installed("1.0.0")
        before = snapshot(self.home)
        outcome = install(self.target, "1.1.0", source=self.source_v2(), dry_run=True)
        self.assertEqual((outcome.status, outcome.detail), ("updated", "1.0.0"))
        self.assertEqual(snapshot(self.home), before)

    def test_current_is_the_same_in_a_dry_run(self):
        self.installed("1.0.0")
        before = snapshot(self.home)
        self.assertEqual(
            install(self.target, "1.0.0", source=self.src, dry_run=True).status, "current"
        )
        self.assertEqual(snapshot(self.home), before)

    def test_refusals_are_the_same_in_a_dry_run(self):
        self.installed("1.0.0")
        self.edit_installed()
        before = snapshot(self.home)
        outcome = install(self.target, "1.1.0", source=self.src, dry_run=True)
        self.assertEqual((outcome.status, outcome.detail), ("refused", EDITED_INSTALL))
        self.assertEqual(snapshot(self.home), before)

    def test_overwrite_of_an_unmarked_folder_is_only_reported(self):
        write_tree(self.target.path, {"old.md": b"old\n"})
        before = snapshot(self.home)
        outcome = install(self.target, "1.0.0", source=self.src, overwrite=True, dry_run=True)
        self.assertEqual(outcome.status, "updated")
        self.assertEqual(snapshot(self.home), before)


class InstallFailureTests(SkillCase):
    def test_failed_first_install_reports_failed_and_leaves_nothing(self):
        with failing_rename(1) as state:
            outcome = install(self.target, "1.0.0", source=self.src)
        self.assertGreaterEqual(state["calls"], 1)
        self.assertEqual(outcome.status, "failed")
        self.assertTrue(outcome.detail)
        self.assertFalse(self.target.path.exists())
        self.assertEqual(leftovers(self.target.path.parent), [])

    def test_failing_to_move_the_old_copy_aside_changes_nothing(self):
        self.installed("1.0.0")
        before = snapshot(self.target.path)
        with failing_rename(1) as state:
            outcome = install(self.target, "1.1.0", source=self.source_v2())
        self.assertGreaterEqual(state["calls"], 1)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(snapshot(self.target.path), before)
        self.assertEqual(leftovers(self.target.path.parent), [])

    def test_failing_to_move_the_new_copy_in_puts_the_old_one_back(self):
        self.installed("1.0.0")
        before = snapshot(self.target.path)
        with failing_rename(2) as state:
            outcome = install(self.target, "1.1.0", source=self.source_v2())
        self.assertGreaterEqual(state["calls"], 2)
        self.assertEqual(outcome.status, "failed")
        self.assertTrue(outcome.detail)
        self.assertEqual(snapshot(self.target.path), before)
        self.assertEqual(os.listdir(self.target.path.parent), ["jzr-media"])

    def test_the_old_copy_is_still_a_valid_install_after_a_failure(self):
        self.installed("1.0.0")
        with failing_rename(2):
            install(self.target, "1.1.0", source=self.source_v2())
        again = install(self.target, "1.1.0", source=self.source_v2())
        self.assertEqual((again.status, again.detail), ("updated", "1.0.0"))

    def test_an_unwritable_parent_is_a_failure_not_an_exception(self):
        (self.home / ".claude").write_text("a file where a folder should be")
        outcome = install(self.target, "1.0.0", source=self.src)
        self.assertEqual(outcome.status, "failed")
        self.assertTrue(outcome.detail)


class RemoveTests(SkillCase):
    def test_missing_target_is_absent(self):
        outcome = remove(self.target)
        self.assertEqual((outcome.status, outcome.detail), ("absent", ""))
        self.assertEqual(snapshot(self.home), {})

    def test_installed_copy_is_removed_and_parents_stay(self):
        self.installed("1.0.0")
        sibling = self.target.path.parent / "other-skill"
        write_tree(sibling, {"SKILL.md": b"other\n"})
        outcome = remove(self.target)
        self.assertEqual((outcome.status, outcome.detail), ("removed", ""))
        self.assertFalse(self.target.path.exists())
        self.assertTrue(self.target.path.parent.is_dir())
        self.assertTrue((self.home / ".claude").is_dir())
        self.assertEqual((sibling / "SKILL.md").read_bytes(), b"other\n")

    def test_a_newer_version_is_still_removed(self):
        self.installed("9.0.0")
        self.assertEqual(remove(self.target).status, "removed")

    def test_unmarked_folder_is_refused_even_with_overwrite(self):
        write_tree(self.target.path, {"SKILL.md": b"not ours\n"})
        before = snapshot(self.target.path)
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                outcome = remove(self.target, overwrite=overwrite)
                self.assertEqual((outcome.status, outcome.detail), ("refused", NOT_OURS_REMOVE))
                self.assertEqual(snapshot(self.target.path), before)

    def test_edited_copy_is_refused_and_kept(self):
        self.installed("1.0.0")
        self.edit_installed()
        outcome = remove(self.target)
        self.assertEqual((outcome.status, outcome.detail), ("refused", EDITED_REMOVE))
        self.assertTrue((self.target.path / "SKILL.md").exists())

    def test_edited_copy_is_removed_with_overwrite(self):
        self.installed("1.0.0")
        (self.target.path / "mine.md").write_text("extra")
        outcome = remove(self.target, overwrite=True)
        self.assertEqual(outcome.status, "removed")
        self.assertFalse(self.target.path.exists())
        self.assertTrue(self.target.path.parent.is_dir())

    def test_plain_file_is_refused_with_or_without_overwrite(self):
        self.target.path.parent.mkdir(parents=True)
        self.target.path.write_text("a file")
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                outcome = remove(self.target, overwrite=overwrite)
                self.assertEqual((outcome.status, outcome.detail), ("refused", IS_FILE))
                self.assertEqual(self.target.path.read_text(), "a file")

    def check_link_is_kept(self, kind: str) -> None:
        real = self.make_link(kind)
        before = snapshot(real)
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                outcome = remove(self.target, overwrite=overwrite)
                self.assertEqual((outcome.status, outcome.detail), ("refused", IS_LINK))
                self.assertEqual(snapshot(real), before)
                self.assertTrue(os.path.lexists(self.target.path))

    def test_symlink_is_refused_and_its_target_survives(self):
        self.check_link_is_kept("symlink")

    def test_junction_is_refused_and_its_target_survives(self):
        self.check_link_is_kept("junction")

    def test_dry_run_reports_removed_and_deletes_nothing(self):
        self.installed("1.0.0")
        before = snapshot(self.home)
        outcome = remove(self.target, dry_run=True)
        self.assertEqual(outcome.status, "removed")
        self.assertEqual(snapshot(self.home), before)

    def test_dry_run_gives_the_same_refusals_and_absences(self):
        self.assertEqual(remove(self.target, dry_run=True).status, "absent")
        self.installed("1.0.0")
        self.edit_installed()
        outcome = remove(self.target, dry_run=True)
        self.assertEqual((outcome.status, outcome.detail), ("refused", EDITED_REMOVE))

    def test_removing_twice_ends_absent(self):
        self.installed("1.0.0")
        self.assertEqual(remove(self.target).status, "removed")
        self.assertEqual(remove(self.target).status, "absent")


class RemoveOldTests(SkillCase):
    """``remove_old`` is ``remove`` except that what jzr did not install is "foreign", not "refused"."""

    def setUp(self) -> None:
        super().setUp()
        self.target = Target(("claude",), self.home / ".claude" / "skills" / OLD_NAME)

    def test_missing_target_is_absent(self):
        outcome = skill.remove_old(self.target)
        self.assertEqual((outcome.status, outcome.detail), ("absent", ""))
        self.assertEqual(outcome.target, self.target)
        self.assertEqual(snapshot(self.home), {})

    def test_installed_copy_is_removed_and_parents_and_siblings_stay(self):
        self.installed("0.2.0")
        sibling = self.target.path.parent / "jzr-media"
        write_tree(sibling, {"SKILL.md": b"new\n"})
        outcome = skill.remove_old(self.target)
        self.assertEqual((outcome.status, outcome.detail), ("removed", ""))
        self.assertFalse(os.path.lexists(self.target.path))
        self.assertTrue(self.target.path.parent.is_dir())
        self.assertEqual((sibling / "SKILL.md").read_bytes(), b"new\n")
        self.assertEqual(leftovers(self.target.path.parent), [])

    def test_a_copy_from_a_newer_or_unparsable_version_is_still_removed(self):
        for version in ("9.0.0", "banana"):
            with self.subTest(version=version):
                other = Target(("claude",), self.root / f"v-{version}" / OLD_NAME)
                install(other, version, source=self.src)
                self.assertEqual(skill.remove_old(other).status, "removed")

    def test_edited_copy_is_refused_not_foreign_and_kept(self):
        self.installed("0.2.0")
        self.edit_installed()
        outcome = skill.remove_old(self.target)
        self.assertEqual((outcome.status, outcome.detail), ("refused", EDITED_REMOVE))
        self.assertEqual((self.target.path / "SKILL.md").read_bytes(), b"edited by the user\n")

    def test_an_added_file_counts_as_edited(self):
        self.installed("0.2.0")
        (self.target.path / "mine.md").write_text("extra")
        self.assertEqual(skill.remove_old(self.target).status, "refused")

    def test_edited_copy_is_removed_with_overwrite(self):
        self.installed("0.2.0")
        self.edit_installed()
        outcome = skill.remove_old(self.target, overwrite=True)
        self.assertEqual(outcome.status, "removed")
        self.assertFalse(os.path.lexists(self.target.path))
        self.assertTrue(self.target.path.parent.is_dir())

    def test_a_folder_without_marker_is_foreign_and_untouched(self):
        write_tree(self.target.path, {"SKILL.md": b"someone else's skill\n"})
        before = snapshot(self.target.path)
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                outcome = skill.remove_old(self.target, overwrite=overwrite)
                self.assertEqual(outcome.status, "foreign", outcome)
                self.assertEqual(outcome.target, self.target)
                self.assertEqual(snapshot(self.target.path), before)

    def test_a_folder_with_an_unusable_marker_is_foreign(self):
        for data in (b"", b"\xff\xfe\x00\x01", b"1.0.0\n"):
            with self.subTest(marker=data):
                other = Target(("claude",), self.root / f"m{len(data)}" / OLD_NAME)
                write_tree(other.path, {"SKILL.md": b"x\n", MARKER: data})
                self.assertEqual(skill.remove_old(other).status, "foreign")
                self.assertTrue((other.path / "SKILL.md").exists())

    def test_a_plain_file_is_foreign_with_or_without_overwrite(self):
        self.target.path.parent.mkdir(parents=True)
        self.target.path.write_text("a file")
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                outcome = skill.remove_old(self.target, overwrite=overwrite)
                self.assertEqual(outcome.status, "foreign", outcome)
                self.assertEqual(self.target.path.read_text(), "a file")

    def check_link_is_foreign(self, kind: str) -> None:
        real = self.make_link(kind)
        before = snapshot(real)
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                outcome = skill.remove_old(self.target, overwrite=overwrite)
                self.assertEqual(outcome.status, "foreign", outcome)
                self.assertEqual(snapshot(real), before)
                self.assertTrue(os.path.lexists(self.target.path))
        self.assertEqual(files(real), SOURCE)

    def test_symlink_is_foreign_and_its_target_survives(self):
        self.check_link_is_foreign("symlink")

    def test_junction_is_foreign_and_its_target_survives(self):
        self.check_link_is_foreign("junction")

    def test_dry_run_reports_removed_and_deletes_nothing(self):
        self.installed("0.2.0")
        before = snapshot(self.home)
        self.assertEqual(skill.remove_old(self.target, dry_run=True).status, "removed")
        self.assertEqual(snapshot(self.home), before)

    def test_dry_run_gives_the_same_absence_refusal_and_foreign(self):
        self.assertEqual(skill.remove_old(self.target, dry_run=True).status, "absent")
        self.installed("0.2.0")
        self.edit_installed()
        self.assertEqual(skill.remove_old(self.target, dry_run=True).status, "refused")
        other = Target(("claude",), self.root / "foreign" / OLD_NAME)
        write_tree(other.path, {"SKILL.md": b"x\n"})
        self.assertEqual(skill.remove_old(other, dry_run=True).status, "foreign")

    def test_a_failure_is_reported_not_raised(self):
        self.installed("0.2.0")
        before = snapshot(self.target.path)
        with failing_rename(1):
            outcome = skill.remove_old(self.target)
        self.assertEqual(outcome.status, "failed")
        self.assertTrue(outcome.detail)
        self.assertEqual(snapshot(self.target.path), before)

    def test_removing_twice_ends_absent(self):
        self.installed("0.2.0")
        self.assertEqual(skill.remove_old(self.target).status, "removed")
        self.assertEqual(skill.remove_old(self.target).status, "absent")

    def test_remove_itself_still_refuses_what_is_not_ours(self):
        write_tree(self.target.path, {"SKILL.md": b"not ours\n"})
        outcome = remove(self.target)
        self.assertEqual((outcome.status, outcome.detail), ("refused", NOT_OURS_REMOVE))


class PublicNamesTests(unittest.TestCase):
    def test_marker_and_names(self):
        self.assertEqual(skill.MARKER, ".jzr-version")
        self.assertEqual(skill.SKILL_NAMES, NAMES)
        self.assertEqual(skill.OLD_SKILL_NAMES, (OLD_NAME,))
        self.assertEqual(skill.AGENTS, ("claude", "codex"))


if __name__ == "__main__":
    unittest.main()
