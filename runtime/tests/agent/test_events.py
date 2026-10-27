import json

import pytest

from verilex_runtime.agent.events import (
    MAX_EVENT_PAYLOAD_CHARS,
    DeltaKind,
    EventType,
    arguments_summary,
    extract_text,
    normalize_user_content,
    reasoning_block,
    text_block,
    validate_payload,
    mcp_arguments_summary,
)


def test_event_type_vocabulary_is_stable():
    assert {e.value for e in EventType} == {
        "turn_started",
        "turn_retry",
        "turn_compacted",
        "message_delta",
        "message_reset",
        "message_completed",
        "turn_completed",
        "turn_failed",
        "turn_cancelled",
        "tool_call_started",
        "tool_call_completed",
        "approval_requested",
        "approval_resolved",
        "attachment_degraded",
        "reasoning_effort_degraded",
    }


def test_delta_kinds_are_text_and_reasoning():
    assert {d.value for d in DeltaKind} == {"text", "reasoning"}


def test_validate_payload_rejects_oversized_payload():
    with pytest.raises(ValueError):
        validate_payload({"delta": "x" * (MAX_EVENT_PAYLOAD_CHARS + 1)})


def test_validate_payload_accepts_normal_payload():
    payload = {"delta": "ok"}
    assert validate_payload(payload) == payload


def test_normalize_user_content_wraps_plain_text():
    assert normalize_user_content("hello") == [{"type": "text", "text": "hello"}]


def test_extract_text_flattens_text_blocks_and_skips_reasoning():
    content = [
        {"type": "reasoning", "text": "think"},
        {"type": "text", "text": "Hello "},
        {"type": "text", "text": "world"},
    ]
    assert extract_text(content) == "Hello world"


def test_extract_text_handles_plain_string():
    assert extract_text("plain") == "plain"


def test_extract_text_handles_non_list():
    assert extract_text(123) == ""
    assert extract_text(None) == ""


def test_text_and_reasoning_blocks_shape():
    assert text_block("a") == {"type": "text", "text": "a"}
    assert reasoning_block("r") == {"type": "reasoning", "text": "r"}


@pytest.mark.parametrize(
    "sensitive",
    [
        "/secret.txt",
        "/private/cases/client-secret.txt",
        r"C:\secret.txt",
        r"C:\Users\client\案件\secret.txt",
        "api_key=sk-sensitive-value-123456789",
        "Token: long-sensitive-token-value-123456789",
        "password = correct-horse-battery-staple",
        "Bearer eyJhbGciOiJIUzI1NiJ9.sensitive.payload",
        "sk-live-sensitive-token-123456789",
        "AWS key AKIAIOSFODNN7EXAMPLE",
        "密钥 secret：correct-horse-battery-staple",
        "密码：超级秘密口令不要泄露",
        "令牌=敏感令牌甲乙丙丁戊己",
        "API密钥：敏感密钥甲乙丙丁戊己",
        "https://example.com/callback?token=long-sensitive-token-value-123456789",
        "Authorization: described in the policy",
        "password is managed by the administrator",
        "secret is a legal term in this clause",
    ],
)
def test_arguments_summary_fail_closes_short_sensitive_string_fields(sensitive):
    assert arguments_summary({"note": sensitive}) == {"note": "[内容已脱敏]"}


def test_arguments_summary_preserves_safe_basename_and_plain_chinese():
    assert arguments_summary(
        {
            "path": "合同.md",
            "note": "已核对修改范围，仅允许本次操作",
            "reference": "https://docs.example.test/approval/guide",
        }
    ) == {
        "path": "合同.md",
        "note": "已核对修改范围，仅允许本次操作",
        "reference": "https://docs.example.test/approval/guide",
    }


@pytest.mark.parametrize(
    "safe",
    [
        "https://example.com/API-key:guide",
    ],
)
def test_arguments_summary_preserves_credential_words_used_as_explanation(safe):
    assert arguments_summary({"note": safe}) == {"note": safe}


def test_arguments_summary_redacts_before_applying_its_length_bound():
    value = ("普通说明" * 60) + " API Key: sk-sensitive-value-123456789"
    assert arguments_summary({"note": value}) == {"note": "[内容已脱敏]"}


def test_mcp_arguments_summary_keeps_only_sorted_names_and_json_types():
    summary = mcp_arguments_summary(
        {
            "z_secret": {"nested": "case-secret"},
            "a_query": "case-secret",
            "count": 2,
            "ratio": 1.5,
            "enabled": True,
            "items": ["case-secret"],
            "empty": None,
        }
    )

    assert summary == {
        "fields": [
            {"name": "a_query", "type": "string"},
            {"name": "count", "type": "integer"},
            {"name": "empty", "type": "null"},
            {"name": "enabled", "type": "boolean"},
            {"name": "items", "type": "array"},
            {"name": "ratio", "type": "number"},
            {"name": "z_secret", "type": "object"},
        ],
        "truncated": False,
    }
    assert "case-secret" not in json.dumps(summary)


def test_mcp_arguments_summary_bounds_field_count_and_names():
    arguments = {f"field-{index:03d}-" + ("x" * 200): index for index in range(100)}

    summary = mcp_arguments_summary(arguments)

    assert summary["truncated"] is True
    assert len(summary["fields"]) <= 64
    assert all(len(field["name"]) <= 128 for field in summary["fields"])
