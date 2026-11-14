"""Acceptance tests for the Windows behaviour of ``jzr_tools.core.cleanup``.

The platform, the shell API, elevation, ``shutil.which`` and the subprocess
runner are faked, so these tests run on Windows and on Linux. Directories are
real throwaway trees; ``LOCALAPPDATA``, ``TEMP`` and ``SystemRoot`` point into
them.

Seams in ``jzr_tools.core.cleanup`` (all looked up at call time, so the tests
can patch them):

* ``_windows() -> bool``           whether to behave as on Windows. Everything that
                                   differs per system (the item list, ``Item.action``
                                   quoting, full command paths, the root tier, files in
                                   use) must ask this function.
* ``_is_elevated() -> bool``       the process is an elevated Windows administrator.
* ``_sudo_enabled() -> bool``      Windows sudo is enabled in the registry.
* ``_recycle_bin_size() -> int``   bytes in the Recycle Bin of every drive
                                   (SHQueryRecycleBinW).
* ``_empty_recycle_bin() -> None`` empties it silently (SHEmptyRecycleBinW); raises
                                   ``OSError`` when the shell API fails.
* ``Outcome.in_use: int = 0``      how many files were left because a program held
                                   them open.

Also used: ``_capture``, ``_execute``, ``_is_root``,
``shutil.which``, ``scan(home=, environ=)``, ``clean(item, ask_password=)``.
On Windows (``_windows()`` true) ``shutil.which`` is the only source of command
paths; on Linux the bare tool names stay as they are.

The recycle-bin item is the item with id ``trash``; the tests never look at how
it stores "empty the bin" (paths and command may both be empty).
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jzr_tools.core import cleanup

MINIMUM = 200_000
MINIMUM_OTHER = 400_000
SMALL = 20_000
MEDIUM = 300_000
LARGE = 600_000
SLACK = 60_000

SUDO = r"C:\Windows\System32\sudo.exe"
NPM = r"C:\Program Files\nodejs\npm.cmd"
UV = r"C:\Users\someone\.local\bin\uv.exe"
DOCKER = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"

LINUX_ONLY = {"thumbnails", "mesa-shaders", "journal", "dnf", "apt"}
WINDOWS_ONLY = {"gpu-shaders", "temp", "crash-dumps", "windows-temp", "windows-update"}

IN_USE_MESSAGE = "The process cannot access the file because it is being used by another process"


def write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    return path


def program(argv0: str) -> str:
    """The lower-case name of a program given by either kind of path, no extension."""
    name = re.split(r"[\\/]", argv0)[-1].lower()
    return name[:-4] if name.endswith((".exe", ".cmd", ".bat")) else name


def windows_error(path: object, winerror: int, message: str = IN_USE_MESSAGE) -> OSError:
    """An OSError as Windows raises it. ``winerror`` is set by hand so it also exists on Linux."""
    error = PermissionError(13, message, os.fspath(path))  # type: ignore[arg-type]
    error.winerror = winerror  # type: ignore[attr-defined]
    return error


class WindowsCase(unittest.TestCase):
    """A throwaway Windows-shaped profile with the platform and every seam faked."""

    windows = True

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "Users" / "someone"
        self.home.mkdir(parents=True)
        self.local = self.root / "Users" / "someone" / "AppData" / "Local"
        self.temp = self.root / "scratch" / "Temp"
        self.system_root = self.root / "Windows"
        self.env = {
            "LOCALAPPDATA": str(self.local),
            "TEMP": str(self.temp),
            "SystemRoot": str(self.system_root),
        }
        self.platform_is_windows = self.windows
        self.installed: dict[str, str] = {}  # tool name -> the full path which() returns
        self.docker_output: str | None = None
        self.elevated = False
        self.sudo_enabled = False
        self.bin_size = 0
        self.bin_emptied = 0
        self.bin_error: OSError | None = None
        self.bin_left = 0  # what stays in the bin after emptying

        def which(name: str, *args: object, **kwargs: object) -> str | None:
            return self.installed.get(name)

        def capture(command: object) -> str | None:
            words = list(command)  # type: ignore[call-overload]
            if any(program(word) == "docker" for word in words[:1]):
                return self.docker_output
            return None

        def empty_bin() -> None:
            if self.bin_error is not None:
                raise self.bin_error
            self.bin_emptied += 1
            self.bin_size = self.bin_left

        for patcher in (
            mock.patch.object(cleanup, "MINIMUM_BYTES", MINIMUM),
            mock.patch.object(cleanup, "MINIMUM_OTHER_BYTES", MINIMUM_OTHER),
            mock.patch.object(cleanup.shutil, "which", side_effect=which),
            mock.patch.object(cleanup, "_capture", side_effect=capture),
            mock.patch.object(cleanup, "_is_root", return_value=False),
            mock.patch.object(cleanup, "_windows", side_effect=lambda: self.platform_is_windows, create=True),
            mock.patch.object(cleanup, "_is_elevated", side_effect=lambda: self.elevated, create=True),
            mock.patch.object(cleanup, "_sudo_enabled", side_effect=lambda: self.sudo_enabled, create=True),
            mock.patch.object(cleanup, "_recycle_bin_size", side_effect=lambda: self.bin_size, create=True),
            mock.patch.object(cleanup, "_empty_recycle_bin", side_effect=empty_bin, create=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def scan(self, environ: dict[str, str] | None = None) -> dict[str, cleanup.Item]:
        items = cleanup.scan(home=self.home, environ=self.env if environ is None else environ)
        found = {item.id: item for item in items}
        self.assertEqual(len(found), len(items), "an id was listed twice")
        return found

    def fill(self, base: Path, *parts: str, size: int = LARGE) -> Path:
        directory = base.joinpath(*parts)
        write(directory / "data.bin", size)
        return directory

    def under_local(self, *parts: str, size: int = LARGE) -> Path:
        return self.fill(self.local, *parts, size=size)


class WindowsItemListTests(WindowsCase):
    def test_every_item_has_its_id_tier_and_directories(self) -> None:
        cargo_home = self.home / ".cargo" / "registry"
        expected = {
            "uv": ("safe", {self.under_local("uv", "cache")}),
            "pip": ("safe", {self.under_local("pip", "Cache")}),
            "npm": ("safe", {self.under_local("npm-cache", "_cacache")}),
            "yarn": ("safe", {self.under_local("Yarn", "Cache")}),
            "pnpm": ("safe", {self.under_local("pnpm-cache")}),
            "go-build": ("safe", {self.under_local("go-build")}),
            "node-gyp": ("safe", {self.under_local("node-gyp", "Cache")}),
            "cargo": (
                "safe",
                {self.fill(cargo_home, "cache"), self.fill(cargo_home, "src")},
            ),
            "gpu-shaders": ("safe", {self.under_local("NVIDIA", "DXCache")}),
            "temp": ("review", {self.fill(self.temp.parent, "Temp"), self.under_local("SquirrelTemp")}),
            "crash-dumps": ("review", {self.under_local("CrashDumps")}),
            "windows-temp": ("root", {self.fill(self.system_root, "Temp")}),
            "windows-update": (
                "root",
                {self.fill(self.system_root, "SoftwareDistribution", "Download")},
            ),
        }
        self.elevated = True  # the root tier is offered
        found = self.scan()
        for item_id, (tier, paths) in expected.items():
            with self.subTest(item_id=item_id):
                self.assertIn(item_id, found)
                self.assertEqual(found[item_id].tier, tier)
                self.assertEqual(set(found[item_id].paths), paths)
                self.assertEqual(found[item_id].command, ())
                self.assertTrue(found[item_id].title.strip() and found[item_id].note.strip())

    def test_directories_are_measured(self) -> None:
        self.under_local("pip", "Cache", size=MEDIUM)
        item = self.scan()["pip"]
        self.assertGreaterEqual(item.size_bytes, MEDIUM)
        self.assertLess(item.size_bytes, MEDIUM + SLACK)

    def test_items_below_the_minimum_are_not_listed(self) -> None:
        self.under_local("pip", "Cache", size=SMALL)
        self.under_local("CrashDumps", size=SMALL)
        self.assertEqual(self.scan(), {})

    def test_localappdata_falls_back_to_appdata_local_below_home(self) -> None:
        directory = self.fill(self.home / "AppData" / "Local", "uv", "cache", size=MEDIUM)
        found = self.scan({"TEMP": str(self.temp)})  # no LOCALAPPDATA
        self.assertEqual(found["uv"].paths, (directory,))

    def test_relative_localappdata_is_ignored(self) -> None:
        directory = self.fill(self.home / "AppData" / "Local", "pip", "Cache", size=MEDIUM)
        found = self.scan({"LOCALAPPDATA": "relative/Local"})
        self.assertEqual(found["pip"].paths, (directory,))

    def test_cargo_lives_below_dot_cargo_in_the_home_directory(self) -> None:
        registry = self.home / ".cargo" / "registry"
        cache = self.fill(registry, "cache", size=MEDIUM)
        src = self.fill(registry, "src", size=MEDIUM)
        self.assertEqual(set(self.scan()["cargo"].paths), {cache, src})

    def test_other_cache_directories_still_become_review_items(self) -> None:
        directory = self.fill(self.home / ".cache", "Mozilla", size=LARGE)
        item = self.scan()["cache:Mozilla"]
        self.assertEqual(item.tier, "review")
        self.assertEqual(item.paths, (directory,))

    def test_other_cache_directories_use_the_larger_minimum(self) -> None:
        self.fill(self.home / ".cache", "Medium", size=MEDIUM)
        self.assertNotIn("cache:Medium", self.scan())


class GpuShaderTests(WindowsCase):
    NAMES = (
        ("NVIDIA", "DXCache"),
        ("NVIDIA", "GLCache"),
        ("D3DSCache",),
        ("AMD", "DxCache"),
        ("AMD", "DxcCache"),
        ("AMD", "VkCache"),
        ("AMD", "GLCache"),
        ("Intel", "ShaderCache"),
    )

    def test_lists_all_eight_directories_that_exist(self) -> None:
        paths = {self.under_local(*parts, size=100_000) for parts in self.NAMES}
        item = self.scan()["gpu-shaders"]
        self.assertEqual(item.tier, "safe")
        self.assertEqual(set(item.paths), paths)
        self.assertGreaterEqual(item.size_bytes, 8 * 100_000)  # the sizes add up across directories

    def test_lists_only_the_directories_that_exist(self) -> None:
        wanted = {self.under_local("NVIDIA", "GLCache"), self.under_local("Intel", "ShaderCache")}
        item = self.scan()["gpu-shaders"]
        self.assertEqual(set(item.paths), wanted)

    def test_no_shader_directories_means_no_item(self) -> None:
        self.under_local("pip", "Cache")
        self.assertNotIn("gpu-shaders", self.scan())

    def test_combined_size_can_cross_the_minimum_when_no_single_directory_does(self) -> None:
        self.under_local("NVIDIA", "DXCache", size=150_000)
        self.under_local("AMD", "VkCache", size=150_000)
        self.assertIn("gpu-shaders", self.scan())


class TempAndDumpTests(WindowsCase):
    def test_temp_is_review_and_covers_temp_and_squirreltemp(self) -> None:
        temp = self.fill(self.temp.parent, "Temp")
        squirrel = self.under_local("SquirrelTemp")
        item = self.scan()["temp"]
        self.assertEqual(item.tier, "review")
        self.assertEqual(set(item.paths), {temp, squirrel})

    def test_temp_follows_the_temp_variable(self) -> None:
        self.under_local("Temp", size=LARGE)  # the decoy fallback
        elsewhere = self.fill(self.root, "elsewhere")
        found = self.scan({**self.env, "TEMP": str(elsewhere)})
        self.assertEqual(found["temp"].paths, (elsewhere,))

    def test_temp_falls_back_to_localappdata_temp(self) -> None:
        fallback = self.under_local("Temp")
        found = self.scan({"LOCALAPPDATA": str(self.local), "SystemRoot": str(self.system_root)})
        self.assertEqual(found["temp"].paths, (fallback,))

    def test_temp_lists_only_what_exists(self) -> None:
        squirrel = self.under_local("SquirrelTemp")
        self.assertEqual(self.scan()["temp"].paths, (squirrel,))

    def test_crash_dumps_are_review(self) -> None:
        dumps = self.under_local("CrashDumps")
        item = self.scan()["crash-dumps"]
        self.assertEqual(item.tier, "review")
        self.assertEqual(item.paths, (dumps,))


class EnvironmentOverrideTests(WindowsCase):
    """The overrides Linux honours still win, plus GOCACHE and YARN_CACHE_FOLDER."""

    def check(self, variable: str, item_id: str, default: tuple[str, ...], expected: tuple[str, ...] = ()) -> None:
        self.under_local(*default)  # the decoy default location
        custom = self.root / f"custom-{variable}"
        real = self.fill(custom, *expected, size=MEDIUM)
        item = self.scan({**self.env, variable: str(custom)})[item_id]
        self.assertEqual(item.paths, (real,))

    def test_uv_cache_dir(self) -> None:
        self.check("UV_CACHE_DIR", "uv", ("uv", "cache"))

    def test_pip_cache_dir(self) -> None:
        self.check("PIP_CACHE_DIR", "pip", ("pip", "Cache"))

    def test_npm_config_cache_names_the_directory_that_holds_cacache(self) -> None:
        self.check("npm_config_cache", "npm", ("npm-cache", "_cacache"), ("_cacache",))

    def test_gocache(self) -> None:
        self.check("GOCACHE", "go-build", ("go-build",))

    def test_yarn_cache_folder(self) -> None:
        self.check("YARN_CACHE_FOLDER", "yarn", ("Yarn", "Cache"))

    def test_cargo_home(self) -> None:
        self.fill(self.home / ".cargo", "registry", "cache")
        custom = self.root / "cargo-elsewhere"
        cache = self.fill(custom, "registry", "cache", size=MEDIUM)
        src = self.fill(custom, "registry", "src", size=MEDIUM)
        item = self.scan({**self.env, "CARGO_HOME": str(custom)})["cargo"]
        self.assertEqual(set(item.paths), {cache, src})

    def test_relative_overrides_are_ignored(self) -> None:
        go = self.under_local("go-build", size=MEDIUM)
        yarn = self.under_local("Yarn", "Cache", size=MEDIUM)
        found = self.scan({**self.env, "GOCACHE": "relative/go", "YARN_CACHE_FOLDER": "relative/yarn"})
        self.assertEqual(found["go-build"].paths, (go,))
        self.assertEqual(found["yarn"].paths, (yarn,))

    def test_an_override_pointing_at_a_missing_directory_hides_the_item(self) -> None:
        self.under_local("go-build")
        found = self.scan({**self.env, "GOCACHE": str(self.root / "missing")})
        self.assertNotIn("go-build", found)


class LinuxGocacheTests(WindowsCase):
    """GOCACHE and YARN_CACHE_FOLDER are honoured on both systems."""

    windows = False

    def test_gocache_replaces_the_cache_directory_on_linux(self) -> None:
        self.fill(self.home / ".cache", "go-build")
        custom = self.fill(self.root, "go-elsewhere", size=MEDIUM)
        found = self.scan({"GOCACHE": str(custom)})
        self.assertEqual(found["go-build"].paths, (custom,))

    def test_yarn_cache_folder_replaces_the_cache_directory_on_linux(self) -> None:
        self.fill(self.home / ".cache", "yarn")
        custom = self.fill(self.root, "yarn-elsewhere", size=MEDIUM)
        found = self.scan({"YARN_CACHE_FOLDER": str(custom)})
        self.assertEqual(found["yarn"].paths, (custom,))

    def test_defaults_are_unchanged_without_the_variables(self) -> None:
        go = self.fill(self.home / ".cache", "go-build", size=MEDIUM)
        self.assertEqual(self.scan({})["go-build"].paths, (go,))


class PlatformSeparationTests(WindowsCase):
    def make_linux_layout(self) -> None:
        cache = self.home / ".cache"
        self.fill(cache, "thumbnails")
        self.fill(cache, "mesa_shader_cache")
        self.fill(self.home / ".local" / "share" / "Trash", "files")
        self.installed.update({"dnf": "/usr/bin/dnf", "apt-get": "/usr/bin/apt-get", "journalctl": "/usr/bin/journalctl"})
        self.dnf = self.root / "var" / "cache" / "dnf"
        write(self.dnf / "x.bin", LARGE)

    def make_windows_layout(self) -> None:
        self.under_local("NVIDIA", "DXCache")
        self.under_local("CrashDumps")
        self.under_local("SquirrelTemp")
        self.fill(self.temp.parent, "Temp")
        self.fill(self.system_root, "Temp")
        self.fill(self.system_root, "SoftwareDistribution", "Download")

    def test_linux_only_ids_never_appear_on_windows(self) -> None:
        self.make_linux_layout()
        self.elevated = True
        self.sudo_enabled = True
        self.installed["sudo"] = SUDO
        with (
            mock.patch.object(cleanup, "DNF_CACHE_PATHS", (self.dnf,), create=True),
            mock.patch.object(cleanup, "_capture", return_value="Archived and active journals take up 4.0G in the file system.\n"),
        ):
            found = self.scan()
        self.assertEqual(LINUX_ONLY & set(found), set())
        self.assertNotIn("trash", found, "the Linux trash is not the Recycle Bin")

    def test_windows_only_ids_never_appear_on_linux(self) -> None:
        self.platform_is_windows = False
        self.make_windows_layout()
        self.bin_size = LARGE
        self.elevated = True
        self.installed["sudo"] = SUDO
        found = self.scan()
        self.assertEqual(WINDOWS_ONLY & set(found), set())
        self.assertNotIn("trash", found, "the Recycle Bin is Windows only")

    def test_known_ids_hold_the_ids_of_both_systems(self) -> None:
        for item_id in LINUX_ONLY | WINDOWS_ONLY | {"uv", "pip", "npm", "cargo", "trash", "docker-images"}:
            with self.subTest(item_id=item_id):
                self.assertIn(item_id, cleanup.KNOWN_IDS)
                self.assertTrue(cleanup.is_known_id(item_id))
        self.assertEqual(len(cleanup.KNOWN_IDS), len(set(cleanup.KNOWN_IDS)))

    def test_known_ids_do_not_depend_on_the_platform(self) -> None:
        # --only must accept the same ids everywhere.
        for windows in (True, False):
            self.platform_is_windows = windows
            for item_id in WINDOWS_ONLY | LINUX_ONLY:
                with self.subTest(windows=windows, item_id=item_id):
                    self.assertTrue(cleanup.is_known_id(item_id))


class FullCommandPathTests(WindowsCase):
    def setUp(self) -> None:
        super().setUp()
        self.executed: list[list[str]] = []
        self.result = (0, "")

        def execute(command: object) -> tuple[int, str]:
            self.executed.append(list(command))  # type: ignore[call-overload]
            return self.result

        patcher = mock.patch.object(cleanup, "_execute", side_effect=execute)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_npm_is_started_by_the_path_which_found_so_npm_cmd_runs(self) -> None:
        self.installed["npm"] = NPM
        self.under_local("npm-cache", "_cacache", size=MEDIUM)
        item = self.scan()["npm"]
        outcome = cleanup.clean(item)
        self.assertEqual(self.executed, [[NPM, "cache", "clean", "--force"]])
        self.assertTrue(outcome.ok, outcome.error)

    def test_uv_is_started_by_its_full_path(self) -> None:
        self.installed["uv"] = UV
        self.under_local("uv", "cache", size=MEDIUM)
        cleanup.clean(self.scan()["uv"])
        self.assertEqual(self.executed, [[UV, "cache", "clean"]])

    def test_without_the_tool_the_directory_is_emptied_instead(self) -> None:
        directory = self.under_local("uv", "cache", size=MEDIUM)
        item = self.scan()["uv"]
        self.assertEqual(item.command, ())
        self.assertEqual(item.paths, (directory,))

    def test_any_command_item_is_started_by_the_full_path_of_its_program(self) -> None:
        self.installed["docker"] = DOCKER
        self.docker_output = ""
        item = cleanup.Item(
            "docker-images", "Docker images", "review", 5_000_000, "n",
            command=("docker", "image", "prune", "--all", "--force"),
        )
        cleanup.clean(item)
        self.assertEqual(self.executed, [[DOCKER, "image", "prune", "--all", "--force"]])

    def test_a_program_which_cannot_find_is_left_as_given(self) -> None:
        item = cleanup.Item("docker-images", "Docker images", "review", 5_000_000, "n", command=("docker", "image", "prune"))
        cleanup.clean(item)
        self.assertEqual(self.executed, [["docker", "image", "prune"]])

    def test_on_linux_bare_names_are_kept(self) -> None:
        self.platform_is_windows = False
        self.installed["docker"] = "/usr/bin/docker"
        self.docker_output = ""
        item = cleanup.Item("docker-images", "Docker images", "review", 5_000_000, "n", command=("docker", "image", "prune"))
        cleanup.clean(item)
        self.assertEqual(self.executed, [["docker", "image", "prune"]])


class ActionQuotingTests(WindowsCase):
    def test_windows_quotes_the_command_line_the_windows_way(self) -> None:
        item = cleanup.Item("npm", "npm", "safe", 5, "n", command=(NPM, "cache", "clean", "--force"))
        self.assertEqual(item.action, f'"{NPM}" cache clean --force')

    def test_windows_quotes_embedded_quotes_and_empty_words(self) -> None:
        item = cleanup.Item("x", "x", "safe", 5, "n", command=("tool", 'say "hi"', ""))
        self.assertEqual(item.action, 'tool "say \\"hi\\"" ""')

    def test_windows_leaves_plain_words_unquoted(self) -> None:
        item = cleanup.Item("docker-images", "d", "review", 5, "n", command=("docker", "image", "prune", "--all"))
        self.assertEqual(item.action, "docker image prune --all")

    def test_linux_keeps_joining_with_spaces(self) -> None:
        self.platform_is_windows = False
        item = cleanup.Item("npm", "npm", "safe", 5, "n", command=(NPM, "cache", "clean"))
        self.assertEqual(item.action, f"{NPM} cache clean")

    def test_directory_items_still_list_their_paths(self) -> None:
        first, second = Path("a") / "b c", Path("a") / "d"
        item = cleanup.Item("temp", "Temp", "review", 5, "n", paths=(first, second))
        self.assertEqual(item.action, f"{first}, {second}")


class RecycleBinTests(WindowsCase):
    def test_the_recycle_bin_is_a_review_item_titled_recycle_bin(self) -> None:
        self.bin_size = 3 * MEDIUM
        item = self.scan()["trash"]
        self.assertEqual(item.title, "Recycle Bin")
        self.assertEqual(item.tier, "review")
        self.assertEqual(item.size_bytes, 3 * MEDIUM)
        self.assertTrue(item.note.strip())

    def test_an_empty_bin_is_not_listed(self) -> None:
        self.bin_size = 0
        self.assertNotIn("trash", self.scan())

    def test_a_bin_below_the_minimum_is_not_listed(self) -> None:
        self.bin_size = MINIMUM - 1
        self.assertNotIn("trash", self.scan())

    def test_a_bin_at_the_minimum_is_listed(self) -> None:
        self.bin_size = MINIMUM
        self.assertIn("trash", self.scan())

    def test_scanning_never_empties_the_bin(self) -> None:
        self.bin_size = LARGE
        self.scan()
        self.assertEqual(self.bin_emptied, 0)

    def test_cleaning_empties_the_bin_through_the_shell_api_and_reports_what_it_freed(self) -> None:
        self.bin_size = 3 * MEDIUM
        item = self.scan()["trash"]
        with mock.patch.object(cleanup, "_execute") as execute:
            outcome = cleanup.clean(item)
        self.assertEqual(self.bin_emptied, 1)
        execute.assert_not_called()
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.item, item)
        self.assertEqual(outcome.freed_bytes, 3 * MEDIUM)

    def test_freed_bytes_are_measured_again_after_emptying(self) -> None:
        self.bin_size = 3 * MEDIUM
        self.bin_left = MEDIUM  # something was added meanwhile
        outcome = cleanup.clean(self.scan()["trash"])
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.freed_bytes, 2 * MEDIUM)

    def test_a_failing_shell_call_fails_the_item_with_its_message(self) -> None:
        self.bin_size = 3 * MEDIUM
        item = self.scan()["trash"]
        self.bin_error = OSError("The Recycle Bin could not be emptied (0x8000FFFF)")
        outcome = cleanup.clean(item)
        self.assertFalse(outcome.ok)
        self.assertIn("could not be emptied", outcome.error)
        self.assertEqual(outcome.freed_bytes, 0)

    def test_the_bin_is_asked_nothing_on_linux(self) -> None:
        self.platform_is_windows = False
        self.bin_size = LARGE
        with mock.patch.object(cleanup, "_recycle_bin_size") as size:
            self.scan()
        size.assert_not_called()


class RootTierOfferTests(WindowsCase):
    def setUp(self) -> None:
        super().setUp()
        self.fill(self.system_root, "Temp")
        self.fill(self.system_root, "SoftwareDistribution", "Download")

    def root_ids(self) -> set[str]:
        return {i.id for i in self.scan().values() if i.tier == "root"}

    def test_elevated_offers_both_items(self) -> None:
        self.elevated = True
        self.assertEqual(self.root_ids(), {"windows-temp", "windows-update"})

    def test_elevated_needs_no_sudo(self) -> None:
        self.elevated = True
        self.sudo_enabled = False
        self.assertEqual(self.root_ids(), {"windows-temp", "windows-update"})

    def test_sudo_enabled_and_on_path_offers_both_items(self) -> None:
        self.sudo_enabled = True
        self.installed["sudo"] = SUDO
        self.assertEqual(self.root_ids(), {"windows-temp", "windows-update"})

    def test_sudo_enabled_but_not_on_path_offers_nothing(self) -> None:
        self.sudo_enabled = True
        self.assertEqual(self.root_ids(), set())

    def test_sudo_on_path_but_not_enabled_offers_nothing(self) -> None:
        self.sudo_enabled = False
        self.installed["sudo"] = SUDO
        self.assertEqual(self.root_ids(), set())

    def test_neither_elevated_nor_sudo_offers_nothing(self) -> None:
        self.assertEqual(self.root_ids(), set())

    def test_the_root_items_name_the_system_directories(self) -> None:
        self.elevated = True
        found = self.scan()
        self.assertEqual(found["windows-temp"].paths, (self.system_root / "Temp",))
        self.assertEqual(
            found["windows-update"].paths, (self.system_root / "SoftwareDistribution" / "Download",)
        )

    def test_the_system_root_comes_from_systemroot(self) -> None:
        self.elevated = True
        elsewhere = self.root / "OtherWindows"
        write(elsewhere / "Temp" / "x.bin", LARGE)
        found = self.scan({**self.env, "SystemRoot": str(elsewhere)})
        self.assertEqual(found["windows-temp"].paths, (elsewhere / "Temp",))
        self.assertNotIn("windows-update", found)

    def test_missing_or_small_directories_are_not_offered(self) -> None:
        self.elevated = True
        (self.system_root / "Temp" / "data.bin").unlink()  # the large file from setUp
        write(self.system_root / "Temp" / "tiny.bin", 1)
        shutil.rmtree(self.system_root / "SoftwareDistribution")
        write(self.system_root / "Temp" / "x.bin", SMALL)
        self.assertEqual(self.root_ids(), set())


class RootTierCleanTests(WindowsCase):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.fill(self.system_root, "Temp", size=MEDIUM)
        self.executed: list[list[str]] = []
        self.child = None  # what the pretend elevated child does
        self.result = (0, "")

        def execute(command: object) -> tuple[int, str]:
            self.executed.append(list(command))  # type: ignore[call-overload]
            if self.child is not None:
                self.child()
            return self.result

        patcher = mock.patch.object(cleanup, "_execute", side_effect=execute)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.installed["sudo"] = SUDO
        self.sudo_enabled = True
        self.elevated = True
        self.item = self.scan()["windows-temp"]  # offered while elevated
        self.elevated = False

    def test_elevated_empties_the_directory_directly(self) -> None:
        self.elevated = True
        outcome = cleanup.clean(self.item)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(self.executed, [])
        self.assertTrue(self.target.is_dir())
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertGreaterEqual(outcome.freed_bytes, MEDIUM)

    def test_elevated_empties_directly_even_without_asking_for_a_password(self) -> None:
        self.elevated = True
        outcome = cleanup.clean(self.item, ask_password=False)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(self.executed, [])
        self.assertEqual(list(self.target.iterdir()), [])

    def test_not_elevated_runs_jzr_through_sudo_for_this_item(self) -> None:
        outcome = cleanup.clean(self.item)
        self.assertEqual(len(self.executed), 1)
        argv = self.executed[0]
        self.assertEqual(argv[0], SUDO, "sudo is started by its full path from which")
        self.assertIn(self.item.id, argv)
        self.assertTrue(outcome.ok, outcome.error)

    def test_not_elevated_does_not_empty_the_directory_itself(self) -> None:
        cleanup.clean(self.item)
        self.assertTrue((self.target / "data.bin").exists(), "only the elevated child may touch it")

    def test_the_directory_is_measured_again_to_report_what_was_freed(self) -> None:
        self.child = lambda: (self.target / "data.bin").unlink()
        outcome = cleanup.clean(self.item)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertGreaterEqual(outcome.freed_bytes, MEDIUM)
        self.assertLess(outcome.freed_bytes, MEDIUM + SLACK)

    def test_nothing_freed_is_reported_as_zero_not_a_negative_figure(self) -> None:
        outcome = cleanup.clean(self.item)  # the child did nothing
        self.assertEqual(outcome.freed_bytes, 0)

    def test_a_failing_sudo_fails_the_item_with_its_message(self) -> None:
        self.result = (1, "sudo: the operation was cancelled by the user")
        outcome = cleanup.clean(self.item)
        self.assertFalse(outcome.ok)
        self.assertIn("cancelled", outcome.error)
        self.assertEqual(outcome.freed_bytes, 0)

    def test_without_a_password_prompt_the_item_is_not_attempted(self) -> None:
        outcome = cleanup.clean(self.item, ask_password=False)
        self.assertEqual(self.executed, [])
        self.assertFalse(outcome.ok)
        self.assertIn("elevated terminal", outcome.error.lower())
        self.assertEqual(outcome.freed_bytes, 0)
        self.assertTrue((self.target / "data.bin").exists())

    def test_safe_and_review_items_never_go_through_sudo(self) -> None:
        directory = self.under_local("CrashDumps", size=MEDIUM)
        item = cleanup.Item("crash-dumps", "Crash dumps", "review", MEDIUM, "n", paths=(directory,))
        for ask in (True, False):
            with self.subTest(ask_password=ask):
                outcome = cleanup.clean(item, ask_password=ask)
                self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(self.executed, [])


class FilesInUseTests(WindowsCase):
    """A file another program holds open is kept and counted; the item stays ok."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.under_local("CrashDumps", size=10)
        (self.target / "data.bin").unlink()

    def item(self) -> cleanup.Item:
        size = cleanup.directory_size(self.target)
        return cleanup.Item("temp", "Temp files", "review", size, "n", paths=(self.target,))

    def fail_unlink(self, names: tuple[str, ...], winerror: int = 32):
        real_unlink = os.unlink

        def unlink(path: object, *args: object, **kwargs: object) -> None:
            if os.fspath(path).endswith(names):  # type: ignore[arg-type]
                raise windows_error(path, winerror)
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

        return mock.patch.object(cleanup.os, "unlink", side_effect=unlink)

    def clean_with_locked(self, *names: str, winerror: int = 32) -> cleanup.Outcome:
        with self.fail_unlink(names, winerror):
            return cleanup.clean(self.item())

    def test_each_in_use_error_code_keeps_the_file_and_counts_it(self) -> None:
        for winerror in (32, 33, 5):
            with self.subTest(winerror=winerror):
                write(self.target / "locked.dat", 50_000)
                write(self.target / "gone.dat", 50_000)
                outcome = self.clean_with_locked("locked.dat", winerror=winerror)
                self.assertTrue(outcome.ok, outcome.error)
                self.assertEqual(outcome.error, "")
                self.assertEqual(outcome.in_use, 1)
                self.assertTrue((self.target / "locked.dat").exists())
                self.assertFalse((self.target / "gone.dat").exists())
                (self.target / "locked.dat").unlink()

    def test_the_other_files_are_still_removed_and_counted_as_freed(self) -> None:
        write(self.target / "locked.dat", 50_000)
        for index in range(3):
            write(self.target / f"gone{index}.dat", 100_000)
        outcome = self.clean_with_locked("locked.dat")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertGreaterEqual(outcome.freed_bytes, 300_000)
        self.assertLess(outcome.freed_bytes, 300_000 + 50_000 + SLACK)
        self.assertEqual(sorted(p.name for p in self.target.iterdir()), ["locked.dat"])

    def test_files_in_nested_directories_are_counted_too(self) -> None:
        write(self.target / "top.lock", 1_000)
        write(self.target / "a" / "one.lock", 1_000)
        write(self.target / "a" / "b" / "two.lock", 1_000)
        write(self.target / "a" / "b" / "free.dat", 1_000)
        outcome = self.clean_with_locked("top.lock", "one.lock", "two.lock")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.in_use, 3)
        self.assertFalse((self.target / "a" / "b" / "free.dat").exists())

    def test_a_directory_kept_only_for_its_kept_files_raises_no_error(self) -> None:
        write(self.target / "sub" / "locked.dat", 1_000)
        write(self.target / "sub" / "gone.dat", 1_000)
        write(self.target / "empty-after" / "gone.dat", 1_000)
        outcome = self.clean_with_locked("locked.dat")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.error, "")
        self.assertEqual(outcome.in_use, 1)
        self.assertTrue((self.target / "sub" / "locked.dat").exists())
        self.assertFalse((self.target / "sub" / "gone.dat").exists())
        self.assertFalse((self.target / "empty-after").exists(), "a directory with nothing kept goes")

    def test_a_deeper_chain_of_directories_is_kept_silently(self) -> None:
        write(self.target / "a" / "b" / "c" / "locked.dat", 1_000)
        outcome = self.clean_with_locked("locked.dat")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.error, "")
        self.assertTrue((self.target / "a" / "b" / "c" / "locked.dat").exists())

    def test_without_files_in_use_the_count_is_zero(self) -> None:
        write(self.target / "gone.dat", 1_000)
        outcome = cleanup.clean(self.item())
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.in_use, 0)

    def test_outcomes_default_to_no_files_in_use(self) -> None:
        item = cleanup.Item("x", "X", "safe", 1, "n")
        self.assertEqual(cleanup.Outcome(item, True, 0).in_use, 0)

    def test_other_errors_still_fail_the_item(self) -> None:
        write(self.target / "odd.dat", 1_000)
        write(self.target / "gone.dat", 1_000)
        for winerror in (1314, 123, 1920):
            with self.subTest(winerror=winerror):
                write(self.target / "odd.dat", 1_000)
                outcome = self.clean_with_locked("odd.dat", winerror=winerror)
                self.assertFalse(outcome.ok)
                self.assertIn("odd.dat", outcome.error)
                self.assertEqual(outcome.in_use, 0)

    def test_an_error_without_a_windows_code_still_fails_the_item(self) -> None:
        write(self.target / "odd.dat", 1_000)
        real_unlink = os.unlink

        def unlink(path: object, *args: object, **kwargs: object) -> None:
            if os.fspath(path).endswith("odd.dat"):  # type: ignore[arg-type]
                raise PermissionError(13, "Permission denied", os.fspath(path))  # type: ignore[arg-type]
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(cleanup.os, "unlink", side_effect=unlink):
            outcome = cleanup.clean(self.item())
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.in_use, 0)

    def test_a_real_error_next_to_a_file_in_use_still_fails_the_item(self) -> None:
        write(self.target / "locked.dat", 1_000)
        write(self.target / "odd.dat", 1_000)
        real_unlink = os.unlink

        def unlink(path: object, *args: object, **kwargs: object) -> None:
            name = os.fspath(path)  # type: ignore[arg-type]
            if name.endswith("locked.dat"):
                raise windows_error(path, 32)
            if name.endswith("odd.dat"):
                raise windows_error(path, 1314, "A required privilege is not held")
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(cleanup.os, "unlink", side_effect=unlink):
            outcome = cleanup.clean(self.item())
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.error)  # the item fails; the error line names the first problem
        self.assertTrue((self.target / "odd.dat").exists())

    def test_on_linux_a_windows_error_code_is_not_special(self) -> None:
        self.platform_is_windows = False
        write(self.target / "locked.dat", 1_000)
        outcome = self.clean_with_locked("locked.dat")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.in_use, 0)


