"""Base64 and URL encoding, and URL parsing, in jzr_tools.core.encoding."""

from __future__ import annotations

import dataclasses
import unittest

from jzr_tools.core.encoding import (
    BAD_ESCAPE,
    BAD_PORT,
    INVALID_BASE64,
    NOT_ABSOLUTE,
    NOT_UTF8,
    EncodingError,
    b64_decode,
    b64_encode,
    parse_url,
    url_decode,
    url_encode,
)

INVALID_BASE64_TEXT = "Invalid Base64 input. Check the alphabet and padding."


class MessageTextTests(unittest.TestCase):
    def test_messages_are_the_user_facing_texts(self) -> None:
        self.assertEqual(INVALID_BASE64, INVALID_BASE64_TEXT)
        self.assertEqual(BAD_ESCAPE, "Invalid percent-encoding: % must be followed by two hex digits.")
        self.assertEqual(NOT_UTF8, "The decoded text is not valid UTF-8.")
        self.assertEqual(NOT_ABSOLUTE, "Not an absolute URL: it needs a scheme such as https://.")
        self.assertEqual(BAD_PORT, "The port is not a number from 0 to 65535.")

    def test_encoding_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(EncodingError, ValueError))


class Base64EncodeTests(unittest.TestCase):
    def test_rfc_4648_vectors(self) -> None:
        vectors = {
            b"": "",
            b"f": "Zg==",
            b"fo": "Zm8=",
            b"foo": "Zm9v",
            b"foob": "Zm9vYg==",
            b"fooba": "Zm9vYmE=",
            b"foobar": "Zm9vYmFy",
        }
        for data, expected in vectors.items():
            with self.subTest(data=data):
                self.assertEqual(b64_encode(data), expected)

    def test_url_safe_has_no_padding(self) -> None:
        vectors = {
            b"": "",
            b"f": "Zg",
            b"fo": "Zm8",
            b"foo": "Zm9v",
            b"foob": "Zm9vYg",
            b"fooba": "Zm9vYmE",
            b"foobar": "Zm9vYmFy",
        }
        for data, expected in vectors.items():
            with self.subTest(data=data):
                self.assertEqual(b64_encode(data, url_safe=True), expected)

    def test_alphabets_differ_in_the_last_two_characters(self) -> None:
        data = b"\xfb\xff\xfe"
        self.assertEqual(b64_encode(data), "+//+")
        self.assertEqual(b64_encode(data, url_safe=True), "-__-")

    def test_non_ascii_text_as_utf8_bytes(self) -> None:
        self.assertEqual(b64_encode("é€😀".encode("utf-8")), "w6nigqzwn5iA")

    def test_long_input_stays_on_one_line(self) -> None:
        out = b64_encode(bytes(range(256)) * 4)
        self.assertNotIn("\n", out)
        self.assertEqual(len(out), 1368)


