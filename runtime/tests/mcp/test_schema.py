from __future__ import annotations

from collections.abc import Mapping

import pytest

from verilex_runtime.mcp.errors import MCPDomainError
from verilex_runtime.mcp.schema import validate_mcp_arguments, validate_mcp_schema


def test_nested_schema_and_enum_are_enforced():
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"enum": ["a", "b"]}}
        },
        "required": ["items"],
    }

    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_arguments(schema, {"items": ["c"]})

    assert raised.value.code == "mcp_arguments_invalid"


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.test/schema.json",
        "http://example.test/schema.json",
        "file:///tmp/schema.json",
        "data:application/json,%7B%7D",
        "other.json#/$defs/value",
        "#/$defs/missing",
    ],
)
def test_remote_and_unresolvable_refs_fail_closed(reference):
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema({"$ref": reference})

    assert raised.value.code == "mcp_schema_unsupported"
    assert reference not in str(raised.value)


def test_local_ref_is_validated_without_external_retrieval():
    schema = {
        "$defs": {"caseId": {"type": "string", "pattern": "^[0-9]+$"}},
        "type": "object",
        "properties": {"id": {"$ref": "#/$defs/caseId"}},
        "required": ["id"],
    }

    canonical = validate_mcp_schema(schema)
    assert canonical.sha256
    assert canonical.value == schema
    validate_mcp_arguments(canonical, {"id": "42"})
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_arguments(canonical, {"id": "not-a-number"})
    assert raised.value.code == "mcp_arguments_invalid"


def test_local_anchor_is_resolved_without_external_retrieval():
    schema = {
        "$defs": {
            "caseId": {
                "$anchor": "caseId",
                "type": "string",
                "pattern": "^[0-9]+$",
            }
        },
        "type": "object",
        "properties": {"id": {"$ref": "#caseId"}},
        "required": ["id"],
    }

    canonical = validate_mcp_schema(schema)
    validate_mcp_arguments(canonical, {"id": "42"})
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_arguments(canonical, {"id": "not-a-number"})
    assert raised.value.code == "mcp_arguments_invalid"


@pytest.mark.parametrize(
    ("arguments", "kwargs"),
    [
        ({"text": "x" * 65_537}, {}),
        ({"nested": {"again": {"too": "deep"}}}, {"max_depth": 3}),
        ({"items": [1, 2, 3, 4]}, {"max_nodes": 4}),
    ],
)
def test_argument_resource_budgets_fail_closed(arguments, kwargs):
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_arguments({"type": "object"}, arguments, **kwargs)

    assert raised.value.code == "mcp_arguments_invalid"


def test_recursive_arguments_fail_closed_without_recursion_error():
    arguments: dict[str, object] = {}
    arguments["self"] = arguments

    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_arguments({"type": "object"}, arguments)

    assert raised.value.code == "mcp_arguments_invalid"


class _HostileArguments(dict):
    def items(self):
        raise RuntimeError("argument-secret")


def test_hostile_argument_container_fails_with_stable_error_without_leak():
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_arguments({"type": "object"}, _HostileArguments())

    assert raised.value.code == "mcp_arguments_invalid"
    assert "argument-secret" not in str(raised.value)


def test_schema_resource_budgets_are_checked_before_jsonschema():
    schema = {
        "type": "object",
        "properties": {f"field-{index}": {"type": "string"} for index in range(8)},
    }

    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(schema, max_nodes=8)

    assert raised.value.code == "mcp_schema_unsupported"


def test_schema_rejects_invalid_regex_during_registration():
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(
            {
                "type": "object",
                "properties": {"value": {"type": "string", "pattern": "["}},
            }
        )

    assert raised.value.code == "mcp_schema_unsupported"


def test_schema_accepts_one_bounded_variable_quantifier():
    canonical = validate_mcp_schema(
        {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9_-]{1,64}$",
                }
            },
        }
    )

    validate_mcp_arguments(canonical, {"slug": "case_42"})


def test_unanchored_bounded_pattern_matches_yuandian_report_tool():
    schema = validate_mcp_schema({"type": "object", "properties": {
        "idempotencyKey": {"type": "string", "pattern": "[A-Za-z0-9_-]{8,64}"}}})
    validate_mcp_arguments(schema, {"idempotencyKey": "test-key-123"})
    with pytest.raises(MCPDomainError):
        validate_mcp_arguments(schema, {"idempotencyKey": "short"})


