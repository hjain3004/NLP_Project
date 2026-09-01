"""A deliberately bounded JSON Schema draft 2020-12 validator (ADR-0003).

The standard library has no JSON Schema validator and ADR-0002 commits to
standard library first. This module implements *exactly* the keyword subset
ADR-0003 enumerates and nothing more:

    type   required   properties   additionalProperties   items
    enum   const      pattern      minItems               minLength
    maximum            format: date-time                  oneOf
    $defs / $ref within a single file

Every rejection is a ``(json_pointer, keyword, message)`` tuple — machine-
readable errors are what make the single bounded resubmission opportunity
meaningful.

An unsupported keyword raises :class:`UnsupportedKeyword`. It never silently
passes: a validator that quietly ignores a constraint is worse than no
validator, because it produces confident green checks over unvalidated data.
"""

from __future__ import annotations

import datetime
import re
from typing import Any

from .errors import UnsupportedKeyword

__all__ = [
    "validate",
    "UnsupportedKeyword",
    "SUPPORTED_KEYWORDS",
    "IGNORED_KEYWORDS",
]

Error = tuple[str, str, str]

#: Keywords this validator actively enforces (ADR-0003).
SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "pattern",
        "minItems",
        "minLength",
        "maximum",
        "format",
        "oneOf",
    }
)

