"""Fail-closed local JSON Schema and MCP argument validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema import validators as jsonschema_validators
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource
from referencing.jsonschema import DRAFT202012

from verilex_runtime.mcp.canonical import canonical_schema_bytes
from verilex_runtime.mcp.errors import MCPDomainError


_LOCAL_SCHEMA_URI = "urn:verilex:mcp:local-schema"
_MAX_PATTERN_BYTES = 512
_MAX_PATTERN_COUNT = 64
_MAX_TOTAL_PATTERN_BYTES = 4_096
_MAX_COMPOSITION_BRANCHES = 64
_MAX_CONTAINS_KEYWORDS = 64
_MAX_UNIQUE_ITEMS_KEYWORDS = 16
_MAX_UNIQUE_ARRAY_ITEMS = 128
_UNIQUE_SCALAR_TYPES = {"string", "integer", "number", "boolean", "null"}
_MAX_DEPENDENT_SCHEMA_ENTRIES = 64
_MAX_DEPENDENT_OBJECT_PROPERTIES = 128

_SINGLE_SUBSCHEMA_KEYWORDS = (
    "additionalProperties",
    "unevaluatedProperties",
    "propertyNames",
    "contains",
    "items",
    "unevaluatedItems",
    "not",
    "if",
    "then",
    "else",
    "contentSchema",
)
_ARRAY_SUBSCHEMA_KEYWORDS = ("allOf", "anyOf", "oneOf", "prefixItems")
_MAP_SUBSCHEMA_KEYWORDS = (
    "$defs",
    "definitions",
    "properties",
    "patternProperties",
    "dependentSchemas",
)


def _deny_schema_retrieval(uri: str):
    raise NoSuchResource(ref=uri)


_LOCAL_ONLY_REGISTRY = Registry(retrieve=_deny_schema_retrieval)


_base_unique_items = Draft202012Validator.VALIDATORS["uniqueItems"]


def _bounded_unique_items(validator, unique_items, instance, schema):
    if (
        unique_items is True
        and type(instance) is list
        and len(instance) > _MAX_UNIQUE_ARRAY_ITEMS
    ):
        yield ValidationError("uniqueItems array exceeds the MCP safety budget")
        return
    yield from _base_unique_items(validator, unique_items, instance, schema)


_BoundedMCPValidator = jsonschema_validators.extend(
    Draft202012Validator,
    validators={"uniqueItems": _bounded_unique_items},
)


def _schema_error() -> MCPDomainError:
    return MCPDomainError("mcp_schema_unsupported", retryable=False)


def _arguments_error() -> MCPDomainError:
    return MCPDomainError("mcp_arguments_invalid", retryable=False)


@dataclass(frozen=True, slots=True)
class CanonicalMCPToolSchema:
    """An immutable canonical snapshot of one validated local tool schema."""

    canonical_bytes: bytes
    sha256: str

    @property
    def value(self) -> dict[str, object]:
        # A fresh value prevents a caller from changing the bytes/hash pair.
        return json.loads(self.canonical_bytes)


def _local_schema_registry(schema: dict[str, object]) -> Registry:
    resource = Resource.from_contents(
        schema,
        default_specification=DRAFT202012,
    )
    return _LOCAL_ONLY_REGISTRY.with_resource(
        _LOCAL_SCHEMA_URI,
        resource,
    ).crawl()


def _direct_subschema_nodes(schema: dict[str, object]) -> list[dict[str, object]]:
    children: list[dict[str, object]] = []
    for keyword in _SINGLE_SUBSCHEMA_KEYWORDS:
        child = schema.get(keyword)
        if type(child) is dict:
            children.append(child)
    for keyword in _ARRAY_SUBSCHEMA_KEYWORDS:
        values = schema.get(keyword)
        if type(values) is list:
            children.extend(child for child in values if type(child) is dict)
    for keyword in _MAP_SUBSCHEMA_KEYWORDS:
        values = schema.get(keyword)
        if type(values) is dict:
            children.extend(
                child for child in values.values() if type(child) is dict
            )
    return children


def _iter_schema_nodes(schema: dict[str, object]):
    stack: list[dict[str, object]] = [schema]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(_direct_subschema_nodes(current))


def _validate_local_references(schema: dict[str, object]) -> None:
    try:
        registry = _local_schema_registry(schema)
        resolver = registry.resolver(_LOCAL_SCHEMA_URI)
    except MCPDomainError:
        raise
    except Exception:
        raise _schema_error() from None

    graph: dict[int, set[int]] = {}
    stack: list[dict[str, object]] = [schema]
    seen: set[int] = set()
    while stack:
        value = stack.pop()
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        graph.setdefault(identity, set())
        children = _direct_subschema_nodes(value)
        for child in children:
            graph[identity].add(id(child))
            stack.append(child)
        for key in ("$ref", "$dynamicRef", "$recursiveRef"):
            if key not in value:
                continue
            reference = value[key]
            if type(reference) is not str or not reference.startswith("#"):
                raise _schema_error()
            try:
                target = resolver.lookup(reference).contents
            except Exception:
                raise _schema_error() from None
            if isinstance(target, bool):
                continue
            if type(target) is not dict:
                raise _schema_error()
            graph[identity].add(id(target))
            stack.append(target)

    colors: dict[int, int] = {}
    for start in graph:
        if colors.get(start) == 2:
            continue
        pending: list[tuple[int, bool]] = [(start, False)]
        while pending:
            node, leaving = pending.pop()
            if leaving:
                colors[node] = 2
                continue
            state = colors.get(node, 0)
            if state == 1:
                raise _schema_error()
            if state == 2:
                continue
            colors[node] = 1
            pending.append((node, True))
            pending.extend((neighbor, False) for neighbor in graph.get(node, ()))


def _validate_schema_shape(
    schema: Mapping[str, object],
    *,
    max_depth: int,
    max_nodes: int,
) -> None:
    nodes = 0
    active: set[int] = set()
    stack: list[tuple[object, int, bool]] = [(schema, 1, False)]
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise _schema_error()
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active:
                raise _schema_error()
            active.add(identity)
            stack.append((current, depth, True))
            try:
                items = tuple(current.items())
            except Exception:
                raise _schema_error() from None
            for key, item in reversed(items):
                if type(key) is not str:
                    raise _schema_error()
                stack.append((item, depth + 1, False))
        elif type(current) is list:
            identity = id(current)
            if identity in active:
                raise _schema_error()
            active.add(identity)
            stack.append((current, depth, True))
            for item in reversed(current):
                stack.append((item, depth + 1, False))
        elif current is None or type(current) in {str, bool, int}:
            continue
        elif type(current) is float:
            if not math.isfinite(current):
                raise _schema_error()
        else:
            raise _schema_error()


def _validate_safe_pattern(expression: str) -> int:
    """Accept a small linear-time regex subset for untrusted schemas."""
    try:
        encoded_size = len(expression.encode("utf-8", "strict"))
        if encoded_size > _MAX_PATTERN_BYTES:
            raise _schema_error()
    except UnicodeError:
        raise _schema_error() from None

    variable_quantifiers = 0
    has_quantifier = False
    index = 0
    atom_available = False
    while index < len(expression):
        character = expression[index]
        if character in {"(", ")", "|"}:
            raise _schema_error()
        if character in {"^", "$"}:
            atom_available = False
            index += 1
            continue
        if character == "[":
            index += 1
            if index < len(expression) and expression[index] == "^":
                index += 1
            class_has_item = False
            while index < len(expression) and expression[index] != "]":
                if expression[index] == "\\":
                    index += 1
                    if index >= len(expression):
                        raise _schema_error()
                class_has_item = True
                index += 1
            if index >= len(expression) or not class_has_item:
                raise _schema_error()
            index += 1
            atom_available = True
            continue
        if character == "\\":
            index += 1
            if index >= len(expression) or expression[index].isdigit():
                raise _schema_error()
            index += 1
            atom_available = True
            continue
        if character in {"*", "+", "?"}:
            if not atom_available:
                raise _schema_error()
            variable_quantifiers += 1
            has_quantifier = True
            if variable_quantifiers > 1:
                raise _schema_error()
            atom_available = False
            index += 1
            continue
        if character == "{":
            if not atom_available:
                raise _schema_error()
            closing = expression.find("}", index + 1)
            if closing < 0:
                raise _schema_error()
            bounds = expression[index + 1 : closing]
            parts = bounds.split(",")
            if len(parts) not in {1, 2} or not parts[0].isdigit():
                raise _schema_error()
            lower = int(parts[0])
            has_quantifier = True
            if len(parts) == 1:
                upper = lower
            else:
                if parts[1] and not parts[1].isdigit():
                    raise _schema_error()
                upper = int(parts[1]) if parts[1] else None
                variable_quantifiers += 1
                if variable_quantifiers > 1:
                    raise _schema_error()
            if lower > 65_536 or (upper is not None and upper > 65_536):
                raise _schema_error()
            atom_available = False
            index = closing + 1
            continue
        if character == "]":
            raise _schema_error()
        atom_available = True
        index += 1

    try:
        if has_quantifier and not expression.startswith("^"):
            raise _schema_error()
        re.compile(expression)
    except (OverflowError, re.error):
        raise _schema_error() from None
    return encoded_size


def _validate_regex_keywords(schema: dict[str, object]) -> None:
    pattern_count = 0
    pattern_bytes = 0
    for current in _iter_schema_nodes(schema):
        pattern = current.get("pattern")
        if pattern is not None:
            if type(pattern) is not str:
                raise _schema_error()
            pattern_count += 1
            pattern_bytes += _validate_safe_pattern(pattern)
        pattern_properties = current.get("patternProperties")
        if type(pattern_properties) is dict:
            for expression in pattern_properties:
                pattern_count += 1
                pattern_bytes += _validate_safe_pattern(expression)
        if (
            pattern_count > _MAX_PATTERN_COUNT
            or pattern_bytes > _MAX_TOTAL_PATTERN_BYTES
        ):
            raise _schema_error()


def _validate_evaluation_complexity(schema: dict[str, object]) -> None:
    composition_branches = 0
    contains_keywords = 0
    unique_items_keywords = 0
    dependent_schema_entries = 0
    for current in _iter_schema_nodes(schema):
        for keyword in ("allOf", "anyOf", "oneOf"):
            branches = current.get(keyword)
            if type(branches) is list:
                composition_branches += len(branches)
        if "contains" in current:
            contains_keywords += 1
            max_items = current.get("maxItems")
            if (
                type(max_items) is not int
                or max_items < 0
                or max_items > _MAX_UNIQUE_ARRAY_ITEMS
            ):
                raise _schema_error()
        if current.get("uniqueItems") is True:
            unique_items_keywords += 1
            max_items = current.get("maxItems")
            items = current.get("items")
            item_type = items.get("type") if type(items) is dict else None
            if (
                type(max_items) is not int
                or max_items < 0
                or max_items > _MAX_UNIQUE_ARRAY_ITEMS
                or type(items) is not dict
                or type(item_type) is not str
                or item_type not in _UNIQUE_SCALAR_TYPES
            ):
                raise _schema_error()
        dependent_schemas = current.get("dependentSchemas")
        if type(dependent_schemas) is dict and dependent_schemas:
            dependent_schema_entries += len(dependent_schemas)
            max_properties = current.get("maxProperties")
            if (
                type(max_properties) is not int
                or max_properties < 0
                or max_properties > _MAX_DEPENDENT_OBJECT_PROPERTIES
            ):
                raise _schema_error()
        if (
            composition_branches > _MAX_COMPOSITION_BRANCHES
            or contains_keywords > _MAX_CONTAINS_KEYWORDS
            or unique_items_keywords > _MAX_UNIQUE_ITEMS_KEYWORDS
            or dependent_schema_entries > _MAX_DEPENDENT_SCHEMA_ENTRIES
        ):
            raise _schema_error()


def validate_mcp_schema(
    schema: Mapping[str, object],
    max_bytes: int = 65_536,
    max_depth: int = 32,
    max_nodes: int = 4_096,
) -> CanonicalMCPToolSchema:
    """Validate a JSON Schema while prohibiting every external reference."""
    if (
        isinstance(max_bytes, bool)
        or isinstance(max_depth, bool)
        or isinstance(max_nodes, bool)
        or not all(
            isinstance(item, int) and item > 0
            for item in (max_bytes, max_depth, max_nodes)
        )
        or not isinstance(schema, Mapping)
    ):
        raise _schema_error()
    try:
        _validate_schema_shape(schema, max_depth=max_depth, max_nodes=max_nodes)
        canonical = canonical_schema_bytes(schema)
        if len(canonical) > max_bytes:
            raise _schema_error()
        value = json.loads(canonical)
        if not isinstance(value, dict):
            raise _schema_error()
        _validate_local_references(value)
        _validate_regex_keywords(value)
        _validate_evaluation_complexity(value)
        Draft202012Validator.check_schema(value)
    except MCPDomainError:
        raise
    except (SchemaError, TypeError, ValueError, UnicodeError):
        raise _schema_error() from None
    except Exception:
        raise _schema_error() from None

    return CanonicalMCPToolSchema(
        canonical_bytes=canonical,
        sha256=hashlib.sha256(canonical).hexdigest(),
    )


def validate_mcp_input_schema(
    schema: Mapping[str, object],
) -> CanonicalMCPToolSchema:
    """Validate the exact object-root schema shared by storage and tools."""
    canonical = validate_mcp_schema(schema)
    if canonical.value.get("type") != "object":
        raise _schema_error()
    return canonical


def _validate_argument_shape(value: object, *, max_depth: int, max_nodes: int) -> None:
    nodes = 0
    active: set[int] = set()
    stack: list[tuple[object, int, bool]] = [(value, 1, False)]
    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise _arguments_error()
        if type(current) is dict:
            identity = id(current)
            if identity in active:
                raise _arguments_error()
            active.add(identity)
            stack.append((current, depth, True))
            for key, item in reversed(tuple(current.items())):
                if type(key) is not str:
                    raise _arguments_error()
                stack.append((item, depth + 1, False))
        elif type(current) is list:
            identity = id(current)
            if identity in active:
                raise _arguments_error()
            active.add(identity)
            stack.append((current, depth, True))
            for item in reversed(current):
                stack.append((item, depth + 1, False))
        elif current is None or type(current) in {str, bool, int}:
            continue
        elif type(current) is float:
            if not math.isfinite(current):
                raise _arguments_error()
        else:
            raise _arguments_error()


def validate_mcp_arguments(
    schema: Mapping[str, object] | CanonicalMCPToolSchema,
    arguments: object,
    max_bytes: int = 65_536,
    max_depth: int = 32,
    max_nodes: int = 4_096,
) -> None:
    """Validate bounded JSON object arguments against the complete local schema."""
    if (
        isinstance(max_bytes, bool)
        or isinstance(max_depth, bool)
        or isinstance(max_nodes, bool)
        or not all(
            isinstance(item, int) and item > 0
            for item in (max_bytes, max_depth, max_nodes)
        )
        or type(arguments) is not dict
    ):
        raise _arguments_error()

    canonical = (
        schema
        if isinstance(schema, CanonicalMCPToolSchema)
        else validate_mcp_schema(schema)
    )
    try:
        _validate_argument_shape(arguments, max_depth=max_depth, max_nodes=max_nodes)
        encoded = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8", "strict")
        if len(encoded) > max_bytes:
            raise _arguments_error()
        validator = _BoundedMCPValidator(
            canonical.value,
            format_checker=FormatChecker(),
            registry=_local_schema_registry(canonical.value),
        )
    except MCPDomainError:
        raise
    except (RecursionError, TypeError, ValueError, UnicodeError, ValidationError):
        raise _arguments_error() from None
    except Exception:
        raise _arguments_error() from None
    try:
        error = next(validator.iter_errors(arguments), None)
    except Exception:
        raise _arguments_error() from None
    if error is not None:
        raise _arguments_error()