@pytest.mark.parametrize(
    "pattern",
    [
        "^(a+)+$",
        "^(a|aa)+$",
        "a*a*a*a*a*b",
        "^a+a+$",
        "a+b",
        ".*b",
        "a{1,65536}b",
        "a{1,}b",
        "a{64}b{64}c{64}",
    ],
)
def test_schema_rejects_potentially_superlinear_regex_before_arguments(pattern):
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(
            {
                "type": "object",
                "properties": {"value": {"type": "string", "pattern": pattern}},
            }
        )

    assert raised.value.code == "mcp_schema_unsupported"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "uniqueItems": True},
        {"type": "array", "uniqueItems": True, "maxItems": 129},
        {"type": "array", "uniqueItems": True, "maxItems": True},
        {
            "type": "array",
            "uniqueItems": True,
            "maxItems": 128,
            "items": {"type": "object"},
        },
        {
            "type": "array",
            "uniqueItems": True,
            "maxItems": 128,
            "items": {"type": ["string", "integer"]},
        },
        {
            "type": "object",
            "allOf": [{"type": "object"} for _ in range(65)],
        },
        {
            "type": "object",
            "properties": {
                f"array-{index}": {
                    "type": "array",
                    "contains": {"const": index},
                }
                for index in range(65)
            },
        },
        {
            "type": "object",
            "$defs": {
                f"unique-{index}": {
                    "type": "array",
                    "uniqueItems": True,
                    "maxItems": 16,
                    "items": {"type": "string"},
                }
                for index in range(17)
            },
        },
    ],
)
def test_schema_rejects_unbounded_validation_complexity(schema):
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(schema)

    assert raised.value.code == "mcp_schema_unsupported"


def test_bounded_unique_items_schema_is_enforced_before_jsonschema():
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "uniqueItems": True,
                "maxItems": 128,
                "items": {"type": "integer"},
            }
        },
    }
    canonical = validate_mcp_schema(schema)
    validate_mcp_arguments(canonical, {"items": [1, 2, 3]})

    with pytest.raises(MCPDomainError) as duplicate:
        validate_mcp_arguments(canonical, {"items": [1, 1]})
    assert duplicate.value.code == "mcp_arguments_invalid"

    with pytest.raises(MCPDomainError) as oversized:
        validate_mcp_arguments(canonical, {"items": list(range(129))})
    assert oversized.value.code == "mcp_arguments_invalid"


def test_repeated_ref_cannot_amplify_object_unique_items_validation():
    schema = {
        "$defs": {
            "unsafeRows": {
                "type": "array",
                "uniqueItems": True,
                "maxItems": 100,
                "items": {"type": "object"},
            }
        },
        "type": "object",
        "allOf": [
            {"properties": {"rows": {"$ref": "#/$defs/unsafeRows"}}}
            for _ in range(64)
        ],
    }

    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(schema)

    assert raised.value.code == "mcp_schema_unsupported"


def test_schema_snapshot_cannot_be_mutated_through_value():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    canonical = validate_mcp_schema(schema)

    first = canonical.value
    first["type"] = "array"

    assert canonical.value == schema
    validate_mcp_arguments(canonical, {"id": "42"})


def test_arguments_nested_constraints_and_additional_properties_are_enforced():
    schema = {
        "type": "object",
        "properties": {
            "matter": {
                "type": "object",
                "properties": {"stage": {"enum": ["trial", "appeal"]}},
                "required": ["stage"],
                "additionalProperties": False,
            }
        },
        "required": ["matter"],
        "additionalProperties": False,
    }

    for invalid in (
        {"matter": {"stage": "enforcement"}},
        {"matter": {"stage": "trial", "secret": "must-not-echo"}},
        {"matter": {}},
    ):
        with pytest.raises(MCPDomainError) as raised:
            validate_mcp_arguments(schema, invalid)
        assert raised.value.code == "mcp_arguments_invalid"
        assert "must-not-echo" not in str(raised.value)


