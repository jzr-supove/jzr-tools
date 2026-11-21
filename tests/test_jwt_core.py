"""Decoding, time claims and HMAC checking of JSON Web Tokens (core, no Click)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest

from jzr_tools.core import jwt as jwtc
from jzr_tools.core import timestamp as ts

SECRET = b"unit-test-secret"
NOW = 1_795_262_400  # 2026-11-21T12:00:00Z
NOW_US = NOW * 1_000_000
DIGESTS = {"HS256": hashlib.sha256, "HS384": hashlib.sha384, "HS512": hashlib.sha512}


def b64(data: bytes, *, pad: bool = False) -> str:
    text = base64.urlsafe_b64encode(data).decode("ascii")
    return text if pad else text.rstrip("=")


def part(value: object) -> str:
    return b64(json.dumps(value, separators=(",", ":")).encode("utf-8"))


def sign(alg: str, secret: bytes, signing_input: str) -> str:
    return b64(hmac.new(secret, signing_input.encode("ascii"), DIGESTS[alg]).digest())


def make_token(
    payload: object | None = None,
    header: object | None = None,
    *,
    alg: str = "HS256",
    secret: bytes = SECRET,
    signature: str | None = None,
) -> str:
    header = {"alg": alg, "typ": "JWT"} if header is None else header
    payload = {"sub": "123"} if payload is None else payload
    signing_input = f"{part(header)}.{part(payload)}"
    if signature is None:
        signature = sign(alg, secret, signing_input) if alg in DIGESTS else ""
    return f"{signing_input}.{signature}"


class CleanTokenTests(unittest.TestCase):
    def test_a_bare_token_is_unchanged(self) -> None:
        self.assertEqual(jwtc.clean_token("aaa.bbb.ccc"), "aaa.bbb.ccc")

    def test_surrounding_whitespace_is_stripped(self) -> None:
        self.assertEqual(jwtc.clean_token("  \taaa.bbb.ccc \r\n"), "aaa.bbb.ccc")

    def test_authorization_bearer_header_is_stripped(self) -> None:
        self.assertEqual(jwtc.clean_token("Authorization: Bearer aaa.bbb.ccc"), "aaa.bbb.ccc")

    def test_bearer_alone_is_stripped_in_any_case(self) -> None:
        for text in ("Bearer aaa.bbb.ccc", "bearer aaa.bbb.ccc", "BEARER aaa.bbb.ccc"):
            with self.subTest(text=text):
                self.assertEqual(jwtc.clean_token(text), "aaa.bbb.ccc")

    def test_extra_spacing_around_the_prefixes_is_ignored(self) -> None:
        self.assertEqual(
            jwtc.clean_token("  authorization:   bearer     aaa.bbb.ccc  "), "aaa.bbb.ccc"
        )
        self.assertEqual(jwtc.clean_token("Authorization:Bearer aaa.bbb.ccc"), "aaa.bbb.ccc")


class DecodeTests(unittest.TestCase):
    def assert_decode_failed(self, text: str) -> None:
        with self.assertRaises(jwtc.JwtError) as caught:
            jwtc.decode(text)
        self.assertEqual(str(caught.exception), jwtc.DECODE_FAILED)
        if text.strip():
            self.assertNotIn(text.strip(), str(caught.exception))

    def test_the_failure_text_and_error_type(self) -> None:
        self.assertEqual(
            jwtc.DECODE_FAILED,
            "Cannot decode this JWT: expected three dot-separated parts with JSON "
            "header and payload objects.",
        )
        self.assertTrue(issubclass(jwtc.JwtError, ValueError))

    def test_a_good_token_gives_header_payload_and_parts(self) -> None:
        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"sub": "123", "name": "Zoë", "exp": 1795341600}
        text = make_token(payload, header)
        head, body, signature = text.split(".")
        token = jwtc.decode(text)
        self.assertEqual(token.header, header)
        self.assertEqual(token.payload, payload)
        self.assertEqual(token.signing_input, f"{head}.{body}".encode("ascii"))
        self.assertEqual(token.signature_part, signature)

    def test_member_order_of_the_token_is_kept(self) -> None:
        text = make_token({"z": 1, "a": 2, "m": 3})
        self.assertEqual(list(jwtc.decode(text).payload), ["z", "a", "m"])

    def test_the_bearer_prefix_is_accepted(self) -> None:
        text = make_token()
        self.assertEqual(jwtc.decode(f"Authorization: Bearer {text}\n").payload, {"sub": "123"})

    def test_padded_parts_are_accepted_and_kept_as_given(self) -> None:
        header = b64(b'{"alg":"HS256"}', pad=True)
        payload = b64(b'{"a":"bc"}', pad=True)  # 10 bytes: two "=" of padding
        self.assertTrue(payload.endswith("=="))
        token = jwtc.decode(f"{header}.{payload}.sig")
        self.assertEqual(token.payload, {"a": "bc"})
        self.assertEqual(token.signing_input, f"{header}.{payload}".encode("ascii"))

    def test_an_empty_signature_part_is_allowed(self) -> None:
        token = jwtc.decode(make_token(alg="none"))
        self.assertEqual(token.signature_part, "")
        self.assertEqual(token.header["alg"], "none")

    def test_wrong_number_of_parts_fails(self) -> None:
        good = make_token().rsplit(".", 1)[0]
        for text in (good, good + ".sig.extra", "", "   ", "abc"):
            with self.subTest(text=text):
                self.assert_decode_failed(text)

    def test_parts_that_are_not_json_fail(self) -> None:
        not_json = b64(b"this is not json")
        self.assert_decode_failed(f"{not_json}.{part({'a': 1})}.sig")
        self.assert_decode_failed(f"{part({'alg': 'HS256'})}.{not_json}.sig")

    def test_header_or_payload_that_is_not_an_object_fails(self) -> None:
        self.assert_decode_failed(f"{part({'alg': 'HS256'})}.{part([1, 2])}.sig")
        self.assert_decode_failed(f"{part(['HS256'])}.{part({'a': 1})}.sig")
        self.assert_decode_failed(f"{part({'alg': 'HS256'})}.{part('text')}.sig")
        self.assert_decode_failed(f"{part({'alg': 'HS256'})}.{part(None)}.sig")

    def test_bad_base64_fails(self) -> None:
        good = part({"a": 1})
        self.assert_decode_failed(f"!!!.{good}.sig")
        self.assert_decode_failed(f"{good}.@@@@.sig")
        self.assert_decode_failed(f"{good[:-1] + '+'}.{good}.sig")  # not URL-safe

    def test_bytes_that_are_not_utf8_fail(self) -> None:
        bad = b64(b'{"a":"\xff"}')
        self.assert_decode_failed(f"{bad}.{part({'a': 1})}.sig")

    def test_the_error_never_contains_the_token(self) -> None:
        text = f"{part({'secret-claim': 'hunter2'})}.not-base64!.sig"
        with self.assertRaises(jwtc.JwtError) as caught:
            jwtc.decode(text)
        for piece in text.split("."):
            self.assertNotIn(piece, str(caught.exception))


class VerifyTests(unittest.TestCase):
    def test_a_correct_signature_is_valid_for_each_algorithm(self) -> None:
        for alg in DIGESTS:
            with self.subTest(alg=alg):
                token = jwtc.decode(make_token(alg=alg))
                self.assertIs(jwtc.verify(token, SECRET), True)

    def test_hmac_names_map_to_hash_names(self) -> None:
        self.assertEqual(
            dict(jwtc.HMAC), {"HS256": "sha256", "HS384": "sha384", "HS512": "sha512"}
        )

    def test_a_wrong_secret_is_invalid(self) -> None:
        for alg in DIGESTS:
            with self.subTest(alg=alg):
                token = jwtc.decode(make_token(alg=alg))
                self.assertIs(jwtc.verify(token, b"another-secret"), False)

    def test_a_secret_that_differs_only_by_a_trailing_newline_is_invalid(self) -> None:
        token = jwtc.decode(make_token())
        self.assertIs(jwtc.verify(token, SECRET + b"\n"), False)

    def test_a_changed_payload_with_the_old_signature_is_invalid(self) -> None:
        original = make_token({"role": "user"})
        head, _body, signature = original.split(".")
        forged = f"{head}.{part({'role': 'admin'})}.{signature}"
        self.assertIs(jwtc.verify(jwtc.decode(original), SECRET), True)
        self.assertIs(jwtc.verify(jwtc.decode(forged), SECRET), False)

    def test_a_signature_made_with_a_different_algorithm_is_invalid(self) -> None:
        text = make_token(alg="HS256")
        head, body, _ = text.split(".")
        wrong = sign("HS512", SECRET, f"{head}.{body}")
        self.assertIs(jwtc.verify(jwtc.decode(f"{head}.{body}.{wrong}"), SECRET), False)

    def test_a_signature_that_does_not_decode_is_invalid_not_an_error(self) -> None:
        text = make_token(signature="!!!not-base64!!!")
        self.assertIs(jwtc.verify(jwtc.decode(text), SECRET), False)

    def test_an_empty_signature_is_invalid(self) -> None:
        text = make_token(signature="")
        self.assertIs(jwtc.verify(jwtc.decode(text), SECRET), False)

    def test_alg_none_cannot_be_checked(self) -> None:
        token = jwtc.decode(make_token(alg="none"))
        with self.assertRaises(jwtc.JwtError) as caught:
            jwtc.verify(token, SECRET)
        self.assertEqual(str(caught.exception), "cannot check alg none with a shared secret")

    def test_asymmetric_algorithms_cannot_be_checked(self) -> None:
        token = jwtc.decode(make_token(alg="RS256", signature="abc"))
        with self.assertRaises(jwtc.JwtError) as caught:
            jwtc.verify(token, SECRET)
        self.assertEqual(str(caught.exception), "cannot check alg RS256 with a shared secret")

    def test_alg_names_are_case_sensitive(self) -> None:
        token = jwtc.decode(make_token(header={"alg": "hs256"}, signature="abc"))
        with self.assertRaises(jwtc.JwtError) as caught:
            jwtc.verify(token, SECRET)
        self.assertEqual(str(caught.exception), "cannot check alg hs256 with a shared secret")

    def test_a_missing_or_non_string_alg_cannot_be_checked(self) -> None:
        for header in ({"typ": "JWT"}, {"alg": 256}, {"alg": None}, {"alg": ["HS256"]}):
            with self.subTest(header=header):
                token = jwtc.decode(make_token(header=header, signature="abc"))
                with self.assertRaises(jwtc.JwtError) as caught:
                    jwtc.verify(token, SECRET)
                self.assertEqual(
                    str(caught.exception), "cannot check this alg with a shared secret"
                )

    def test_a_crit_header_is_refused_even_with_a_good_signature(self) -> None:
        header = {"alg": "HS256", "crit": ["exp"], "exp": 1}
        token = jwtc.decode(make_token(header=header))
        with self.assertRaises(jwtc.JwtError) as caught:
            jwtc.verify(token, SECRET)
        self.assertEqual(
            str(caught.exception), "cannot check a token with crit header parameters"
        )

    def test_errors_never_contain_the_secret(self) -> None:
        token = jwtc.decode(make_token(alg="none"))
        with self.assertRaises(jwtc.JwtError) as caught:
            jwtc.verify(token, SECRET)
        self.assertNotIn(SECRET.decode(), str(caught.exception))


class TimeClaimsTests(unittest.TestCase):
    def claims(self, payload: dict) -> dict[str, jwtc.Claim]:
        exp, iat, nbf = jwtc.time_claims(payload, NOW_US)
        self.assertEqual((exp.name, iat.name, nbf.name), ("exp", "iat", "nbf"))
        return {"exp": exp, "iat": iat, "nbf": nbf}

    def test_absent_claims(self) -> None:
        for claim in self.claims({"sub": "x"}).values():
            with self.subTest(name=claim.name):
                self.assertFalse(claim.present)
                self.assertIsNone(claim.value)
                self.assertIsNone(claim.utc)
                self.assertIsNone(claim.relative)
                self.assertIsNone(claim.status)
                self.assertIsNone(claim.problem)

    def test_exp_in_the_future_is_not_expired(self) -> None:
        exp = self.claims({"exp": NOW + 86400})["exp"]
        self.assertTrue(exp.present)
        self.assertEqual(exp.value, NOW + 86400)
        self.assertEqual(exp.utc, "2026-11-22T12:00:00Z")
        self.assertEqual(exp.relative, "in 1 day")
        self.assertEqual(exp.status, "not expired")
        self.assertIsNone(exp.problem)

    def test_exp_in_the_past_is_expired(self) -> None:
        exp = self.claims({"exp": NOW - 7200})["exp"]
        self.assertEqual(exp.utc, "2026-11-21T10:00:00Z")
        self.assertEqual(exp.relative, "2 hours ago")
        self.assertEqual(exp.status, "expired")

    def test_exp_equal_to_now_is_expired(self) -> None:
        exp = self.claims({"exp": NOW})["exp"]
        self.assertEqual(exp.status, "expired")
        self.assertEqual(exp.relative, "now")

    def test_exp_one_second_ahead_is_not_expired(self) -> None:
        self.assertEqual(self.claims({"exp": NOW + 1})["exp"].status, "not expired")

    def test_nbf_in_the_future_is_not_yet_valid(self) -> None:
        nbf = self.claims({"nbf": NOW + 7200})["nbf"]
        self.assertEqual(nbf.status, "not yet valid")
        self.assertEqual(nbf.utc, "2026-11-21T14:00:00Z")
        self.assertEqual(nbf.relative, "in 2 hours")

    def test_nbf_in_the_past_or_now_has_no_status(self) -> None:
        for value in (NOW - 60, NOW):
            with self.subTest(value=value):
                nbf = self.claims({"nbf": value})["nbf"]
                self.assertTrue(nbf.present)
                self.assertIsNone(nbf.status)
                self.assertEqual(nbf.utc, ts.utc_text(value * 1_000_000))

    def test_iat_never_has_a_status(self) -> None:
        for value in (NOW - 7200, NOW + 7200):
            with self.subTest(value=value):
                iat = self.claims({"iat": value})["iat"]
                self.assertTrue(iat.present)
                self.assertIsNone(iat.status)
                self.assertEqual(iat.utc, ts.utc_text(value * 1_000_000))
                self.assertEqual(iat.relative, ts.relative_text(value * 1_000_000, NOW_US))

    def test_texts_come_from_the_timestamp_module(self) -> None:
        value = 1_700_000_000
        claim = self.claims({"exp": value})["exp"]
        self.assertEqual(claim.utc, ts.utc_text(value * 1_000_000))
        self.assertEqual(claim.relative, ts.relative_text(value * 1_000_000, NOW_US))

    def test_negative_values_are_before_1970(self) -> None:
        exp = self.claims({"exp": -86400})["exp"]
        self.assertEqual(exp.utc, "1969-12-31T00:00:00Z")
        self.assertEqual(exp.status, "expired")

    def test_text_and_bool_values_are_not_numbers(self) -> None:
        for value in ("1795341600", "soon", True, False):
            for name in ("exp", "iat", "nbf"):
                with self.subTest(name=name, value=value):
                    claim = self.claims({name: value})[name]
                    self.assertTrue(claim.present)
                    self.assertEqual(claim.value, value)
                    self.assertEqual(claim.problem, "not a number")
                    self.assertIsNone(claim.utc)
                    self.assertIsNone(claim.relative)
                    self.assertIsNone(claim.status)

    def test_huge_values_are_out_of_range_but_still_get_a_status(self) -> None:
        huge = 10**20
        exp = self.claims({"exp": huge})["exp"]
        self.assertEqual(exp.problem, "out of range")
        self.assertEqual(exp.value, huge)
        self.assertIsNone(exp.utc)
        self.assertIsNone(exp.relative)
        self.assertEqual(exp.status, "not expired")
        self.assertEqual(self.claims({"exp": -huge})["exp"].status, "expired")
        self.assertEqual(self.claims({"nbf": huge})["nbf"].status, "not yet valid")
        self.assertIsNone(self.claims({"nbf": -huge})["nbf"].status)
        self.assertEqual(self.claims({"iat": huge})["iat"].problem, "out of range")

    def test_a_year_10000_value_is_out_of_range(self) -> None:
        exp = self.claims({"exp": 253_402_300_800})["exp"]
        self.assertEqual(exp.problem, "out of range")
        self.assertEqual(exp.status, "not expired")


if __name__ == "__main__":
    unittest.main()
