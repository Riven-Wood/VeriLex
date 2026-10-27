"""Project successful bundled-PDF outputs into the existing workspace file list.

No disk scan, no PDF text sent to a model to make preview work. Only the stable
bundled command, successful complete results, and the thread's current readable
workspace are eligible. File preview keeps its existing path/content checks.
"""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from verilex_runtime.storage.models import ToolCallStatus
from verilex_runtime.thread_artifacts import MAX_THREAD_ARTIFACTS, artifact_record_sort_key
from .launcher import COMMAND


def _unwrap(raw):
    for _ in range(9):
        if isinstance(raw, str):
            raw = json.loads(raw.removeprefix("⟦UNTRUSTED_MCP_RESULT_START⟧\n").removesuffix("\n⟦UNTRUSTED_MCP_RESULT_END⟧"))
        elif isinstance(raw, dict):
            if raw.get("ok") is False or raw.get("isError") is True or raw.get("truncated") is True or raw.get("code") is not None:
                return None
            if "outputs" in raw:
                return raw
            if "output" in raw:
                raw = raw["output"]
            elif "text" in raw:
                raw = raw["text"]
            elif isinstance(raw.get("content"), list) and len(raw["content"]) == 1:
                raw = raw["content"][0]
            else:
                return None
        else:
            return None
    return None


def with_pdf_outputs(base, thread, tool_calls, mcp_repository, workspaces):
    if not thread.workspace_id or mcp_repository is None or workspaces is None:
        return base
    workspace = workspaces.get(thread.workspace_id)
    if not workspace.allow_read:
        return base
    root = Path(workspace.root_path).resolve()
    prefixes = set()
    for server in mcp_repository.list_servers():
        if (server.transport == "stdio" and server.command == COMMAND and server.cwd
                and not server.args and not server.env and Path(server.cwd).resolve() == root):
            prefixes.add("mcp__" + hashlib.sha256(server.id.encode()).hexdigest()[:10] + "__")
    if not prefixes:
        return base
    occupied = {item.get("path", item.get("published_relative_path")) for item in base}
    latest = {}
    for record in tool_calls.list_by_thread(thread.id):
        if record.status != ToolCallStatus.SUCCEEDED or not any(record.name.startswith(p) for p in prefixes):
            continue
        if not record.result_json or len(record.result_json) > 1_000_000:
            continue
        try:
            data = _unwrap(record.result_json)
            outputs = data.get("outputs") if data else None
            if not isinstance(outputs, list) or len(outputs) > 50:
                continue
            for output in outputs:
                if not isinstance(output, dict):
                    continue
                relative = output.get("relative_path")
                if not isinstance(relative, str) or not re.fullmatch(r"PDF结果-[0-9a-f-]{36}/[^/\\\x00-\x1f]{1,110}\.pdf", relative, re.IGNORECASE):
                    continue
                parsed = PurePosixPath(relative)
                if str(root / relative) != output.get("path") or relative in occupied:
                    continue
                latest[relative] = {"kind":"legacy_workspace", "path":relative, "name":parsed.name,
                    "last_operation":"pdf_pages", "last_operated_at":record.finished_at or record.started_at or ""}
        except (ValueError, TypeError, RecursionError):
            continue
    return sorted([*base,*latest.values()],key=artifact_record_sort_key,reverse=True)[:MAX_THREAD_ARTIFACTS]