@unittest.skipUnless(os.name == "nt", "holding a file open blocks deleting it only on Windows")
class RealOpenFileTests(WindowsCase):
    """Real locks: Python's open() does not share delete access, so Windows refuses the delete."""

    def setUp(self) -> None:
        super().setUp()
        self.target = self.under_local("Temp", size=10)
        (self.target / "data.bin").unlink()
        self.handles: list[object] = []
        self.addCleanup(self.close_all)

    def close_all(self) -> None:
        for handle in self.handles:
            handle.close()  # type: ignore[attr-defined]

    def hold(self, path: Path) -> Path:
        self.handles.append(open(path, "rb"))
        return path

    def clean(self) -> cleanup.Outcome:
        size = cleanup.directory_size(self.target)
        return cleanup.clean(cleanup.Item("temp", "Temp files", "review", size, "n", paths=(self.target,)))

    def test_a_file_held_open_is_kept_counted_and_the_rest_removed(self) -> None:
        locked = self.hold(write(self.target / "locked.dat", 50_000))
        gone = write(self.target / "gone.dat", 100_000)
        nested_gone = write(self.target / "dir" / "gone.dat", 100_000)
        outcome = self.clean()
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.error, "")
        self.assertEqual(outcome.in_use, 1)
        self.assertTrue(locked.exists())
        self.assertFalse(gone.exists())
        self.assertFalse(nested_gone.exists())
        self.assertGreaterEqual(outcome.freed_bytes, 200_000)

    def test_a_directory_holding_an_open_file_is_kept_without_an_error(self) -> None:
        locked = self.hold(write(self.target / "sub" / "deeper" / "locked.dat", 50_000))
        write(self.target / "sub" / "gone.dat", 10_000)
        outcome = self.clean()
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.error, "")
        self.assertEqual(outcome.in_use, 1)
        self.assertTrue(locked.exists())
        self.assertFalse((self.target / "sub" / "gone.dat").exists())

    def test_several_open_files_are_all_counted(self) -> None:
        for index in range(3):
            self.hold(write(self.target / f"l{index}.dat", 1_000))
        outcome = self.clean()
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.in_use, 3)

    def test_once_the_program_lets_go_the_file_is_removed(self) -> None:
        locked = write(self.target / "locked.dat", 1_000)
        handle = open(locked, "rb")
        try:
            self.assertEqual(self.clean().in_use, 1)
        finally:
            handle.close()
        outcome = self.clean()
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.in_use, 0)
        self.assertFalse(locked.exists())


if __name__ == "__main__":
    unittest.main()