class Base64DecodeTests(unittest.TestCase):
    def test_rfc_4648_vectors(self) -> None:
        for data in [b"", b"f", b"fo", b"foo", b"foob", b"fooba", b"foobar"]:
            with self.subTest(data=data):
                self.assertEqual(b64_decode(b64_encode(data)), data)
                self.assertEqual(b64_decode(b64_encode(data, url_safe=True), url_safe=True), data)

    def test_known_values(self) -> None:
        self.assertEqual(b64_decode("aGk="), b"hi")
        self.assertEqual(b64_decode("w6nigqzwn5iA").decode("utf-8"), "é€😀")
        self.assertEqual(b64_decode("+//+"), b"\xfb\xff\xfe")
        self.assertEqual(b64_decode("-__-", url_safe=True), b"\xfb\xff\xfe")

    def test_every_byte_value_round_trips(self) -> None:
        data = bytes(range(256))
        self.assertEqual(b64_decode(b64_encode(data)), data)
        self.assertEqual(b64_decode(b64_encode(data, url_safe=True), url_safe=True), data)

    def test_whitespace_and_newlines_are_ignored(self) -> None:
        self.assertEqual(b64_decode("Zm9v\nYmFy\n"), b"foobar")
        self.assertEqual(b64_decode("  Zm 9v\tYm\r\nFy  "), b"foobar")
        self.assertEqual(b64_decode("Zm9vYg\n==", url_safe=False), b"foob")
        self.assertEqual(b64_decode("Zm9v\nYg", url_safe=True), b"foob")

    def test_empty_and_blank_input_decode_to_nothing(self) -> None:
        self.assertEqual(b64_decode(""), b"")
        self.assertEqual(b64_decode(" \n\t"), b"")
        self.assertEqual(b64_decode("", url_safe=True), b"")

    def assert_refused(self, text: str, *, url_safe: bool = False) -> None:
        with self.assertRaises(EncodingError) as ctx:
            b64_decode(text, url_safe=url_safe)
        self.assertEqual(str(ctx.exception), INVALID_BASE64_TEXT)

    def test_characters_outside_the_alphabet_are_refused(self) -> None:
        for text in ["Zm9v!", "Zm9v YmFy!", "Zm 9é", "Zm9v\x00"]:
            with self.subTest(text=text):
                self.assert_refused(text)
                self.assert_refused(text, url_safe=True)

    def test_the_other_alphabets_characters_are_refused(self) -> None:
        self.assert_refused("-__-")
        self.assert_refused("Zm-v")
        self.assert_refused("Zm_v")
        self.assert_refused("+//+", url_safe=True)
        self.assert_refused("Zm+v", url_safe=True)
        self.assert_refused("Zm/v", url_safe=True)

    def test_padding_in_the_wrong_place_is_refused(self) -> None:
        for text in ["=Zg=", "Zg=a", "Z=g=", "==Zg", "=", "===="]:
            with self.subTest(text=text):
                self.assert_refused(text)
                self.assert_refused(text, url_safe=True)

    def test_three_or_more_padding_characters_are_refused(self) -> None:
        self.assert_refused("Z===")
        self.assert_refused("Zg===")
        self.assert_refused("Zg===", url_safe=True)
        self.assert_refused("Zm9v====")

    def test_impossible_lengths_are_refused(self) -> None:
        self.assert_refused("Z")
        self.assert_refused("Zm9vY")
        self.assert_refused("Zm9vYmFyZ")
        self.assert_refused("Z", url_safe=True)
        self.assert_refused("Zm9vY", url_safe=True)

    def test_wrong_padding_count_is_refused(self) -> None:
        self.assert_refused("Zg=")  # needs two
        self.assert_refused("Zm8==")  # needs one
        self.assert_refused("Zm9v=")  # needs none
        self.assert_refused("Zg=", url_safe=True)
        self.assert_refused("Zm8==", url_safe=True)
        self.assert_refused("Zm9v=", url_safe=True)

    def test_missing_padding_is_refused_for_standard(self) -> None:
        self.assert_refused("Zg")
        self.assert_refused("Zm8")
        self.assert_refused("Zm9vYg")

    def test_missing_padding_is_accepted_for_url_safe(self) -> None:
        self.assertEqual(b64_decode("Zg", url_safe=True), b"f")
        self.assertEqual(b64_decode("Zm8", url_safe=True), b"fo")
        self.assertEqual(b64_decode("Zm9vYg", url_safe=True), b"foob")

    def test_correct_padding_is_accepted_for_url_safe(self) -> None:
        self.assertEqual(b64_decode("Zg==", url_safe=True), b"f")
        self.assertEqual(b64_decode("Zm8=", url_safe=True), b"fo")

    def test_non_canonical_trailing_bits_are_refused(self) -> None:
        self.assertEqual(b64_decode("aGk="), b"hi")
        self.assert_refused("aGl=")
        self.assert_refused("Zh==")
        self.assert_refused("Zm9=")
        self.assert_refused("aGl", url_safe=True)
        self.assert_refused("Zh", url_safe=True)

    def test_the_message_never_contains_the_input(self) -> None:
        secret = "TopSecretToken!"
        with self.assertRaises(EncodingError) as ctx:
            b64_decode(secret)
        self.assertNotIn("TopSecret", str(ctx.exception))
        self.assertNotIn("TopSecret", repr(ctx.exception.args))


class UrlEncodeTests(unittest.TestCase):
    def test_matches_encode_uri_component(self) -> None:
        self.assertEqual(
            url_encode("a b&c=d/é!~*'()"),
            "a%20b%26c%3Dd%2F%C3%A9!~*'()",
        )

    def test_unreserved_characters_are_kept(self) -> None:
        text = "AZaz09-_.!~*'()"
        self.assertEqual(url_encode(text), text)

    def test_reserved_characters_are_encoded_with_upper_case_hex(self) -> None:
        self.assertEqual(url_encode(":/?#[]@$&+,;="), "%3A%2F%3F%23%5B%5D%40%24%26%2B%2C%3B%3D")
        self.assertEqual(url_encode("ÿ"), "%C3%BF")

    def test_other_ascii_is_encoded(self) -> None:
        self.assertEqual(url_encode('"%<>\\^`{|}\t\n'), "%22%25%3C%3E%5C%5E%60%7B%7C%7D%09%0A")

    def test_astral_characters_use_four_utf8_bytes(self) -> None:
        self.assertEqual(url_encode("\U0001F600"), "%F0%9F%98%80")

    def test_empty(self) -> None:
        self.assertEqual(url_encode(""), "")

    def test_default_mode_encodes_space_as_percent_20(self) -> None:
        self.assertEqual(url_encode("a b"), "a%20b")
        self.assertEqual(url_encode("a+b"), "a%2Bb")

    def test_form_mode_uses_plus_for_space_and_keeps_plus_escaped(self) -> None:
        self.assertEqual(url_encode("a b+c", form=True), "a+b%2Bc")
        self.assertEqual(url_encode("a b&c=d/é!~*'()", form=True), "a+b%26c%3Dd%2F%C3%A9!~*'()")