def test_shared_non_recursive_argument_value_is_accepted():
    shared = ["a"]
    schema = {
        "type": "object",
        "properties": {
            "left": {"type": "array"},
            "right": {"type": "array"},
        },
    }

    validate_mcp_arguments(schema, {"left": shared, "right": shared})


class _HostileMapping(Mapping):
    def __iter__(self):
        raise RuntimeError("schema-secret")

    def __len__(self):
        raise RuntimeError("schema-secret")

    def __getitem__(self, key):
        del key
        raise RuntimeError("schema-secret")


def test_hostile_schema_mapping_fails_with_stable_error_without_leak():
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(_HostileMapping())

    assert raised.value.code == "mcp_schema_unsupported"
    assert "schema-secret" not in str(raised.value)


def test_remote_ref_is_rejected_even_when_hidden_in_unused_definition():
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(
            {
                "type": "object",
                "$defs": {
                    "unused": {"$ref": "HTTPS://must-not-echo.example/schema"}
                },
            }
        )

    assert raised.value.code == "mcp_schema_unsupported"
    assert "must-not-echo" not in str(raised.value)


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "#"},
        {
            "$defs": {
                "left": {"$ref": "#/$defs/right"},
                "right": {
                    "anyOf": [
                        {"$ref": "#/$defs/left"},
                        {"type": "string"},
                    ]
                },
            },
            "type": "object",
        },
    ],
)
def test_direct_and_indirect_local_reference_cycles_are_rejected(schema):
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(schema)

    assert raised.value.code == "mcp_schema_unsupported"


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "contains": {"type": "string"}},
        {
            "type": "array",
            "contains": {"type": "string"},
            "maxItems": 129,
        },
        {
            "type": "object",
            "maxProperties": 128,
            "dependentSchemas": {
                f"field-{index}": {"type": "object"} for index in range(65)
            },
        },
        {
            "type": "object",
            "dependentSchemas": {"trigger": {"type": "object"}},
        },
    ],
)
def test_contains_and_dependent_schema_fanout_must_be_locally_bounded(schema):
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(schema)

    assert raised.value.code == "mcp_schema_unsupported"


def test_bounded_contains_and_dependent_schema_are_supported():
    schema = {
        "type": "object",
        "maxProperties": 8,
        "properties": {
            "trigger": {"type": "boolean"},
            "items": {
                "type": "array",
                "maxItems": 8,
                "contains": {"const": "required"},
            },
        },
        "dependentSchemas": {
            "trigger": {"required": ["items"]},
        },
    }
    canonical = validate_mcp_schema(schema)

    validate_mcp_arguments(canonical, {"trigger": True, "items": ["required"]})
    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_arguments(canonical, {"trigger": True, "items": ["other"]})
    assert raised.value.code == "mcp_arguments_invalid"


def test_empty_dependent_schemas_needs_no_property_budget():
    canonical = validate_mcp_schema(
        {"type": "object", "dependentSchemas": {}}
    )

    validate_mcp_arguments(canonical, {})


def test_ref_fanout_cannot_amplify_dependent_contains_scan():
    expensive = {
        "type": "object",
        "maxProperties": 128,
        "dependentSchemas": {
            f"field-{index}": {
                "properties": {
                    "items": {
                        "type": "array",
                        "maxItems": 2_000,
                        "contains": {"const": "never"},
                    }
                }
            }
            for index in range(32)
        },
    }
    schema = {
        "$defs": {"expensive": expensive},
        "type": "object",
        "allOf": [
            {"$ref": "#/$defs/expensive"}
            for _ in range(32)
        ],
    }

    with pytest.raises(MCPDomainError) as raised:
        validate_mcp_schema(schema)

    assert raised.value.code == "mcp_schema_unsupported"


@pytest.mark.parametrize(
    "schema",
    [
        {
            "type": "object",
            "examples": [{"$ref": "https://example.test/literal"}],
        },
        {
            "type": "object",
            "properties": {
                "payload": {
                    "const": {"$ref": "https://example.test/literal"},
                }
            },
        },
        {
            "type": "object",
            "properties": {
                "payload": {"default": {"$ref": "plain-text"}},
            },
        },
    ],
)
def test_reference_like_instance_literals_are_not_interpreted_as_schema_refs(schema):
    canonical = validate_mcp_schema(schema)

    assert canonical.value == schema
