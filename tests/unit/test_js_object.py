"""The bounded JavaScript-object-literal parser.

Covers the grammar that is actually present in Croma payloads (JSON plus a bare
``undefined``) and, just as importantly, that everything outside that grammar fails
closed rather than being guessed at.
"""

from __future__ import annotations

import unittest

from croma_intel.domain.errors import JsParseError
from croma_intel.domain.product import UNKNOWN
from croma_intel.extraction.js_object import (
    MAX_DEPTH,
    find_value_span,
    parse,
    parse_span,
    scan_object_members,
    skip_value,
)


class TestJsonSubset(unittest.TestCase):
    def test_scalars(self):
        self.assertEqual(parse('"x"'), "x")
        self.assertEqual(parse("12"), 12)
        self.assertEqual(parse("-4"), -4)
        self.assertEqual(parse("1.5"), 1.5)
        self.assertEqual(parse("1e3"), 1000.0)
        self.assertIs(parse("true"), True)
        self.assertIs(parse("false"), False)
        self.assertIsNone(parse("null"))

    def test_integers_stay_integers(self):
        """displayOrder is compared and sorted; 15 must not become 15.0."""
        self.assertIsInstance(parse("15"), int)
        self.assertIsInstance(parse("15.0"), float)

    def test_nested_structures(self):
        self.assertEqual(
            parse('{"a":[1,{"b":null}],"c":{"d":[]}}'),
            {"a": [1, {"b": None}], "c": {"d": []}},
        )

    def test_empty_containers(self):
        self.assertEqual(parse("{}"), {})
        self.assertEqual(parse("[]"), [])

    def test_whitespace_tolerated(self):
        self.assertEqual(parse('  {\n "a" :\t1 ,\r\n "b":2 }  '), {"a": 1, "b": 2})


class TestStringDecoding(unittest.TestCase):
    def test_unicode_escapes(self):
        """Croma escapes every '/' in its URLs as \\u002F."""
        self.assertEqual(parse(r'"/p/323360"'), "/p/323360")

    def test_escaped_quote_does_not_terminate_the_string(self):
        self.assertEqual(parse(r'"a \" b"'), 'a " b')

    def test_escaped_backslash_before_quote(self):
        self.assertEqual(parse(r'"ends with backslash \\"'), "ends with backslash \\")

    def test_surrogate_pair(self):
        self.assertEqual(parse(r'"😀"'), "\U0001f600")

    def test_brace_inside_a_string_does_not_close_the_object(self):
        """A '}' in a product description must not truncate the payload."""
        self.assertEqual(parse('{"d":"a } b { c","e":1}'), {"d": "a } b { c", "e": 1})

    def test_unterminated_string_fails(self):
        with self.assertRaises(JsParseError):
            parse('{"a":"never closed}')


class TestUndefinedHandling(unittest.TestCase):
    """``undefined`` is the whole reason this parser exists."""

    def test_undefined_becomes_unknown_not_none(self):
        parsed = parse('{"errorMessage":undefined}')
        self.assertIs(parsed["errorMessage"], UNKNOWN)
        self.assertIsNot(parsed["errorMessage"], None)

    def test_undefined_is_distinguishable_from_null(self):
        parsed = parse('{"a":undefined,"b":null}')
        self.assertIs(parsed["a"], UNKNOWN)
        self.assertIsNone(parsed["b"])

    def test_undefined_in_an_array(self):
        parsed = parse("[1,undefined,3]")
        self.assertEqual(len(parsed), 3)
        self.assertIs(parsed[1], UNKNOWN)

    def test_the_word_undefined_inside_a_string_is_left_alone(self):
        """Exactly what a global string replacement would corrupt."""
        parsed = parse('{"note":"behaviour is undefined here","v":undefined}')
        self.assertEqual(parsed["note"], "behaviour is undefined here")
        self.assertIs(parsed["v"], UNKNOWN)

    def test_a_key_named_undefined_is_still_a_key(self):
        self.assertEqual(parse('{"undefined":1}'), {"undefined": 1})


class TestFailsClosed(unittest.TestCase):
    """Rule 10: refuse rather than guess."""

    def test_unknown_bare_token_rejected(self):
        with self.assertRaises(JsParseError) as ctx:
            parse('{"a":NaN}')
        self.assertIn("NaN", str(ctx.exception))

    def test_single_quoted_string_rejected(self):
        with self.assertRaises(JsParseError):
            parse("{'a':1}")

    def test_unquoted_key_rejected(self):
        with self.assertRaises(JsParseError):
            parse("{a:1}")

    def test_trailing_comma_rejected(self):
        with self.assertRaises(JsParseError):
            parse('{"a":1,}')
        with self.assertRaises(JsParseError):
            parse("[1,]")

    def test_trailing_content_rejected(self):
        with self.assertRaises(JsParseError):
            parse('{"a":1} rubbish')

    def test_missing_colon_rejected(self):
        with self.assertRaises(JsParseError):
            parse('{"a" 1}')

    def test_error_carries_a_position(self):
        with self.assertRaises(JsParseError) as ctx:
            parse('{"a":oops}')
        self.assertGreater(ctx.exception.position, 0)

    def test_runaway_nesting_fails_closed(self):
        with self.assertRaises(JsParseError):
            parse("[" * (MAX_DEPTH + 5) + "]" * (MAX_DEPTH + 5))


class TestLazyScanning(unittest.TestCase):
    """Locating a value without materialising it — the bounded-extraction primitive."""

    DOC = '{"keep":{"n":1},"skip":[1,2,{"deep":"x"}],"also":"y"}'

    def test_members_are_located_not_parsed(self):
        members = scan_object_members(self.DOC, 0)
        self.assertEqual(sorted(members), ["also", "keep", "skip"])
        start, end = members["keep"]
        self.assertEqual(self.DOC[start:end], '{"n":1}')

    def test_only_the_requested_member_is_materialised(self):
        members = scan_object_members(self.DOC, 0)
        self.assertEqual(parse_span(self.DOC, members["keep"]), {"n": 1})

    def test_skipping_does_not_validate_the_skipped_region(self):
        """A member nobody asks for cannot break extraction of one they do.

        This is the property that lets a 392 KB malformed chrome reducer sit
        harmlessly beside the 49 KB the product actually needs.
        """
        doc = '{"good":{"a":1},"bad":{"b":NaN}}'
        members = scan_object_members(doc, 0)
        self.assertEqual(parse_span(doc, members["good"]), {"a": 1})
        with self.assertRaises(JsParseError):
            parse_span(doc, members["bad"])

    def test_span_of_a_nested_value(self):
        start, end = find_value_span(self.DOC, self.DOC.index("["))
        self.assertEqual(self.DOC[start:end], '[1,2,{"deep":"x"}]')

    def test_skip_value_handles_strings_containing_delimiters(self):
        doc = '["a,b}c",2]'
        self.assertEqual(skip_value(doc, 0), len(doc))

    def test_duplicate_keys_resolve_deterministically(self):
        members = scan_object_members('{"a":1,"a":2}', 0)
        start, end = members["a"]
        self.assertEqual('{"a":1,"a":2}'[start:end], "1")

    def test_scan_requires_an_object(self):
        with self.assertRaises(JsParseError):
            scan_object_members("[1,2]", 0)


if __name__ == "__main__":
    unittest.main()
