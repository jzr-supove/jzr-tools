"""Acceptance tests for ``jzr_tools.core.procs``."""

from __future__ import annotations

import importlib
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import psutil

POSIX = os.name == "posix"
MIB = 1024**2

# The faked tests here describe the POSIX rules (SIGTERM first, the Linux
# session names). The Windows rules are pinned in test_procs_windows, whose
# tests patch the platform themselves.
_posix_rules = mock.patch("jzr_tools.core.procs._windows", return_value=False)


def setUpModule() -> None:  # noqa: N802
    _posix_rules.start()


def tearDownModule() -> None:  # noqa: N802
    _posix_rules.stop()


def load():
    """Import lazily so every test fails on its own while the module is missing."""
    return importlib.import_module("jzr_tools.core.procs")


def make_proc(pid: int = 100, **overrides):
    procs = load()
    values = dict(
        pid=pid,
        name="app",
        exe="/usr/bin/app",
        user="alice",
        command="/usr/bin/app --run",
        memory_bytes=10 * MIB,
        swap_bytes=0,
        started=1000.0,
        protected="",
    )
    values.update(overrides)
    return procs.Proc(**values)


class ProcsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.procs = load()


# --------------------------------------------------------------------------
# group_processes and Group (pure)
# --------------------------------------------------------------------------


class GroupingTests(ProcsTestCase):
    def test_same_user_and_executable_form_one_group(self) -> None:
        groups = self.procs.group_processes(
            [make_proc(1), make_proc(2), make_proc(3, exe="/usr/bin/other")]
        )

        by_name = {g.name: g for g in groups}
        self.assertEqual(sorted(by_name), ["app", "other"])
        self.assertEqual(sorted(by_name["app"].pids), [1, 2])
        self.assertEqual(by_name["other"].pids, (3,))

    def test_same_executable_for_different_users_is_two_groups(self) -> None:
        groups = self.procs.group_processes(
            [make_proc(1, user="alice"), make_proc(2, user="bob")]
        )

        self.assertEqual(len(groups), 2)
        self.assertEqual({g.user for g in groups}, {"alice", "bob"})
        self.assertEqual(sorted(p for g in groups for p in g.pids), [1, 2])

    def test_same_file_name_in_different_directories_is_not_merged(self) -> None:
        groups = self.procs.group_processes(
            [make_proc(1, exe="/usr/bin/tool"), make_proc(2, exe="/opt/tool/tool")]
        )

        self.assertEqual(len(groups), 2)

    def test_unreadable_executable_groups_by_process_name(self) -> None:
        groups = self.procs.group_processes(
            [
                make_proc(1, name="helper", exe=""),
                make_proc(2, name="helper", exe=""),
                make_proc(3, name="other", exe=""),
            ]
        )

        by_name = {g.name: g for g in groups}
        self.assertEqual(sorted(by_name), ["helper", "other"])
        self.assertEqual(sorted(by_name["helper"].pids), [1, 2])
        self.assertEqual(by_name["other"].pids, (3,))

    def test_name_is_the_executable_file_name_not_the_process_name(self) -> None:
        (group,) = self.procs.group_processes(
            [make_proc(1, name="MainThread", exe="/opt/browser/bin/browser-bin")]
        )

        self.assertEqual(group.name, "browser-bin")

    def test_name_keeps_digits_and_dots_when_it_has_letters(self) -> None:
        (group,) = self.procs.group_processes([make_proc(1, exe="/usr/bin/python3.12")])

        self.assertEqual(group.name, "python3.12")

    def test_name_without_letters_uses_the_oldest_process_name(self) -> None:
        for exe in ("/home/a/.local/share/app/versions/1.2.3", "/opt/app/2025"):
            with self.subTest(exe=exe):
                groups = self.procs.group_processes(
                    [
                        make_proc(1, name="young", exe=exe, started=3000.0, memory_bytes=50 * MIB),
                        make_proc(2, name="oldest", exe=exe, started=1000.0),
                        make_proc(3, name="middle", exe=exe, started=2000.0),
                    ]
                )

                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0].name, "oldest")

    def test_groups_are_ordered_by_memory_largest_first(self) -> None:
        groups = self.procs.group_processes(
            [
                make_proc(1, exe="/bin/small", memory_bytes=5 * MIB),
                # Two modest processes outweigh one medium one.
                make_proc(2, exe="/bin/big", memory_bytes=40 * MIB),
                make_proc(3, exe="/bin/big", memory_bytes=40 * MIB),
                make_proc(4, exe="/bin/medium", memory_bytes=60 * MIB),
            ]
        )

        self.assertEqual([g.name for g in groups], ["big", "medium", "small"])

    def test_processes_inside_a_group_are_largest_first(self) -> None:
        (group,) = self.procs.group_processes(
            [
                make_proc(1, memory_bytes=5 * MIB),
                make_proc(2, memory_bytes=30 * MIB),
                make_proc(3, memory_bytes=10 * MIB),
            ]
        )

        self.assertEqual([p.pid for p in group.processes], [2, 3, 1])
        self.assertEqual(group.pids, (2, 3, 1))

    def test_no_processes_means_no_groups(self) -> None:
        self.assertEqual(self.procs.group_processes([]), [])

    def test_accepts_any_iterable(self) -> None:
        groups = self.procs.group_processes(make_proc(i) for i in (1, 2))

        self.assertEqual(sorted(groups[0].pids), [1, 2])


