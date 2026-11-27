"""Tests of the bare machine in jzr_bench.arms: the hidden programs, find_bare_machine, bare_args, the wrapper
and prepare_arm, then the real bubblewrap (skipped, with the reason, where it or its overlays are missing) and
run_all with the real bubblewrap.

The first half is offline: arguments are compared, nothing is started. The second half starts bubblewrap with
`--overlay-src` and whiteouts, as the benchmark does. Fake programs and sandboxes live outside /tmp (in
/var/tmp or the home folder's cache), because inside a run /tmp is somebody else's.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import stream_fixtures as fx  # noqa: E402
import test_arms as ta  # noqa: E402
import test_private_tmp as tpt  # noqa: E402
import test_runner as tr  # noqa: E402
from jzr_bench import arms  # noqa: E402
from jzr_bench import runner as runner_module  # noqa: E402
from jzr_bench.arms import ARMS, SYSTEM_PATH, ArmSetup, Sandbox, prepare_arm  # noqa: E402

REQUIRED_HIDDEN = [
    "ffmpeg", "ffprobe", "magick", "convert", "identify", "gs", "pdfunite", "pdfseparate", "pdftoppm",
    "qpdf", "soffice", "exiftool", "heif-convert", "cwebp", "pandoc",
]  # fmt: skip
GENERAL_TOOLS = ["python3", "pip", "curl", "git", "jq", "sh", "bash", "ls", "mv", "rename"]
JZR_ARMS = ("skill", "mcp")
FFMPEG_AT = ".local/share/jzr/ffmpeg"  # under the sandbox's HOME


def fake_python3(folder: Path, site_dirs: list[str]) -> Path:
    """A python3 that is the real interpreter, except that site.getsitepackages() returns `site_dirs`.

    It runs `-c CODE` the way python3 does (flags before -c are skipped), so it does not matter how
    find_bare_machine asks.
    """
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "python3"
    script.write_text(
        f"#!{sys.executable}\n"
        "import site, sys\n"
        f"site.getsitepackages = lambda *a, **k: {site_dirs!r}\n"
        "args = sys.argv[1:]\n"
        "i = 0\n"
        "while i < len(args) and args[i].startswith('-') and args[i] != '-c':\n"
        "    i += 1\n"
        "if i + 1 >= len(args) or args[i] != '-c':\n"
        "    sys.exit('fake python3 only runs -c CODE')\n"
        "sys.argv = ['-c', *args[i + 2:]]\n"
        "exec(compile(args[i + 1], '<string>', 'exec'), {'__name__': '__main__'})\n"
    )
    script.chmod(0o755)
    return script


def parse(args: list[str]) -> list[tuple[str, tuple[str, ...]]]:
    """bubblewrap arguments as (option, operands) pairs; fails on an option the bare machine should not use."""
    arity = {"--overlay-src": 1, "--overlay": 3, "--tmpfs": 1, "--ro-bind": 2}
    groups: list[tuple[str, tuple[str, ...]]] = []
    i = 0
    while i < len(args):
        option = args[i]
        if option not in arity:
            raise AssertionError(f"unexpected bubblewrap argument {option!r} in {args}")
        groups.append((option, tuple(args[i + 1 : i + 1 + arity[option]])))
        i += 1 + arity[option]
    return groups


def force_remove(path: Path) -> None:
    """Test clean-up only: overlay work folders are mode 000, which shutil.rmtree cannot enter."""
    for folder, dirs, _ in os.walk(path):
        os.chmod(folder, 0o700)
        for name in dirs:
            try:
                os.chmod(Path(folder, name), 0o700)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def is_whiteout(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISCHR(info.st_mode) and os.major(info.st_rdev) == 0 and os.minor(info.st_rdev) == 0


# -- the list -------------------------------------------------------------------------------------


class HiddenProgramsTest(unittest.TestCase):
    def test_it_is_a_tuple_of_plain_unique_names(self) -> None:
        hidden = arms.BARE_HIDDEN_PROGRAMS
        self.assertIsInstance(hidden, tuple)
        self.assertTrue(all(isinstance(name, str) and name and "/" not in name for name in hidden))
        self.assertEqual(len(set(hidden)), len(hidden), "a whiteout is made once per name")

    def test_the_media_pdf_and_office_programs_are_hidden(self) -> None:
        for name in REQUIRED_HIDDEN:
            with self.subTest(name=name):
                self.assertIn(name, arms.BARE_HIDDEN_PROGRAMS)

    def test_the_general_tools_stay(self) -> None:
        for name in GENERAL_TOOLS:
            with self.subTest(name=name):
                self.assertNotIn(name, arms.BARE_HIDDEN_PROGRAMS)

    def test_the_machines_are_full_and_bare(self) -> None:
        self.assertEqual(tuple(arms.MACHINES), ("full", "bare"))


# -- find_bare_machine ----------------------------------------------------------------------------


class FindBareMachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()

    def folder(self, name: str) -> Path:
        path = self.tmp / name
        path.mkdir(parents=True)
        return path

    def test_every_real_folder_of_the_path_once_in_order(self) -> None:
        first, second = self.folder("first"), self.folder("second")
        machine = arms.find_bare_machine(f"{first}:{second}")
        self.assertEqual(machine.bin_dirs, (first, second))
        self.assertIsInstance(machine.bin_dirs, tuple)
        self.assertTrue(all(isinstance(p, Path) for p in machine.bin_dirs))

    def test_a_folder_that_is_a_link_to_another_is_not_twice(self) -> None:
        usr_bin = self.folder("usr/bin")
        (self.tmp / "bin").symlink_to(usr_bin)  # like /bin -> usr/bin
        local = self.folder("usr/local/bin")
        for path in (f"{self.tmp}/bin:{usr_bin}", f"{usr_bin}:{self.tmp}/bin", f"{local}:{self.tmp}/bin:{usr_bin}"):
            with self.subTest(path=path):
                machine = arms.find_bare_machine(path)
                self.assertEqual(machine.bin_dirs.count(usr_bin), 1)
                self.assertEqual(len(machine.bin_dirs), len(set(machine.bin_dirs)))
                self.assertNotIn(self.tmp / "bin", machine.bin_dirs, "the link itself is not a folder to cover")

    def test_the_same_folder_written_twice_is_once(self) -> None:
        one = self.folder("one")
        self.assertEqual(arms.find_bare_machine(f"{one}:{one}:{one}/.").bin_dirs, (one,))

    def test_missing_folders_and_plain_files_are_dropped(self) -> None:
        real = self.folder("real")
        (self.tmp / "afile").write_text("not a folder\n")
        machine = arms.find_bare_machine(f"{self.tmp}/nope:{real}:{self.tmp}/afile")
        self.assertEqual(machine.bin_dirs, (real,))

    def test_no_folder_at_all_gives_no_bin_dirs(self) -> None:
        self.assertEqual(arms.find_bare_machine(f"{self.tmp}/nope").bin_dirs, ())

    def test_site_dirs_come_from_the_first_python3_on_the_path(self) -> None:
        site_a, site_b = self.folder("site-a"), self.folder("site-b")
        first, second = self.folder("first"), self.folder("second")
        fake_python3(first, [str(site_a)])
        fake_python3(second, [str(site_b)])
        machine = arms.find_bare_machine(f"{first}:{second}")
        self.assertEqual(machine.site_dirs, (site_a,))
        machine = arms.find_bare_machine(f"{second}:{first}")
        self.assertEqual(machine.site_dirs, (site_b,))

    def test_a_folder_without_python3_does_not_stop_the_search(self) -> None:
        site = self.folder("site")
        empty, with_python = self.folder("empty"), self.folder("with-python")
        fake_python3(with_python, [str(site)])
        self.assertEqual(arms.find_bare_machine(f"{empty}:{with_python}").site_dirs, (site,))

    def test_only_the_site_dirs_that_exist_in_the_order_given(self) -> None:
        one, two = self.folder("site-one"), self.folder("site-two")
        folder = self.folder("py")
        fake_python3(folder, [str(one), str(self.tmp / "gone"), str(two)])
        machine = arms.find_bare_machine(str(folder))
        self.assertEqual(machine.site_dirs, (one, two))
        self.assertIsInstance(machine.site_dirs, tuple)

    def test_no_python3_no_site_dirs(self) -> None:
        folder = self.folder("tools")
        machine = arms.find_bare_machine(str(folder))
        self.assertEqual(machine.site_dirs, ())
        self.assertEqual(machine.bin_dirs, (folder,))

    def test_the_jzr_ffmpeg_is_not_known_yet(self) -> None:
        self.assertIsNone(arms.find_bare_machine(str(self.folder("x"))).jzr_ffmpeg)

    def test_the_default_is_the_system_path(self) -> None:
        machine = arms.find_bare_machine()
        wanted = {os.path.realpath(p) for p in SYSTEM_PATH.split(":") if os.path.isdir(p)}
        self.assertEqual({str(p) for p in machine.bin_dirs}, wanted)
        self.assertEqual(len(machine.bin_dirs), len(wanted))

    def test_a_bare_machine_is_a_frozen_value(self) -> None:
        machine = arms.BareMachine(bin_dirs=(Path("/a"),), site_dirs=())
        self.assertIsNone(machine.jzr_ffmpeg)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            machine.jzr_ffmpeg = Path("/x")  # type: ignore[misc]


# -- bare_args ------------------------------------------------------------------------------------


class BareArgsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.sandbox = Sandbox.at(self.tmp / "sandbox")
        self.sandbox.root.mkdir()
        self.bins = (self.tmp / "usr" / "bin", self.tmp / "usr" / "local" / "bin")
        for folder in self.bins:
            folder.mkdir(parents=True)
        self.ffmpeg = self.tmp / "jzr-data" / "ffmpeg"
        (self.ffmpeg / "bin").mkdir(parents=True)

    def site(self, name: str, entries: list[str]) -> Path:
        folder = self.tmp / name
        for entry in entries:
            (folder / entry).mkdir(parents=True)
        folder.mkdir(exist_ok=True)
        return folder

    def args(self, *, sites: tuple[Path, ...] = (), ffmpeg: Path | None = None, jzr: bool = False) -> list[str]:
        machine = arms.BareMachine(bin_dirs=self.bins, site_dirs=sites, jzr_ffmpeg=ffmpeg)
        result = arms.bare_args(machine, self.sandbox, jzr=jzr)
        self.assertIsInstance(result, list)
        self.assertTrue(all(isinstance(word, str) for word in result))
        return result

    def overlays(self, groups: list[tuple[str, tuple[str, ...]]]) -> list[tuple[str, str, str, str]]:
        """(source, upper, work, destination) of every overlay, checking --overlay-src comes right before it."""
        found = []
        for index, (option, operands) in enumerate(groups):
            if option == "--overlay":
                before = groups[index - 1]
                self.assertEqual(before[0], "--overlay-src", "an overlay needs its source named first")
                found.append((before[1][0], *operands))
        return found

    # -- the bin dirs ----------------------------------------------------------------------------

    def test_one_overlay_per_bin_dir_over_itself(self) -> None:
        overlays = self.overlays(parse(self.args()))
        self.assertEqual([(src, dest) for src, _, _, dest in overlays], [(str(b), str(b)) for b in self.bins])

    def test_the_overlay_folders_are_made_inside_the_sandbox_and_are_not_shared(self) -> None:
        overlays = self.overlays(parse(self.args()))
        uppers = [Path(upper) for _, upper, _, _ in overlays]
        works = [Path(work) for _, _, work, _ in overlays]
        self.assertEqual(len(set(uppers)), len(self.bins))
        for upper, work in zip(uppers, works, strict=True):
            self.assertTrue(upper.is_dir(), upper)
            self.assertTrue(work.is_dir(), work)
            self.assertEqual((upper.name, work.name), ("upper", "work"))
            self.assertEqual(upper.parent, work.parent, "overlayfs wants the work folder next to the upper one")
            self.assertTrue(upper.is_relative_to(self.sandbox.root / "overlay"), upper)
            self.assertEqual(list(work.iterdir()), [])

    def test_every_hidden_program_has_a_whiteout_in_every_upper_folder(self) -> None:
        overlays = self.overlays(parse(self.args()))
        self.assertEqual(len(overlays), 2)
        for _, upper, _, dest in overlays:
            for name in arms.BARE_HIDDEN_PROGRAMS:
                with self.subTest(dest=dest, name=name):
                    self.assertTrue(is_whiteout(Path(upper) / name), f"{name} is not a 0/0 character device")

    def test_a_whiteout_is_made_even_for_a_program_the_folder_does_not_have(self) -> None:
        (self.bins[0] / "ffmpeg").write_text("")  # only the first folder has one
        overlays = self.overlays(parse(self.args()))
        for _, upper, _, _ in overlays:
            self.assertTrue(is_whiteout(Path(upper) / "ffmpeg"))

    def test_nothing_but_whiteouts_hides_anything_else(self) -> None:
        overlays = self.overlays(parse(self.args()))
        for _, upper, _, _ in overlays:
            names = {p.name for p in Path(upper).iterdir()}
            for name in GENERAL_TOOLS:
                self.assertNotIn(name, names)

    def test_no_site_dirs_no_tmpfs_and_no_bind(self) -> None:
        groups = parse(self.args())
        self.assertEqual({option for option, _ in groups}, {"--overlay-src", "--overlay"})

    # -- the site dirs ---------------------------------------------------------------------------

    def test_a_site_dir_is_replaced_by_an_empty_one_and_pip_is_put_back(self) -> None:
        site = self.site(
            "site-packages",
            ["pip", "pip-24.0.dist-info", "pipx", "pip_tools-7.0.dist-info", "PIL", "Pillow-10.0.dist-info", "yaml"],
        )
        (site / "six.py").write_text("")
        (site / "README.txt").write_text("")
        groups = parse(self.args(sites=(site,)))
        self.assertEqual([operands for option, operands in groups if option == "--tmpfs"], [(str(site),)])
        binds = [operands for option, operands in groups if option == "--ro-bind"]
        self.assertCountEqual(
            binds,
            [
                (str(site / "pip"), str(site / "pip")),
                (str(site / "pip-24.0.dist-info"), str(site / "pip-24.0.dist-info")),
            ],
        )

    def test_the_tmpfs_comes_before_what_is_put_back_on_it(self) -> None:
        site = self.site("site-packages", ["pip", "pip-24.0.dist-info"])
        groups = parse(self.args(sites=(site,)))
        tmpfs_at = groups.index(("--tmpfs", (str(site),)))
        for index, (option, operands) in enumerate(groups):
            if option == "--ro-bind":
                self.assertGreater(index, tmpfs_at, operands)

    def test_a_site_dir_without_pip_is_only_emptied(self) -> None:
        site = self.site("site-packages", ["PIL", "yaml", "pipx"])
        groups = parse(self.args(sites=(site,)))
        self.assertIn(("--tmpfs", (str(site),)), groups)
        self.assertEqual([g for g in groups if g[0] == "--ro-bind"], [])

    def test_every_site_dir_is_handled_on_its_own(self) -> None:
        one = self.site("lib/site-packages", ["pip", "pip-24.0.dist-info", "PIL"])
        two = self.site("lib64/site-packages", ["numpy"])
        three = self.site("local/site-packages", ["pip", "x"])
        groups = parse(self.args(sites=(one, two, three)))
        self.assertEqual(
            [operands[0] for option, operands in groups if option == "--tmpfs"], [str(one), str(two), str(three)]
        )
        binds = {operands for option, operands in groups if option == "--ro-bind"}
        self.assertEqual(
            binds,
            {(str(p), str(p)) for p in (one / "pip", one / "pip-24.0.dist-info", three / "pip")},
        )
        for option, operands in groups:
            if option == "--ro-bind":
                owner = next(s for s in (one, two, three) if Path(operands[0]).is_relative_to(s))
                self.assertLess(groups.index(("--tmpfs", (str(owner),))), groups.index((option, operands)))

    # -- jzr's ffmpeg ----------------------------------------------------------------------------

    def jzr_binds(self, groups: list[tuple[str, tuple[str, ...]]]) -> list[tuple[str, ...]]:
        return [operands for option, operands in groups if option == "--ro-bind"]

    def test_the_jzr_ffmpeg_is_bound_read_only_under_the_sandbox_home(self) -> None:
        groups = parse(self.args(ffmpeg=self.ffmpeg, jzr=True))
        self.assertEqual(
            self.jzr_binds(groups), [(str(self.ffmpeg), f"{self.sandbox.root}/home/.local/share/jzr/ffmpeg")]
        )

    def test_not_bound_when_the_arm_is_not_a_jzr_arm(self) -> None:
        self.assertEqual(self.jzr_binds(parse(self.args(ffmpeg=self.ffmpeg, jzr=False))), [])

    def test_not_bound_when_there_is_no_copy_to_bind(self) -> None:
        self.assertEqual(self.jzr_binds(parse(self.args(ffmpeg=None, jzr=True))), [])

    def test_the_jzr_ffmpeg_comes_next_to_site_dirs_without_disturbing_them(self) -> None:
        site = self.site("site-packages", ["pip"])
        groups = parse(self.args(sites=(site,), ffmpeg=self.ffmpeg, jzr=True))
        self.assertCountEqual(
            self.jzr_binds(groups),
            [(str(site / "pip"), str(site / "pip")), (str(self.ffmpeg), f"{self.sandbox.home}/{FFMPEG_AT}")],
        )


# -- the wrapper ----------------------------------------------------------------------------------


class PrivateTmpWrapperExtraTest(unittest.TestCase):
    EXTRA = ["--overlay-src", "/usr/bin", "--overlay", "/s/u", "/s/w", "/usr/bin", "--tmpfs", "/x"]

    def wrapper(self, root: str, extra: list[str] | None = None) -> list[str]:
        sandbox = Sandbox.at(Path(root))
        if extra is None:
            return arms.private_tmp_wrapper(ta.FAKE_BWRAP, sandbox)
        return arms.private_tmp_wrapper(ta.FAKE_BWRAP, sandbox, extra=extra)

    def test_the_extra_arguments_come_as_they_are_before_die_with_parent(self) -> None:
        wrapper = self.wrapper("/var/tmp/wab12cd34", self.EXTRA)
        start = wrapper.index("--overlay-src")
        self.assertEqual(wrapper[start : start + len(self.EXTRA)], self.EXTRA)
        self.assertLess(start + len(self.EXTRA) - 1, wrapper.index("--die-with-parent"))

    def test_they_come_after_the_machines_file_system_is_mounted(self) -> None:
        wrapper = self.wrapper("/var/tmp/wab12cd34", self.EXTRA)
        self.assertLess(wrapper.index("--dev-bind"), wrapper.index("--overlay-src"))

    def test_the_rest_of_the_wrapper_is_what_it_was(self) -> None:
        plain = self.wrapper("/var/tmp/wab12cd34")
        wrapper = self.wrapper("/var/tmp/wab12cd34", self.EXTRA)
        self.assertEqual([w for w in wrapper if w not in self.EXTRA], [w for w in plain if w not in self.EXTRA])
        self.assertEqual(wrapper[0], ta.FAKE_BWRAP)
        self.assertEqual(wrapper[-1], "--")
        self.assertEqual(wrapper.count("--"), 1)
        self.assertEqual(wrapper.count("--die-with-parent"), 1)

    def test_extra_arguments_also_come_before_it_for_a_sandbox_inside_tmp(self) -> None:
        wrapper = self.wrapper("/tmp/wab12cd34", self.EXTRA)
        self.assertLess(wrapper.index("--overlay-src"), wrapper.index("--die-with-parent"))
        self.assertEqual(ta.bind_pairs(wrapper), [("/tmp/wab12cd34/tmp", "/tmp"), ("/tmp/wab12cd34", "/tmp/wab12cd34")])

    def test_no_extra_is_the_old_wrapper(self) -> None:
        plain = self.wrapper("/var/tmp/wab12cd34")
        self.assertEqual(
            plain, arms.private_tmp_wrapper(ta.FAKE_BWRAP, Sandbox.at(Path("/var/tmp/wab12cd34")), extra=())
        )
        self.assertNotIn("--overlay", plain)


# -- prepare_arm ----------------------------------------------------------------------------------


class PrepareArmBareTest(ta.ArmTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bin_dirs = (self.tmp / "machine" / "bin", self.tmp / "machine" / "local-bin")
        for folder in self.bin_dirs:
            folder.mkdir(parents=True)
        self.site = self.tmp / "machine" / "site-packages"
        (self.site / "pip").mkdir(parents=True)
        (self.site / "PIL").mkdir()
        self.ffmpeg = self.tmp / "jzr-data" / "ffmpeg"
        (self.ffmpeg / "bin").mkdir(parents=True)
        self.machine = arms.BareMachine(self.bin_dirs, (self.site,), self.ffmpeg)

    def prepare_bare(self, arm: str, *, bwrap: str | None = ta.FAKE_BWRAP, bare: Any = ...) -> ArmSetup:
        root = self.tmp / f"sandbox-bare-{arm}"
        root.mkdir(exist_ok=True)
        extra: dict[str, Any] = {"bare": self.machine if bare is ... else bare}
        if bwrap is not None:
            extra["bwrap"] = bwrap
        return prepare_arm(
            arm,
            root,
            self.source,
            self.auth,
            model="sonnet",
            max_turns=30,
            system_path=self.system_path,
            **extra,
        )

    def test_bare_without_bwrap_is_refused_in_every_arm(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                with self.assertRaises(ValueError):
                    self.prepare_bare(arm, bwrap=None)

    def test_the_setup_says_bare(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                self.assertEqual(self.prepare_bare(arm).machine, "bare")

    def test_without_bare_the_machine_is_full_and_the_wrapper_is_as_before(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                with_bwrap = self.prepare(arm, bwrap=ta.FAKE_BWRAP)
                self.assertEqual(with_bwrap.machine, "full")
                self.assertEqual(with_bwrap.wrapper, arms.private_tmp_wrapper(ta.FAKE_BWRAP, with_bwrap.sandbox))
                shutil.rmtree(with_bwrap.sandbox.root)  # prepare makes the arm's sandbox once
                shared = self.prepare(arm)
                self.assertEqual(shared.machine, "full")
                self.assertEqual(shared.wrapper, [])

    def test_bare_none_is_the_full_machine(self) -> None:
        setup = self.prepare_bare("baseline", bare=None)
        self.assertEqual(setup.machine, "full")
        self.assertEqual(setup.wrapper, arms.private_tmp_wrapper(ta.FAKE_BWRAP, setup.sandbox))

    def test_a_hand_made_setup_is_on_the_full_machine(self) -> None:
        sandbox = Sandbox.at(self.tmp / "hand")
        self.assertEqual(ArmSetup(arm="baseline", sandbox=sandbox, env={}, claude_args=[]).machine, "full")

    def test_the_wrapper_holds_the_bare_arguments_before_die_with_parent(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare_bare(arm)
                wrapper = setup.wrapper
                self.assertEqual(wrapper[:4], [ta.FAKE_BWRAP, "--dev-bind", "/", "/"])
                self.assertEqual(wrapper[-1], "--")
                self.assertEqual(wrapper.count("--"), 1)
                self.assertEqual(wrapper[wrapper.index("--chdir") + 1], str(setup.sandbox.work))
                self.assertIn((str(setup.sandbox.tmp), "/tmp"), ta.bind_pairs(wrapper))
                done = wrapper.index("--die-with-parent")
                for folder in self.bin_dirs:
                    self.assertEqual(wrapper.count(str(folder)), 2, "the overlay's source and its destination")
                    self.assertIn("--overlay-src", wrapper)
                    self.assertLess(wrapper.index(str(folder)), done)
                self.assertEqual(wrapper.count("--overlay-src"), 2)
                self.assertEqual(wrapper.count("--overlay"), 2)
                self.assertIn(["--tmpfs", str(self.site)], [wrapper[i : i + 2] for i in range(len(wrapper))])
                self.assertLess(wrapper.index("--tmpfs"), done)

    def test_the_whiteouts_exist_when_the_setup_is_returned(self) -> None:
        setup = self.prepare_bare("baseline")
        uppers = sorted((setup.sandbox.root / "overlay").glob("*/upper"))
        self.assertEqual(len(uppers), 2)
        for upper in uppers:
            self.assertTrue(is_whiteout(upper / "ffmpeg"))
            self.assertTrue(upper.parent.joinpath("work").is_dir())

    def test_pip_is_put_back_in_the_wrapper(self) -> None:
        wrapper = self.prepare_bare("baseline").wrapper
        self.assertIn(
            ["--ro-bind", str(self.site / "pip"), str(self.site / "pip")],
            [wrapper[i : i + 3] for i in range(len(wrapper))],
        )
        self.assertNotIn(str(self.site / "PIL"), wrapper)

    def test_the_jzr_ffmpeg_is_bound_for_skill_and_mcp_but_not_for_the_baseline(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare_bare(arm)
                dest = str(setup.sandbox.home / ".local" / "share" / "jzr" / "ffmpeg")
                binds = [
                    (setup.wrapper[i + 1], setup.wrapper[i + 2])
                    for i, w in enumerate(setup.wrapper)
                    if w == "--ro-bind"
                ]
                if arm in JZR_ARMS:
                    self.assertIn((str(self.ffmpeg), dest), binds)
                else:
                    self.assertNotIn(str(self.ffmpeg), [src for src, _ in binds])
                    self.assertNotIn(dest, [d for _, d in binds])
                self.assertNotIn(str(self.ffmpeg), setup.claude_args)

    def test_without_a_jzr_ffmpeg_no_arm_gets_the_bind(self) -> None:
        self.machine = dataclasses.replace(self.machine, jzr_ffmpeg=None)
        for arm in ARMS:
            with self.subTest(arm=arm):
                wrapper = self.prepare_bare(arm).wrapper
                self.assertFalse([w for w in wrapper if w.endswith(FFMPEG_AT)])

    def test_environment_and_claude_arguments_are_the_private_tmp_ones(self) -> None:
        for arm in ARMS:
            with self.subTest(arm=arm):
                setup = self.prepare_bare(arm)
                full = self.prepare(arm, bwrap=ta.FAKE_BWRAP)
                self.assertEqual(set(setup.env), ta.PRIVATE_TMP_KEYS | {ta.OAUTH})
                self.assertNotIn("TMPDIR", setup.env)
                same_but_for_the_folder = [
                    a.replace(str(full.sandbox.root), str(setup.sandbox.root)) for a in full.claude_args
                ]
                self.assertEqual(setup.claude_args, same_but_for_the_folder)
                self.assertEqual(
                    setup.env["PATH"], full.env["PATH"].replace(str(full.sandbox.bin), str(setup.sandbox.bin))
                )

    def test_the_other_arm_checks_still_apply(self) -> None:
        with self.assertRaises(ValueError):
            self.prepare_bare("split")


# -- the real bubblewrap --------------------------------------------------------------------------


def overlay_problem() -> str | None:
    """Why `bwrap --overlay-src` with a whiteout cannot be used here, or None when it can."""
    if tpt.BWRAP_PROBLEM is not None or tpt.BWRAP is None:
        return f"needs bubblewrap: {tpt.BWRAP_PROBLEM}"
    if tpt.OUTSIDE_TMP is None:
        return "needs a writable folder outside /tmp (/var/tmp or ~/.cache)"
    probe = Path(tempfile.mkdtemp(prefix="probe", dir=tpt.OUTSIDE_TMP))
    try:
        (probe / "upper").mkdir()
        (probe / "work").mkdir()
        try:
            os.mknod(probe / "upper" / "ffmpeg", stat.S_IFCHR | 0, os.makedev(0, 0))
        except OSError as error:
            return f"cannot make a whiteout (character device 0/0): {error}"
        done = subprocess.run(
            [
                tpt.BWRAP, "--dev-bind", "/", "/",
                "--overlay-src", "/usr/bin", "--overlay", str(probe / "upper"), str(probe / "work"), "/usr/bin",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
        )  # fmt: skip
        if done.returncode != 0:
            return f"`bwrap --overlay-src ... --overlay ...` fails here: {done.stderr.strip()[:200]}"
        return None
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"the overlay probe could not run: {error}"
    finally:
        force_remove(probe)


OVERLAY_PROBLEM = overlay_problem()
needs_overlay = unittest.skipIf(
    OVERLAY_PROBLEM is not None, f"bare machine needs bubblewrap overlays: {OVERLAY_PROBLEM}"
)

HIDEABLE = ["ffmpeg", "magick", "gs"]  # the programs this test looks for on the full machine


def on_the_full_machine(name: str) -> str | None:
    return shutil.which(name, path=SYSTEM_PATH)


def third_party_module() -> str | None:
    """A module the full machine's python3 has and the standard library does not."""
    python = shutil.which("python3", path=SYSTEM_PATH)
    if python is None:
        return None
    for module in ("PIL", "yaml", "mutagen"):
        done = subprocess.run(
            [python, "-c", f"import {module}"],
            env={"PATH": SYSTEM_PATH},
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
        if done.returncode == 0:
            return module
    return None


class RealBareCase(ta.ArmTestCase):
    """prepare_arm with the real bubblewrap and the real machine, sandboxes in /var/tmp."""

    def setUp(self) -> None:
        super().setUp()
        assert tpt.OUTSIDE_TMP is not None
        self.parent = Path(tempfile.mkdtemp(prefix="b", dir=tpt.OUTSIDE_TMP))
        self.addCleanup(force_remove, self.parent)
        self.machine = arms.find_bare_machine()
        self.jzr_ffmpeg = self.parent / "jzr-data" / "ffmpeg"
        (self.jzr_ffmpeg / "bin").mkdir(parents=True)
        (self.jzr_ffmpeg / "bin" / "ffmpeg").write_text("a stand-in for jzr's own ffmpeg\n")

    def bare_setup(self, arm: str = "baseline", *, ffmpeg: bool = True) -> ArmSetup:
        root = Path(tempfile.mkdtemp(prefix="w", dir=self.parent))
        machine = dataclasses.replace(self.machine, jzr_ffmpeg=self.jzr_ffmpeg if ffmpeg else None)
        return prepare_arm(
            arm,
            root,
            self.source,
            self.auth,
            model="sonnet",
            max_turns=3,
            system_path=self.system_path,
            bwrap=tpt.BWRAP,
            bare=machine,
        )

    def sh(self, setup: ArmSetup, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        """`sh -c script args` inside the run's wrapper, with the real system PATH (the sandbox's PATH is a stub)."""
        env = dict(setup.env, PATH=SYSTEM_PATH)
        return subprocess.run(
            [*setup.wrapper, "/bin/sh", "-c", script, "sh", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )

    def ok(self, setup: ArmSetup, script: str, *args: str) -> str:
        done = self.sh(setup, script, *args)
        self.assertEqual(done.returncode, 0, f"{script!r}: {done.stderr}")
        return done.stdout


@needs_overlay
class RealBareProgramsTest(RealBareCase):
    def test_no_hidden_program_exists_in_any_folder_of_the_system_path(self) -> None:
        present = [
            f"{d}/{p}" for d in SYSTEM_PATH.split(":") for p in arms.BARE_HIDDEN_PROGRAMS if os.path.lexists(f"{d}/{p}")
        ]
        if not present:
            self.skipTest("this machine has none of the hidden programs, so there is nothing to hide")
        setup = self.bare_setup()
        script = (
            'IFS=:; for d in $SYSPATH; do for p in "$@"; do '
            'if [ -e "$d/$p" ] || [ -L "$d/$p" ]; then echo "$d/$p"; fi; done; done'
        )
        done = subprocess.run(
            [*setup.wrapper, "/bin/sh", "-c", script, "sh", *arms.BARE_HIDDEN_PROGRAMS],
            env=dict(setup.env, SYSPATH=SYSTEM_PATH),
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.split(), [], f"still there, of the {len(present)} the full machine has")

    def test_a_hidden_program_is_not_found_and_running_it_is_command_not_found(self) -> None:
        found = [name for name in HIDEABLE if on_the_full_machine(name)]
        if not found:
            self.skipTest(f"none of {', '.join(HIDEABLE)} is on this machine, so there is nothing to hide")
        setup = self.bare_setup()
        for name in found:
            with self.subTest(program=name):
                looked = self.sh(setup, 'command -v "$1"', name)
                self.assertNotEqual(looked.returncode, 0)
                self.assertEqual(looked.stdout.strip(), "")
                ran = self.sh(setup, '"$1" --version', name)
                self.assertEqual(ran.returncode, 127, ran.stderr)
                self.assertIn("not found", ran.stderr)
                self.assertNotIn("ermission", ran.stderr)
                self.assertNotIn("ermission", looked.stderr)

    def test_a_hidden_program_called_by_its_real_path_is_not_there_either(self) -> None:
        paths = [os.path.realpath(p) for p in map(on_the_full_machine, HIDEABLE) if p]
        if not paths:
            self.skipTest(f"none of {', '.join(HIDEABLE)} is on this machine")
        setup = self.bare_setup()
        for path in paths:
            with self.subTest(path=path):
                ran = self.sh(setup, '"$1" --version', path)
                self.assertEqual(ran.returncode, 127, ran.stderr)
                self.assertNotIn("ermission", ran.stderr)

    def test_the_general_tools_still_work(self) -> None:
        setup = self.bare_setup()
        self.assertEqual(self.ok(setup, "ls -d /usr/bin"), "/usr/bin\n")
        self.assertIn("git version", self.ok(setup, "git --version"))
        self.assertEqual(self.ok(setup, 'python3 -c "import json, sqlite3, zipfile; print(6 * 7)"'), "42\n")
        for name in ("curl", "jq", "zip", "rename", "bash"):
            if on_the_full_machine(name):
                with self.subTest(program=name):
                    self.assertTrue(self.ok(setup, 'command -v "$1"', name).strip().endswith(name))

    def test_every_general_tool_the_machine_has_is_still_found(self) -> None:
        setup = self.bare_setup()
        for name in GENERAL_TOOLS:
            if on_the_full_machine(name):
                with self.subTest(program=name):
                    self.ok(setup, 'command -v "$1"', name)

    def test_pip_works(self) -> None:
        setup = self.bare_setup()
        self.assertTrue(self.ok(setup, "python3 -m pip --version").startswith("pip "))

    def test_a_third_party_module_of_the_system_python_cannot_be_imported(self) -> None:
        module = third_party_module()
        if module is None:
            self.skipTest("the system python3 has none of PIL, yaml, mutagen, so there is nothing to hide")
        setup = self.bare_setup()
        done = self.sh(setup, 'python3 -c "import $1" 2>&1', module)
        self.assertNotEqual(done.returncode, 0, f"import {module} still works")
        self.assertIn("ModuleNotFoundError", done.stdout)

    def test_everything_is_back_outside_the_wrapper_afterwards(self) -> None:
        module = third_party_module()
        found = [(name, on_the_full_machine(name)) for name in (*HIDEABLE, "python3", "git", "ls")]
        setup = self.bare_setup()
        self.ok(setup, "true")
        for name, path in found:
            self.assertEqual(on_the_full_machine(name), path, name)
            if path:
                self.assertTrue(os.path.exists(path), path)
        if module:
            python = shutil.which("python3", path=SYSTEM_PATH)
            done = subprocess.run([python, "-c", f"import {module}"], env={"PATH": SYSTEM_PATH}, capture_output=True)
            self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_wrapper_still_gives_the_private_tmp_and_the_work_folder(self) -> None:
        setup = self.bare_setup()
        self.ok(setup, "echo marker > /tmp/bare-probe && pwd > /tmp/where")
        self.assertEqual((setup.sandbox.tmp / "bare-probe").read_text(), "marker\n")
        self.assertEqual(
            os.path.realpath((setup.sandbox.tmp / "where").read_text().strip()), str(setup.sandbox.work.resolve())
        )
        self.assertFalse(os.path.exists("/tmp/bare-probe"))


@needs_overlay
class RealBareJzrFfmpegTest(RealBareCase):
    def test_a_jzr_arm_sees_the_ffmpeg_folder_under_its_home_read_only(self) -> None:
        for arm in JZR_ARMS:
            with self.subTest(arm=arm):
                setup = self.bare_setup(arm)
                folder = f"$HOME/{FFMPEG_AT}"
                self.assertEqual(self.ok(setup, f"cat {folder}/bin/ffmpeg"), "a stand-in for jzr's own ffmpeg\n")
                refused = self.sh(setup, f"touch {folder}/new 2>&1")
                self.assertNotEqual(refused.returncode, 0, "the jzr ffmpeg folder is writable")
                self.assertIn("ead-only", refused.stdout)
                removed = self.sh(setup, f"rm {folder}/bin/ffmpeg 2>&1")
                self.assertNotEqual(removed.returncode, 0)
                self.assertTrue((self.jzr_ffmpeg / "bin" / "ffmpeg").exists())
                self.assertFalse((self.jzr_ffmpeg / "new").exists())

    def test_the_rest_of_home_stays_writable(self) -> None:
        setup = self.bare_setup("skill")
        self.ok(setup, 'echo hi > "$HOME/note.txt"')
        self.assertEqual((setup.sandbox.home / "note.txt").read_text(), "hi\n")

    def test_the_baseline_has_no_such_folder(self) -> None:
        setup = self.bare_setup("baseline")
        self.assertEqual(
            self.ok(setup, f'if [ -e "$HOME/{FFMPEG_AT}" ]; then echo there; else echo none; fi'), "none\n"
        )
        self.assertEqual(
            self.ok(setup, 'if [ -e "$HOME/.local/share/jzr" ]; then echo there; else echo none; fi'), "none\n"
        )

    def test_an_arm_on_a_bare_machine_without_a_copy_has_no_folder(self) -> None:
        setup = self.bare_setup("skill", ffmpeg=False)
        self.assertEqual(
            self.ok(setup, f'if [ -e "$HOME/{FFMPEG_AT}" ]; then echo there; else echo none; fi'), "none\n"
        )


@needs_overlay
class CheckBareRealTest(RealBareCase):
    """runner.check_bare against the real bubblewrap: [] when the machine holds, else what is wrong."""

    def setUp(self) -> None:
        super().setUp()
        self.check_parent = Path(tempfile.mkdtemp(prefix="c", dir=self.parent))

    def check(self, machine: arms.BareMachine) -> list[str]:
        assert tpt.BWRAP is not None
        problems = runner_module.check_bare(tpt.BWRAP, machine, self.check_parent)
        self.assertIsInstance(problems, list)
        self.assertTrue(all(isinstance(p, str) for p in problems), problems)
        return problems

    def hidden_here(self) -> str | None:
        return next((name for name in arms.BARE_HIDDEN_PROGRAMS if on_the_full_machine(name)), None)

    def pil_here(self) -> bool:
        python = shutil.which("python3", path=SYSTEM_PATH)
        done = subprocess.run([python or "python3", "-c", "import PIL"], env={"PATH": SYSTEM_PATH}, capture_output=True)
        return done.returncode == 0

    def test_the_machine_the_runner_builds_holds(self) -> None:
        self.assertEqual(self.check(self.machine), [])

    def test_the_throwaway_sandbox_is_gone_afterwards_overlay_folders_included(self) -> None:
        self.check(self.machine)
        self.assertEqual(list(self.check_parent.iterdir()), [], "the check left its sandbox behind")

    def test_a_machine_that_hides_nothing_is_reported_by_program_and_by_module(self) -> None:
        hidden = self.hidden_here()
        if hidden is None and not self.pil_here():
            self.skipTest("this machine has no hidden program and no PIL, so nothing is visible to report")
        problems = " ".join(self.check(arms.BareMachine(bin_dirs=(), site_dirs=())))
        if hidden is not None:
            self.assertIn(hidden, problems)
        if self.pil_here():
            self.assertIn("PIL", problems)
        self.assertEqual(list(self.check_parent.iterdir()), [])

    def test_programs_hidden_but_the_site_packages_not_is_reported_as_the_module(self) -> None:
        if not self.pil_here():
            self.skipTest("the system python3 has no PIL")
        problems = self.check(dataclasses.replace(self.machine, site_dirs=()))
        self.assertTrue(problems)
        self.assertIn("PIL", " ".join(problems))

    def test_site_packages_emptied_but_the_programs_not_hidden_is_reported_as_the_program(self) -> None:
        hidden = self.hidden_here()
        if hidden is None:
            self.skipTest("this machine has none of the hidden programs")
        problems = self.check(dataclasses.replace(self.machine, bin_dirs=()))
        self.assertTrue(problems)
        self.assertIn(hidden, " ".join(problems))


# -- run_all with the real bubblewrap -------------------------------------------------------------


@tr.needs_the_real_source
@needs_overlay
class BareRunAllRealBwrapTest(tpt.RealBwrapCase, tr.RunAllCase):
    """run_all as `--bare-arms` starts it, with the real bubblewrap, the real check and a fake claude."""

    def new_parent(self) -> Path:
        assert tpt.OUTSIDE_TMP is not None
        parent = Path(tempfile.mkdtemp(prefix="p", dir=tpt.OUTSIDE_TMP))
        self.addCleanup(force_remove, parent)
        return parent

    def probing_claude(self, command: str | None, module: str | None) -> str:
        """Appends one line to seen.txt: HOME, what `command -v` says about `command`, whether `module` imports."""
        skill = self.canned(fx.init_for_arm("skill"), fx.result_event(text="ok"))
        baseline = self.tmp / "canned-baseline.jsonl"
        baseline.write_text("\n".join(fx.to_lines(fx.init_for_arm("baseline"), fx.result_event(text="ok"))) + "\n")
        look = f"$(command -v {command} || echo none)" if command else "n/a"
        mod = f"$(python3 -c 'import {module}' 2>/dev/null && echo imports || echo missing)" if module else "n/a"
        return self.fake_claude(
            'if [ "$1" != "-p" ]; then echo "9.9.9 (fake claude)"; exit 0; fi\n'
            f'echo "$HOME|{look}|{mod}|$(test -f "$HOME/{FFMPEG_AT}/bin/ffmpeg" && echo ffmpeg-folder || echo no-folder)"'
            f' >> "{self.rec}/seen.txt"\n'
            f'if [ -e "$HOME/../bin/jzr" ]; then /usr/bin/cat "{skill}"; else /usr/bin/cat "{baseline}"; fi\nexit 0'
        )

    def seen(self) -> dict[str, list[str]]:
        """The probe lines by sandbox HOME."""
        lines = [line.split("|") for line in (self.rec / "seen.txt").read_text().splitlines()]
        return {home: rest for home, *rest in lines}

    def run_bare(self, claude: str, *argv: str, parent: Path | None = None) -> tuple[int, str, Path, Path]:
        parent = parent or self.new_parent()
        code, output, out = self.run_all_with("--sandbox-dir", str(parent), *argv, claude=claude, sandbox_dir=False)
        return code, output, out, parent

    def test_the_bare_run_cannot_see_what_the_full_run_in_the_same_invocation_sees(self) -> None:
        program = next((name for name in HIDEABLE if on_the_full_machine(name)), None)
        module = third_party_module()
        if program is None and module is None:
            self.skipTest("this machine has no ffmpeg, magick, gs or third-party Python module to hide")
        claude = self.probing_claude(program, module)
        code, output, out, parent = self.run_bare(
            claude, "--arms", "baseline", "--bare-arms", "baseline", "--keep-sandboxes"
        )
        self.assertEqual(code, 0, output)
        records = {r["run_id"]: r for r in self.records(out)}
        self.assertEqual(set(records), {"r01-hello-baseline", "r01-hello-baseline-bare"})
        seen = self.seen()
        full, bare = (seen[f"{records[i]['sandbox']}/home"] for i in ("r01-hello-baseline", "r01-hello-baseline-bare"))
        self.assertEqual(
            (records["r01-hello-baseline"]["machine"], records["r01-hello-baseline-bare"]["machine"]), ("full", "bare")
        )
        if program:
            self.assertEqual(full[0], on_the_full_machine(program))
            self.assertEqual(bare[0], "none")
        if module:
            self.assertEqual((full[1], bare[1]), ("imports", "missing"))
        self.assertEqual((full[2], bare[2]), ("no-folder", "no-folder"), "the baseline gets no jzr ffmpeg")
        for record in records.values():
            self.assertIs(record["passed"], True, record)
            self.assertIs(record["isolation_ok"], True, record)

    def test_the_sandboxes_of_bare_runs_can_be_removed_afterwards(self) -> None:
        claude = self.probing_claude(None, None)
        code, output, out, parent = self.run_bare(claude, "--arms", "", "--bare-arms", "baseline", "--runs", "2")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.records(out)), 2)
        # overlayfs leaves a work folder of mode 000 in each; the runner must still get rid of the sandbox
        self.assertEqual(list(parent.iterdir()), [], "sandbox folders left behind")

    def test_the_jzr_arm_gets_the_ffmpeg_folder_and_the_baseline_does_not(self) -> None:
        shared = self.tmp / "jzr-data" / "ffmpeg"
        (shared / "bin").mkdir(parents=True)
        (shared / "bin" / "ffmpeg").write_text("stand-in\n")
        calls: list[Any] = []

        def fake_ensure(jzr_bin: Any, cache: Any = None) -> Path:
            calls.append((jzr_bin, cache))
            return shared

        claude = self.probing_claude(None, None)
        with mock.patch.object(runner_module, "ensure_jzr_ffmpeg", side_effect=fake_ensure):
            code, output, out, parent = self.run_bare(
                claude, "--arms", "", "--bare-arms", "baseline,skill", "--keep-sandboxes"
            )
        self.assertEqual(code, 0, output)
        self.assertEqual(len(calls), 1)
        records = {r["arm"]: r for r in self.records(out)}
        seen = self.seen()
        self.assertEqual(seen[f"{records['skill']['sandbox']}/home"][2], "ffmpeg-folder")
        self.assertEqual(seen[f"{records['baseline']['sandbox']}/home"][2], "no-folder")
        self.assertIs(records["skill"]["isolation_ok"], True)
        self.assertIs(records["baseline"]["isolation_ok"], True)


if __name__ == "__main__":
    unittest.main()