class UrlDecodeTests(unittest.TestCase):
    def test_decodes_percent_escapes_as_utf8(self) -> None:
        self.assertEqual(url_decode("a%20b%26c%3Dd%2F%C3%A9"), "a b&c=d/é")
        self.assertEqual(url_decode("%F0%9F%98%80"), "\U0001F600")

    def test_lower_case_hex_is_accepted(self) -> None:
        self.assertEqual(url_decode("%c3%a9"), "é")

    def test_plus_stays_a_plus_by_default(self) -> None:
        self.assertEqual(url_decode("a+b"), "a+b")

    def test_form_mode_turns_plus_into_space(self) -> None:
        self.assertEqual(url_decode("a+b%20c", form=True), "a b c")

    def test_form_mode_keeps_an_escaped_plus(self) -> None:
        self.assertEqual(url_decode("a%2Bb+c", form=True), "a+b c")

    def test_text_without_escapes_is_unchanged(self) -> None:
        self.assertEqual(url_decode("plain-text_1.2"), "plain-text_1.2")
        self.assertEqual(url_decode(""), "")

    def test_round_trips(self) -> None:
        for text in ["a b&c=d/é!~*'()", "100% sure", "😀 + 😀", "line\nbreak", ""]:
            with self.subTest(text=text):
                self.assertEqual(url_decode(url_encode(text)), text)
                self.assertEqual(url_decode(url_encode(text, form=True), form=True), text)

    def test_bad_escapes_are_refused(self) -> None:
        for text in ["%", "%4", "%zz", "100%", "a%2", "%G1", "%1G", "ok%20then%"]:
            for form in (False, True):
                with self.subTest(text=text, form=form):
                    with self.assertRaises(EncodingError) as ctx:
                        url_decode(text, form=form)
                    self.assertEqual(str(ctx.exception), BAD_ESCAPE)

    def test_bytes_that_are_not_utf8_are_refused_not_replaced(self) -> None:
        for text in ["%FF", "%C3", "a%80b", "%C0%AF"]:
            with self.subTest(text=text):
                with self.assertRaises(EncodingError) as ctx:
                    url_decode(text)
                self.assertEqual(str(ctx.exception), NOT_UTF8)

    def test_error_messages_do_not_contain_the_input(self) -> None:
        with self.assertRaises(EncodingError) as ctx:
            url_decode("topsecret%zz")
        self.assertNotIn("topsecret", str(ctx.exception))
        with self.assertRaises(EncodingError) as ctx:
            url_decode("topsecret%FF")
        self.assertNotIn("topsecret", str(ctx.exception))


FULL_URL = "https://bob:s3cr3tpw@Example.COM:8443/a/b%20c?q=hello+world&q=again&=x&flag&k=%C3%A9#top"