class GroupPropertyTests(ProcsTestCase):
    def build(self, *processes):
        return self.procs.Group(name="app", user="alice", processes=tuple(processes))

    def test_memory_and_swap_are_sums(self) -> None:
        group = self.build(
            make_proc(1, memory_bytes=10 * MIB, swap_bytes=1 * MIB),
            make_proc(2, memory_bytes=32 * MIB, swap_bytes=3 * MIB),
        )

        self.assertEqual(group.memory_bytes, 42 * MIB)
        self.assertEqual(group.swap_bytes, 4 * MIB)

    def test_pids_follow_the_process_order(self) -> None:
        group = self.build(make_proc(7), make_proc(3), make_proc(9))

        self.assertEqual(group.pids, (7, 3, 9))

    def test_endable_drops_protected_processes_and_keeps_order(self) -> None:
        keep_a, mine, keep_b = make_proc(1), make_proc(2, protected="this terminal"), make_proc(3)
        group = self.build(keep_a, mine, keep_b)

        self.assertEqual(group.endable, (keep_a, keep_b))

    def test_endable_is_empty_when_everything_is_protected(self) -> None:
        group = self.build(make_proc(1, protected="system"), make_proc(2, protected="system"))

        self.assertEqual(group.endable, ())

    def test_protected_is_the_reason_only_when_every_process_is_protected(self) -> None:
        self.assertEqual(
            self.build(make_proc(1, protected="system"), make_proc(2, protected="system")).protected,
            "system",
        )
        self.assertEqual(
            self.build(make_proc(1, protected="system"), make_proc(2)).protected, ""
        )
        self.assertEqual(self.build(make_proc(1), make_proc(2)).protected, "")

    def test_protected_reason_survives_grouping(self) -> None:
        groups = self.procs.group_processes(
            [
                make_proc(1, exe="/usr/lib/systemd/systemd", protected="system"),
                make_proc(2, exe="/usr/lib/systemd/systemd", protected="system"),
                make_proc(3, exe="/usr/bin/vim"),
            ]
        )

        by_name = {g.name: g for g in groups}
        self.assertEqual(by_name["systemd"].protected, "system")
        self.assertEqual(by_name["vim"].protected, "")


class ConstantsTests(ProcsTestCase):
    def test_grace_period_is_five_seconds(self) -> None:
        self.assertEqual(self.procs.GRACE_SECONDS, 5.0)

    def test_session_names_are_exactly_the_specified_list(self) -> None:
        self.assertEqual(
            set(self.procs.SESSION_NAMES),
            {
                "systemd", "gnome-shell", "gnome-session-binary", "plasmashell",
                "kwin_wayland", "kwin_x11", "Xorg", "Xwayland", "dbus-daemon",
                "dbus-broker", "pipewire", "pipewire-pulse", "wireplumber", "sshd",
                "login", "gdm", "sddm", "lightdm",
            },
        )


# --------------------------------------------------------------------------
# snapshot with psutil faked
# --------------------------------------------------------------------------

ME = 5000
PARENT = 4000
GRANDPARENT = 3000


def current_user() -> str:
    import getpass

    return getpass.getuser()


