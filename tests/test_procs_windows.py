"""Acceptance tests for Windows support of ``host procs``.

Seams the tests patch in production code:

``jzr_tools.core.procs``
    ``_windows() -> bool``
        True on Windows.  Called at run time (never cached at import), by
        ``snapshot``, ``end_processes`` and by ``jzr_tools.commands.host_procs``
        (as ``core._windows()``) to decide about the Swap column / ``null`` swap.
    ``_close_windows(pid: int) -> bool``
        Posts WM_CLOSE to every top-level window of the process; returns True
        when at least one top-level window was found (and sent the message).
    ``end_processes`` on Windows: identity check as before, then
    ``_close_windows(process.pid)`` for each chosen process (protected and
    recycled-PID ones are skipped), ``psutil.Process.terminate`` is never
    called.  When at least one process had a window it calls
    ``psutil.wait_procs(all_handles, timeout=grace)`` once; when none had one
    it does not call ``wait_procs`` at all and every still-existing chosen
    process is a survivor at once.
    ``kill_processes`` keeps using ``psutil.Process.kill`` everywhere.

Every other behaviour (Windows owner rules in ``snapshot``, the table without
a Swap column, ``null`` swap in ``--json``) is observed through the existing
public functions: ``snapshot()``, ``render_procs()`` and the ``host procs``
command.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import psutil

import test_host_procs_cli as cli_tests
import test_procs_core as core_tests
from host_fixtures import strip_ansi
from test_host_layout import Parsed

WINDOWS = sys.platform == "win32"
MIB = 1024**2

USER = "PC\\alice"
SESSION_NAMES_WINDOWS = [
    "explorer.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "wininit.exe",
    "services.exe", "lsass.exe", "smss.exe", "sihost.exe", "ctfmon.exe",
    "fontdrvhost.exe", "svchost.exe", "taskhostw.exe", "RuntimeBroker.exe",
    "ShellExperienceHost.exe", "StartMenuExperienceHost.exe", "SearchHost.exe",
    "TextInputHost.exe", "LogonUI.exe", "audiodg.exe",
]


def procs_module():
    import importlib

    return importlib.import_module("jzr_tools.core.procs")


def fake_windows(procs, value: bool):
    """Patch the platform predicate; ``create=True`` because it is not there yet."""
    return mock.patch.object(procs, "_windows", return_value=value, create=True)


# --------------------------------------------------------------------------
# snapshot: who is protected
# --------------------------------------------------------------------------


class UnreadableOwner(core_tests.FakeProcess):
    """A process whose owner cannot be read (psutil raises AccessDenied)."""

    def username(self):
        raise psutil.AccessDenied(self.pid)


class SnapshotMixin:
    windows = True

    def setUp(self) -> None:  # noqa: N802
        self.procs = procs_module()
        for patcher in (
            fake_windows(self.procs, self.windows),
            mock.patch.object(self.procs, "_swap_share", return_value=None, create=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def snapshot(self, *extra, root: bool = False):
        me, parent, grand = core_tests.ME, core_tests.PARENT, core_tests.GRANDPARENT
        fakes = [
            core_tests.FakeProcess(grand, name="WindowsTerminal.exe", exe="/t/WindowsTerminal.exe", username=USER),
            core_tests.FakeProcess(parent, name="pwsh.exe", exe="/t/pwsh.exe", username=USER),
            core_tests.FakeProcess(me, name="python.exe", exe="/t/python.exe", username=USER),
            *extra,
        ]
        table = {f.pid: f for f in fakes}

        def lookup(pid=None):
            pid = me if pid is None else pid
            if pid not in table:
                raise psutil.NoSuchProcess(pid)
            fake = table[pid]
            fake.parents = lambda: [table[p] for p in (parent, grand) if p in table]
            return fake

        with mock.patch.object(self.procs.psutil, "process_iter", side_effect=lambda *a, **k: iter(fakes)), \
             mock.patch.object(self.procs.psutil, "Process", side_effect=lookup), \
             mock.patch.object(self.procs, "_is_root", return_value=root):
            return {p.pid: p for p in self.procs.snapshot()}


class WindowsProtectionTests(SnapshotMixin, unittest.TestCase):
    def test_every_listed_name_is_system_even_for_the_users_own_process(self) -> None:
        fakes = [
            core_tests.FakeProcess(800 + i, name=name, exe="/w/" + name, username=USER)
            for i, name in enumerate(SESSION_NAMES_WINDOWS)
        ]

        result = self.snapshot(*fakes)

        for i, name in enumerate(SESSION_NAMES_WINDOWS):
            with self.subTest(name=name):
                self.assertEqual(result[800 + i].protected, "system")

    def test_names_are_compared_case_insensitively(self) -> None:
        variants = ["Explorer.EXE", "EXPLORER.EXE", "DWM.exe", "svchost.EXE", "runtimebroker.exe", "LSASS.EXE"]
        fakes = [
            core_tests.FakeProcess(800 + i, name=name, exe="/w/" + name, username=USER)
            for i, name in enumerate(variants)
        ]

        result = self.snapshot(*fakes)

        for i, name in enumerate(variants):
            with self.subTest(name=name):
                self.assertEqual(result[800 + i].protected, "system")

    def test_an_ordinary_program_of_the_same_user_is_not_protected(self) -> None:
        result = self.snapshot(core_tests.FakeProcess(700, name="notepad.exe", exe="/w/notepad.exe", username=USER))

        self.assertEqual(result[700].protected, "")

    def test_a_name_that_only_resembles_a_listed_one_is_not_protected(self) -> None:
        result = self.snapshot(
            core_tests.FakeProcess(700, name="explorer.exe.bak", exe="/w/explorer.exe.bak", username=USER),
            core_tests.FakeProcess(701, name="myexplorer.exe", exe="/w/myexplorer.exe", username=USER),
        )

        self.assertEqual(result[700].protected, "")
        self.assertEqual(result[701].protected, "")

    def test_owner_nt_authority_is_system_not_another_user(self) -> None:
        owners = ["NT AUTHORITY\\SYSTEM", "NT AUTHORITY\\LOCAL SERVICE", "NT AUTHORITY\\NETWORK SERVICE"]
        fakes = [
            core_tests.FakeProcess(800 + i, name="agent.exe", exe="/w/agent.exe", username=owner)
            for i, owner in enumerate(owners)
        ]

        result = self.snapshot(*fakes)

        for i, owner in enumerate(owners):
            with self.subTest(owner=owner):
                self.assertEqual(result[800 + i].protected, "system")

    def test_owner_prefix_is_matched_without_regard_to_case(self) -> None:
        result = self.snapshot(core_tests.FakeProcess(700, name="agent.exe", exe="/w/agent.exe", username="nt authority\\system"))

        self.assertEqual(result[700].protected, "system")

    def test_empty_owner_is_system_not_another_user(self) -> None:
        result = self.snapshot(core_tests.FakeProcess(700, name="agent.exe", exe="/w/agent.exe", username=""))

        self.assertEqual(result[700].protected, "system")

    def test_unreadable_owner_is_system_not_another_user(self) -> None:
        result = self.snapshot(UnreadableOwner(700, name="agent.exe", exe="/w/agent.exe"))

        self.assertEqual(result[700].protected, "system")
        self.assertEqual(result[700].user, "")

    def test_pids_zero_and_four_are_system(self) -> None:
        # Their command lines are given so the kernel-thread rule does not drop them.
        result = self.snapshot(
            core_tests.FakeProcess(0, name="System Idle Process", exe="", cmdline=["idle"], username=USER),
            core_tests.FakeProcess(4, name="System", exe="", cmdline=["System"], username=USER),
        )

        self.assertEqual(result[0].protected, "system")
        self.assertEqual(result[4].protected, "system")

    def test_another_real_user_is_still_another_user(self) -> None:
        result = self.snapshot(core_tests.FakeProcess(700, name="app.exe", exe="/w/app.exe", username="PC\\bob"))

        self.assertEqual(result[700].protected, "another user")

    def test_an_elevated_session_does_not_lift_the_system_rules(self) -> None:
        result = self.snapshot(
            core_tests.FakeProcess(700, name="Explorer.EXE", exe="/w/Explorer.EXE", username=USER),
            core_tests.FakeProcess(701, name="app.exe", exe="/w/app.exe", username="PC\\bob"),
            root=True,
        )

        self.assertEqual(result[700].protected, "system")
        self.assertEqual(result[701].protected, "")  # same as root on Linux

    def test_this_terminal_and_its_ancestors_stay_protected(self) -> None:
        result = self.snapshot()

        for pid in (core_tests.ME, core_tests.PARENT, core_tests.GRANDPARENT):
            with self.subTest(pid=pid):
                self.assertEqual(result[pid].protected, "this terminal")


class LinuxRulesUnchangedTests(SnapshotMixin, unittest.TestCase):
    windows = False

    def test_windows_names_are_not_protected_on_linux(self) -> None:
        result = self.snapshot(
            core_tests.FakeProcess(700, name="explorer.exe", exe="/usr/bin/explorer.exe", username=USER),
            core_tests.FakeProcess(701, name="svchost.exe", exe="/usr/bin/svchost.exe", username=USER),
        )

        self.assertEqual(result[700].protected, "")
        self.assertEqual(result[701].protected, "")

    def test_nt_authority_and_empty_owners_are_just_other_users_on_linux(self) -> None:
        result = self.snapshot(
            core_tests.FakeProcess(700, name="a", exe="/usr/bin/a", username="NT AUTHORITY\\SYSTEM"),
            core_tests.FakeProcess(701, name="b", exe="/usr/bin/b", username=""),
        )

        self.assertEqual(result[700].protected, "another user")
        self.assertEqual(result[701].protected, "another user")

    def test_linux_session_names_still_protect_and_stay_case_sensitive(self) -> None:
        result = self.snapshot(
            core_tests.FakeProcess(700, name="sshd", exe="/usr/sbin/sshd", username=USER),
            core_tests.FakeProcess(701, name="SSHD", exe="/usr/sbin/SSHD", username=USER),
        )

        self.assertEqual(result[700].protected, "system")
        self.assertEqual(result[701].protected, "")


# --------------------------------------------------------------------------
# ending processes (psutil and the window seam faked)
# --------------------------------------------------------------------------


class WinTarget(core_tests.Target):
    """A fake psutil.Process; terminate() is a hard kill on Windows, so it must not be used."""

    def terminate(self):
        raise AssertionError("psutil terminate() is TerminateProcess on Windows; it must never be called")


class EndOnWindowsMixin:
    windows = True

    def setUp(self) -> None:  # noqa: N802
        self.procs = procs_module()

    def run_function(self, name, targets, processes, *, windows=(), obey=(), grace=0.25):
        """Run ``end_processes``/``kill_processes``.

        ``windows``: pids that have a top-level window.  ``obey``: pids that exit
        when their window is closed.  Returns (result, close_calls, wait_mock).
        """
        table = {t.pid: t for t in targets}
        close_calls: list[int] = []

        def lookup(pid):
            if pid not in table:
                raise psutil.NoSuchProcess(pid)
            return table[pid]

        def close(pid):
            close_calls.append(pid)
            if pid in obey:
                table[pid].alive = False
            return pid in windows

        def wait_procs(items, timeout=None, callback=None):
            return [p for p in items if not p.is_running()], [p for p in items if p.is_running()]

        with fake_windows(self.procs, self.windows), \
             mock.patch.object(self.procs, "_close_windows", side_effect=close, create=True), \
             mock.patch.object(self.procs.psutil, "Process", side_effect=lookup), \
             mock.patch.object(self.procs.psutil, "wait_procs", side_effect=wait_procs) as wait:
            result = getattr(self.procs, name)(processes, grace=grace)
        return result, close_calls, wait

    @staticmethod
    def pids(items):
        return sorted(p.pid for p in items)


class WindowsEndTests(EndOnWindowsMixin, unittest.TestCase):
    def test_a_polite_close_is_posted_for_every_chosen_process(self) -> None:
        targets = [WinTarget(1), WinTarget(2), WinTarget(3)]

        _, closes, _ = self.run_function(
            "end_processes", targets, [core_tests.make_proc(i) for i in (1, 2, 3)], windows={2}
        )

        self.assertEqual(sorted(closes), [1, 2, 3])

    def test_protected_and_recycled_processes_are_never_closed(self) -> None:
        targets = [WinTarget(1), WinTarget(2), WinTarget(3), WinTarget(4, started=9000.0)]
        processes = [
            core_tests.make_proc(1),
            core_tests.make_proc(2, protected="system"),
            core_tests.make_proc(3, protected="this terminal"),
            core_tests.make_proc(4),
        ]

        result, closes, _ = self.run_function("end_processes", targets, processes, windows={1, 2, 3, 4})

        self.assertEqual(closes, [1])
        self.assertIn(4, self.pids(result.ended))  # its PID belongs to something else now

    def test_terminate_is_never_called(self) -> None:
        targets = [WinTarget(1), WinTarget(2)]

        # WinTarget.terminate raises AssertionError; any call would fail the test.
        self.run_function("end_processes", targets, [core_tests.make_proc(1), core_tests.make_proc(2)], windows={1})

        self.assertEqual([t.signals for t in targets], [[], []])

    def test_with_a_window_it_waits_once_for_all_chosen_processes_for_the_grace_period(self) -> None:
        targets = [WinTarget(1), WinTarget(2), WinTarget(3)]
        processes = [core_tests.make_proc(i) for i in (1, 2, 3)]

        result, _, wait = self.run_function(
            "end_processes", targets, processes, windows={1}, obey={1, 3}, grace=0.25
        )

        wait.assert_called_once()
        (handles,), kwargs = wait.call_args[0], wait.call_args[1]
        self.assertEqual(sorted(h.pid for h in handles), [1, 2, 3])
        self.assertEqual(kwargs.get("timeout"), 0.25)
        self.assertEqual(self.pids(result.ended), [1, 3])
        self.assertEqual(self.pids(result.survivors), [2])
        self.assertEqual(result.failed, ())

    def test_with_a_window_that_ignores_the_close_the_process_survives(self) -> None:
        result, _, wait = self.run_function(
            "end_processes", [WinTarget(1)], [core_tests.make_proc(1)], windows={1}, obey=set()
        )

        wait.assert_called_once()
        self.assertEqual(self.pids(result.survivors), [1])
        self.assertEqual(result.ended, ())

    def test_without_any_window_nothing_is_waited_for_and_all_are_survivors_at_once(self) -> None:
        targets = [WinTarget(1), WinTarget(2)]

        result, closes, wait = self.run_function(
            "end_processes", targets, [core_tests.make_proc(1), core_tests.make_proc(2)], windows=set()
        )

        wait.assert_not_called()
        self.assertEqual(sorted(closes), [1, 2])
        self.assertEqual(self.pids(result.survivors), [1, 2])
        self.assertEqual(result.ended, ())
        self.assertEqual(result.failed, ())
        self.assertEqual([t.signals for t in targets], [[], []])  # not even killed yet

    def test_a_process_that_is_already_gone_is_ended_even_when_no_window_was_found(self) -> None:
        # PID 9 is not in the table, so psutil.Process(9) raises NoSuchProcess.
        result, _, wait = self.run_function(
            "end_processes", [WinTarget(1)], [core_tests.make_proc(1), core_tests.make_proc(9)], windows=set()
        )

        wait.assert_not_called()
        self.assertEqual(self.pids(result.ended), [9])
        self.assertEqual(self.pids(result.survivors), [1])

    def test_nothing_to_end_gives_an_empty_result_without_waiting(self) -> None:
        result, closes, wait = self.run_function("end_processes", [], [])

        self.assertEqual((result.ended, result.survivors, result.failed), ((), (), ()))
        self.assertEqual(closes, [])
        wait.assert_not_called()

    def test_force_kill_uses_kill_and_never_terminate(self) -> None:
        targets = [WinTarget(1), WinTarget(2, survives=True)]

        result, _, _ = self.run_function(
            "kill_processes", targets, [core_tests.make_proc(1), core_tests.make_proc(2)]
        )

        self.assertEqual([t.signals for t in targets], [["kill"], ["kill"]])
        self.assertEqual(self.pids(result.ended), [1])
        self.assertEqual(self.pids(result.survivors), [2])

    def test_force_kill_follows_the_survivors_of_the_polite_step(self) -> None:
        targets = [WinTarget(1), WinTarget(2)]
        processes = [core_tests.make_proc(1), core_tests.make_proc(2)]
        first, _, _ = self.run_function("end_processes", targets, processes, windows=set())

        second, _, _ = self.run_function("kill_processes", targets, list(first.survivors))

        self.assertEqual(self.pids(second.ended), [1, 2])
        self.assertEqual(second.survivors, ())
        self.assertEqual([t.signals for t in targets], [["kill"], ["kill"]])


class LinuxEndUnchangedTests(EndOnWindowsMixin, unittest.TestCase):
    windows = False

    def test_end_processes_still_terminates_waits_and_posts_no_window_close(self) -> None:
        targets = [core_tests.Target(1), core_tests.Target(2)]

        result, closes, wait = self.run_function(
            "end_processes", targets, [core_tests.make_proc(1), core_tests.make_proc(2)]
        )

        self.assertEqual([t.signals for t in targets], [["term"], ["term"]])
        self.assertEqual(closes, [])
        wait.assert_called_once()
        self.assertEqual(self.pids(result.ended), [1, 2])


# --------------------------------------------------------------------------
# real throwaway process (Windows only)
# --------------------------------------------------------------------------


@unittest.skipUnless(WINDOWS, "needs real Windows process semantics")
class RealWindowsProcessTests(unittest.TestCase):
    """Only processes this test starts are ever touched."""

    def setUp(self) -> None:
        self.procs = procs_module()
        self.child = subprocess.Popen(
            [sys.executable, "-c", "print('ready', flush=True)\nimport time\ntime.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.addCleanup(self.cleanup)
        self.assertEqual(self.child.stdout.readline().strip(), "ready")
        # The venv's python.exe may be a launcher that starts the real interpreter;
        # remember those children (started by us) so cleanup can end them too.
        self.descendants = psutil.Process(self.child.pid).children(recursive=True)
        threading.Thread(target=self.child.wait, daemon=True).start()
        live = psutil.Process(self.child.pid)
        self.proc = core_tests.make_proc(
            self.child.pid,
            name=live.name(),
            exe=live.exe(),
            command=" ".join(live.cmdline()),
            memory_bytes=live.memory_info().rss,
            started=live.create_time(),
        )

    def cleanup(self) -> None:
        for process in [*self.descendants, psutil.Process(self.child.pid) if self.child.poll() is None else None]:
            if process is not None:
                try:
                    process.kill()
                except psutil.Error:
                    pass
        try:
            self.child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        self.child.stdout.close()

    def test_a_windowless_child_survives_the_polite_step_at_once_and_dies_under_force(self) -> None:
        started = time.monotonic()
        first = self.procs.end_processes([self.proc], grace=3.0)
        elapsed = time.monotonic() - started

        self.assertEqual([p.pid for p in first.survivors], [self.child.pid])
        self.assertEqual(first.ended, ())
        self.assertEqual(first.failed, ())
        self.assertLess(elapsed, 2.0, "no window, so the grace period must not be spent")
        self.assertIsNone(self.child.poll(), "the polite step must not hard-kill the child")
        self.assertTrue(psutil.pid_exists(self.child.pid))

        second = self.procs.kill_processes(first.survivors, grace=5.0)

        self.assertEqual([p.pid for p in second.ended], [self.child.pid])
        self.assertEqual(second.survivors, ())
        self.assertEqual(second.failed, ())
        self.assertFalse(psutil.pid_exists(self.child.pid))

    def test_close_windows_finds_no_window_for_a_windowless_process_and_leaves_it_running(self) -> None:
        self.assertIs(self.procs._close_windows(self.child.pid), False)

        self.assertIsNone(self.child.poll())

    def test_close_windows_for_a_process_that_has_exited_is_false(self) -> None:
        done = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=subprocess.CREATE_NO_WINDOW)
        done.wait()

        self.assertIs(self.procs._close_windows(done.pid), False)


# --------------------------------------------------------------------------
# swap: table, memory section, JSON
# --------------------------------------------------------------------------

SIZE_ANYWHERE = cli_tests.SIZE


def parsed_report(test: unittest.TestCase, text: str) -> Parsed:
    return Parsed(test, cli_tests.frame_text(text), False)


class RenderBase:
    windows = True

    def render(self, each: bool, **kwargs) -> Parsed:
        procs = procs_module()
        rows = cli_tests.sample_singles() if each else cli_tests.sample_groups()
        outside = {"memory_bytes": 5 * 1024 * MIB, "swap_bytes": 111 * 1024}
        with fake_windows(procs, self.windows):
            text = cli_tests.render_procs(rows, rows, cli_tests.TOTALS, each=each, outside=outside, **kwargs)
        return parsed_report(self, text)

    def total_lines(self, parsed: Parsed) -> list[str]:
        return dict(parsed.sections)["Total"]


class WindowsRenderTests(RenderBase, unittest.TestCase):
    def test_group_table_has_no_swap_column(self) -> None:
        parsed = self.render(each=False)

        header = cli_tests.table_header(parsed).split()
        self.assertEqual(header, ["Application", "Procs", "Memory", "User"])

    def test_each_table_has_no_swap_column(self) -> None:
        parsed = self.render(each=True)

        header = cli_tests.table_header(parsed).split()
        self.assertEqual(header, ["PID", "Name", "Memory", "User", "Command"])

    def test_table_rows_carry_no_swap_figures(self) -> None:
        # The sample swap figures are all in KiB; memory figures are MiB or GiB.
        for each in (False, True):
            with self.subTest(each=each):
                parsed = self.render(each=each)

                for row in cli_tests.table_rows(parsed):
                    self.assertNotIn("KiB", row)

    def test_total_rows_show_one_size_each_and_no_swap_figures(self) -> None:
        for each in (False, True):
            with self.subTest(each=each):
                lines = self.total_lines(self.render(each=each))

                self.assertGreaterEqual(len(lines), 3)
                for line in lines:
                    self.assertEqual(len(SIZE_ANYWHERE.findall(line)), 1, line)
                    self.assertNotIn("KiB", line)

    def test_memory_section_keeps_the_swap_bar(self) -> None:
        parsed = self.render(each=False)

        swap = [line for line in parsed.host_lines if line.startswith("Swap")]
        self.assertEqual(len(swap), 1, parsed.host_lines)
        self.assertIn("8.0 GiB", swap[0])  # the bar reads "1.5 / 8.0 GiB"
        self.assertIn("1.5", swap[0])

    def test_ascii_output_drops_the_swap_column_too(self) -> None:
        procs = procs_module()
        rows = cli_tests.sample_groups()
        with fake_windows(procs, True):
            text = cli_tests.render_procs(rows, rows, cli_tests.TOTALS, ascii=True)

        header = next(l for l in strip_ansi(text).splitlines() if "Procs" in l)
        self.assertNotIn("Swap", header)


class LinuxRenderUnchangedTests(RenderBase, unittest.TestCase):
    windows = False

    def test_group_table_keeps_the_swap_column(self) -> None:
        header = cli_tests.table_header(self.render(each=False)).split()
        self.assertEqual(header, ["Application", "Procs", "Memory", "Swap", "User"])

    def test_each_table_keeps_the_swap_column(self) -> None:
        header = cli_tests.table_header(self.render(each=True)).split()
        self.assertEqual(header, ["PID", "Name", "Memory", "Swap", "User", "Command"])

    def test_table_rows_carry_the_swap_figures(self) -> None:
        parsed = self.render(each=False)
        self.assertTrue(any("KiB" in row for row in cli_tests.table_rows(parsed)))

    def test_total_rows_show_memory_and_swap(self) -> None:
        for line in self.total_lines(self.render(each=False)):
            self.assertEqual(len(SIZE_ANYWHERE.findall(line)), 2, line)

    def test_ascii_output_keeps_the_swap_column(self) -> None:
        procs = procs_module()
        rows = cli_tests.sample_groups()
        with fake_windows(procs, False):
            text = cli_tests.render_procs(rows, rows, cli_tests.TOTALS, ascii=True)
        header = next(l for l in strip_ansi(text).splitlines() if "Procs" in l)
        self.assertIn("Swap", header)


class WindowsCommandMixin:
    windows = True

    def setUp(self) -> None:  # noqa: N802
        super().setUp()
        self.patch(self.procs, "_windows", return_value=self.windows, create=True)


class WindowsCommandTests(WindowsCommandMixin, cli_tests.FakedCommand):
    def test_command_output_has_no_swap_column_but_keeps_the_swap_bar(self) -> None:
        parsed = self.parsed(self.run_cli())

        self.assertNotIn("Swap", cli_tests.table_header(parsed).split())
        self.assertTrue(any(line.startswith("Swap") for line in parsed.host_lines))

    def test_json_group_rows_have_null_swap_bytes(self) -> None:
        data = json.loads(self.run_cli("--json").stdout)

        self.assertGreater(len(data["rows"]), 0)
        for row in data["rows"]:
            self.assertIn("swap_bytes", row)
            self.assertIsNone(row["swap_bytes"], row)
            self.assertIsInstance(row["memory_bytes"], int)

    def test_json_single_process_rows_have_null_swap_bytes(self) -> None:
        data = json.loads(self.run_cli("--json", "--each").stdout)

        for row in data["rows"]:
            self.assertIsNone(row["swap_bytes"], row)

    def test_json_totals_have_null_swap_bytes(self) -> None:
        totals = json.loads(self.run_cli("--json").stdout)["totals"]

        self.assertIsNone(totals["shown"]["swap_bytes"])
        self.assertIsNone(totals["all"]["swap_bytes"])
        self.assertIsNone(totals["outside"]["swap_bytes"])
        self.assertIsInstance(totals["all"]["memory_bytes"], int)

    def test_json_machine_figures_keep_the_page_file(self) -> None:
        data = json.loads(self.run_cli("--json").stdout)

        self.assertEqual(data["memory"], cli_tests.TOTALS)


class WindowsKillFlowTests(WindowsCommandMixin, cli_tests.FakedCommand):
    """The flow is the same list -> confirm -> polite -> force question."""

    interactive = True

    def test_survivors_of_the_polite_step_lead_to_the_force_kill_question(self) -> None:
        self.tick("tiny")
        survivor = [p for p in cli_tests.sample_processes() if p.name == "tiny"]
        self.end_return = cli_tests.end_result(survivors=survivor)

        result = self.run_cli("--kill")

        self.assertEqual(self.confirm.call_count, 2, result.output)
        self.assertIn("Force kill?", self.confirm.call_args_list[1].args[0])
        self.assertEqual([p.pid for p in self.kill_calls[0]], [30])
        self.assertEqual(result.exit_code, 0, result.output)

    def test_declining_the_force_kill_leaves_the_survivor_running_and_exits_one(self) -> None:
        self.tick("tiny")
        survivor = [p for p in cli_tests.sample_processes() if p.name == "tiny"]
        self.end_return = cli_tests.end_result(survivors=survivor)
        self.confirm.side_effect = [True, False]

        result = self.run_cli("--kill")

        self.assertEqual(self.kill_calls, [])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("still running", result.output)

    def test_force_skips_the_force_kill_question(self) -> None:
        self.tick("tiny")
        survivor = [p for p in cli_tests.sample_processes() if p.name == "tiny"]
        self.end_return = cli_tests.end_result(survivors=survivor)

        result = self.run_cli("--kill", "--force")

        self.assertEqual(self.confirm.call_count, 1, result.output)
        self.assertEqual([p.pid for p in self.kill_calls[0]], [30])
        self.assertEqual(result.exit_code, 0, result.output)


class LinuxCommandUnchangedTests(WindowsCommandMixin, cli_tests.FakedCommand):
    windows = False

    def test_json_keeps_numeric_swap_bytes(self) -> None:
        data = json.loads(self.run_cli("--json").stdout)

        rows = {r["name"]: r for r in data["rows"]}
        self.assertEqual(rows["firefox"]["swap_bytes"], 600 * 1024)
        self.assertIsInstance(data["totals"]["all"]["swap_bytes"], int)
        self.assertIsInstance(data["totals"]["outside"]["swap_bytes"], int)

    def test_table_keeps_the_swap_column(self) -> None:
        parsed = self.parsed(self.run_cli())

        self.assertIn("Swap", cli_tests.table_header(parsed).split())


if __name__ == "__main__":
    unittest.main()
