"""CLI behaviour of `jzr hash`, run in temporary folders."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

ALGORITHMS = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
    "sha3-256": hashlib.sha3_256,
    "sha3-512": hashlib.sha3_512,
    "blake2b": hashlib.blake2b,
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HashCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self._tmp = tempfile.TemporaryDirectory(prefix="jzr-hash-cli-")
        self.addCleanup(self._tmp.cleanup)
        self._old_cwd = os.getcwd()
        self.folder = Path(self._tmp.name).resolve()
        os.chdir(self.folder)
        self.addCleanup(os.chdir, self._old_cwd)
        # Unknown command names consult the bindings file: keep it off the real one.
        patcher = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": str(self.folder / ".config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name: str, data: bytes) -> Path:
        path = self.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_hash(self, *args: str, input: bytes | str | None = None):
        return self.runner.invoke(app, ["hash", *args], input=input)

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def out_lines(self, result) -> list[str]:
        return result.stdout.splitlines()


class HashFileTests(HashCliCase):
    def test_a_file_prints_its_sha256_and_name(self) -> None:
        self.write("a.txt", b"hello\n")
        result = self.run_hash("a.txt")
        self.assert_exit(result, 0)
        digest = sha256(b"hello\n")
        self.assertEqual(self.out_lines(result), [f"{digest}  a.txt"])
        self.assertEqual(result.stderr, "")

    def test_the_name_is_printed_as_given(self) -> None:
        self.write("sub/b.bin", b"\x00\x01\x02")
        result = self.run_hash("sub/b.bin", "./sub/b.bin")
        self.assert_exit(result, 0)
        digest = sha256(b"\x00\x01\x02")
        self.assertEqual(self.out_lines(result), [f"{digest}  sub/b.bin", f"{digest}  ./sub/b.bin"])

    def test_several_files_give_one_line_each_in_order(self) -> None:
        self.write("one", b"1")
        self.write("two", b"22")
        self.write("three", b"333")
        result = self.run_hash("two", "one", "three")
        self.assert_exit(result, 0)
        self.assertEqual(
            self.out_lines(result),
            [f"{sha256(b'22')}  two", f"{sha256(b'1')}  one", f"{sha256(b'333')}  three"],
        )

    def test_an_empty_file_has_the_hash_of_no_bytes(self) -> None:
        self.write("empty", b"")
        result = self.run_hash("empty")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), [f"{sha256(b'')}  empty"])

    def test_a_file_larger_than_one_chunk_is_hashed_whole(self) -> None:
        data = os.urandom(1024 * 1024 + 4097)
        self.write("big.bin", data)
        result = self.run_hash("big.bin")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), [f"{sha256(data)}  big.bin"])

    def test_a_missing_file_fails_alone(self) -> None:
        self.write("here.txt", b"x")
        self.write("there.txt", b"y")
        result = self.run_hash("here.txt", "gone.txt", "there.txt")
        self.assert_exit(result, 1)
        self.assertEqual(
            self.out_lines(result),
            [f"{sha256(b'x')}  here.txt", f"{sha256(b'y')}  there.txt"],
        )
        self.assertEqual(result.stderr.splitlines(), ["fail gone.txt: not found"])

    def test_a_folder_fails_alone(self) -> None:
        (self.folder / "dir").mkdir()
        self.write("f", b"x")
        result = self.run_hash("dir", "f")
        self.assert_exit(result, 1)
        self.assertEqual(self.out_lines(result), [f"{sha256(b'x')}  f"])
        self.assertEqual(result.stderr.splitlines(), ["fail dir: is a folder"])

    def test_the_output_is_accepted_back_by_check(self) -> None:
        self.write("a.txt", b"aaa")
        self.write("b.txt", b"bbb")
        result = self.run_hash("a.txt", "b.txt")
        self.assert_exit(result, 0)
        self.write("sums.txt", result.stdout.encode())
        checked = self.run_hash("--check", "sums.txt")
        self.assert_exit(checked, 0)
        self.assertEqual(self.out_lines(checked), ["a.txt: OK", "b.txt: OK"])


class HashInputTests(HashCliCase):
    def test_stdin_without_files_prints_only_the_hex(self) -> None:
        result = self.run_hash(input=b"piped bytes")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), [sha256(b"piped bytes")])

    def test_a_dash_reads_stdin(self) -> None:
        result = self.run_hash("-", input=b"piped bytes")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), [sha256(b"piped bytes")])

    def test_stdin_is_hashed_as_bytes_without_newline_changes(self) -> None:
        data = b"line1\r\nline2\n\xff\xfe"
        result = self.run_hash(input=data)
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), [sha256(data)])

    def test_text_prints_only_the_hex_of_the_utf8_bytes(self) -> None:
        for flag in ("-t", "--text"):
            with self.subTest(flag=flag):
                result = self.run_hash(flag, "héllo")
                self.assert_exit(result, 0)
                self.assertEqual(self.out_lines(result), [sha256("héllo".encode("utf-8"))])

    def test_empty_text_is_the_hash_of_no_bytes(self) -> None:
        result = self.run_hash("-t", "")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), [sha256(b"")])

    def test_text_does_not_wait_for_stdin(self) -> None:
        result = self.run_hash("-t", "abc", input=b"ignored")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), [sha256(b"abc")])


class HashAlgorithmTests(HashCliCase):
    def test_every_algorithm_matches_hashlib_for_files_and_text(self) -> None:
        data = b"The quick brown fox"
        self.write("fox.txt", data)
        for name, factory in ALGORITHMS.items():
            expected = factory(data).hexdigest()
            for flag in ("-a", "--algorithm"):
                with self.subTest(algorithm=name, flag=flag):
                    result = self.run_hash(flag, name, "fox.txt")
                    self.assert_exit(result, 0)
                    self.assertEqual(self.out_lines(result), [f"{expected}  fox.txt"])
            with self.subTest(algorithm=name, source="text"):
                result = self.run_hash("-a", name, "-t", data.decode())
                self.assert_exit(result, 0)
                self.assertEqual(self.out_lines(result), [expected])

    def test_an_unknown_algorithm_is_a_usage_error(self) -> None:
        self.write("a", b"x")
        result = self.run_hash("-a", "md4", "a")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("md4", result.stderr)


class HashExpectTests(HashCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("a.txt", b"abc")
        self.digest = sha256(b"abc")

    def test_a_matching_digest_prints_ok_and_exits_0(self) -> None:
        result = self.run_hash("a.txt", "--expect", self.digest)
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["OK"])

    def test_a_different_digest_prints_mismatch_and_exits_1(self) -> None:
        result = self.run_hash("a.txt", "--expect", sha256(b"other"))
        self.assert_exit(result, 1)
        self.assertEqual(self.out_lines(result), ["MISMATCH"])

    def test_case_and_surrounding_spaces_are_ignored(self) -> None:
        result = self.run_hash("a.txt", "--expect", f"  {self.digest.upper()}  ")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["OK"])

    def test_works_for_text_and_stdin(self) -> None:
        result = self.run_hash("-t", "abc", "--expect", self.digest)
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["OK"])
        result = self.run_hash("--expect", self.digest, input=b"abc")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["OK"])
        result = self.run_hash("--expect", self.digest, input=b"abd")
        self.assert_exit(result, 1)
        self.assertEqual(self.out_lines(result), ["MISMATCH"])

    def test_uses_the_chosen_algorithm(self) -> None:
        expected = hashlib.md5(b"abc").hexdigest()
        result = self.run_hash("a.txt", "-a", "md5", "--expect", expected)
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["OK"])
        result = self.run_hash("a.txt", "--expect", expected)
        self.assert_exit(result, 1)
        self.assertEqual(self.out_lines(result), ["MISMATCH"])

    def test_a_value_that_is_not_hex_is_a_usage_error(self) -> None:
        for value in ("not-hex", "xyz", "12 34 zz"):
            with self.subTest(value=value):
                result = self.run_hash("a.txt", "--expect", value)
                self.assert_exit(result, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("not a hex digest", result.stderr)

    def test_more_than_one_input_is_a_usage_error(self) -> None:
        self.write("b.txt", b"abc")
        result = self.run_hash("a.txt", "b.txt", "--expect", self.digest)
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--expect checks one input; use --check for several files.", result.stderr)


class HashCheckTests(HashCliCase):
    def sum_file(self, name: str, entries: list[tuple[str, str]], mark: str = " ") -> Path:
        text = "".join(f"{digest} {mark}{file}\n" for digest, file in entries)
        return self.write(name, text.encode())

    def test_reports_ok_failed_and_missing_per_line_and_counts_the_bad_ones(self) -> None:
        self.write("good.txt", b"good")
        self.write("changed.txt", b"before")
        entries = [
            (sha256(b"good"), "good.txt"),
            (sha256(b"before"), "changed.txt"),
            (sha256(b"lost"), "lost.txt"),
        ]
        self.sum_file("sums.txt", entries)
        self.write("changed.txt", b"after")
        result = self.run_hash("--check", "sums.txt")
        self.assert_exit(result, 1)
        self.assertEqual(
            self.out_lines(result),
            ["good.txt: OK", "changed.txt: FAILED", "lost.txt: missing"],
        )
        self.assertEqual(result.stderr.splitlines(), ["2 of 3 files did not match."])

    def test_all_ok_exits_0_with_no_count_line(self) -> None:
        self.write("a", b"1")
        self.write("b", b"2")
        self.sum_file("sums.txt", [(sha256(b"1"), "a"), (sha256(b"2"), "b")])
        for flag in ("-c", "--check"):
            with self.subTest(flag=flag):
                result = self.run_hash(flag, "sums.txt")
                self.assert_exit(result, 0)
                self.assertEqual(self.out_lines(result), ["a: OK", "b: OK"])
                self.assertEqual(result.stderr, "")

    def test_names_are_resolved_against_the_sum_file_folder(self) -> None:
        self.write("box/a.txt", b"inside")
        self.write("box/deep/b.txt", b"deeper")
        self.sum_file(
            "box/SUMS", [(sha256(b"inside"), "a.txt"), (sha256(b"deeper"), "deep/b.txt")]
        )
        elsewhere = self.folder / "elsewhere"
        elsewhere.mkdir()
        os.chdir(elsewhere)
        result = self.run_hash("--check", "../box/SUMS")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["a.txt: OK", "deep/b.txt: OK"])

    def test_binary_mode_names_and_upper_case_digests_are_accepted(self) -> None:
        self.write("a.bin", b"data")
        self.write("sums.txt", f"{sha256(b'data').upper()} *a.bin\n".encode())
        result = self.run_hash("--check", "sums.txt")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["a.bin: OK"])

    def test_comments_blank_lines_and_windows_line_endings_are_skipped(self) -> None:
        self.write("a", b"1")
        text = f"# made by hand\r\n\r\n{sha256(b'1')}  a\r\n"
        self.write("sums.txt", text.encode())
        result = self.run_hash("--check", "sums.txt")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["a: OK"])

    def test_the_algorithm_comes_from_the_digest_length(self) -> None:
        self.write("a", b"1")
        self.write("b", b"2")
        self.sum_file(
            "sums.txt",
            [(hashlib.md5(b"1").hexdigest(), "a"), (hashlib.sha512(b"2").hexdigest(), "b")],
        )
        result = self.run_hash("--check", "sums.txt")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["a: OK", "b: OK"])

    def test_the_algorithm_option_decides_for_digests_of_ambiguous_length(self) -> None:
        self.write("a", b"1")
        self.sum_file("sums.txt", [(hashlib.sha3_256(b"1").hexdigest(), "a")])
        result = self.run_hash("--check", "sums.txt", "-a", "sha3-256")
        self.assert_exit(result, 0)
        self.assertEqual(self.out_lines(result), ["a: OK"])

    def test_a_malformed_line_stops_with_its_line_number(self) -> None:
        self.write("a", b"1")
        self.write("sums.txt", f"{sha256(b'1')}  a\nthis is not a checksum\n".encode())
        result = self.run_hash("--check", "sums.txt")
        self.assert_exit(result, 1)
        self.assertEqual(result.stderr.splitlines(), ["sums.txt:2: not a checksum line"])
        self.assertNotIn("Traceback", result.stderr)

    def test_a_digest_length_with_no_algorithm_is_an_error_on_stderr(self) -> None:
        self.write("a", b"1")
        self.write("sums.txt", b"abcdef  a\n")
        result = self.run_hash("--check", "sums.txt")
        self.assert_exit(result, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1, self.describe(result))
        self.assertNotIn("Traceback", result.stderr)


class HashIncompatibleOptionTests(HashCliCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("a.txt", b"abc")
        self.write("sums.txt", f"{sha256(b'abc')}  a.txt\n".encode())

    def test_text_with_files_is_a_usage_error(self) -> None:
        result = self.run_hash("-t", "abc", "a.txt")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--text", result.stderr)

    def test_text_with_check_is_a_usage_error(self) -> None:
        result = self.run_hash("-t", "abc", "--check", "sums.txt")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--text", result.stderr)

    def test_check_with_files_is_a_usage_error(self) -> None:
        result = self.run_hash("--check", "sums.txt", "a.txt")
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--check", result.stderr)

    def test_check_with_expect_is_a_usage_error(self) -> None:
        result = self.run_hash("--check", "sums.txt", "--expect", sha256(b"abc"))
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("--check", result.stderr)
        self.assertIn("--expect", result.stderr)


class HashJsonTests(HashCliCase):
    def test_plain_hashing_is_a_list_of_file_algorithm_and_hash(self) -> None:
        self.write("a.txt", b"abc")
        self.write("b.txt", b"abd")
        result = self.run_hash("a.txt", "b.txt", "--json")
        self.assert_exit(result, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"file": "a.txt", "algorithm": "sha256", "hash": sha256(b"abc")},
                {"file": "b.txt", "algorithm": "sha256", "hash": sha256(b"abd")},
            ],
        )

    def test_algorithm_is_named_in_the_document(self) -> None:
        self.write("a.txt", b"abc")
        result = self.run_hash("a.txt", "-a", "sha3-256", "--json")
        self.assert_exit(result, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [{"file": "a.txt", "algorithm": "sha3-256", "hash": hashlib.sha3_256(b"abc").hexdigest()}],
        )

    def test_text_has_a_null_file(self) -> None:
        result = self.run_hash("-t", "abc", "--json")
        self.assert_exit(result, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [{"file": None, "algorithm": "sha256", "hash": sha256(b"abc")}],
        )

    def test_stdin_dash_is_named_with_a_dash(self) -> None:
        result = self.run_hash("-", "--json", input=b"abc")
        self.assert_exit(result, 0)
        self.assertEqual(
            json.loads(result.stdout),
            [{"file": "-", "algorithm": "sha256", "hash": sha256(b"abc")}],
        )

    def test_check_is_a_list_of_file_and_status(self) -> None:
        self.write("good", b"1")
        self.write("changed", b"2")
        self.write(
            "sums.txt",
            (
                f"{sha256(b'1')}  good\n{sha256(b'old')}  changed\n{sha256(b'3')}  lost\n"
            ).encode(),
        )
        result = self.run_hash("--check", "sums.txt", "--json")
        self.assert_exit(result, 1)
        self.assertEqual(
            json.loads(result.stdout),
            [
                {"file": "good", "status": "OK"},
                {"file": "changed", "status": "FAILED"},
                {"file": "lost", "status": "missing"},
            ],
        )

    def test_check_with_everything_ok_exits_0(self) -> None:
        self.write("good", b"1")
        self.write("sums.txt", f"{sha256(b'1')}  good\n".encode())
        result = self.run_hash("--check", "sums.txt", "--json")
        self.assert_exit(result, 0)
        self.assertEqual(json.loads(result.stdout), [{"file": "good", "status": "OK"}])

    def test_expect_is_one_document_with_the_verdict(self) -> None:
        self.write("a.txt", b"abc")
        digest = sha256(b"abc")
        result = self.run_hash("a.txt", "--expect", digest, "--json")
        self.assert_exit(result, 0)
        self.assertEqual(
            json.loads(result.stdout),
            {
                "file": "a.txt",
                "algorithm": "sha256",
                "hash": digest,
                "expected": digest,
                "match": True,
            },
        )

    def test_expect_mismatch_has_match_false_and_exits_1(self) -> None:
        self.write("a.txt", b"abc")
        wrong = sha256(b"other")
        result = self.run_hash("a.txt", "--expect", wrong, "--json")
        self.assert_exit(result, 1)
        document = json.loads(result.stdout)
        self.assertIs(document["match"], False)
        self.assertEqual(document["hash"], sha256(b"abc"))
        self.assertEqual(document["expected"], wrong)

    def test_expect_with_text_has_a_null_file(self) -> None:
        digest = sha256(b"abc")
        result = self.run_hash("-t", "abc", "--expect", digest, "--json")
        self.assert_exit(result, 0)
        self.assertIsNone(json.loads(result.stdout)["file"])


class HashLineEndingTests(unittest.TestCase):
    """Output redirected to a file must be readable by other checksum tools."""

    def test_redirected_lines_end_in_lf_on_every_platform(self) -> None:
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "a.txt").write_bytes(b"hello")
            Path(folder, "b.txt").write_bytes(b"other")
            completed = subprocess.run(
                [sys.executable, "-m", "jzr_tools", "hash", "a.txt", "b.txt"],
                cwd=folder,
                capture_output=True,
                env={**os.environ, "JZR_CONFIG_DIR": folder},
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn(b"\r", completed.stdout)
        self.assertEqual(
            completed.stdout,
            f"{sha256(b'hello')}  a.txt\n{sha256(b'other')}  b.txt\n".encode(),
        )


if __name__ == "__main__":
    unittest.main()