class FakeProcess:
    """A stand-in for ``psutil.Process`` that works both as ``.info`` and as methods."""

    def __init__(
        self,
        pid: int,
        *,
        name: str = "thing",
        exe: str | None = "/usr/bin/thing",
        cmdline: list[str] | None = None,
        username: str | None = None,
        ppid: int = 1,
        created: float = 1000.0,
        rss: int = 20 * MIB,
        pss: int | None = None,
        uss: int | None = None,
        swap: int | None = None,
        full_info_error: Exception | None = None,
        info_error: Exception | None = None,
        exe_error: Exception | None = None,
        cmdline_error: Exception | None = None,
        vanished: bool = False,
    ) -> None:
        self.pid = pid
        self._name = name
        self._exe = exe
        self._cmdline = ["/usr/bin/thing", "--flag"] if cmdline is None else cmdline
        self._username = current_user() if username is None else username
        self._ppid = ppid
        self._created = created
        self._rss = rss
        self._pss, self._uss, self._swap = pss, uss, swap
        self._full_info_error = full_info_error
        self._info_error = info_error
        self._exe_error = exe_error
        self._cmdline_error = cmdline_error
        self._vanished = vanished

    def _gone(self):
        if self._vanished:
            raise psutil.NoSuchProcess(self.pid)

    def name(self):
        self._gone()
        return self._name

    def exe(self):
        self._gone()
        if self._exe_error:
            raise self._exe_error
        return self._exe or ""

    def cmdline(self):
        self._gone()
        if self._cmdline_error:
            raise self._cmdline_error
        return list(self._cmdline)

    def username(self):
        self._gone()
        return self._username

    def ppid(self):
        self._gone()
        return self._ppid

    def create_time(self):
        self._gone()
        return self._created

    def memory_info(self):
        self._gone()
        if self._info_error:
            raise self._info_error
        return SimpleNamespace(rss=self._rss, vms=self._rss * 2)

    def memory_full_info(self):
        self._gone()
        if self._full_info_error:
            raise self._full_info_error
        fields = {"rss": self._rss}
        if self._pss is not None:
            fields["pss"] = self._pss
        if self._uss is not None:
            fields["uss"] = self._uss
        if self._swap is not None:
            fields["swap"] = self._swap
        return SimpleNamespace(**fields)

    def parents(self):
        return []

    @property
    def info(self):
        self._gone()

        def safe(call):
            try:
                return call()
            except psutil.Error:
                return None

        return {
            "pid": self.pid,
            "name": safe(self.name),
            "exe": safe(self.exe),
            "cmdline": safe(self.cmdline),
            "username": safe(self.username),
            "ppid": safe(self.ppid),
            "create_time": safe(self.create_time),
            "memory_info": safe(self.memory_info),
            "memory_full_info": safe(self.memory_full_info),
        }


