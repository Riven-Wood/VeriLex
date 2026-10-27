import json

import pytest

import verilex_runtime.sessions.audit_recorder as audit_module

from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.migrations import run_migrations


def _recorder(tmp_path):
    database = Database(tmp_path / "verilex.sqlite3")
    run_migrations(database)
    return AuditRecorder(AuditRepository(database)), AuditRepository(database)


def test_record_tool_call_persists_entry(tmp_path):
    recorder, repo = _recorder(tmp_path)
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-1",
        thread_id="t1",
        name="write_file",
        outcome="succeeded",
        details={"path": "a.txt"},
    )
    entries = repo.list_by_thread("t1")
    assert len(entries) == 1
    assert entries[0].action == "tool_call"
    assert entries[0].subject == "write_file"
    assert entries[0].outcome == "succeeded"
    assert entries[0].details == {"path": "a.txt"}


def test_record_approval_includes_reason(tmp_path):
    recorder, repo = _recorder(tmp_path)
    recorder.record_approval(
        turn_id="turn1",
        tool_call_id="call-1",
        thread_id="t1",
        approval_id="appr-1",
        decision="allowed",
        reason="looks good",
    )
    entries = repo.list_by_thread("t1")
    assert entries[0].outcome == "allowed"
    assert entries[0].details == {"approvalId": "appr-1", "reason": "looks good"}


def test_record_approval_without_reason(tmp_path):
    recorder, repo = _recorder(tmp_path)
    recorder.record_approval(
        turn_id="turn1",
        tool_call_id="call-1",
        thread_id="t1",
        approval_id="appr-1",
        decision="denied",
    )
    entries = repo.list_by_thread("t1")
    assert entries[0].details == {"approvalId": "appr-1"}


def test_record_workspace_and_provider(tmp_path):
    recorder, repo = _recorder(tmp_path)
    recorder.record_workspace(
        workspace_id="workspace-1",
        outcome="created",
        details={"allow_write": True},
    )
    recorder.record_provider(
        provider_name="My API",
        outcome="created",
    )
    recent = repo.list_recent()
    assert len(recent) == 2
    assert {e.action for e in recent} == {"workspace", "provider"}
    workspace_entry = next(entry for entry in recent if entry.action == "workspace")
    assert workspace_entry.subject == "workspace-1"
    assert "/tmp/proj" not in repr(workspace_entry)


def test_record_mcp_server_deleted_keeps_only_id_and_returned_count(tmp_path):
    recorder, repo = _recorder(tmp_path)

    recorder.record_mcp_server_deleted(
        server_id="server-1",
        selected_thread_count=2,
    )

    entry = repo.list_recent()[0]
    assert entry.action == "mcp_server_deleted"
    assert entry.subject == "server-1"
    assert entry.outcome == "deleted"
    assert entry.details == {
        "serverId": "server-1",
        "selectedThreadCount": 2,
    }


def test_record_model_capability_contains_only_bounded_model_metadata(tmp_path):
    recorder, repo = _recorder(tmp_path)
    marker = "MODEL-MARKER-"

    recorder.record_model_capability(
        provider_id="provider-1",
        model=marker + ("x" * 300),
        outcome="created",
        supports_vision=True,
    )

    entry = repo.list_recent()[0]
    assert entry.action == "model_capability"
    assert entry.subject == "provider-1"
    assert entry.outcome == "created"
    assert set(entry.details) == {"model", "supportsVision"}
    assert entry.details["model"].startswith(marker)
    assert entry.details["model"].endswith("…")
    assert len(entry.details["model"]) == 201
    assert entry.details["supportsVision"] is True


def test_record_attachment_keeps_only_safe_bounded_metadata(tmp_path):
    recorder, repo = _recorder(tmp_path)
    full_sha = "a" * 64
    recorder.record_attachment(
        thread_id="t1",
        upload_id="upl-1",
        attachment_id=None,
        outcome="created",
        media_type="text/plain",
        size_bytes=12,
        sha256=full_sha,
        code=None,
        display_name="x" * 300,
    )

    entry = repo.list_by_thread("t1")[0]
    assert entry.action == "attachment"
    assert entry.subject == "upl-1"
    assert set(entry.details) == {
        "uploadId",
        "mediaType",
        "sizeBytes",
        "sha256Prefix",
        "displayName",
    }
    assert entry.details["sha256Prefix"] == "a" * 12
    assert full_sha not in repr(entry)
    assert len(entry.details["displayName"]) == 201


