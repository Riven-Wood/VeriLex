"""Expose successful bundled Excel deliveries in the existing conversation files."""
import hashlib
import json
from pathlib import Path

from verilex_runtime.storage.models import ToolCallStatus
from verilex_runtime.thread_artifacts import MAX_THREAD_ARTIFACTS, artifact_record_sort_key
from .launch import COMMAND


def _delivery(raw):
    for _ in range(10):
        if isinstance(raw, str):
            raw = json.loads(raw.removeprefix('⟦UNTRUSTED_MCP_RESULT_START⟧\n').removesuffix('\n⟦UNTRUSTED_MCP_RESULT_END⟧'))
        elif isinstance(raw, dict):
            if raw.get('ok') is False or raw.get('isError') is True or raw.get('truncated') is True or raw.get('code') is not None:
                return None
            if isinstance(raw.get('created_file'), str) and raw.get('source_preserved') is True:
                return raw
            if 'output' in raw:
                raw = raw['output']
            elif 'text' in raw:
                raw = raw['text']
            elif isinstance(raw.get('content'), list) and len(raw['content']) == 1:
                raw = raw['content'][0]
            else:
                return None
        else:
            return None
    return None


def with_excel_outputs(base, thread, tool_calls, mcp_repository, workspaces):
    if not thread.workspace_id or mcp_repository is None or workspaces is None:
        return base
    workspace = workspaces.get(thread.workspace_id)
    if not workspace.allow_read:
        return base
    root = Path(workspace.root_path).resolve()
    prefixes = set()
    for server in mcp_repository.list_servers():
        if server.transport == 'stdio' and server.command == COMMAND and server.cwd and not server.args and not server.env and Path(server.cwd).resolve() == root:
            prefixes.add('mcp__' + hashlib.sha256(server.id.encode()).hexdigest()[:10] + '__')
    occupied = {item.get('path', item.get('published_relative_path')) for item in base}
    latest = {}
    for record in tool_calls.list_by_thread(thread.id):
        if record.status != ToolCallStatus.SUCCEEDED or not any(record.name.startswith(p) for p in prefixes):
            continue
        if not record.result_json or len(record.result_json) > 1_000_000:
            continue
        try:
            data = _delivery(record.result_json)
            if not data:
                continue
            path = Path(data['created_file'])
            if not path.is_absolute() or path.suffix.lower() != '.xlsx' or '..' in path.parts:
                continue
            relative = path.relative_to(root).as_posix()
            arguments = json.loads(record.arguments_json)
            expected = Path(arguments.get('output', ''))
            if not expected.is_absolute():
                expected = root / expected
            if expected != path or relative in occupied:
                continue
            latest[relative] = {'kind': 'legacy_workspace', 'path': relative, 'name': path.name,
                'last_operation': 'excel_deliver_workbook', 'last_operated_at': record.finished_at or record.started_at or ''}
        except (ValueError, TypeError, RecursionError):
            continue
    return sorted([*base, *latest.values()], key=artifact_record_sort_key, reverse=True)[:MAX_THREAD_ARTIFACTS]