@unittest.skipUnless(POSIX, "uses POSIX user and process semantics")
class SnapshotTests(ProcsTestCase):
    """Relies on ``psutil.process_iter(attrs)`` + ``.info``; the current
    process is ``psutil.Process()`` with ``.parents()`` and ``.username()``, and
    root is ``procs._is_root()``. ``procs._swap_share(pid)`` is patched (``None``
    by default) so real /proc data of look-alike PIDs never leaks in."""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(self.procs, "_swap_share", return_value=None, create=True)
        self.swap_share = patcher.start()
        self.addCleanup(patcher.stop)

    def run_snapshot(self, fakes, *, root: bool = False):
        table = {f.pid: f for f in fakes}

        def lookup(pid=None):
            if pid is None:
                pid = ME
            if pid not in table:
                raise psutil.NoSuchProcess(pid)
            fake = table[pid]
            # nearest ancestor first, as psutil does
            fake.parents = lambda: [table[p] for p in (PARENT, GRANDPARENT) if p in table]
            return fake

        with mock.patch.object(self.procs.psutil, "process_iter", side_effect=lambda *a, **k: iter(fakes)), \
             mock.patch.object(self.procs.psutil, "Process", side_effect=lookup), \
             mock.patch("os.getpid", return_value=ME), \
             mock.patch.object(self.procs, "_is_root", return_value=root):
            result = self.procs.snapshot()
        return {p.pid: p for p in result}

    def base_fakes(self, *extra):
        return [
            FakeProcess(1, name="systemd", exe="/usr/lib/systemd/systemd", ppid=0),
            FakeProcess(GRANDPARENT, name="gnome-terminal-", exe="/usr/libexec/gnome-terminal-server", ppid=1),
            FakeProcess(PARENT, name="bash", exe="/usr/bin/bash", ppid=GRANDPARENT),
            FakeProcess(ME, name="python3", exe="/usr/bin/python3", ppid=PARENT),
            *extra,
        ]

    # -- fields ---------------------------------------------------------

    def test_fields_are_copied_from_the_process(self) -> None:
        fake = FakeProcess(
            700,
            name="editor",
            exe="/usr/bin/editor",
            cmdline=["/usr/bin/editor", "--file", "a b.txt"],
            created=1234.5,
            pss=12 * MIB,
            swap=2 * MIB,
        )

        proc = self.run_snapshot(self.base_fakes(fake))[700]

        self.assertEqual(proc.name, "editor")
        self.assertEqual(proc.exe, "/usr/bin/editor")
        self.assertEqual(proc.user, current_user())
        self.assertEqual(proc.command, "/usr/bin/editor --file a b.txt")
        self.assertEqual(proc.started, 1234.5)
        self.assertEqual(proc.memory_bytes, 12 * MIB)
        self.assertEqual(proc.swap_bytes, 2 * MIB)

    # -- memory metric --------------------------------------------------

    def test_memory_prefers_pss_over_uss_and_rss(self) -> None:
        fake = FakeProcess(700, rss=90 * MIB, uss=40 * MIB, pss=25 * MIB)

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].memory_bytes, 25 * MIB)

    def test_memory_falls_back_to_uss_when_there_is_no_pss(self) -> None:
        fake = FakeProcess(700, rss=90 * MIB, uss=40 * MIB)

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].memory_bytes, 40 * MIB)

    def test_memory_falls_back_to_rss_when_full_info_is_denied(self) -> None:
        fake = FakeProcess(
            700, rss=90 * MIB, full_info_error=psutil.AccessDenied(700), username="someone-else"
        )

        proc = self.run_snapshot(self.base_fakes(fake))[700]

        self.assertEqual(proc.memory_bytes, 90 * MIB)
        self.assertEqual(proc.swap_bytes, 0)

    def test_memory_falls_back_to_rss_when_full_info_has_only_rss(self) -> None:
        fake = FakeProcess(700, rss=77 * MIB)

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].memory_bytes, 77 * MIB)

    def test_swap_is_zero_when_the_system_does_not_report_it(self) -> None:
        fake = FakeProcess(700, pss=10 * MIB)

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].swap_bytes, 0)

    # -- proportional swap share ----------------------------------------

    def test_swap_uses_the_proportional_share_instead_of_the_swap_attribute(self) -> None:
        fake = FakeProcess(700, pss=10 * MIB, swap=9 * MIB)
        self.swap_share.side_effect = lambda pid: 3 * MIB if pid == 700 else None

        result = self.run_snapshot(self.base_fakes(fake))

        self.assertEqual(result[700].swap_bytes, 3 * MIB)
        self.swap_share.assert_any_call(700)

    def test_a_proportional_share_of_zero_is_used_not_treated_as_missing(self) -> None:
        fake = FakeProcess(700, pss=10 * MIB, swap=9 * MIB)
        self.swap_share.side_effect = lambda pid: 0 if pid == 700 else None

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].swap_bytes, 0)

    def test_swap_attribute_is_used_when_the_share_is_unavailable(self) -> None:
        fake = FakeProcess(700, pss=10 * MIB, swap=9 * MIB)
        self.swap_share.return_value = None

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].swap_bytes, 9 * MIB)

    def test_swap_is_zero_without_share_or_attribute(self) -> None:
        fake = FakeProcess(700, pss=10 * MIB)
        self.swap_share.return_value = None

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].swap_bytes, 0)

    def test_share_is_not_asked_for_when_full_info_was_denied(self) -> None:
        fake = FakeProcess(
            700, rss=90 * MIB, full_info_error=psutil.AccessDenied(700), username="someone-else"
        )
        self.swap_share.return_value = 5 * MIB  # would be wrong to use

        proc = self.run_snapshot(self.base_fakes(fake))[700]

        self.assertEqual(proc.swap_bytes, 0)
        self.assertEqual(proc.memory_bytes, 90 * MIB)
        for call in self.swap_share.call_args_list:
            self.assertNotEqual(call.args, (700,))

    def test_the_share_does_not_change_the_memory_figure(self) -> None:
        fake = FakeProcess(700, rss=90 * MIB, uss=40 * MIB, pss=25 * MIB)
        self.swap_share.return_value = 1 * MIB

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].memory_bytes, 25 * MIB)

    # -- what is left out / degraded ------------------------------------

    def test_kernel_threads_are_left_out(self) -> None:
        kthread = FakeProcess(2, name="kthreadd", exe="", cmdline=[], ppid=0)
        worker = FakeProcess(99, name="kworker/0:1", exe="", cmdline=[], ppid=2)
        real = FakeProcess(700)

        result = self.run_snapshot(self.base_fakes(kthread, worker, real))

        self.assertNotIn(2, result)
        self.assertNotIn(99, result)
        self.assertIn(700, result)

    def test_process_without_a_readable_executable_is_kept_with_an_empty_exe(self) -> None:
        fake = FakeProcess(
            700, name="guarded", exe=None, exe_error=psutil.AccessDenied(700), username="root"
        )

        proc = self.run_snapshot(self.base_fakes(fake))[700]

        self.assertEqual(proc.exe, "")
        self.assertEqual(proc.name, "guarded")

    def test_process_without_a_readable_command_line_has_an_empty_command(self) -> None:
        fake = FakeProcess(700, cmdline_error=psutil.AccessDenied(700))

        proc = self.run_snapshot(self.base_fakes(fake))[700]

        self.assertEqual(proc.command, "")

    def test_a_process_that_vanishes_is_skipped_without_raising(self) -> None:
        gone = FakeProcess(700, vanished=True)
        stays = FakeProcess(701)

        result = self.run_snapshot(self.base_fakes(gone, stays))

        self.assertNotIn(700, result)
        self.assertIn(701, result)

    def test_a_process_whose_memory_is_unreadable_does_not_break_the_snapshot(self) -> None:
        stubborn = FakeProcess(
            700,
            username="someone-else",
            full_info_error=psutil.AccessDenied(700),
            info_error=psutil.AccessDenied(700),
        )
        stays = FakeProcess(701)

        result = self.run_snapshot(self.base_fakes(stubborn, stays))

        self.assertIn(701, result)

    # -- protection -----------------------------------------------------

    def test_current_process_and_its_ancestors_are_this_terminal(self) -> None:
        result = self.run_snapshot(self.base_fakes(FakeProcess(700)))

        for pid in (ME, PARENT, GRANDPARENT):
            with self.subTest(pid=pid):
                self.assertEqual(result[pid].protected, "this terminal")

    def test_unrelated_process_of_the_same_user_is_not_protected(self) -> None:
        result = self.run_snapshot(self.base_fakes(FakeProcess(700)))

        self.assertEqual(result[700].protected, "")

    def test_a_child_of_the_current_process_is_not_protected(self) -> None:
        result = self.run_snapshot(self.base_fakes(FakeProcess(700, ppid=ME)))

        self.assertEqual(result[700].protected, "")

    def test_pid_one_is_system(self) -> None:
        result = self.run_snapshot(self.base_fakes(FakeProcess(700)))

        self.assertEqual(result[1].protected, "system")

    def test_session_name_also_matches_the_executable_file_name(self) -> None:
        fake = FakeProcess(700, name="renamed-title", exe="/usr/bin/gnome-shell")

        self.assertEqual(self.run_snapshot(self.base_fakes(fake))[700].protected, "system")

    def test_every_session_process_name_is_system(self) -> None:
        names = sorted(self.procs.SESSION_NAMES)
        fakes = [FakeProcess(800 + i, name=n, exe=f"/usr/bin/{n}") for i, n in enumerate(names)]

        result = self.run_snapshot(self.base_fakes(*fakes))

        for i, name in enumerate(names):
            with self.subTest(name=name):
                self.assertEqual(result[800 + i].protected, "system")

    def test_a_name_that_only_resembles_a_session_name_is_not_protected(self) -> None:
        result = self.run_snapshot(self.base_fakes(FakeProcess(700, name="systemd-fake", exe="/x/systemd-fake")))

        self.assertEqual(result[700].protected, "")

    def test_other_users_processes_are_protected_for_a_normal_user(self) -> None:
        result = self.run_snapshot(self.base_fakes(FakeProcess(700, username="someone-else")))

        self.assertEqual(result[700].protected, "another user")
        self.assertEqual(result[700].user, "someone-else")

    def test_other_users_processes_are_not_protected_for_root(self) -> None:
        result = self.run_snapshot(
            self.base_fakes(
                FakeProcess(700, username="someone-else"),
                FakeProcess(701, name="sshd", exe="/usr/sbin/sshd", username="root"),
            ),
            root=True,
        )

        self.assertEqual(result[700].protected, "")
        self.assertEqual(result[701].protected, "system")  # root does not lift the system list
        self.assertEqual(result[ME].protected, "this terminal")


