"""Tests of the hashing logic: digests of bytes, streams and files, checksum
line formatting and parsing, and verifying a checksum file.

Digests are compared with `hashlib` directly, plus two sha256 values written
out literally. Checksum files live in temporary folders.
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest

from jzr_tools.core.hashing import (
    ALGORITHMS,
    CHUNK_SIZE,
    CheckResult,
    HashError,
    algorithm_for_length,
    check_sums,
    format_sum_line,
    hash_bytes,
    hash_file,
    hash_stream,
    new_hasher,
    normalize_hex,
    parse_sum_line,
)

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

REFERENCE = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
    "sha3-256": hashlib.sha3_256,
    "sha3-512": hashlib.sha3_512,
    "blake2b": hashlib.blake2b,
}


class TempFolderCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name).resolve()

    def write(self, relative: str, data: bytes = b"") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


class DigestTests(unittest.TestCase):
    def test_known_sha256_values(self) -> None:
        self.assertEqual(hash_bytes(b""), EMPTY_SHA256)
        self.assertEqual(hash_bytes(b"abc"), ABC_SHA256)
        self.assertEqual(hash_bytes(b"abc", "sha256"), ABC_SHA256)

    def test_algorithm_list(self) -> None:
        self.assertEqual(set(ALGORITHMS), set(REFERENCE))
        self.assertEqual(len(ALGORITHMS), 8)

    def test_every_algorithm_matches_hashlib(self) -> None:
        for name in ALGORITHMS:
            for data in (b"", b"abc", bytes(range(256)) * 5):
                with self.subTest(algorithm=name, size=len(data)):
                    self.assertEqual(hash_bytes(data, name), REFERENCE[name](data).hexdigest())

    def test_digests_are_lower_case_hex(self) -> None:
        digest = hash_bytes(b"\xff" * 10, "sha512")
        self.assertEqual(digest, digest.lower())
        self.assertEqual(len(digest), 128)

    def test_unknown_algorithm(self) -> None:
        for name in ("crc32", "SHA256", "", "sha-256"):
            with self.subTest(algorithm=name):
                with self.assertRaises(HashError):
                    hash_bytes(b"x", name)
                with self.assertRaises(HashError):
                    new_hasher(name)

    def test_new_hasher_is_incremental(self) -> None:
        hasher = new_hasher("sha3-256")
        hasher.update(b"a")
        hasher.update(b"bc")
        self.assertEqual(hasher.hexdigest(), hashlib.sha3_256(b"abc").hexdigest())


class StreamAndFileTests(TempFolderCase):
    def big(self) -> bytes:
        block = bytes(range(251))
        return (block * (CHUNK_SIZE * 2 // len(block) + 1))[: CHUNK_SIZE * 2 + 123]

    def test_stream_larger_than_one_chunk(self) -> None:
        data = self.big()
        self.assertGreater(len(data), CHUNK_SIZE)
        for name in ("sha256", "md5", "blake2b"):
            with self.subTest(algorithm=name):
                self.assertEqual(hash_stream(io.BytesIO(data), name), REFERENCE[name](data).hexdigest())

    def test_stream_exactly_one_chunk_and_empty(self) -> None:
        data = b"z" * CHUNK_SIZE
        self.assertEqual(hash_stream(io.BytesIO(data)), hashlib.sha256(data).hexdigest())
        self.assertEqual(hash_stream(io.BytesIO(b"")), EMPTY_SHA256)

    def test_stream_is_read_in_bounded_pieces(self) -> None:
        sizes = []

        class Recording(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)

        data = self.big()
        hash_stream(Recording(data))
        self.assertTrue(sizes)
        for size in sizes:
            self.assertTrue(0 < size <= CHUNK_SIZE, size)

    def test_stream_with_unknown_algorithm(self) -> None:
        with self.assertRaises(HashError):
            hash_stream(io.BytesIO(b"x"), "nope")

    def test_file_larger_than_one_chunk(self) -> None:
        data = self.big()
        path = self.write("big.bin", data)
        self.assertEqual(hash_file(path), hashlib.sha256(data).hexdigest())
        self.assertEqual(hash_file(path, "sha512"), hashlib.sha512(data).hexdigest())

    def test_small_file_and_empty_file(self) -> None:
        self.assertEqual(hash_file(self.write("abc.txt", b"abc")), ABC_SHA256)
        self.assertEqual(hash_file(self.write("empty.txt")), EMPTY_SHA256)

    def test_file_is_hashed_as_bytes_without_newline_translation(self) -> None:
        data = b"one\r\ntwo\nthree\r"
        self.assertEqual(hash_file(self.write("eol.txt", data)), hashlib.sha256(data).hexdigest())

    def test_file_with_unknown_algorithm(self) -> None:
        with self.assertRaises(HashError):
            hash_file(self.write("a.txt", b"a"), "nope")

    def test_missing_file_raises_os_error(self) -> None:
        with self.assertRaises(OSError):
            hash_file(self.root / "missing.txt")

    def test_file_handle_is_released(self) -> None:
        path = self.write("a.txt", b"a")
        hash_file(path)
        path.unlink()
        self.assertFalse(path.exists())


class NormalizeHexTests(unittest.TestCase):
    def test_strips_and_lowers(self) -> None:
        self.assertEqual(normalize_hex("  ABCdef0123  \n"), "abcdef0123")

    def test_already_normal(self) -> None:
        self.assertEqual(normalize_hex(ABC_SHA256), ABC_SHA256)

    def test_not_hex(self) -> None:
        for value in ("xyz", "12g4", "ab-cd"):
            with self.subTest(value=value):
                with self.assertRaises(HashError) as caught:
                    normalize_hex(value)
                self.assertEqual(str(caught.exception), "not a hex digest")


class SumLineTests(unittest.TestCase):
    def test_format(self) -> None:
        self.assertEqual(format_sum_line("abcd", "file.txt"), "abcd  file.txt")
        self.assertEqual(format_sum_line("abcd", "my file.txt"), "abcd  my file.txt")

    def test_parse_text_form(self) -> None:
        self.assertEqual(parse_sum_line(f"{ABC_SHA256}  abc.txt"), (ABC_SHA256, "abc.txt"))

    def test_parse_binary_form(self) -> None:
        self.assertEqual(parse_sum_line(f"{ABC_SHA256} *abc.bin"), (ABC_SHA256, "abc.bin"))

    def test_parse_drops_line_endings(self) -> None:
        for ending in ("\n", "\r\n", "\r", ""):
            with self.subTest(ending=repr(ending)):
                self.assertEqual(parse_sum_line(f"abcd  f.txt{ending}"), ("abcd", "f.txt"))
                self.assertEqual(parse_sum_line(f"abcd *f.txt{ending}"), ("abcd", "f.txt"))

    def test_parse_lowers_the_digest(self) -> None:
        self.assertEqual(parse_sum_line("ABCDEF  f.txt"), ("abcdef", "f.txt"))

    def test_parse_keeps_spaces_inside_the_name(self) -> None:
        self.assertEqual(parse_sum_line("abcd  my file (1).txt"), ("abcd", "my file (1).txt"))
        self.assertEqual(parse_sum_line("abcd *my file.bin"), ("abcd", "my file.bin"))

    def test_parse_keeps_folders_in_the_name(self) -> None:
        self.assertEqual(parse_sum_line("abcd  sub/dir/f.txt"), ("abcd", "sub/dir/f.txt"))

    def test_blank_lines_and_comments_are_skipped(self) -> None:
        for line in ("", "\n", "\r\n", "# made by hand\n", "#abcd  f.txt"):
            with self.subTest(line=line):
                self.assertIsNone(parse_sum_line(line))

    def test_malformed_lines(self) -> None:
        for line in (
            "justoneword",
            "zzzz  f.txt",
            "abcd f.txt",
            "abcd",
            "  abcd  f.txt",
            "not a checksum line at all",
        ):
            with self.subTest(line=line):
                with self.assertRaises(HashError):
                    parse_sum_line(line)

    def test_format_then_parse_round_trips(self) -> None:
        self.assertEqual(
            parse_sum_line(format_sum_line(ABC_SHA256, "dir/a b.txt")), (ABC_SHA256, "dir/a b.txt")
        )


class AlgorithmForLengthTests(unittest.TestCase):
    def test_known_lengths(self) -> None:
        self.assertEqual(algorithm_for_length(32), "md5")
        self.assertEqual(algorithm_for_length(40), "sha1")
        self.assertEqual(algorithm_for_length(64), "sha256")
        self.assertEqual(algorithm_for_length(96), "sha384")
        self.assertEqual(algorithm_for_length(128), "sha512")

    def test_other_lengths(self) -> None:
        for length in (0, 1, 31, 63, 65, 127, 256):
            with self.subTest(length=length):
                self.assertIsNone(algorithm_for_length(length))


class CheckSumsTests(TempFolderCase):
    def sum_file(self, *lines: str, name: str = "SUMS.txt", eol: str = "\n") -> Path:
        return self.write(name, (eol.join(lines) + eol).encode())

    def test_ok_failed_and_missing(self) -> None:
        self.write("good.txt", b"abc")
        self.write("bad.txt", b"changed")
        sums = self.sum_file(
            format_sum_line(ABC_SHA256, "good.txt"),
            format_sum_line(ABC_SHA256, "bad.txt"),
            format_sum_line(ABC_SHA256, "gone.txt"),
        )
        results = check_sums(sums)
        self.assertEqual(
            [(r.name, r.status) for r in results],
            [("good.txt", "OK"), ("bad.txt", "FAILED"), ("gone.txt", "missing")],
        )
        self.assertTrue(all(isinstance(r, CheckResult) for r in results))
        self.assertEqual(results[0].detail, "")

    def test_names_are_resolved_against_the_sum_file_folder(self) -> None:
        self.write("data/a.txt", b"abc")
        self.write("data/sub/b.txt", b"abc")
        sums = self.sum_file(
            format_sum_line(ABC_SHA256, "a.txt"),
            format_sum_line(ABC_SHA256, "sub/b.txt"),
            name="data/SUMS.txt",
        )
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(elsewhere.name)
        self.assertEqual([r.status for r in check_sums(sums)], ["OK", "OK"])

    def test_a_file_in_the_current_folder_is_not_used(self) -> None:
        # The listed file exists next to the cwd but not next to the sum file.
        self.write("lists/SUMS.txt", f"{ABC_SHA256}  a.txt\n".encode())
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        Path(other.name, "a.txt").write_bytes(b"abc")
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(other.name)
        results = check_sums(self.root / "lists" / "SUMS.txt")
        self.assertEqual([(r.name, r.status) for r in results], [("a.txt", "missing")])

    def test_binary_marker_and_crlf_sum_file(self) -> None:
        self.write("a.bin", b"abc")
        self.write("b.txt", b"abc")
        sums = self.sum_file(f"{ABC_SHA256} *a.bin", f"{ABC_SHA256}  b.txt", eol="\r\n")
        self.assertEqual(
            [(r.name, r.status) for r in check_sums(sums)], [("a.bin", "OK"), ("b.txt", "OK")]
        )

    def test_upper_case_digest_in_the_sum_file(self) -> None:
        self.write("a.txt", b"abc")
        sums = self.sum_file(f"{ABC_SHA256.upper()}  a.txt")
        self.assertEqual([r.status for r in check_sums(sums)], ["OK"])

    def test_comments_and_blank_lines_are_skipped(self) -> None:
        self.write("a.txt", b"abc")
        sums = self.sum_file("# checksums", "", format_sum_line(ABC_SHA256, "a.txt"), "")
        self.assertEqual([(r.name, r.status) for r in check_sums(sums)], [("a.txt", "OK")])

    def test_sum_file_with_only_comments_gives_no_results(self) -> None:
        self.assertEqual(check_sums(self.sum_file("# nothing here", "")), [])

    def test_algorithm_is_chosen_from_each_digest_length(self) -> None:
        self.write("a.txt", b"abc")
        self.write("b.txt", b"abc")
        self.write("c.txt", b"abc")
        sums = self.sum_file(
            format_sum_line(hashlib.md5(b"abc").hexdigest(), "a.txt"),
            format_sum_line(hashlib.sha1(b"abc").hexdigest(), "b.txt"),
            format_sum_line(hashlib.sha512(b"abc").hexdigest(), "c.txt"),
        )
        self.assertEqual([r.status for r in check_sums(sums)], ["OK", "OK", "OK"])

    def test_explicit_algorithm_overrides_the_length(self) -> None:
        self.write("a.txt", b"abc")
        digest = hashlib.sha3_256(b"abc").hexdigest()
        sums = self.sum_file(format_sum_line(digest, "a.txt"))
        self.assertEqual([r.status for r in check_sums(sums)], ["FAILED"])
        self.assertEqual([r.status for r in check_sums(sums, algorithm="sha3-256")], ["OK"])

    def test_explicit_algorithm_allows_lengths_that_cannot_be_guessed(self) -> None:
        self.write("a.txt", b"abc")
        sums = self.sum_file(format_sum_line(hashlib.sha3_512(b"abc").hexdigest(), "a.txt"))
        self.assertEqual([r.status for r in check_sums(sums, algorithm="sha3-512")], ["OK"])
        self.assertEqual([r.status for r in check_sums(sums, algorithm="blake2b")], ["FAILED"])

    def test_unknown_explicit_algorithm(self) -> None:
        self.write("a.txt", b"abc")
        sums = self.sum_file(format_sum_line(ABC_SHA256, "a.txt"))
        with self.assertRaises(HashError):
            check_sums(sums, algorithm="nope")

    def test_digest_length_without_an_algorithm_names_the_line(self) -> None:
        self.write("a.txt", b"abc")
        sums = self.sum_file("# first", format_sum_line(ABC_SHA256, "a.txt"), "abcdef0123  a.txt")
        with self.assertRaises(HashError) as caught:
            check_sums(sums)
        self.assertIn("3", str(caught.exception))

    def test_unknown_length_is_fine_when_the_algorithm_is_given(self) -> None:
        self.write("a.txt", b"abc")
        sums = self.sum_file("abcdef0123  a.txt")
        self.assertEqual([r.status for r in check_sums(sums, algorithm="sha256")], ["FAILED"])

    def test_malformed_line_raises_with_file_name_and_line_number(self) -> None:
        self.write("a.txt", b"abc")
        sums = self.sum_file(
            "# header", "", "this is not a checksum", format_sum_line(ABC_SHA256, "a.txt"),
            name="release.sha256",
        )
        with self.assertRaises(HashError) as caught:
            check_sums(sums)
        self.assertEqual(str(caught.exception), "release.sha256:3: not a checksum line")

    def test_malformed_first_line(self) -> None:
        sums = self.sum_file("zzzz  a.txt")
        with self.assertRaises(HashError) as caught:
            check_sums(sums)
        self.assertEqual(str(caught.exception), "SUMS.txt:1: not a checksum line")

    def test_missing_sum_file_raises_os_error(self) -> None:
        with self.assertRaises(OSError):
            check_sums(self.root / "nothing.txt")


if __name__ == "__main__":
    unittest.main()
