"""Windows exclusion diagnostics and HNS recovery (never restart real services)."""

import shutil
import subprocess
import unittest
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app
from jzr_tools.core.network import KillPortResult
from jzr_tools.core.windows_network import (
    PortExclusion, _run, check_port_bindings, get_port_exclusions, restart_hns,
)


TABLE = """Protocol tcp Port Exclusion Ranges
Start Port    End Port
----------    --------
      2970        3069
     50000       50059     *

* - Administered port exclusions.
"""
EXCLUSION = PortExclusion("ipv4", "tcp", 2970, 3069)


class ExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.windows = self.enterContext(mock.patch(
            "jzr_tools.core.windows_network.is_windows", return_value=True,
        ))
        self.run = self.enterContext(mock.patch(
            "jzr_tools.core.windows_network._run", return_value=TABLE,
        ))

    def test_exact_inclusive_ranges_across_protocols_and_families(self) -> None:
        for port in (2970, 3000, 3069):
            with self.subTest(port=port):
                matches = get_port_exclusions(port)
                self.assertEqual([(x.family, x.protocol) for x in matches], [
                    ("ipv4", "tcp"), ("ipv4", "udp"), ("ipv6", "tcp"), ("ipv6", "udp"),
                ])
                self.assertTrue(all(x.start == 2970 and x.end == 3069 for x in matches))
                self.assertTrue(all(not x.administered for x in matches))
        for port in (2969, 3070, 30000):
            self.assertEqual(get_port_exclusions(port), [])
        self.assertTrue(all("store=active" in call.args[0] for call in self.run.call_args_list))

    def test_administered_and_localized_output(self) -> None:
        self.run.return_value = "Локализованный заголовок\r\n----------    --------\r\n50000 50059 *\r\n"
        matches = get_port_exclusions(50000)
        self.assertTrue(all(x.administered for x in matches))
        self.assertIn("administered", matches[0].describe())

    def test_empty_table_is_valid_but_unrecognized_output_is_not(self) -> None:
        self.run.return_value = "Start Port End Port\n---------- --------\n"
        self.assertEqual(get_port_exclusions(3000), [])
        for output in ("", "Access is denied.", "The following command was not found"):
            self.run.return_value = output
            with self.assertRaisesRegex(RuntimeError, "Cannot parse"):
                get_port_exclusions(3000)

    def test_non_windows_never_runs_netsh(self) -> None:
        self.windows.return_value = False
        self.assertEqual(get_port_exclusions(3000), [])
        self.run.assert_not_called()


class HnsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.windows = self.enterContext(mock.patch(
            "jzr_tools.core.windows_network.is_windows", return_value=True,
        ))
        self.windll = self.enterContext(mock.patch(
            "jzr_tools.core.windows_network.ctypes.windll", create=True,
        ))
        self.windll.shell32.IsUserAnAdmin.return_value = 1
        self.run = self.enterContext(mock.patch("jzr_tools.core.windows_network._run"))

    def test_admin_required_without_spawning_service_command(self) -> None:
        self.windll.shell32.IsUserAnAdmin.return_value = 0
        with self.assertRaisesRegex(RuntimeError, "Administrator"):
            restart_hns()
        self.run.assert_not_called()

    def test_non_windows_rejected(self) -> None:
        self.windows.return_value = False
        with self.assertRaisesRegex(RuntimeError, "only available on Windows"):
            restart_hns()
        self.run.assert_not_called()

    def test_restart_attempts_start_in_finally_without_forcing_dependents(self) -> None:
        restart_hns()
        command = self.run.call_args.args[0]
        self.assertIn("-NonInteractive", command)
        script = command[-1]
        self.assertIn("Stop-Service -Name hns", script)
        self.assertIn("finally", script)
        self.assertIn("Start-Service -Name hns", script)
        self.assertIn("WaitForStatus", script)
        self.assertNotIn("-Force", script)

    def test_failed_restart_explains_service_recovery(self) -> None:
        self.run.side_effect = RuntimeError("service start failed")
        with self.assertRaisesRegex(RuntimeError, "HNS restart failed:.*Start-Service hns"):
            restart_hns()

    @unittest.skipUnless(shutil.which("powershell.exe"), "Windows PowerShell is not installed")
    def test_restart_script_parses_in_windows_powershell_without_execution(self) -> None:
        restart_hns()
        script = self.run.call_args.args[0][-1]
        # Parse the captured production script as data; never execute its service commands.
        parser = (
            "$source = [Console]::In.ReadToEnd(); "
            "$tokens = $null; $parseErrors = $null; "
            "[void][System.Management.Automation.Language.Parser]::ParseInput("
            "$source, [ref]$tokens, [ref]$parseErrors); "
            "if ($parseErrors.Count) { $parseErrors | ForEach-Object { $_.Message }; exit 1 }"
        )
        result = subprocess.run(
            [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", parser],
            input=script, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class NativeCommandTests(unittest.TestCase):
    def test_native_failures_are_reported(self) -> None:
        for error in (
            FileNotFoundError("netsh missing"),
            subprocess.TimeoutExpired("netsh", 10),
            subprocess.CalledProcessError(1, "netsh", stderr="Access denied"),
        ):
            with self.subTest(error=error), mock.patch(
                "jzr_tools.core.windows_network.subprocess.run", side_effect=error,
            ), self.assertRaises(RuntimeError):
                _run(["netsh"])

    def test_bind_failure_after_restart_is_not_success(self) -> None:
        with mock.patch("jzr_tools.core.windows_network.socket.socket", side_effect=OSError("denied")):
            with self.assertRaisesRegex(RuntimeError, "still cannot bind.*TCP"):
                check_port_bindings(3000)


class WindowsKillPortCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.kill = self.enterContext(mock.patch(
            "jzr_tools.commands.network.kill_port", return_value=KillPortResult(),
        ))
        self.windows = self.enterContext(mock.patch("jzr_tools.commands.network.is_windows", return_value=True))
        self.exclusions = self.enterContext(mock.patch(
            "jzr_tools.commands.network.get_port_exclusions", return_value=[EXCLUSION],
        ))
        self.restart = self.enterContext(mock.patch("jzr_tools.commands.network.restart_hns_service"))
        self.bind = self.enterContext(mock.patch("jzr_tools.commands.network.check_port_bindings"))

    def invoke(self, *options: str):
        return CliRunner().invoke(app, ["killport", "3000", *options])

    def test_default_reports_exclusion_without_restarting(self) -> None:
        self.exclusions.return_value = [EXCLUSION, PortExclusion("ipv6", "tcp", 2970, 3069)]
        result = self.invoke()
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Port 3000 is excluded for TCP on IPv4 and IPv6 (range 2970-3069).", result.stderr)
        self.assertEqual(result.stderr.count("Port 3000 is excluded"), 1)
        self.assertIn("--restart-hns", result.stderr)
        self.assertNotIn("No visible processes", result.stdout)
        self.restart.assert_not_called()

    def test_exclusions_checked_even_when_process_was_killed(self) -> None:
        self.kill.return_value = KillPortResult(killed=[101])
        result = self.invoke()
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Killed PID 101", result.stdout)
        self.assertIn("2970-3069", result.stderr)

    def test_explicit_recovery_rechecks_and_binds(self) -> None:
        self.exclusions.side_effect = [[EXCLUSION], []]
        result = self.invoke("--restart-hns")
        self.assertEqual(result.exit_code, 0, result.output)
        self.restart.assert_called_once()
        self.assertEqual(self.exclusions.call_count, 2)
        self.bind.assert_called_once_with(3000)
        self.assertIn("passed local TCP/UDP bind checks", result.stdout)

    def test_remaining_exclusion_is_failure(self) -> None:
        result = self.invoke("--restart-hns")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("still excluded", result.stderr)
        self.restart.assert_called_once()
        self.bind.assert_not_called()

    def test_no_exclusion_does_not_restart_even_with_flag(self) -> None:
        self.exclusions.return_value = []
        result = self.invoke("--restart-hns")
        self.assertEqual(result.exit_code, 0)
        self.restart.assert_not_called()

    def test_administered_exclusion_is_not_restarted(self) -> None:
        self.exclusions.return_value = [PortExclusion("ipv4", "tcp", 3000, 3000, True)]
        result = self.invoke("--restart-hns")
        self.assertEqual(result.exit_code, 1)
        self.assertIn("administered exclusion", result.stderr)
        self.restart.assert_not_called()

    def test_non_windows_flag_fails_before_killing(self) -> None:
        self.windows.return_value = False
        result = self.invoke("--restart-hns")
        self.assertEqual(result.exit_code, 1)
        self.kill.assert_not_called()
        self.restart.assert_not_called()

    def test_inspection_error_does_not_report_no_process_success(self) -> None:
        self.exclusions.side_effect = RuntimeError("Cannot inspect exclusions")
        result = self.invoke()
        self.assertEqual(result.exit_code, 1)
        self.assertIn("Cannot inspect exclusions", result.stderr)
        self.assertNotIn("No visible processes", result.stdout)

    def test_restart_or_bind_errors_do_not_report_success(self) -> None:
        for stage in (self.restart, self.bind):
            with self.subTest(stage=stage):
                self.exclusions.side_effect = [[EXCLUSION], []]
                stage.side_effect = RuntimeError("recovery failed")
                result = self.invoke("--restart-hns")
                self.assertEqual(result.exit_code, 1)
                self.assertIn("recovery failed", result.stderr)
                self.assertNotIn("passed local", result.stdout)
                stage.side_effect = None


if __name__ == "__main__":
    unittest.main()