@unittest.skipUnless(POSIX, "uses POSIX processes")
class RealSnapshotTests(ProcsTestCase):
    def test_contains_the_current_process_marked_as_this_terminal(self) -> None:
        by_pid = {p.pid: p for p in self.procs.snapshot()}

        me = by_pid[os.getpid()]
        self.assertEqual(me.protected, "this terminal")
        self.assertGreater(me.memory_bytes, 0)
        self.assertGreater(me.started, 0)
        self.assertTrue(me.name)

    def test_the_parent_of_this_process_is_protected_too(self) -> None:
        by_pid = {p.pid: p for p in self.procs.snapshot()}

        parent = by_pid.get(os.getppid())
        if parent is None:
            self.skipTest("parent process not visible")
        self.assertNotEqual(parent.protected, "", "an ancestor must never be endable")

    def test_every_entry_is_a_well_formed_proc(self) -> None:
        processes = self.procs.snapshot()

        self.assertGreater(len(processes), 1)
        for p in processes:
            self.assertIsInstance(p.pid, int)
            self.assertGreaterEqual(p.memory_bytes, 0)
            self.assertGreaterEqual(p.swap_bytes, 0)
            self.assertIn(p.protected, ("", "this terminal", "system", "another user"))
        pid1 = [p for p in processes if p.pid == 1]
        if pid1:
            self.assertNotEqual(pid1[0].protected, "")

    @unittest.skipUnless(sys.platform.startswith("linux"), "proportional swap is Linux-only")
    def test_summed_swap_does_not_exceed_the_swap_in_use(self) -> None:
        tolerance = 64 * MIB
        total = sum(p.swap_bytes for p in self.procs.snapshot())

        used = self.procs.memory_totals()["swap_used_bytes"]

        self.assertLessEqual(total, used + tolerance)


