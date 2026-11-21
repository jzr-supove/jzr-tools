"""CLI behaviour of `jzr jwt`, with a fixed clock."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from jzr_tools.cli import app

SECRET = "cli-test-secret"
NOW = 1_795_262_400  # 2026-11-21T12:00:00Z
NOW_US = NOW * 1_000_000
EXP = NOW + 86400  # 2026-11-22T12:00:00Z
IAT = NOW - 7200  # 2026-11-21T10:00:00Z
NOTE = (
    "signature not checked (give --secret-env or --secret-file to check an HMAC signature)"
)
DECODE_FAILED = (
    "Cannot decode this JWT: expected three dot-separated parts with JSON header and "
    "payload objects."
)
DIGESTS = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def part(value: object) -> str:
    return b64(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def make_token(
    payload: dict | None = None,
    header: dict | None = None,
    *,
    secret: str = SECRET,
    signature: str | None = None,
) -> str:
    header = {"alg": "HS256", "typ": "JWT"} if header is None else header
    payload = {"sub": "123", "exp": EXP, "iat": IAT} if payload is None else payload
    signing_input = f"{part(header)}.{part(payload)}"
    if signature is None:
        alg = header.get("alg")
        if alg in DIGESTS:
            mac = hmac.new(secret.encode(), signing_input.encode("ascii"), DIGESTS[alg])
            signature = b64(mac.digest())
        else:
            signature = ""
    return f"{signing_input}.{signature}"


EXPECTED_LAYOUT = (
    "Header\n"
    "{\n"
    '  "alg": "HS256",\n'
    '  "typ": "JWT"\n'
    "}\n"
    "\n"
    "Payload\n"
    "{\n"
    '  "sub": "123",\n'
    '  "exp": 1795348800,\n'
    '  "iat": 1795255200\n'
    "}\n"
    "\n"
    "exp  2026-11-22T12:00:00Z (in 1 day)   not expired\n"
    "iat  2026-11-21T10:00:00Z (2 hours ago)\n"
    "nbf  not present\n"
)


class JwtCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()
        self.tmp = tempfile.TemporaryDirectory(prefix="jzr-jwt-")
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"JZR_CONFIG_DIR": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        clock = mock.patch("jzr_tools.core.timestamp.now", return_value=NOW_US)
        clock.start()
        self.addCleanup(clock.stop)

    def run_cli(self, *args: str, input: str | None = None, env: dict | None = None):
        return self.runner.invoke(app, ["jwt", *args], input=input, env=env)

    def describe(self, result) -> str:
        return f"exit={result.exit_code}\nstdout={result.stdout!r}\nstderr={result.stderr!r}"

    def assert_exit(self, result, code: int) -> None:
        self.assertEqual(result.exit_code, code, self.describe(result))

    def secret_file(self, content: bytes) -> str:
        path = Path(self.tmp.name) / "secret.bin"
        path.write_bytes(content)
        return str(path)


class LayoutTests(JwtCliCase):
    def test_text_layout_is_exact(self) -> None:
        result = self.run_cli(make_token())
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, EXPECTED_LAYOUT)

    def test_without_a_secret_stderr_says_the_signature_was_not_checked(self) -> None:
        result = self.run_cli(make_token())
        self.assertEqual(result.stderr.strip(), NOTE)

    def test_the_note_is_not_printed_when_a_secret_is_given(self) -> None:
        result = self.run_cli(make_token(), "--secret-env", "JWT_SECRET", env={"JWT_SECRET": SECRET})
        self.assertNotIn("not checked", result.stderr)

    def test_json_blocks_keep_token_order_and_non_ascii_text(self) -> None:
        token = make_token({"z": "Zoë", "a": 1}, {"typ": "JWT", "alg": "HS256"})
        result = self.run_cli(token)
        self.assert_exit(result, 0)
        self.assertTrue(
            result.stdout.startswith(
                'Header\n{\n  "typ": "JWT",\n  "alg": "HS256"\n}\n\n'
                'Payload\n{\n  "z": "Zoë",\n  "a": 1\n}\n\n'
            ),
            result.stdout,
        )

    def test_all_three_claims_present(self) -> None:
        token = make_token({"exp": NOW - 60, "iat": NOW - 3600, "nbf": NOW + 7200})
        result = self.run_cli(token)
        self.assertTrue(
            result.stdout.endswith(
                "\n\n"
                "exp  2026-11-21T11:59:00Z (1 minute ago)   expired\n"
                "iat  2026-11-21T11:00:00Z (1 hour ago)\n"
                "nbf  2026-11-21T14:00:00Z (in 2 hours)   not yet valid\n"
            ),
            result.stdout,
        )

    def test_claims_that_are_not_numbers_or_out_of_range(self) -> None:
        token = make_token({"exp": "soon", "iat": True, "nbf": 10**20})
        result = self.run_cli(token)
        self.assert_exit(result, 0)
        self.assertTrue(
            result.stdout.endswith(
                '\n\nexp  "soon" (not a number)\n'
                "iat  true (not a number)\n"
                f"nbf  {10**20} (out of range)   not yet valid\n"
            ),
            result.stdout,
        )

    def test_an_expired_token_still_exits_zero_without_require_valid(self) -> None:
        result = self.run_cli(make_token({"exp": NOW - 1, "nbf": NOW + 60}))
        self.assert_exit(result, 0)
        self.assertIn("expired", result.stdout)

    def test_the_token_can_come_from_stdin(self) -> None:
        result = self.run_cli(input=make_token() + "\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, EXPECTED_LAYOUT)

    def test_a_dash_reads_stdin_too(self) -> None:
        result = self.run_cli("-", input=make_token())
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, EXPECTED_LAYOUT)

    def test_an_authorization_bearer_prefix_is_accepted(self) -> None:
        result = self.run_cli(f"Authorization: Bearer {make_token()}")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, EXPECTED_LAYOUT)

    def test_a_bearer_prefix_on_stdin_is_accepted(self) -> None:
        result = self.run_cli(input=f"Bearer {make_token()}\n")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, EXPECTED_LAYOUT)


class SecretTests(JwtCliCase):
    def test_secret_env_valid(self) -> None:
        result = self.run_cli(make_token(), "--secret-env", "JWT_SECRET", env={"JWT_SECRET": SECRET})
        self.assert_exit(result, 0)
        self.assertTrue(result.stdout.endswith("nbf  not present\nsignature valid\n"), result.stdout)
        self.assertTrue(result.stdout.startswith("Header\n"))

    def test_secret_env_wrong_secret_is_invalid_and_exits_one(self) -> None:
        result = self.run_cli(make_token(), "--secret-env", "JWT_SECRET", env={"JWT_SECRET": "nope"})
        self.assert_exit(result, 1)
        self.assertTrue(result.stdout.endswith("nbf  not present\nsignature INVALID\n"), result.stdout)
        self.assertNotIn("nope", result.stderr)

    def test_secret_env_with_each_hmac_algorithm(self) -> None:
        for alg in DIGESTS:
            with self.subTest(alg=alg):
                token = make_token(header={"alg": alg, "typ": "JWT"})
                result = self.run_cli(
                    token, "--secret-env", "JWT_SECRET", env={"JWT_SECRET": SECRET}
                )
                self.assert_exit(result, 0)
                self.assertIn("signature valid", result.stdout)

    def test_unset_secret_env_is_a_usage_error(self) -> None:
        result = self.run_cli(
            make_token(), "--secret-env", "JZR_TEST_NO_SUCH_VAR", env={"JZR_TEST_NO_SUCH_VAR": None}
        )
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("The environment variable JZR_TEST_NO_SUCH_VAR is not set.", result.stderr)

    def test_secret_file_valid(self) -> None:
        result = self.run_cli(make_token(), "--secret-file", self.secret_file(SECRET.encode()))
        self.assert_exit(result, 0)
        self.assertTrue(result.stdout.endswith("signature valid\n"), result.stdout)

    def test_secret_file_drops_one_trailing_lf_or_crlf(self) -> None:
        for ending in (b"\n", b"\r\n"):
            with self.subTest(ending=ending):
                path = self.secret_file(SECRET.encode() + ending)
                result = self.run_cli(make_token(), "--secret-file", path)
                self.assert_exit(result, 0)
                self.assertIn("signature valid", result.stdout)

    def test_secret_file_drops_only_one_line_ending(self) -> None:
        path = self.secret_file(SECRET.encode() + b"\n\n")
        result = self.run_cli(make_token(), "--secret-file", path)
        self.assert_exit(result, 1)
        self.assertIn("signature INVALID", result.stdout)

    def test_secret_file_is_read_as_raw_bytes(self) -> None:
        raw = bytes(range(0, 256))
        token = make_token()
        head, body, _ = token.split(".")
        mac = hmac.new(raw, f"{head}.{body}".encode("ascii"), hashlib.sha256).digest()
        result = self.run_cli(f"{head}.{body}.{b64(mac)}", "--secret-file", self.secret_file(raw))
        self.assert_exit(result, 0)
        self.assertIn("signature valid", result.stdout)

    def test_missing_secret_file_is_a_usage_error(self) -> None:
        missing = str(Path(self.tmp.name) / "absent.key")
        result = self.run_cli(make_token(), "--secret-file", missing)
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")

    def test_both_secret_options_is_a_usage_error(self) -> None:
        result = self.run_cli(
            make_token(),
            "--secret-env", "JWT_SECRET",
            "--secret-file", self.secret_file(SECRET.encode()),
            env={"JWT_SECRET": SECRET},
        )
        self.assert_exit(result, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("Give --secret-env or --secret-file, not both.", result.stderr)

    def test_alg_none_with_a_secret_reports_on_stderr_after_the_decoded_output(self) -> None:
        token = make_token(header={"alg": "none", "typ": "JWT"})
        result = self.run_cli(token, "--secret-env", "JWT_SECRET", env={"JWT_SECRET": SECRET})
        self.assert_exit(result, 1)
        self.assertIn("cannot check alg none with a shared secret", result.stderr)
        self.assertTrue(result.stdout.startswith("Header\n"), result.stdout)
        self.assertIn("exp  2026-11-22T12:00:00Z", result.stdout)
        self.assertNotIn("signature valid", result.stdout)
        self.assertNotIn("signature INVALID", result.stdout)

    def test_a_crit_header_with_a_secret_reports_on_stderr(self) -> None:
        token = make_token(header={"alg": "HS256", "crit": ["x"], "x": 1})
        result = self.run_cli(token, "--secret-env", "JWT_SECRET", env={"JWT_SECRET": SECRET})
        self.assert_exit(result, 1)
        self.assertIn("cannot check a token with crit header parameters", result.stderr)
        self.assertTrue(result.stdout.startswith("Header\n"))


class RequireValidTests(JwtCliCase):
    def test_a_good_token_passes_with_and_without_a_secret(self) -> None:
        result = self.run_cli(make_token(), "--require-valid")
        self.assert_exit(result, 0)
        self.assertEqual(result.stdout, EXPECTED_LAYOUT)
        result = self.run_cli(
            make_token(), "--require-valid", "--secret-env", "JWT_SECRET", env={"JWT_SECRET": SECRET}
        )
        self.assert_exit(result, 0)
        self.assertNotIn("not valid", result.stderr)

    def test_expired_fails_with_its_reason(self) -> None:
        result = self.run_cli(make_token({"exp": NOW - 1}), "--require-valid")
        self.assert_exit(result, 1)
        self.assertIn("not valid: expired\n", result.stderr)
        self.assertIn("expired", result.stdout)

    def test_exp_equal_to_now_fails(self) -> None:
        result = self.run_cli(make_token({"exp": NOW}), "--require-valid")
        self.assert_exit(result, 1)
        self.assertIn("not valid: expired\n", result.stderr)

    def test_not_yet_valid_fails_with_its_reason(self) -> None:
        result = self.run_cli(make_token({"nbf": NOW + 1}), "--require-valid")
        self.assert_exit(result, 1)
        self.assertIn("not valid: not yet valid\n", result.stderr)

    def test_an_invalid_signature_fails_with_its_reason(self) -> None:
        result = self.run_cli(
            make_token(), "--require-valid", "--secret-env", "JWT_SECRET", env={"JWT_SECRET": "bad"}
        )
        self.assert_exit(result, 1)
        self.assertIn("not valid: signature invalid\n", result.stderr)
        self.assertIn("signature INVALID", result.stdout)

    def test_all_reasons_are_listed_in_order(self) -> None:
        token = make_token({"exp": NOW - 5, "nbf": NOW + 5})
        result = self.run_cli(
            token, "--require-valid", "--secret-env", "JWT_SECRET", env={"JWT_SECRET": "bad"}
        )
        self.assert_exit(result, 1)
        self.assertIn("not valid: expired, not yet valid, signature invalid\n", result.stderr)

    def test_without_the_flag_the_same_token_exits_zero(self) -> None:
        result = self.run_cli(make_token({"exp": NOW - 5, "nbf": NOW + 5}))
        self.assert_exit(result, 0)
        self.assertNotIn("not valid", result.stderr)

    def test_a_missing_signature_check_does_not_count_as_invalid(self) -> None:
        result = self.run_cli(make_token(), "--require-valid")
        self.assertNotIn("signature invalid", result.stderr)


class JsonTests(JwtCliCase):
    def test_shape_without_a_secret(self) -> None:
        result = self.run_cli(make_token(), "--json")
        self.assert_exit(result, 0)
        document = json.loads(result.stdout)
        self.assertEqual(set(document), {"header", "payload", "claims", "signature"})
        self.assertEqual(document["header"], {"alg": "HS256", "typ": "JWT"})
        self.assertEqual(document["payload"], {"sub": "123", "exp": EXP, "iat": IAT})
        self.assertEqual(set(document["claims"]), {"exp", "iat", "nbf"})
        self.assertEqual(
            document["claims"]["exp"],
            {
                "value": EXP,
                "utc": "2026-11-22T12:00:00Z",
                "relative": "in 1 day",
                "status": "not expired",
                "problem": None,
            },
        )
        self.assertEqual(
            document["claims"]["iat"],
            {
                "value": IAT,
                "utc": "2026-11-21T10:00:00Z",
                "relative": "2 hours ago",
                "status": None,
                "problem": None,
            },
        )
        self.assertIsNone(document["claims"]["nbf"])
        self.assertEqual(document["signature"], "not checked")

    def test_the_note_still_appears_on_stderr(self) -> None:
        result = self.run_cli(make_token(), "--json")
        self.assertEqual(result.stderr.strip(), NOTE)

    def test_signature_values(self) -> None:
        cases = ((SECRET, "valid", 0), ("wrong", "invalid", 1))
        for secret, expected, code in cases:
            with self.subTest(secret=secret):
                result = self.run_cli(
                    make_token(), "--json", "--secret-env", "JWT_SECRET", env={"JWT_SECRET": secret}
                )
                self.assert_exit(result, code)
                self.assertEqual(json.loads(result.stdout)["signature"], expected)
                self.assertNotIn("not checked", result.stderr)

    def test_problem_and_status_fields(self) -> None:
        token = make_token({"exp": "soon", "nbf": 10**20})
        document = json.loads(self.run_cli(token, "--json").stdout)
        self.assertEqual(document["claims"]["exp"]["value"], "soon")
        self.assertEqual(document["claims"]["exp"]["problem"], "not a number")
        self.assertIsNone(document["claims"]["exp"]["utc"])
        self.assertEqual(document["claims"]["nbf"]["problem"], "out of range")
        self.assertEqual(document["claims"]["nbf"]["status"], "not yet valid")
        self.assertIsNone(document["claims"]["iat"])

    def test_json_keeps_non_ascii_text_and_stays_one_document(self) -> None:
        result = self.run_cli(make_token({"name": "Zoë"}), "--json")
        self.assertEqual(json.loads(result.stdout)["payload"], {"name": "Zoë"})

    def test_require_valid_with_json_still_exits_one_when_expired(self) -> None:
        result = self.run_cli(make_token({"exp": NOW - 1}), "--json", "--require-valid")
        self.assert_exit(result, 1)
        self.assertEqual(json.loads(result.stdout)["claims"]["exp"]["status"], "expired")
        self.assertIn("not valid: expired", result.stderr)


class FailureTests(JwtCliCase):
    def test_decode_failure_prints_nothing_on_stdout(self) -> None:
        result = self.run_cli("not-a-token-xyz")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(), DECODE_FAILED)

    def test_decode_failure_with_json_prints_nothing_on_stdout(self) -> None:
        result = self.run_cli("not-a-token-xyz", "--json")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")

    def test_decode_failure_from_stdin(self) -> None:
        result = self.run_cli(input="a.b\n")
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(DECODE_FAILED, result.stderr)

    def test_decode_failure_with_a_secret_option(self) -> None:
        result = self.run_cli(
            "x.y.z", "--secret-env", "JWT_SECRET", env={"JWT_SECRET": SECRET}
        )
        self.assert_exit(result, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn(DECODE_FAILED, result.stderr)

    def test_neither_the_token_nor_the_secret_appears_in_stderr(self) -> None:
        secret = "very-private-secret-value"
        wrong = "mismatching-secret-qq"
        good = make_token(secret=secret)
        none_alg = make_token(header={"alg": "none"})
        crit = make_token(header={"alg": "HS256", "crit": ["x"], "x": 1}, secret=secret)
        junk = "junk-token-" + part({"leak": "me"}) + ".!!"
        cases = [
            (good, ["--secret-env", "JWT_SECRET", "--require-valid"], {"JWT_SECRET": wrong}),
            (make_token({"exp": NOW - 1}, secret=secret), ["--require-valid"], {}),
            (none_alg, ["--secret-env", "JWT_SECRET"], {"JWT_SECRET": secret}),
            (crit, ["--secret-env", "JWT_SECRET"], {"JWT_SECRET": secret}),
            (junk, ["--secret-env", "JWT_SECRET"], {"JWT_SECRET": secret}),
            (good, ["--secret-env", "NO_SUCH_JWT_VAR_X"], {"NO_SUCH_JWT_VAR_X": None}),
            (good, ["--secret-env", "JWT_SECRET", "--secret-file", self.secret_file(b"zz")],
             {"JWT_SECRET": secret}),
        ]
        for token, options, env in cases:
            with self.subTest(token=token[:12], options=options):
                result = self.run_cli(token, *options, env=env)
                self.assertNotEqual(result.exit_code, 0, self.describe(result))
                self.assertNotIn(token, result.stderr)
                for piece in token.split("."):
                    if piece:
                        self.assertNotIn(piece, result.stderr)
                self.assertNotIn(secret, result.stderr)
                self.assertNotIn(wrong, result.stderr)


if __name__ == "__main__":
    unittest.main()
