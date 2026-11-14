"""Output to a pipe or file is UTF-8, whatever the locale's encoding is."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PipedOutputEncodingTests(unittest.TestCase):
    def test_non_latin_file_name_reaches_a_cp1252_pipe_as_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "\u0444\u043e\u0442\u043e.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\n")
            env = os.environ.copy()
            env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
            env["PYTHONIOENCODING"] = "cp1252"
            env["JZR_CONFIG_DIR"] = folder
            env.pop("PYTHONUTF8", None)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from jzr_tools.cli import main; main()",
                    "image",
                    "convert",
                    str(source),
                    "--to",
                    "jpg",
                    "--dry-run",
                ],
                capture_output=True,
                env=env,
                check=False,
            )
        stderr = proc.stderr.decode("utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, stderr)
        self.assertNotIn("Traceback", stderr)
        stdout = proc.stdout.decode("utf-8")
        self.assertIn("фото.jpg", stdout)
        self.assertNotIn("\\u0444", stdout)


if __name__ == "__main__":
    unittest.main()