@unittest.skipUnless(sys.platform.startswith("linux"), "reads /proc")
class SwapShareTests(ProcsTestCase):
    def test_the_current_process_has_a_non_negative_integer_share(self) -> None:
        share = self.procs._swap_share(os.getpid())

        self.assertIsInstance(share, int)
        self.assertNotIsInstance(share, bool)
        self.assertGreaterEqual(share, 0)

    def test_a_pid_that_does_not_exist_gives_none(self) -> None:
        pid = 2**22 + 12345  # above the kernel's PID limit
        self.assertFalse(psutil.pid_exists(pid))

        self.assertIsNone(self.procs._swap_share(pid))

    def test_a_reaped_child_gives_none(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()

        self.assertIsNone(self.procs._swap_share(child.pid))

    def test_share_is_in_bytes_not_kibibytes(self) -> None:
        with open("/proc/self/smaps_rollup") as handle:
            rollup = handle.read()
        if "SwapPss" not in rollup:
            self.skipTest("kernel does not report SwapPss")
        # Compare against the kernel's own figure, read moments earlier.
        reported = int(next(l for l in rollup.splitlines() if l.startswith("SwapPss:")).split()[1]) * 1024

        share = self.procs._swap_share(os.getpid())

        # Both are read moments apart; allow a small drift but not a 1024x unit error.
        self.assertLessEqual(abs(share - reported), 8 * MIB)


# --------------------------------------------------------------------------
# end_processes / kill_processes with psutil faked
# --------------------------------------------------------------------------


class Target:
    """A fake ``psutil.Process`` that can be signalled."""

    def __init__(self, pid, started=1000.0, *, survives=False, deny=False, vanish_on_signal=False):
        self.pid = pid
        self.started = started
        self.survives = survives
        self.deny = deny
        self.vanish_on_signal = vanish_on_signal
        self.alive = True
        self.signals: list[str] = []

    def create_time(self):
        if not self.alive and not self.survives:
            raise psutil.NoSuchProcess(self.pid)
        return self.started

    def _signal(self, which):
        self.signals.append(which)
        if self.deny:
            raise psutil.AccessDenied(self.pid)
        if self.vanish_on_signal:
            self.alive = False
            raise psutil.NoSuchProcess(self.pid)
        if not self.survives:
            self.alive = False

    def terminate(self):
        self._signal("term")

    def kill(self):
        self._signal("kill")

    def send_signal(self, number):
        self._signal("term" if number == getattr(signal, "SIGTERM", 15) else "kill")

    def is_running(self):
        return self.alive

    def status(self):
        return "sleeping" if self.alive else "dead"

    def wait(self, timeout=None):
        if self.alive:
            raise psutil.TimeoutExpired(timeout or 0, self.pid)
        return 0


class SignalTests:
    """Shared by end_processes (SIGTERM) and kill_processes (SIGKILL)."""

    function_name: str
    signal_name: str

    def setUp(self) -> None:  # noqa: N802
        self.procs = load()

    def run_signal(self, targets, processes, grace=0.05):
        table = {t.pid: t for t in targets}

        def lookup(pid):
            if pid not in table:
                raise psutil.NoSuchProcess(pid)
            return table[pid]

        def wait_procs(items, timeout=None, callback=None):
            gone = [p for p in items if not p.is_running()]
            alive = [p for p in items if p.is_running()]
            return gone, alive

        function = getattr(self.procs, self.function_name)
        with mock.patch.object(self.procs.psutil, "Process", side_effect=lookup), \
             mock.patch.object(self.procs.psutil, "wait_procs", side_effect=wait_procs):
            return function(processes, grace=grace)

    @staticmethod
    def pids(items):
        return sorted(p.pid for p in items)

    def test_every_unprotected_process_is_signalled_and_reported_ended(self) -> None:
        targets = [Target(1), Target(2)]
        processes = [make_proc(1), make_proc(2)]

        result = self.run_signal(targets, processes)

        self.assertEqual([t.signals for t in targets], [[self.signal_name]] * 2)
        self.assertEqual(self.pids(result.ended), [1, 2])
        self.assertEqual(result.survivors, ())
        self.assertEqual(result.failed, ())

    def test_protected_processes_are_never_signalled(self) -> None:
        targets = [Target(1), Target(2), Target(3), Target(4)]
        processes = [
            make_proc(1),
            make_proc(2, protected="this terminal"),
            make_proc(3, protected="system"),
            make_proc(4, protected="another user"),
        ]

        result = self.run_signal(targets, processes)

        self.assertEqual([t.signals for t in targets], [[self.signal_name], [], [], []])
        # Protected processes passed in appear in none of the three results.
        self.assertEqual(self.pids(result.ended), [1])
        self.assertEqual(result.survivors, ())
        self.assertEqual(result.failed, ())

    def test_a_recycled_pid_is_not_signalled(self) -> None:
        target = Target(1, started=9000.0)  # a different process now owns the PID
        processes = [make_proc(1, started=1000.0)]

        result = self.run_signal([target], processes)

        self.assertEqual(target.signals, [])
        # The original process is gone, so it counts as ended.
        self.assertEqual(self.pids(result.ended), [1])
        self.assertEqual(result.survivors, ())
        self.assertEqual(result.failed, ())

    def test_a_start_time_within_a_second_is_the_same_process(self) -> None:
        target = Target(1, started=1000.6)

        result = self.run_signal([target], [make_proc(1, started=1000.0)])

        self.assertEqual(target.signals, [self.signal_name])
        self.assertEqual(self.pids(result.ended), [1])

    def test_only_the_recycled_one_is_skipped_among_several(self) -> None:
        targets = [Target(1), Target(2, started=9000.0), Target(3)]

        result = self.run_signal(targets, [make_proc(1), make_proc(2), make_proc(3)])

        self.assertEqual([t.signals for t in targets], [[self.signal_name], [], [self.signal_name]])
        self.assertEqual(self.pids(result.ended), [1, 2, 3])

    def test_a_process_that_no_longer_exists_counts_as_ended(self) -> None:
        # pid 1 is not in the table: psutil.Process(1) raises NoSuchProcess.
        result = self.run_signal([Target(2)], [make_proc(1), make_proc(2)])

        self.assertEqual(self.pids(result.ended), [1, 2])
        self.assertEqual(result.failed, ())
        self.assertEqual(result.survivors, ())

    def test_a_process_that_exits_while_being_signalled_counts_as_ended(self) -> None:
        target = Target(1, vanish_on_signal=True)

        result = self.run_signal([target], [make_proc(1)])

        self.assertEqual(self.pids(result.ended), [1])
        self.assertEqual(result.failed, ())

    def test_access_denied_goes_to_failed_with_a_reason(self) -> None:
        targets = [Target(1, deny=True), Target(2)]

        result = self.run_signal(targets, [make_proc(1), make_proc(2)])

        self.assertEqual([p.pid for p, _ in result.failed], [1])
        self.assertEqual(result.failed[0][1], "permission denied")
        self.assertEqual(self.pids(result.ended), [2])
        self.assertNotIn(1, self.pids(result.survivors))

    def test_one_failure_does_not_stop_the_others(self) -> None:
        targets = [Target(1, deny=True), Target(2), Target(3)]

        self.run_signal(targets, [make_proc(1), make_proc(2), make_proc(3)])

        self.assertEqual(targets[1].signals, [self.signal_name])
        self.assertEqual(targets[2].signals, [self.signal_name])

    def test_processes_still_running_after_the_wait_are_survivors(self) -> None:
        targets = [Target(1, survives=True), Target(2)]

        result = self.run_signal(targets, [make_proc(1), make_proc(2)])

        self.assertEqual(self.pids(result.survivors), [1])
        self.assertEqual(self.pids(result.ended), [2])
        self.assertEqual(result.failed, ())

    def test_nothing_to_do_gives_an_empty_result(self) -> None:
        result = self.run_signal([], [])

        self.assertEqual((result.ended, result.survivors, result.failed), ((), (), ()))

    def test_result_holds_the_proc_values_that_were_passed(self) -> None:
        proc = make_proc(1, name="thing", memory_bytes=33 * MIB)

        result = self.run_signal([Target(1)], [proc])

        self.assertEqual(result.ended, (proc,))


class EndProcessesTests(SignalTests, unittest.TestCase):
    function_name = "end_processes"
    signal_name = "term"


class KillProcessesTests(SignalTests, unittest.TestCase):
    function_name = "kill_processes"
    signal_name = "kill"


# --------------------------------------------------------------------------
# Real processes
# --------------------------------------------------------------------------

IGNORE_TERM = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "print('ready', flush=True)\n"
    "time.sleep(300)\n"
)
NORMAL = "import sys, time\nprint('ready', flush=True)\ntime.sleep(300)\n"