def test_long_field_values_are_truncated(tmp_path):
    recorder, repo = _recorder(tmp_path)
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-1",
        thread_id="t1",
        name="write_file",
        outcome="succeeded",
        details={"content": "x" * 2000},
    )
    entries = repo.list_by_thread("t1")
    assert entries[0].details["content"].endswith("…")


def test_persistence_failure_is_swallowed(tmp_path):
    class BrokenRepo:
        def insert(self, entry):
            raise RuntimeError("db gone")

    recorder = AuditRecorder(BrokenRepo())
    # Should not raise even when persistence fails.
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-1",
        thread_id="t1",
        name="write_file",
        outcome="succeeded",
    )


def test_web_search_audit_keeps_only_whitelisted_metadata(tmp_path):
    recorder, repo = _recorder(tmp_path)
    secret_query = "CONFIDENTIAL-QUERY"
    secret_summary = "SECRET-SUMMARY"
    secret_url = "https://result.example/private"
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-1",
        thread_id="t1",
        name="web_search",
        outcome="succeeded",
        duration_ms=17,
        details={
            "arguments": {"query": secret_query},
            "result": json.dumps(
                {
                    "provider": "duckduckgo",
                    "result_count": 3,
                    "results": [
                        {"url": secret_url, "snippet": secret_summary}
                    ],
                }
            ),
            "Cookie": "cookie-secret",
            "Authorization": "bearer-secret",
            "proxy": "http://127.0.0.1:9000",
            "traceback": "internal stack",
        },
    )

    entry = repo.list_by_thread("t1")[0]
    assert entry.details == {
        "status": "succeeded",
        "durationMs": 17,
        "provider": "duckduckgo",
        "resultCount": 3,
    }
    serialized = repr(entry)
    for forbidden in (
        secret_query,
        secret_summary,
        secret_url,
        "cookie-secret",
        "bearer-secret",
        "127.0.0.1",
        "internal stack",
    ):
        assert forbidden not in serialized


def test_web_search_audit_accepts_largest_valid_bounded_tool_result(tmp_path):
    recorder, repo = _recorder(tmp_path)
    result = json.dumps(
        {
            "provider": "duckduckgo",
            "result_count": 10,
            "results": [
                {
                    "title": "\\" * 512,
                    "url": "https://example.com/" + ("a" * 2_000),
                    "snippet": "\\" * 2_000,
                }
                for _ in range(10)
            ],
        }
    )
    assert 64 * 1024 < len(result) < 128 * 1024

    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-large-search",
        thread_id="t1",
        name="web_search",
        outcome="succeeded",
        duration_ms=20,
        details={"result": result},
    )

    entry = repo.list_by_thread("t1")[0]
    assert entry.details["resultCount"] == 10


def test_web_read_audit_never_copies_page_content_or_sensitive_fields(tmp_path):
    recorder, repo = _recorder(tmp_path)
    secret_body = "PAGE-BODY-MUST-NOT-BE-AUDITED"
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-1",
        thread_id="t1",
        name="web_read",
        outcome="succeeded",
        duration_ms=29,
        details={
            "arguments": {"url": "https://origin.example/private"},
            "result": json.dumps(
                {
                    "source_id": "call-1",
                    "site": "final.example.com",
                    "url": "https://final.example.com/private",
                    "character_count": 31,
                    "truncated": True,
                    "text": secret_body,
                    "content_sha256": "f" * 64,
                }
            ),
            "Cookie": "cookie-secret",
            "Authorization": "bearer-secret",
            "proxy": "http://127.0.0.1:9000",
            "traceback": "internal stack",
        },
    )

    entry = repo.list_by_thread("t1")[0]
    assert entry.details == {
        "status": "succeeded",
        "durationMs": 29,
        "site": "final.example.com",
        "characterCount": 31,
        "truncated": True,
    }
    serialized = repr(entry)
    for forbidden in (
        secret_body,
        "origin.example",
        "/private",
        "cookie-secret",
        "bearer-secret",
        "127.0.0.1",
        "internal stack",
        "f" * 64,
    ):
        assert forbidden not in serialized