#: Structural or annotation keywords that carry no assertion and are skipped
#: without complaint. ``$defs`` is traversed only through ``$ref``.
IGNORED_KEYWORDS: frozenset[str] = frozenset(
    {
        "$schema",
        "$id",
        "$anchor",
        "$comment",
        "$defs",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

_DATE_TIME_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$"
)


# --------------------------------------------------------------------------- #
# Public entry point                                                          #
# --------------------------------------------------------------------------- #

def validate(instance: Any, schema: Any) -> tuple[bool, list[Error]]:
    """Validate ``instance`` against ``schema``.

    Returns ``(ok, errors)`` where ``errors`` is a list of
    ``(json_pointer, keyword, message)`` tuples, empty when ``ok`` is True.

    Raises :class:`UnsupportedKeyword` if ``schema`` (or any subschema reached)
    uses a keyword outside the ADR-0003 subset — this is a schema-authoring
    defect, not an instance failure, so it is surfaced immediately rather than
    folded into the error list.
    """
    errors: list[Error] = []
    _validate(instance, schema, "", schema, (), errors)
    return (not errors, errors)


# --------------------------------------------------------------------------- #
# Core recursion                                                              #
# --------------------------------------------------------------------------- #

def _validate(
    instance: Any,
    schema: Any,
    pointer: str,
    root: Any,
    ref_chain: tuple[str, ...],
    errors: list[Error],
) -> None:
    if schema is True:
        return
    if schema is False:
        errors.append((pointer, "schema", "no value is valid at this position"))
        return
    if not isinstance(schema, dict):
        raise UnsupportedKeyword(
            "subschema must be an object or boolean",
            json_pointer=pointer,
            keyword="(schema)",
        )

    _reject_unsupported_keywords(schema, pointer)

    if "$ref" in schema:
        target = _resolve_ref(schema["$ref"], root, pointer)
        if schema["$ref"] not in ref_chain:
            _validate(instance, target, pointer, root, ref_chain + (schema["$ref"],), errors)

    # type — if it fails, dependent keywords would only add noise, so stop here.
    if "type" in schema and not _type_ok(instance, schema["type"]):
        errors.append(
            (pointer, "type", f"expected type {schema['type']!r}, got {_json_type(instance)!r}")
        )
        return

    if "const" in schema and not _json_equal(instance, schema["const"]):
        errors.append((pointer, "const", f"must equal {schema['const']!r}"))

    if "enum" in schema and not any(_json_equal(instance, option) for option in schema["enum"]):
        errors.append((pointer, "enum", f"must be one of {schema['enum']!r}"))

    if "oneOf" in schema:
        matched = 0
        for branch in schema["oneOf"]:
            probe: list[Error] = []
            _validate(instance, branch, pointer, root, ref_chain, probe)
            if not probe:
                matched += 1
        if matched != 1:
            errors.append(
                (pointer, "oneOf", f"matched {matched} of {len(schema['oneOf'])} branches, expected exactly 1")
            )

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append((pointer, "minLength", f"shorter than minLength {schema['minLength']}"))
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append((pointer, "pattern", f"does not match pattern {schema['pattern']!r}"))
        if "format" in schema:
            _check_format(instance, schema["format"], pointer, errors)

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append((pointer, "maximum", f"greater than maximum {schema['maximum']}"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append((pointer, "minItems", f"fewer than minItems {schema['minItems']}"))
        if "items" in schema:
            before = len(errors)
            for index, element in enumerate(instance):
                _validate(element, schema["items"], f"{pointer}/{index}", root, ref_chain, errors)
            if len(errors) > before:
                errors.append((pointer, "items", "one or more array items failed validation"))

    if isinstance(instance, dict):
        _validate_object(instance, schema, pointer, root, ref_chain, errors)


def _validate_object(
    instance: dict[str, Any],
    schema: dict[str, Any],
    pointer: str,
    root: Any,
    ref_chain: tuple[str, ...],
    errors: list[Error],
) -> None:
    for name in schema.get("required", []):
        if name not in instance:
            errors.append((pointer, "required", f"missing required property {name!r}"))

    declared = schema.get("properties", {})
    if declared:
        before = len(errors)
        for name, subschema in declared.items():
            if name in instance:
                _validate(
                    instance[name],
                    subschema,
                    f"{pointer}/{_escape(name)}",
                    root,
                    ref_chain,
                    errors,
                )
        if len(errors) > before:
            errors.append((pointer, "properties", "one or more properties failed validation"))

    if "additionalProperties" in schema:
        extra = [name for name in instance if name not in declared]
        ap = schema["additionalProperties"]
        if ap is False:
            for name in extra:
                errors.append(
                    (f"{pointer}/{_escape(name)}", "additionalProperties", "property not permitted")
                )
        elif isinstance(ap, dict):
            for name in extra:
                _validate(
                    instance[name], ap, f"{pointer}/{_escape(name)}", root, ref_chain, errors
                )


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _reject_unsupported_keywords(schema: dict[str, Any], pointer: str) -> None:
    for keyword in schema:
        if keyword in SUPPORTED_KEYWORDS or keyword in IGNORED_KEYWORDS or keyword == "$ref":
            continue
        raise UnsupportedKeyword(
            f"keyword {keyword!r} is outside the ADR-0003 subset",
            json_pointer=pointer,
            keyword=keyword,
        )


def _resolve_ref(ref: str, root: Any, pointer: str) -> Any:
    if not ref.startswith("#/"):
        raise UnsupportedKeyword(
            f"only single-file '#/...' $ref is supported, got {ref!r}",
            json_pointer=pointer,
            keyword="$ref",
        )
    node: Any = root
    for raw in ref[2:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or token not in node:
            raise UnsupportedKeyword(
                f"$ref {ref!r} does not resolve within this document",
                json_pointer=pointer,
                keyword="$ref",
            )
        node = node[token]
    return node


def _check_format(value: str, fmt: Any, pointer: str, errors: list[Error]) -> None:
    if fmt != "date-time":
        raise UnsupportedKeyword(
            f"only format 'date-time' is supported, got {fmt!r}",
            json_pointer=pointer,
            keyword="format",
        )
    if _DATE_TIME_RE.match(value) is None:
        errors.append((pointer, "format", "not an RFC 3339 date-time"))
        return
    try:
        datetime.datetime.fromisoformat(value.replace("z", "+00:00").replace("Z", "+00:00"))
    except ValueError:
        errors.append((pointer, "format", "not a real calendar date-time"))


def _type_ok(instance: Any, declared: Any) -> bool:
    names = declared if isinstance(declared, list) else [declared]
    return any(_is_type(instance, name) for name in names)


def _is_type(instance: Any, name: str) -> bool:
    if name == "object":
        return isinstance(instance, dict)
    if name == "array":
        return isinstance(instance, list)
    if name == "string":
        return isinstance(instance, str)
    if name == "boolean":
        return isinstance(instance, bool)
    if name == "null":
        return instance is None
    if name == "integer":
        if isinstance(instance, bool):
            return False
        if isinstance(instance, int):
            return True
        return isinstance(instance, float) and instance.is_integer()
    if name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    raise UnsupportedKeyword(f"unknown type name {name!r}", json_pointer="", keyword="type")


def _json_type(instance: Any) -> str:
    if instance is None:
        return "null"
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, list):
        return "array"
    if isinstance(instance, dict):
        return "object"
    return type(instance).__name__


def _json_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")