@unittest.skipUnless(POSIX, "real SIGTERM semantics differ on Windows")
class RealProcessTests(ProcsTestCase):
    def spawn(self, code: str):
        child = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._cleanup, child)
        self.assertEqual(child.stdout.readline().strip(), "ready")
        # Reap as soon as it dies, otherwise a zombie still looks "running".
        threading.Thread(target=child.wait, daemon=True).start()
        return child

    @staticmethod
    def _cleanup(child: subprocess.Popen) -> None:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        child.stdout.close()

    @staticmethod
    def to_proc(child: subprocess.Popen):
        live = psutil.Process(child.pid)
        return make_proc(
            child.pid,
            name=live.name(),
            exe=live.exe(),
            command=" ".join(live.cmdline()),
            memory_bytes=live.memory_info().rss,
            started=live.create_time(),
        )

    @staticmethod
    def gone(pid: int, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not psutil.pid_exists(pid):
                return True
            time.sleep(0.05)
        return not psutil.pid_exists(pid)

    def test_a_cooperative_child_ends_on_the_polite_request(self) -> None:
        child = self.spawn(NORMAL)
        proc = self.to_proc(child)

        result = self.procs.end_processes([proc], grace=3.0)

        self.assertEqual([p.pid for p in result.ended], [child.pid])
        self.assertEqual(result.survivors, ())
        self.assertEqual(result.failed, ())
        self.assertTrue(self.gone(child.pid))

    def test_a_child_ignoring_sigterm_survives_the_polite_request(self) -> None:
        child = self.spawn(IGNORE_TERM)
        proc = self.to_proc(child)

        result = self.procs.end_processes([proc], grace=1.0)

        self.assertEqual([p.pid for p in result.survivors], [child.pid])
        self.assertEqual(result.ended, ())
        self.assertEqual(result.failed, ())
        self.assertTrue(psutil.pid_exists(child.pid))
        self.assertIsNone(child.poll())

    def test_a_child_ignoring_sigterm_dies_under_force(self) -> None:
        child = self.spawn(IGNORE_TERM)
        proc = self.to_proc(child)

        first = self.procs.end_processes([proc], grace=1.0)
        second = self.procs.kill_processes(first.survivors, grace=3.0)

        self.assertEqual([p.pid for p in second.ended], [child.pid])
        self.assertEqual(second.survivors, ())
        self.assertTrue(self.gone(child.pid))

    def test_protected_real_child_is_left_alone(self) -> None:
        child = self.spawn(NORMAL)
        proc = self.to_proc(child)
        protected = make_proc(**{**proc.__dict__, "protected": "system"})

        result = self.procs.end_processes([protected], grace=0.5)
        forced = self.procs.kill_processes([protected], grace=0.5)

        self.assertEqual(result.ended, ())
        self.assertEqual(forced.ended, ())
        self.assertIsNone(child.poll())
        self.assertTrue(psutil.pid_exists(child.pid))

    def test_a_real_process_with_a_different_start_time_is_not_signalled(self) -> None:
        child = self.spawn(NORMAL)
        proc = self.to_proc(child)
        stale = make_proc(**{**proc.__dict__, "started": proc.started - 3600})

        self.procs.end_processes([stale], grace=0.5)
        self.procs.kill_processes([stale], grace=0.5)

        self.assertIsNone(child.poll())
        self.assertTrue(psutil.pid_exists(child.pid))

    def test_snapshot_sees_a_real_child_and_leaves_it_unprotected(self) -> None:
        child = self.spawn(NORMAL)

        by_pid = {p.pid: p for p in self.procs.snapshot()}

        self.assertIn(child.pid, by_pid)
        self.assertEqual(by_pid[child.pid].protected, "")
        self.assertIn("time.sleep", by_pid[child.pid].command)


if __name__ == "__main__":
    unittest.main()
