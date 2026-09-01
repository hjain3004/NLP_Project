"""The bounded ADR-0003 validator.

Each of the thirteen supported keywords gets at least one accept case and one
reject case asserting the exact ``(json_pointer, keyword)`` tuple. Plus:
single-file ``$defs``/``$ref`` resolution, the mandatory raise on an
unsupported keyword, the Task 9 Step 6 snippet verbatim, and a sweep proving
every committed schema stays inside the subset.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

from agentctl.jsonschema_subset import SUPPORTED_KEYWORDS, UnsupportedKeyword, validate


def _repo_root() -> pathlib.Path:
    out = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
    return pathlib.Path(out)


class KeywordMatrixTests(unittest.TestCase):
    """One accept + one reject per supported keyword."""

    def _reject(self, schema: dict, instance: object) -> list[tuple[str, str, str]]:
        ok, errs = validate(instance, schema)
        self.assertFalse(ok, f"expected rejection, got clean validation: {schema}")
        return errs

    def _accept(self, schema: dict, instance: object) -> None:
        ok, errs = validate(instance, schema)
        self.assertTrue(ok, f"expected acceptance, got {errs}")

    def assertTuple(self, errs, pointer: str, keyword: str) -> None:
        self.assertIn(
            (pointer, keyword),
            [(p, k) for (p, k, _m) in errs],
            f"no ({pointer!r}, {keyword!r}) in {errs}",
        )

    def test_type(self) -> None:
        self._accept({"type": "integer"}, 1)
        self.assertTuple(self._reject({"type": "integer"}, "x"), "", "type")

    def test_required(self) -> None:
        self._accept({"type": "object", "required": ["a"]}, {"a": 1})
        self.assertTuple(self._reject({"type": "object", "required": ["a"]}, {}), "", "required")

    def test_properties(self) -> None:
        schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
        self._accept(schema, {"a": 1})
        self._accept(schema, {})  # absent property is fine
        errs = self._reject(schema, {"a": "x"})
        self.assertEqual((errs[0][0], errs[0][1]), ("/a", "type"))
        self.assertTuple(errs, "", "properties")

    def test_additional_properties(self) -> None:
        schema = {"type": "object", "properties": {"a": {}}, "additionalProperties": False}
        self._accept(schema, {"a": 1})
        self.assertTuple(self._reject(schema, {"a": 1, "b": 2}), "/b", "additionalProperties")

    def test_items(self) -> None:
        schema = {"type": "array", "items": {"type": "integer"}}
        self._accept(schema, [1, 2, 3])
        errs = self._reject(schema, [1, "x"])
        self.assertTuple(errs, "/1", "type")
        self.assertTuple(errs, "", "items")

    def test_enum(self) -> None:
        self._accept({"enum": ["a", "b"]}, "a")
        self.assertTuple(self._reject({"enum": ["a", "b"]}, "c"), "", "enum")

    def test_const(self) -> None:
        self._accept({"const": "event"}, "event")
        self.assertTuple(self._reject({"const": "event"}, "x"), "", "const")

    def test_pattern(self) -> None:
        schema = {"type": "string", "pattern": "^[0-9a-f]{4}$"}
        self._accept(schema, "a1b2")
        self.assertTuple(self._reject(schema, "zzzz"), "", "pattern")

    def test_min_items(self) -> None:
        self._accept({"type": "array", "minItems": 2}, [1, 2])
        self.assertTuple(self._reject({"type": "array", "minItems": 2}, [1]), "", "minItems")

    def test_min_length(self) -> None:
        self._accept({"type": "string", "minLength": 3}, "abc")
        self.assertTuple(self._reject({"type": "string", "minLength": 3}, "ab"), "", "minLength")

    def test_maximum(self) -> None:
        self._accept({"type": "integer", "maximum": 3}, 2)
        self.assertTuple(self._reject({"type": "integer", "maximum": 3}, 5), "", "maximum")

    def test_format_date_time(self) -> None:
        schema = {"type": "string", "format": "date-time"}
        self._accept(schema, "2026-09-01T12:00:00Z")
        self.assertTuple(self._reject(schema, "not-a-date"), "", "format")
        # A well-formed but impossible date is still rejected.
        self.assertTuple(self._reject(schema, "2026-13-01T00:00:00Z"), "", "format")

    def test_one_of(self) -> None:
        schema = {"oneOf": [{"type": "string"}, {"type": "null"}]}
        self._accept(schema, "x")
        self._accept(schema, None)
        self.assertTuple(self._reject(schema, 5), "", "oneOf")


class RefTests(unittest.TestCase):
    def test_single_file_ref_resolves(self) -> None:
        schema = {
            "$defs": {"Hex": {"type": "string", "pattern": "^[0-9a-f]+$"}},
            "$ref": "#/$defs/Hex",
        }
        ok, _ = validate("abc123", schema)
        self.assertTrue(ok)
        ok, errs = validate("XYZ", schema)
        self.assertFalse(ok)
        self.assertIn("pattern", [k for (_p, k, _m) in errs])

    def test_external_ref_is_unsupported(self) -> None:
        with self.assertRaises(UnsupportedKeyword):
            validate("x", {"$ref": "other.json#/$defs/Thing"})


class UnsupportedKeywordTests(unittest.TestCase):
    def test_multiple_of_raises(self) -> None:
        with self.assertRaises(UnsupportedKeyword):
            validate({}, {"type": "object", "multipleOf": 2})

    def test_supported_set_is_exactly_thirteen(self) -> None:
        self.assertEqual(len(SUPPORTED_KEYWORDS), 13)


class PlanSnippetTests(unittest.TestCase):
    """The assertions in Task 9 Step 6, verbatim."""

    SCHEMA = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }

    def test_accepts_valid(self) -> None:
        ok, errs = validate({"x": 1}, self.SCHEMA)
        self.assertTrue(ok, errs)

    def test_rejects_wrong_type_with_exact_tuple(self) -> None:
        ok, errs = validate({"x": "no"}, self.SCHEMA)
        self.assertFalse(ok)
        self.assertEqual(errs[0][0], "/x")
        self.assertEqual(errs[0][1], "type")


class CorpusStaysInSubsetTests(unittest.TestCase):
    def test_every_committed_schema_traverses_without_unsupported_keyword(self) -> None:
        schema_dir = _repo_root() / "agent-system" / "schemas"
        paths = sorted(schema_dir.glob("*.schema.json"))
        self.assertEqual(len(paths), 10, "expected ten committed schemas")
        for path in paths:
            schema = json.loads(path.read_text(encoding="utf-8"))
            try:
                validate({}, schema)  # errors are fine; a raise is not
            except UnsupportedKeyword as exc:  # pragma: no cover - failure path
                self.fail(f"{path.name} uses an out-of-subset keyword: {exc}")


if __name__ == "__main__":
    unittest.main()
