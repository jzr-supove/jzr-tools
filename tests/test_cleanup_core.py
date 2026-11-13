"""Acceptance tests for ``jzr_tools.core.cleanup`` (scan and removal).

Everything runs against a throwaway home directory with fake cache trees.
Sizes are allocated blocks, so fixtures write real bytes, never sparse files.
The minimums are patched down so the fixtures stay a few hundred kilobytes.
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jzr_tools.core import cleanup

MIB = 1024**2
GIB = 1024**3

# These tests describe the Linux items and rules. The Windows ones are in
# test_cleanup_windows, whose tests patch the platform themselves.
_linux_rules = mock.patch("jzr_tools.core.cleanup._windows", return_value=False)


def setUpModule() -> None:  # noqa: N802
    _linux_rules.start()


def tearDownModule() -> None:  # noqa: N802
    _linux_rules.stop()

MINIMUM = 200_000
MINIMUM_OTHER = 400_000
SMALL = 20_000  # below both minimums
MEDIUM = 300_000  # at least MINIMUM, below MINIMUM_OTHER
LARGE = 600_000  # at least both minimums
SLACK = 60_000  # allocated blocks and directory entries beyond the bytes written

DOCKER_SAMPLE = "\n".join(
    [
        '{"Type":"Images","TotalCount":"12","Active":"2","Size":"30.1GB","Reclaimable":"21.42GB (71%)"}',
        '{"Type":"Containers","TotalCount":"4","Active":"1","Size":"2.5GB","Reclaimable":"1.5GB (60%)"}',
        '{"Type":"Local Volumes","TotalCount":"3","Active":"1","Size":"9GB","Reclaimable":"8GB (88%)"}',
        '{"Type":"Build Cache","TotalCount":"80","Active":"0","Size":"47.9GB","Reclaimable":"37.95GB"}',
    ]
)
JOURNAL_SAMPLE = "Archived and active journals take up 4.0G in the file system.\n"


def write(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    return path


def make_shallow_directory(case: unittest.TestCase) -> Path:
    """Create a throwaway directory next to the filesystem root, removed afterwards.

    /tmp/xxxx on POSIX, X:\\xxxx (the drive of the temp directory) on Windows.
    """
    parent = Path(tempfile.gettempdir()).anchor if os.name == "nt" else "/tmp"
    shallow = Path(tempfile.mkdtemp(dir=parent)).resolve()
    case.addCleanup(shutil.rmtree, shallow, True)
    return shallow


class CleanupCase(unittest.TestCase):
    """A temporary home, the minimums patched down, and every seam faked."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home" / "user"
        self.home.mkdir(parents=True)
        self.installed: set[str] = set()
        self.docker_output: str | None = None
        self.journal_output: str | None = None
        self.is_root = False

        def which(name: str, *args: object, **kwargs: object) -> str | None:
            return f"/usr/bin/{name}" if name in self.installed else None

        def capture(command: object) -> str | None:
            words = list(command)  # type: ignore[call-overload]
            if "docker" in words:
                return self.docker_output
            if "journalctl" in words:
                return self.journal_output
            return None

        for patcher in (
            mock.patch.object(cleanup, "MINIMUM_BYTES", MINIMUM),
            mock.patch.object(cleanup, "MINIMUM_OTHER_BYTES", MINIMUM_OTHER),
            mock.patch.object(cleanup.shutil, "which", side_effect=which),
            mock.patch.object(cleanup, "_capture", side_effect=capture),
            mock.patch.object(cleanup, "_is_root", side_effect=lambda: self.is_root),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def scan(self, environ: dict[str, str] | None = None) -> dict[str, cleanup.Item]:
        items = cleanup.scan(home=self.home, environ=environ or {})
        found = {item.id: item for item in items}
        self.assertEqual(len(found), len(items), "an id was listed twice")
        return found

    def cache(self, *parts: str, size: int = LARGE) -> Path:
        return self.fill(self.home / ".cache", *parts, size=size)

    def fill(self, base: Path, *parts: str, size: int = LARGE) -> Path:
        directory = base.joinpath(*parts)
        write(directory / "data.bin", size)
        return directory

    def assert_size(self, item: cleanup.Item, written: int) -> None:
        self.assertGreaterEqual(item.size_bytes, written)
        self.assertLess(item.size_bytes, written + SLACK)


class ModuleSurfaceTests(unittest.TestCase):
    def test_constants_match_the_spec(self) -> None:
        self.assertEqual(cleanup.MINIMUM_BYTES, 10 * MIB)
        self.assertEqual(cleanup.MINIMUM_OTHER_BYTES, 100 * MIB)
        self.assertEqual(cleanup.JOURNAL_KEEP_BYTES, 500 * MIB)
        self.assertEqual(tuple(cleanup.TIERS), ("safe", "review", "root"))

    def test_known_ids_are_exactly_the_documented_items(self) -> None:
        self.assertEqual(
            set(cleanup.KNOWN_IDS),
            {
                "docker-build-cache", "uv", "pip", "npm", "cargo", "thumbnails",
                "go-build", "yarn", "pnpm", "node-gyp", "mesa-shaders", "trash",
                "docker-containers", "docker-images", "journal", "dnf", "apt",
                # Windows items: --only is the same on every system (spec, host clean)
                "gpu-shaders", "temp", "crash-dumps", "windows-temp", "windows-update",
            },
        )
        self.assertEqual(len(cleanup.KNOWN_IDS), len(set(cleanup.KNOWN_IDS)))

    def test_is_known_id_accepts_known_ids_and_any_cache_name(self) -> None:
        for item_id in ("uv", "docker-images", "journal", "cache:Mozilla", "cache:a b"):
            with self.subTest(item_id=item_id):
                self.assertTrue(cleanup.is_known_id(item_id))

    def test_is_known_id_rejects_everything_else(self) -> None:
        for item_id in ("", "nope", "UV", "cache", "docker", "Cache:x"):
            with self.subTest(item_id=item_id):
                self.assertFalse(cleanup.is_known_id(item_id))

    def test_item_action_is_the_command_line_for_command_items(self) -> None:
        item = cleanup.Item(
            "docker-build-cache", "Docker build cache", "safe", 5, "n",
            command=("docker", "builder", "prune", "--all", "--force"),
        )
        self.assertEqual(item.action, "docker builder prune --all --force")

    def test_item_action_lists_the_paths_for_directory_items(self) -> None:
        first, second = Path("a") / "b" / "c", Path("a") / "b" / "d"
        item = cleanup.Item("trash", "Trash", "review", 5, "n", paths=(first, second))
        self.assertIn(str(first), item.action)
        self.assertIn(str(second), item.action)
        self.assertIn(",", item.action)

    def test_command_wins_over_paths_in_the_action(self) -> None:
        item = cleanup.Item("uv", "uv", "safe", 5, "n", paths=(Path("/a/b/c"),), command=("uv", "cache", "clean"))
        self.assertEqual(item.action, "uv cache clean")

    def test_item_and_outcome_are_immutable(self) -> None:
        item = cleanup.Item("x", "X", "safe", 1, "n")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            item.size_bytes = 2  # type: ignore[misc]
        outcome = cleanup.Outcome(item, True, 0)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            outcome.ok = False  # type: ignore[misc]
        self.assertEqual(outcome.error, "")


class ScanDirectoryItemTests(CleanupCase):
    def test_empty_home_lists_nothing(self) -> None:
        self.assertEqual(cleanup.scan(home=self.home, environ={}), [])

    def test_pip_is_safe_and_empties_its_cache_directory(self) -> None:
        directory = self.cache("pip", size=MEDIUM)
        item = self.scan()["pip"]
        self.assertEqual(item.tier, "safe")
        self.assertEqual(item.paths, (directory,))
        self.assertEqual(item.command, ())
        self.assert_size(item, MEDIUM)
        self.assertTrue(item.title and item.note)

    def test_simple_cache_directories_are_safe_items_with_their_own_path(self) -> None:
        cases = {
            "thumbnails": "thumbnails",
            "go-build": "go-build",
            "yarn": "yarn",
            "pnpm": "pnpm",
            "node-gyp": "node-gyp",
        }
        for item_id, name in cases.items():
            with self.subTest(item_id=item_id):
                directory = self.cache(name, size=MEDIUM)
                item = self.scan()[item_id]
                self.assertEqual(item.tier, "safe")
                self.assertEqual(item.paths, (directory,))
                self.assertEqual(item.command, ())
                self.assert_size(item, MEDIUM)

    def test_mesa_shaders_combines_both_cache_directories(self) -> None:
        first = self.cache("mesa_shader_cache", size=MEDIUM)
        second = self.cache("mesa_shader_cache_db", size=MEDIUM)
        item = self.scan()["mesa-shaders"]
        self.assertEqual(item.tier, "safe")
        self.assertEqual(set(item.paths), {first, second})
        self.assertGreaterEqual(item.size_bytes, 2 * MEDIUM)
        self.assertLess(item.size_bytes, 2 * MEDIUM + 2 * SLACK)

    def test_mesa_sizes_add_up_across_directories_to_cross_the_minimum(self) -> None:
        self.cache("mesa_shader_cache", size=150_000)
        self.cache("mesa_shader_cache_db", size=150_000)
        self.assertIn("mesa-shaders", self.scan())

    def test_cargo_empties_registry_cache_and_src(self) -> None:
        cache = self.fill(self.home / ".cargo", "registry", "cache", size=MEDIUM)
        src = self.fill(self.home / ".cargo", "registry", "src", size=MEDIUM)
        item = self.scan()["cargo"]
        self.assertEqual(item.tier, "safe")
        self.assertEqual(set(item.paths), {cache, src})
        self.assertEqual(item.command, ())
        self.assertGreaterEqual(item.size_bytes, 2 * MEDIUM)

    def test_cargo_does_not_touch_the_rest_of_cargo_home(self) -> None:
        self.fill(self.home / ".cargo", "bin", size=LARGE)
        self.fill(self.home / ".cargo", "registry", "index", size=LARGE)
        self.assertNotIn("cargo", self.scan())

    def test_trash_is_review_and_covers_files_info_and_expunged(self) -> None:
        base = self.home / ".local" / "share" / "Trash"
        paths = {self.fill(base, name, size=MEDIUM) for name in ("files", "info", "expunged")}
        item = self.scan()["trash"]
        self.assertEqual(item.tier, "review")
        self.assertEqual(set(item.paths), paths)
        self.assertEqual(item.command, ())

    def test_other_cache_directories_become_review_items_named_by_directory(self) -> None:
        directory = self.cache("Mozilla", size=LARGE)
        item = self.scan()["cache:Mozilla"]
        self.assertEqual(item.tier, "review")
        self.assertEqual(item.paths, (directory,))
        self.assertEqual(item.command, ())
        self.assert_size(item, LARGE)

    def test_cache_name_rule_uses_the_larger_minimum(self) -> None:
        self.cache("Medium", size=MEDIUM)  # over MINIMUM, under MINIMUM_OTHER
        self.assertNotIn("cache:Medium", self.scan())

    def test_cache_name_rule_ignores_files_directly_under_cache(self) -> None:
        write(self.home / ".cache" / "huge.bin", LARGE)
        self.assertEqual(cleanup.scan(home=self.home, environ={}), [])

    def test_cache_name_rule_only_looks_one_level_down(self) -> None:
        write(self.home / ".cache" / "outer" / "a" / "x.bin", 250_000)
        write(self.home / ".cache" / "outer" / "b" / "y.bin", 250_000)
        found = self.scan()
        self.assertIn("cache:outer", found)  # the sum counts
        self.assertEqual([i for i in found if i.startswith("cache:")], ["cache:outer"])

    def test_known_cache_directories_are_not_duplicated_as_cache_entries(self) -> None:
        for name in (
            "uv", "pip", "thumbnails", "go-build", "yarn", "pnpm", "node-gyp",
            "mesa_shader_cache", "mesa_shader_cache_db",
        ):
            self.cache(name, size=LARGE)
        found = self.scan()
        self.assertEqual([i for i in found if i.startswith("cache:")], [])
        for item_id in ("pip", "thumbnails", "go-build", "yarn", "pnpm", "node-gyp", "mesa-shaders"):
            self.assertIn(item_id, found)

    def test_known_name_below_its_minimum_is_not_listed_as_cache_entry_either(self) -> None:
        self.cache("pip", size=SMALL)
        found = self.scan()
        self.assertNotIn("pip", found)
        self.assertNotIn("cache:pip", found)

    def test_items_below_the_minimum_are_not_listed(self) -> None:
        self.cache("pip", size=SMALL)
        self.cache("yarn", size=SMALL)
        self.fill(self.home / ".local" / "share" / "Trash", "files", size=SMALL)
        self.assertEqual(cleanup.scan(home=self.home, environ={}), [])

    def test_missing_directories_are_simply_absent(self) -> None:
        self.cache("pip", size=MEDIUM)
        self.assertEqual(set(self.scan()), {"pip"})

    def test_size_is_the_sum_of_every_file_below_the_directory(self) -> None:
        directory = self.home / ".cache" / "pip"
        for index in range(4):
            write(directory / f"d{index}" / "f.bin", 100_000)
        item = self.scan()["pip"]
        self.assertGreaterEqual(item.size_bytes, 400_000)
        self.assertLess(item.size_bytes, 400_000 + 4 * SLACK)


class ScanEnvironmentOverrideTests(CleanupCase):
    def test_xdg_cache_home_replaces_the_default_cache_directory(self) -> None:
        decoy = self.cache("pip", size=LARGE)
        elsewhere = self.root / "elsewhere"
        real = self.fill(elsewhere, "pip", size=MEDIUM)
        self.cache("Mozilla", size=LARGE)  # decoy for the cache:<name> rule
        self.fill(elsewhere, "Chrome", size=LARGE)
        found = self.scan({"XDG_CACHE_HOME": str(elsewhere)})
        self.assertEqual(found["pip"].paths, (real,))
        self.assertNotEqual(found["pip"].paths, (decoy,))
        self.assertIn("cache:Chrome", found)
        self.assertNotIn("cache:Mozilla", found)

    def test_xdg_data_home_replaces_the_default_trash_location(self) -> None:
        self.fill(self.home / ".local" / "share" / "Trash", "files", size=LARGE)
        data = self.root / "data"
        files = self.fill(data / "Trash", "files", size=MEDIUM)
        found = self.scan({"XDG_DATA_HOME": str(data)})
        self.assertEqual(found["trash"].paths, (files,))

    def test_uv_cache_dir_wins_over_the_cache_directory(self) -> None:
        self.cache("uv", size=LARGE)
        custom = self.fill(self.root, "my-uv", size=MEDIUM)
        item = self.scan({"UV_CACHE_DIR": str(custom)})["uv"]
        self.assertEqual(item.paths, (custom,))

    def test_uv_defaults_to_the_cache_directory(self) -> None:
        directory = self.cache("uv", size=MEDIUM)
        self.assertEqual(self.scan()["uv"].paths, (directory,))

    def test_pip_cache_dir_wins_over_the_cache_directory(self) -> None:
        self.cache("pip", size=LARGE)
        custom = self.fill(self.root, "my-pip", size=MEDIUM)
        item = self.scan({"PIP_CACHE_DIR": str(custom)})["pip"]
        self.assertEqual(item.paths, (custom,))

    def test_npm_config_cache_replaces_the_npm_directory(self) -> None:
        self.fill(self.home / ".npm", "_cacache", size=LARGE)
        custom = self.root / "npm-elsewhere"
        cacache = self.fill(custom, "_cacache", size=MEDIUM)
        item = self.scan({"npm_config_cache": str(custom)})["npm"]
        self.assertEqual(item.paths, (cacache,))

    def test_npm_defaults_to_the_cacache_below_dot_npm(self) -> None:
        cacache = self.fill(self.home / ".npm", "_cacache", size=MEDIUM)
        item = self.scan()["npm"]
        self.assertEqual(item.paths, (cacache,))
        self.assertEqual(item.tier, "safe")

    def test_npm_ignores_the_rest_of_dot_npm(self) -> None:
        self.fill(self.home / ".npm", "_logs", size=LARGE)
        self.assertNotIn("npm", self.scan())

    def test_cargo_home_replaces_dot_cargo(self) -> None:
        self.fill(self.home / ".cargo", "registry", "cache", size=LARGE)
        custom = self.root / "cargo-elsewhere"
        cache = self.fill(custom, "registry", "cache", size=MEDIUM)
        src = self.fill(custom, "registry", "src", size=MEDIUM)
        item = self.scan({"CARGO_HOME": str(custom)})["cargo"]
        self.assertEqual(set(item.paths), {cache, src})

    def test_override_pointing_at_a_missing_directory_hides_the_item(self) -> None:
        self.cache("pip", size=LARGE)  # the default location must not be a fallback
        found = self.scan({"PIP_CACHE_DIR": str(self.root / "missing")})
        self.assertNotIn("pip", found)


class ScanToolTests(CleanupCase):
    def test_uv_uses_its_own_cleaner_when_installed(self) -> None:
        self.installed.add("uv")
        self.cache("uv", size=MEDIUM)
        item = self.scan()["uv"]
        self.assertEqual(item.tier, "safe")
        self.assertEqual(tuple(item.command), ("uv", "cache", "clean"))
        self.assert_size(item, MEDIUM)

    def test_uv_empties_the_directory_when_not_installed(self) -> None:
        directory = self.cache("uv", size=MEDIUM)
        item = self.scan()["uv"]
        self.assertEqual(tuple(item.command), ())
        self.assertEqual(item.paths, (directory,))

    def test_npm_uses_its_own_cleaner_when_installed(self) -> None:
        self.installed.add("npm")
        self.fill(self.home / ".npm", "_cacache", size=MEDIUM)
        item = self.scan()["npm"]
        self.assertEqual(tuple(item.command), ("npm", "cache", "clean", "--force"))

    def test_npm_empties_the_directory_when_not_installed(self) -> None:
        cacache = self.fill(self.home / ".npm", "_cacache", size=MEDIUM)
        item = self.scan()["npm"]
        self.assertEqual(tuple(item.command), ())
        self.assertEqual(item.paths, (cacache,))

    def test_uv_missing_directory_means_no_item_even_with_the_tool(self) -> None:
        self.installed.add("uv")
        self.assertNotIn("uv", self.scan())

    def test_pip_never_needs_the_pip_tool(self) -> None:
        self.cache("pip", size=MEDIUM)  # nothing is installed
        self.assertEqual(tuple(self.scan()["pip"].command), ())


class ScanDockerTests(CleanupCase):
    def setUp(self) -> None:
        super().setUp()
        self.installed.add("docker")
        self.docker_output = DOCKER_SAMPLE

    def test_docker_items_come_from_the_reclaimable_figures(self) -> None:
        found = self.scan()
        self.assertEqual(
            {i for i in found if i.startswith("docker")},
            {"docker-build-cache", "docker-containers", "docker-images"},
        )
        self.assertAlmostEqual(found["docker-build-cache"].size_bytes, 37_950_000_000, delta=1_000)
        self.assertAlmostEqual(found["docker-images"].size_bytes, 21_420_000_000, delta=1_000)
        self.assertAlmostEqual(found["docker-containers"].size_bytes, 1_500_000_000, delta=1_000)

    def test_docker_tiers_and_commands(self) -> None:
        found = self.scan()
        self.assertEqual(found["docker-build-cache"].tier, "safe")
        self.assertEqual(found["docker-containers"].tier, "review")
        self.assertEqual(found["docker-images"].tier, "review")
        self.assertEqual(
            tuple(found["docker-build-cache"].command),
            ("docker", "builder", "prune", "--all", "--force"),
        )
        self.assertEqual(
            tuple(found["docker-containers"].command),
            ("docker", "container", "prune", "--force"),
        )
        self.assertEqual(
            tuple(found["docker-images"].command),
            ("docker", "image", "prune", "--all", "--force"),
        )
        for item_id in ("docker-build-cache", "docker-containers", "docker-images"):
            self.assertEqual(tuple(found[item_id].paths), ())

    def test_docker_volumes_are_never_offered(self) -> None:
        for item in self.scan().values():
            self.assertNotIn("volume", item.id)
            self.assertNotIn("volume", item.action)

    def test_docker_query_is_the_json_system_df(self) -> None:
        self.scan()
        queries = [list(call.args[0]) for call in cleanup._capture.call_args_list]  # type: ignore[attr-defined]
        self.assertIn(["docker", "system", "df", "--format", "{{json .}}"], queries)

    def test_unit_parsing_covers_bytes_kilo_mega_giga_tera(self) -> None:
        self.docker_output = "\n".join(
            [
                '{"Type":"Images","Reclaimable":"2TB (50%)"}',
                '{"Type":"Containers","Reclaimable":"300MB"}',
                '{"Type":"Build Cache","Reclaimable":"250kB"}',
            ]
        )
        found = self.scan()
        self.assertAlmostEqual(found["docker-images"].size_bytes, 2_000_000_000_000, delta=1_000)
        self.assertAlmostEqual(found["docker-containers"].size_bytes, 300_000_000, delta=1_000)
        self.assertAlmostEqual(found["docker-build-cache"].size_bytes, 250_000, delta=10)

    def test_figures_below_the_minimum_are_dropped(self) -> None:
        self.docker_output = "\n".join(
            [
                '{"Type":"Images","Reclaimable":"0B"}',
                '{"Type":"Containers","Reclaimable":"50kB (1%)"}',
                '{"Type":"Build Cache","Reclaimable":"1.5GB"}',
            ]
        )
        found = self.scan()
        self.assertEqual({i for i in found if i.startswith("docker")}, {"docker-build-cache"})

    def test_unreachable_daemon_hides_every_docker_item(self) -> None:
        self.docker_output = None
        self.assertEqual({i for i in self.scan() if i.startswith("docker")}, set())

    def test_garbage_output_hides_every_docker_item_without_crashing(self) -> None:
        self.docker_output = "Cannot connect to the Docker daemon\nnot json\n"
        self.assertEqual({i for i in self.scan() if i.startswith("docker")}, set())

    def test_docker_not_installed_hides_every_docker_item(self) -> None:
        self.installed.discard("docker")
        self.assertEqual({i for i in self.scan() if i.startswith("docker")}, set())


class ScanRootTests(CleanupCase):
    def setUp(self) -> None:
        super().setUp()
        self.journal_output = JOURNAL_SAMPLE
        self.installed.update({"journalctl", "dnf", "apt-get"})
        # The spec says the system paths are module constants; their names are
        # not in the design, so these are the names this suite assumes.
        self.dnf_dirs = (self.root / "var" / "cache" / "libdnf5", self.root / "var" / "cache" / "dnf")
        self.apt_dirs = (self.root / "var" / "cache" / "apt" / "archives",)
        for patcher in (
            mock.patch.object(cleanup, "DNF_CACHE_PATHS", self.dnf_dirs, create=True),
            mock.patch.object(cleanup, "APT_CACHE_PATHS", self.apt_dirs, create=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def root_ids(self) -> set[str]:
        return {i for i in self.scan() if i in ("journal", "dnf", "apt")}

    def test_root_items_need_sudo_or_root(self) -> None:
        write(self.dnf_dirs[0] / "x.bin", LARGE)
        write(self.apt_dirs[0] / "x.deb", LARGE)
        self.assertEqual(self.root_ids(), set())

    def test_root_items_appear_when_sudo_is_installed(self) -> None:
        self.installed.add("sudo")
        write(self.dnf_dirs[0] / "x.bin", LARGE)
        write(self.apt_dirs[0] / "x.deb", LARGE)
        self.assertEqual(self.root_ids(), {"journal", "dnf", "apt"})

    def test_root_items_appear_when_the_user_is_root(self) -> None:
        self.is_root = True
        write(self.dnf_dirs[1] / "x.bin", LARGE)
        self.assertEqual(self.root_ids(), {"journal", "dnf"})

    def test_journal_size_is_usage_minus_the_kept_500_mib(self) -> None:
        self.installed.add("sudo")
        item = self.scan()["journal"]
        self.assertEqual(item.tier, "root")
        self.assertEqual(item.size_bytes, 4 * GIB - 500 * MIB)

    def test_journal_command_is_unprefixed_vacuum_to_500m(self) -> None:
        self.installed.add("sudo")
        item = self.scan()["journal"]
        self.assertEqual(tuple(item.command), ("journalctl", "--vacuum-size=500M"))
        self.assertEqual(tuple(item.paths), ())

    def test_journal_units_cover_megabytes_and_fractions(self) -> None:
        self.installed.add("sudo")
        self.journal_output = "Archived and active journals take up 700.5M in the file system.\n"
        item = self.scan()["journal"]
        self.assertAlmostEqual(item.size_bytes, int(700.5 * MIB) - 500 * MIB, delta=1_000)

    def test_journal_at_or_under_the_kept_size_is_not_listed(self) -> None:
        self.installed.add("sudo")
        for output in (
            "Archived and active journals take up 400.0M in the file system.\n",
            "Archived and active journals take up 500.0M in the file system.\n",
        ):
            with self.subTest(output=output):
                self.journal_output = output
                self.assertNotIn("journal", self.scan())

    def test_journal_is_absent_when_the_query_fails(self) -> None:
        self.installed.add("sudo")
        self.journal_output = None
        self.assertNotIn("journal", self.scan())

    def test_journal_is_absent_for_unparseable_output(self) -> None:
        self.installed.add("sudo")
        self.journal_output = "No journal files were found.\n"
        self.assertNotIn("journal", self.scan())

    def test_dnf_is_root_tier_and_runs_clean_all(self) -> None:
        self.installed.add("sudo")
        write(self.dnf_dirs[0] / "x.bin", MEDIUM)
        write(self.dnf_dirs[1] / "y.bin", MEDIUM)
        item = self.scan()["dnf"]
        self.assertEqual(item.tier, "root")
        self.assertEqual(tuple(item.command), ("dnf", "clean", "all"))
        self.assertGreaterEqual(item.size_bytes, 2 * MEDIUM)

    def test_apt_is_root_tier_and_runs_apt_get_clean(self) -> None:
        self.installed.add("sudo")
        write(self.apt_dirs[0] / "x.deb", MEDIUM)
        item = self.scan()["apt"]
        self.assertEqual(item.tier, "root")
        self.assertEqual(tuple(item.command), ("apt-get", "clean"))
        self.assert_size(item, MEDIUM)

    def test_package_caches_below_the_minimum_or_missing_are_absent(self) -> None:
        self.installed.add("sudo")
        write(self.dnf_dirs[0] / "x.bin", SMALL)
        self.assertEqual(self.root_ids(), {"journal"})


class ScanOrderingTests(CleanupCase):
    def test_items_come_in_tier_order_then_largest_first(self) -> None:
        self.installed.update({"sudo", "journalctl"})
        self.journal_output = JOURNAL_SAMPLE
        self.cache("pip", size=MEDIUM)
        self.cache("yarn", size=500_000)
        self.cache("go-build", size=250_000)
        self.fill(self.home / ".local" / "share" / "Trash", "files", size=350_000)
        self.cache("Mozilla", size=900_000)
        items = cleanup.scan(home=self.home, environ={})
        ids = [i.id for i in items]
        self.assertEqual(ids[:3], ["yarn", "pip", "go-build"])
        self.assertEqual(ids[3:5], ["cache:Mozilla", "trash"])
        self.assertEqual(items[-1].id, "journal")
        tiers = [i.tier for i in items]
        self.assertEqual(tiers, sorted(tiers, key=cleanup.TIERS.index))

    def test_every_item_has_a_title_a_note_and_a_valid_tier(self) -> None:
        self.cache("pip", size=MEDIUM)
        self.cache("Mozilla", size=LARGE)
        for item in cleanup.scan(home=self.home, environ={}):
            self.assertIn(item.tier, cleanup.TIERS)
            self.assertTrue(item.title.strip())
            self.assertTrue(item.note.strip())
            self.assertTrue(item.paths or item.command)


class DirectorySizeTests(CleanupCase):
    def test_counts_allocated_bytes_of_every_file(self) -> None:
        directory = self.root / "d"
        write(directory / "a.bin", 100_000)
        write(directory / "sub" / "b.bin", 150_000)
        size = cleanup.directory_size(directory)
        self.assertGreaterEqual(size, 250_000)
        self.assertLess(size, 250_000 + SLACK)

    def test_missing_directory_is_zero(self) -> None:
        self.assertEqual(cleanup.directory_size(self.root / "nope"), 0)

    def test_empty_directory_is_small(self) -> None:
        directory = self.root / "d"
        directory.mkdir()
        self.assertLess(cleanup.directory_size(directory), 16_384)

    @unittest.skipIf(
        os.name == "nt",
        "hard links are not de-duplicated on Windows: scandir reports no link counts there "
        "and an lstat per file made sizing the uv cache 10x slower (spec, Sizes)",
    )
    def test_hard_links_are_counted_once(self) -> None:
        directory = self.root / "d"
        original = write(directory / "a" / "orig.bin", 100_000)
        (directory / "b").mkdir()
        os.link(original, directory / "b" / "link1.bin")
        os.link(original, directory / "link2.bin")
        size = cleanup.directory_size(directory)
        self.assertGreaterEqual(size, 100_000)
        self.assertLess(size, 100_000 + SLACK)

    def test_symlinks_are_not_followed(self) -> None:
        outside = self.root / "outside"
        write(outside / "big.bin", LARGE)
        directory = self.root / "d"
        write(directory / "small.bin", 10_000)
        (directory / "dirlink").symlink_to(outside, target_is_directory=True)
        (directory / "filelink").symlink_to(outside / "big.bin")
        self.assertLess(cleanup.directory_size(directory), 10_000 + SLACK)

    def test_a_known_cache_reached_through_a_link_is_measured(self) -> None:
        relocated = self.fill(self.root, "relocated", "pip", size=MEDIUM)
        (self.home / ".cache").mkdir()
        (self.home / ".cache" / "pip").symlink_to(relocated, target_is_directory=True)
        item = self.scan()["pip"]
        self.assert_size(item, MEDIUM)

    def test_symlinks_directly_under_the_cache_directory_are_never_offered_as_cache_entries(self) -> None:
        outside = self.root / "outside"
        write(outside / "big.bin", LARGE)
        (self.home / ".cache").mkdir()
        (self.home / ".cache" / "Linked").symlink_to(outside, target_is_directory=True)
        self.assertNotIn("cache:Linked", self.scan())

    def test_unreadable_subdirectory_does_not_raise_even_when_scandir_refuses_it(self) -> None:
        directory = self.root / "d"
        write(directory / "ok.bin", 100_000)
        locked = directory / "locked"
        write(locked / "x.bin", 100_000)
        real_scandir = os.scandir

        def scandir(path: object = "."):
            if os.fspath(path) == str(locked):  # type: ignore[arg-type]
                raise PermissionError(13, "Access is denied", str(locked))
            return real_scandir(path)  # type: ignore[arg-type]

        with mock.patch.object(cleanup.os, "scandir", side_effect=scandir):
            size = cleanup.directory_size(directory)
        self.assertGreaterEqual(size, 100_000)
        self.assertLess(size, 100_000 + SLACK)  # the locked file is left out

    @unittest.skipIf(os.name == "nt", "chmod 0 does not make a directory unreadable on Windows")
    def test_unreadable_subdirectory_does_not_raise(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root can read everything")
        directory = self.root / "d"
        write(directory / "ok.bin", 100_000)
        locked = directory / "locked"
        write(locked / "x.bin", 100_000)
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        self.assertGreaterEqual(cleanup.directory_size(directory), 100_000)


class EmptyDirectoryTests(CleanupCase):
    def make_target(self) -> Path:
        target = self.home / ".cache" / "pip"
        write(target / "a.bin", 50_000)
        write(target / "sub" / "deep" / "b.bin", 50_000)
        (target / "sub" / "emptydir").mkdir()
        return target

    def test_removes_the_contents_and_keeps_the_directory(self) -> None:
        target = self.make_target()
        errors = cleanup.empty_directory(target, home=self.home)
        self.assertEqual(errors, [])
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])

    def test_leaves_siblings_alone(self) -> None:
        target = self.make_target()
        sibling = write(self.home / ".cache" / "other" / "keep.bin", 1_000)
        cleanup.empty_directory(target, home=self.home)
        self.assertTrue(sibling.exists())

    def test_symlinks_are_removed_but_never_followed(self) -> None:
        target = self.make_target()
        outside = self.root / "outside"
        keep = write(outside / "keep.bin", 5_000)
        (target / "dirlink").symlink_to(outside, target_is_directory=True)
        (target / "sub" / "filelink").symlink_to(keep)
        errors = cleanup.empty_directory(target, home=self.home)
        self.assertEqual(errors, [])
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue(keep.exists())

    def test_a_failing_entry_is_reported_and_the_rest_is_still_removed_with_a_faked_error(self) -> None:
        target = self.home / ".cache" / "pip"
        write(target / "a.bin", 1_000)
        write(target / "m.bin", 1_000)
        write(target / "z.bin", 1_000)
        real_unlink = os.unlink

        def unlink(path: object, *args: object, **kwargs: object) -> None:
            if os.fspath(path).endswith("m.bin"):  # type: ignore[arg-type]
                raise PermissionError(13, "Permission denied", os.fspath(path))  # type: ignore[arg-type]
            real_unlink(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch.object(cleanup.os, "unlink", side_effect=unlink):
            errors = cleanup.empty_directory(target, home=self.home)
        self.assertEqual(len(errors), 1)
        self.assertIn("m.bin", errors[0])
        self.assertFalse((target / "a.bin").exists())
        self.assertFalse((target / "z.bin").exists())
        self.assertTrue((target / "m.bin").exists())

    @unittest.skipIf(os.name == "nt", "chmod 0o500 does not stop deleting inside a directory on Windows")
    def test_a_failing_entry_is_reported_and_the_rest_is_still_removed(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root ignores permissions")
        target = self.home / ".cache" / "pip"
        write(target / "a.bin", 1_000)
        write(target / "z.bin", 1_000)
        locked = target / "locked"
        write(locked / "x.bin", 1_000)
        locked.chmod(0o500)
        self.addCleanup(locked.chmod, 0o700)
        errors = cleanup.empty_directory(target, home=self.home)
        self.assertTrue(errors)
        self.assertTrue(all(isinstance(e, str) and e for e in errors))
        self.assertFalse((target / "a.bin").exists())
        self.assertFalse((target / "z.bin").exists())

    def assert_refused(self, path: Path, sentinel: Path | None = None) -> None:
        errors = cleanup.empty_directory(path, home=self.home)
        self.assertTrue(errors, f"{path} should be refused")
        if sentinel is not None:
            self.assertTrue(sentinel.exists(), f"{path} was emptied")

    def test_refuses_the_home_directory(self) -> None:
        sentinel = write(self.home / "precious.txt", 100)
        self.assert_refused(self.home, sentinel)

    def test_refuses_a_parent_of_the_home_directory(self) -> None:
        sentinel = write(self.home / "precious.txt", 100)
        for parent in (self.home.parent, self.home.parent.parent):
            with self.subTest(parent=str(parent)):
                self.assert_refused(parent, sentinel)

    def test_refuses_the_filesystem_root_without_deleting_anything(self) -> None:
        # Deletion primitives are replaced so a missing guard cannot hurt.
        with (
            mock.patch.object(shutil, "rmtree") as rmtree,
            mock.patch.object(os, "unlink") as unlink,
            mock.patch.object(os, "remove") as remove,
            mock.patch.object(os, "rmdir") as rmdir,
            mock.patch.object(pathlib.Path, "unlink") as path_unlink,
            mock.patch.object(pathlib.Path, "rmdir") as path_rmdir,
        ):
            errors = cleanup.empty_directory(Path("/"), home=self.home)
        self.assertTrue(errors)
        for deleter in (rmtree, unlink, remove, rmdir, path_unlink, path_rmdir):
            deleter.assert_not_called()

    def shallow_directory(self) -> Path:
        try:
            return make_shallow_directory(self)
        except OSError as exc:
            self.skipTest(f"cannot create a directory next to the filesystem root: {exc}")

    def test_refuses_paths_fewer_than_four_parts_deep(self) -> None:
        shallow = self.shallow_directory()
        self.assertLess(len(shallow.parts), 4)
        sentinel = write(shallow / "keep.txt", 100)
        self.assert_refused(shallow, sentinel)

    def test_accepts_a_directory_exactly_four_parts_deep(self) -> None:
        target = self.shallow_directory()
        while len(target.parts) < 4:  # the root counts as the first part
            target /= "cache"
        self.assertEqual(len(target.parts), 4)
        write(target / "x.bin", 100)
        self.assertEqual(cleanup.empty_directory(target, home=self.home), [])
        self.assertEqual(list(target.iterdir()), [])


class CleanPathItemTests(CleanupCase):
    def item(self, *paths: Path, tier: str = "safe") -> cleanup.Item:
        size = sum(cleanup.directory_size(p) for p in paths)
        return cleanup.Item("pip", "pip cache", tier, size, "note", paths=tuple(paths))

    def test_empties_the_directories_and_keeps_them(self) -> None:
        first = self.cache("pip", size=MEDIUM)
        second = self.cache("yarn", size=MEDIUM)
        outcome = cleanup.clean(self.item(first, second))
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.error, "")
        for directory in (first, second):
            self.assertTrue(directory.is_dir())
            self.assertEqual(list(directory.iterdir()), [])

    def test_reports_the_bytes_that_came_back(self) -> None:
        directory = self.cache("pip", size=MEDIUM)
        item = self.item(directory)
        outcome = cleanup.clean(item)
        self.assertGreaterEqual(outcome.freed_bytes, MEDIUM)
        self.assertEqual(outcome.freed_bytes, item.size_bytes - cleanup.directory_size(directory))

    def test_returns_the_item_it_was_given(self) -> None:
        directory = self.cache("pip", size=MEDIUM)
        item = self.item(directory)
        self.assertEqual(cleanup.clean(item).item, item)

    def test_freed_bytes_never_go_negative(self) -> None:
        directory = self.cache("pip", size=MEDIUM)
        item = cleanup.Item("pip", "pip", "safe", 0, "n", paths=(directory,))
        self.assertEqual(cleanup.clean(item).freed_bytes, 0)

    def test_never_runs_a_command_for_a_path_item(self) -> None:
        directory = self.cache("pip", size=MEDIUM)
        with mock.patch.object(cleanup, "_execute") as execute:
            cleanup.clean(self.item(directory))
        execute.assert_not_called()

    def test_does_not_follow_symlinks_out_of_the_directory(self) -> None:
        directory = self.cache("pip", size=MEDIUM)
        keep = write(self.root / "outside" / "keep.bin", 5_000)
        (directory / "link").symlink_to(keep.parent, target_is_directory=True)
        outcome = cleanup.clean(self.item(directory))
        self.assertTrue(outcome.ok, outcome.error)
        self.assertTrue(keep.exists())

    def test_a_directory_reached_through_a_link_is_emptied_in_place(self) -> None:
        real = self.fill(self.root, "relocated", "pip", size=MEDIUM)
        link = self.root / "home" / "user" / ".cache" / "pip"
        link.parent.mkdir(parents=True)
        link.symlink_to(real, target_is_directory=True)
        outcome = cleanup.clean(self.item(link))
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(list(real.iterdir()), [])
        self.assertTrue(real.is_dir())

    def test_a_refused_directory_fails_the_item_without_deleting(self) -> None:
        item = cleanup.Item("pip", "pip", "safe", 100, "n", paths=(Path("/"),))
        with (
            mock.patch.object(shutil, "rmtree") as rmtree,
            mock.patch.object(os, "unlink") as unlink,
            mock.patch.object(os, "remove") as remove,
            mock.patch.object(os, "rmdir") as rmdir,
            mock.patch.object(pathlib.Path, "unlink") as path_unlink,
            mock.patch.object(pathlib.Path, "rmdir") as path_rmdir,
        ):
            outcome = cleanup.clean(item)
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.error)
        self.assertEqual(outcome.freed_bytes, 0)
        for deleter in (rmtree, unlink, remove, rmdir, path_unlink, path_rmdir):
            deleter.assert_not_called()

    def test_one_refused_directory_fails_the_item_but_the_other_is_still_emptied(self) -> None:
        good = self.cache("pip", size=MEDIUM)
        try:
            shallow = make_shallow_directory(self)
        except OSError as exc:
            self.skipTest(f"cannot create a directory next to the filesystem root: {exc}")
        keep = write(shallow / "keep.txt", 10)
        item = cleanup.Item("x", "x", "safe", 1, "n", paths=(shallow, good))
        outcome = cleanup.clean(item)
        self.assertFalse(outcome.ok)
        self.assertTrue(keep.exists())
        self.assertEqual(list(good.iterdir()), [])


class CleanCommandItemTests(CleanupCase):
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
        self.installed.add("sudo")

    def command_item(self, item_id: str, tier: str, command: tuple[str, ...], size: int = 2_000_000_000) -> cleanup.Item:
        return cleanup.Item(item_id, item_id, tier, size, "note", command=command)

    def test_runs_exactly_the_documented_command(self) -> None:
        commands = {
            "uv": ("uv", "cache", "clean"),
            "npm": ("npm", "cache", "clean", "--force"),
            "docker-build-cache": ("docker", "builder", "prune", "--all", "--force"),
            "docker-containers": ("docker", "container", "prune", "--force"),
            "docker-images": ("docker", "image", "prune", "--all", "--force"),
        }
        self.docker_output = ""
        for item_id, command in commands.items():
            with self.subTest(item_id=item_id):
                self.executed.clear()
                outcome = cleanup.clean(self.command_item(item_id, "safe", command))
                self.assertEqual(self.executed, [list(command)])
                self.assertTrue(outcome.ok, outcome.error)

    def test_root_items_are_prefixed_with_sudo(self) -> None:
        commands = [
            ("journalctl", "--vacuum-size=500M"),
            ("dnf", "clean", "all"),
            ("apt-get", "clean"),
        ]
        for command in commands:
            with self.subTest(command=command):
                self.executed.clear()
                cleanup.clean(self.command_item("x", "root", command))
                self.assertEqual(self.executed, [["sudo", *command]])

    def test_root_items_use_sudo_n_when_not_allowed_to_ask(self) -> None:
        self.executed.clear()
        cleanup.clean(self.command_item("journal", "root", ("journalctl", "--vacuum-size=500M")), ask_password=False)
        self.assertEqual(self.executed, [["sudo", "-n", "journalctl", "--vacuum-size=500M"]])

    def test_no_sudo_is_added_when_already_root(self) -> None:
        self.is_root = True
        for ask in (True, False):
            with self.subTest(ask_password=ask):
                self.executed.clear()
                cleanup.clean(self.command_item("dnf", "root", ("dnf", "clean", "all")), ask_password=ask)
                self.assertEqual(self.executed, [["dnf", "clean", "all"]])

    def test_non_root_items_never_get_sudo(self) -> None:
        for tier in ("safe", "review"):
            for ask in (True, False):
                with self.subTest(tier=tier, ask_password=ask):
                    self.executed.clear()
                    cleanup.clean(self.command_item("uv", tier, ("uv", "cache", "clean")), ask_password=ask)
                    self.assertEqual(self.executed, [["uv", "cache", "clean"]])

    def test_command_wins_over_paths(self) -> None:
        directory = self.cache("uv", size=MEDIUM)
        item = cleanup.Item("uv", "uv", "safe", MEDIUM, "n", paths=(directory,), command=("uv", "cache", "clean"))
        cleanup.clean(item)
        self.assertEqual(self.executed, [["uv", "cache", "clean"]])
        self.assertTrue((directory / "data.bin").exists(), "the directory must be left to the tool")

    def test_paths_of_a_command_item_only_measure_what_was_freed(self) -> None:
        directory = self.cache("uv", size=MEDIUM)
        before = cleanup.directory_size(directory)
        item = cleanup.Item("uv", "uv", "safe", before, "n", paths=(directory,), command=("uv", "cache", "clean"))
        self.result = (0, "")
        self.executed.clear()

        def execute(command: object) -> tuple[int, str]:
            self.executed.append(list(command))  # type: ignore[call-overload]
            (directory / "data.bin").unlink()
            return 0, ""

        with mock.patch.object(cleanup, "_execute", side_effect=execute):
            outcome = cleanup.clean(item)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertGreaterEqual(outcome.freed_bytes, MEDIUM)

    def test_a_failing_command_is_a_failed_outcome_with_the_error_line(self) -> None:
        self.result = (1, "permission denied while talking to the Docker daemon")
        item = self.command_item("docker-images", "review", ("docker", "image", "prune", "--all", "--force"))
        outcome = cleanup.clean(item)
        self.assertFalse(outcome.ok)
        self.assertIn("permission denied", outcome.error)
        self.assertEqual(outcome.freed_bytes, 0)
        self.assertEqual(outcome.item, item)

    def test_a_failing_command_without_output_still_explains_itself(self) -> None:
        self.result = (3, "")
        outcome = cleanup.clean(self.command_item("uv", "safe", ("uv", "cache", "clean")))
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.error.strip())

    def test_a_missing_sudo_password_fails_with_sudo_n(self) -> None:
        self.result = (1, "sudo: a password is required")
        outcome = cleanup.clean(
            self.command_item("journal", "root", ("journalctl", "--vacuum-size=500M")), ask_password=False
        )
        self.assertFalse(outcome.ok)
        self.assertIn("password", outcome.error)

    def test_docker_freed_bytes_are_the_drop_in_reclaimable_figure(self) -> None:
        self.installed.add("docker")
        self.docker_output = '{"Type":"Build Cache","Reclaimable":"500MB"}\n'
        item = self.command_item(
            "docker-build-cache", "safe", ("docker", "builder", "prune", "--all", "--force"), size=2_000_000_000
        )
        outcome = cleanup.clean(item)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertAlmostEqual(outcome.freed_bytes, 1_500_000_000, delta=1_000)

    def test_docker_images_are_measured_from_the_images_row(self) -> None:
        self.installed.add("docker")
        self.docker_output = (
            '{"Type":"Images","Reclaimable":"1GB (10%)"}\n'
            '{"Type":"Build Cache","Reclaimable":"0B"}\n'
        )
        item = self.command_item("docker-images", "review", ("docker", "image", "prune", "--all", "--force"), size=3_000_000_000)
        self.assertAlmostEqual(cleanup.clean(item).freed_bytes, 2_000_000_000, delta=1_000)

    def test_journal_freed_bytes_come_from_the_new_disk_usage(self) -> None:
        self.installed.add("journalctl")
        self.journal_output = "Archived and active journals take up 524.0M in the file system.\n"
        item = self.command_item("journal", "root", ("journalctl", "--vacuum-size=500M"), size=1000 * MIB)
        outcome = cleanup.clean(item)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertAlmostEqual(outcome.freed_bytes, 976 * MIB, delta=1_000)

    def test_freed_bytes_never_go_negative_for_commands(self) -> None:
        self.installed.add("docker")
        self.docker_output = '{"Type":"Build Cache","Reclaimable":"9GB"}\n'
        item = self.command_item("docker-build-cache", "safe", ("docker", "builder", "prune", "--all", "--force"), size=1_000)
        self.assertEqual(cleanup.clean(item).freed_bytes, 0)


if __name__ == "__main__":
    unittest.main()