class ParseUrlTests(unittest.TestCase):
    def test_full_url(self) -> None:
        parsed = parse_url(FULL_URL)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.host, "example.com")
        self.assertEqual(parsed.port, 8443)
        self.assertEqual(parsed.user, "bob")
        self.assertTrue(parsed.has_password)
        self.assertEqual(parsed.path, "/a/b%20c")
        self.assertEqual(parsed.fragment, "top")
        self.assertEqual(
            parsed.query,
            (
                ("q", "hello world"),
                ("q", "again"),
                ("", "x"),
                ("flag", ""),
                ("k", "é"),
            ),
        )

    def test_query_is_a_tuple_of_pairs(self) -> None:
        parsed = parse_url("https://h/?a=1&b=2")
        self.assertIsInstance(parsed.query, tuple)
        self.assertEqual(parsed.query, (("a", "1"), ("b", "2")))

    def test_the_scheme_is_lower_cased(self) -> None:
        self.assertEqual(parse_url("HTTPS://example.com/").scheme, "https")

    def test_surrounding_whitespace_is_stripped(self) -> None:
        parsed = parse_url("  \nhttps://example.com/x\n ")
        self.assertEqual(parsed.host, "example.com")
        self.assertEqual(parsed.path, "/x")

    def test_missing_parts_are_none_or_empty(self) -> None:
        parsed = parse_url("https://example.com")
        self.assertEqual(parsed.host, "example.com")
        self.assertIsNone(parsed.port)
        self.assertIsNone(parsed.user)
        self.assertFalse(parsed.has_password)
        self.assertEqual(parsed.path, "")
        self.assertIsNone(parsed.fragment)
        self.assertEqual(parsed.query, ())

    def test_an_empty_fragment_is_none(self) -> None:
        self.assertIsNone(parse_url("https://example.com/#").fragment)

    def test_user_without_password(self) -> None:
        parsed = parse_url("ftp://alice@files.example.com/pub")
        self.assertEqual(parsed.user, "alice")
        self.assertFalse(parsed.has_password)

    def test_user_is_percent_decoded(self) -> None:
        parsed = parse_url("https://b%40b:pw@example.com/")
        self.assertEqual(parsed.user, "b@b")
        self.assertTrue(parsed.has_password)

    def test_plus_in_query_is_a_space_and_escaped_plus_is_a_plus(self) -> None:
        parsed = parse_url("https://h/?a=x+y&b=x%2By")
        self.assertEqual(parsed.query, (("a", "x y"), ("b", "x+y")))

    def test_bad_escape_in_query_is_left_as_written(self) -> None:
        parsed = parse_url("https://h/?a=%zz&b=100%")
        self.assertEqual(parsed.query, (("a", "%zz"), ("b", "100%")))

    def test_query_that_is_not_utf8_is_refused(self) -> None:
        with self.assertRaises(EncodingError) as ctx:
            parse_url("https://h/?a=%FF")
        self.assertEqual(str(ctx.exception), NOT_UTF8)

    def test_ipv6_host_has_no_brackets(self) -> None:
        parsed = parse_url("http://[::1]:8080/x")
        self.assertEqual(parsed.host, "::1")
        self.assertEqual(parsed.port, 8080)
        self.assertEqual(parsed.path, "/x")

    def test_url_without_a_host(self) -> None:
        parsed = parse_url("mailto:alice@example.com?subject=Hi+there")
        self.assertEqual(parsed.scheme, "mailto")
        self.assertIsNone(parsed.host)
        self.assertIsNone(parsed.port)
        self.assertIsNone(parsed.user)
        self.assertFalse(parsed.has_password)
        self.assertEqual(parsed.path, "alice@example.com")
        self.assertEqual(parsed.query, (("subject", "Hi there"),))

    def test_port_bounds(self) -> None:
        self.assertEqual(parse_url("http://h:0/").port, 0)
        self.assertEqual(parse_url("http://h:65535/").port, 65535)

    def test_ports_out_of_range_are_refused(self) -> None:
        for text in ["http://h:99999/", "http://h:65536/"]:
            with self.subTest(text=text):
                with self.assertRaises(EncodingError) as ctx:
                    parse_url(text)
                self.assertEqual(str(ctx.exception), BAD_PORT)

    def test_a_port_that_is_not_a_number_is_refused(self) -> None:
        with self.assertRaises(EncodingError) as ctx:
            parse_url("http://h:abc/")
        self.assertEqual(str(ctx.exception), BAD_PORT)

    def test_relative_references_are_refused(self) -> None:
        for text in ["/a/b", "example.com/x", "//example.com/x", "?q=1", "#frag", "", "   "]:
            with self.subTest(text=text):
                with self.assertRaises(EncodingError) as ctx:
                    parse_url(text)
                self.assertEqual(str(ctx.exception), NOT_ABSOLUTE)

    def test_the_password_is_not_kept_anywhere(self) -> None:
        parsed = parse_url(FULL_URL)
        self.assertTrue(dataclasses.is_dataclass(parsed))
        self.assertNotIn("s3cr3tpw", repr(parsed))
        self.assertNotIn("s3cr3tpw", str(parsed))
        for value in dataclasses.asdict(parsed).values():
            self.assertNotIn("s3cr3tpw", repr(value))
        slots = [name for klass in type(parsed).__mro__ for name in getattr(klass, "__slots__", ())]
        attributes = getattr(parsed, "__dict__", {})
        for name in list(attributes) + slots:
            self.assertNotIn("s3cr3tpw", repr(getattr(parsed, name)))

    def test_the_result_is_frozen(self) -> None:
        parsed = parse_url("https://example.com/")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parsed.host = "other"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