def test_failed_web_audit_ignores_untrusted_result_and_nonweb_audit_is_unchanged(tmp_path):
    recorder, repo = _recorder(tmp_path)
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-web",
        thread_id="t1",
        name="web_read",
        outcome="failed",
        duration_ms=5,
        details={"result": "internal_error at http://127.0.0.1/private"},
    )
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-file",
        thread_id="t1",
        name="read_file",
        outcome="succeeded",
        details={"path": "a.txt", "result": "ordinary result"},
    )

    entries = {entry.subject: entry for entry in repo.list_by_thread("t1")}
    assert entries["web_read"].details == {
        "status": "failed",
        "durationMs": 5,
    }
    assert entries["read_file"].details == {
        "path": "a.txt",
        "result": "ordinary result",
    }


def test_web_audit_rejects_host_field_injection_and_unbounded_duration(tmp_path):
    recorder, repo = _recorder(tmp_path)
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-web",
        thread_id="t1",
        name="web_read",
        outcome="succeeded",
        duration_ms=10**100,
        details={
            "result": json.dumps(
                {
                    "site": "example.com PAGE-BODY-IN-SITE\nCookie secret",
                    "character_count": 10,
                    "truncated": False,
                }
            )
        },
    )

    entry = repo.list_by_thread("t1")[0]
    assert entry.details == {
        "status": "succeeded",
        "characterCount": 10,
        "truncated": False,
    }
    assert "PAGE-BODY-IN-SITE" not in repr(entry)


def test_web_audit_normalizes_untrusted_outcome_before_persisting(tmp_path):
    recorder, repo = _recorder(tmp_path)
    marker = "OUTCOME-INJECTION Cookie Authorization 127.0.0.1"

    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-web",
        thread_id="t1",
        name="web_search",
        outcome=marker,
        duration_ms=1,
        details={"result": "PRIVATE QUERY AND BODY"},
    )

    entry = repo.list_by_thread("t1")[0]
    assert entry.outcome == "unknown"
    assert entry.details == {
        "status": "unknown",
        "durationMs": 1,
        "provider": "duckduckgo",
    }
    assert marker not in repr(entry)
    assert "PRIVATE QUERY" not in repr(entry)


@pytest.mark.parametrize(
    "site",
    [
        "localhost",
        "api.localhost",
        "printer.local",
        "service.internal",
        "example.test",
        "127.0.0.1",
        "2130706433",
        "127.1",
        "0x7f000001",
        "0177.0.0.1",
        "224.0.0.1",
        "ff0e::1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "fec0::1",
        "example.com\nCookie secret",
    ],
)
def test_web_read_audit_rejects_nonpublic_or_special_site(tmp_path, site):
    recorder, repo = _recorder(tmp_path)

    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-web",
        thread_id="t1",
        name="web_read",
        outcome="succeeded",
        details={
            "result": json.dumps(
                {
                    "site": site,
                    "character_count": 5,
                    "truncated": False,
                }
            )
        },
    )

    entry = repo.list_by_thread("t1")[0]
    assert "site" not in entry.details
    assert site not in repr(entry)


def test_deep_or_oversized_web_result_cannot_interrupt_audit(tmp_path):
    recorder, repo = _recorder(tmp_path)
    deeply_nested_json = ("[" * 20_000) + "0" + ("]" * 20_000)

    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-deep",
        thread_id="t1",
        name="web_search",
        outcome="succeeded",
        duration_ms=2,
        details={"result": deeply_nested_json},
    )
    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-long",
        thread_id="t1",
        name="web_read",
        outcome="succeeded",
        duration_ms=3,
        details={"result": "x" * 100_000},
    )

    entries = {entry.tool_call_id: entry for entry in repo.list_by_thread("t1")}
    assert entries["call-deep"].details == {
        "status": "succeeded",
        "durationMs": 2,
        "provider": "duckduckgo",
    }
    assert entries["call-long"].details == {
        "status": "succeeded",
        "durationMs": 3,
    }


def test_web_audit_whitelist_failure_is_best_effort(tmp_path, monkeypatch):
    recorder, repo = _recorder(tmp_path)

    def fail_whitelist(**_kwargs):
        raise RuntimeError("stack and secret")

    monkeypatch.setattr(audit_module, "_web_tool_details", fail_whitelist)

    recorder.record_tool_call(
        turn_id="turn1",
        tool_call_id="call-web",
        thread_id="t1",
        name="web_read",
        outcome="succeeded",
        duration_ms=4,
        details={"result": "PRIVATE BODY"},
    )

    entry = repo.list_by_thread("t1")[0]
    assert entry.outcome == "succeeded"
    assert entry.details == {"status": "succeeded", "durationMs": 4}
    assert "PRIVATE BODY" not in repr(entry)
    assert "stack and secret" not in repr(entry)
