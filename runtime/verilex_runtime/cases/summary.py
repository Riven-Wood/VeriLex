"""Bounded Yuandian contract-v1 projection, verified against get_api_doc 2026-09-07.

Only the caller's trusted configured case endpoint may select this adapter.
Tests use synthetic content, including the authorized 2026-09-04 probe's shape;
replaying that shape does not constitute live model/application acceptance.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

TOOLS = frozenset({"yuandian_rh_ptal_search", "yuandian_rh_qwal_search", "yuandian_rh_case_details", "yuandian_case_vector_search"})


def _text(value: object, limit: int, *, clip: bool = False) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        return None
    if len(value) > limit:
        return value[:limit - 1] + "…" if clip else None
    return value


def _unwrap(value: object) -> dict | None:
    for _ in range(5):
        if isinstance(value, str):
            value = value.removeprefix("⟦UNTRUSTED_MCP_RESULT_START⟧\n").removesuffix("\n⟦UNTRUSTED_MCP_RESULT_END⟧")
            value = json.loads(value)
        elif isinstance(value, dict):
            if value.get("isError") is True or value.get("truncated") is True:
                return None
            if "text" in value and "data" not in value and "extra" not in value:
                if value.get("code") not in (None,):
                    return None
                value = value["text"]
            elif "content" in value and isinstance(value["content"], list):
                blocks = value["content"]
                if len(blocks) != 1 or not isinstance(blocks[0], dict) or blocks[0].get("type") != "text":
                    return None
                value = blocks[0].get("text")
            else:
                return value
        else:
            return None
    return value if isinstance(value, dict) else None


def case_results_summary(tool_name: str, output: str, *, source_name: str, fetched_at: str, is_yuandian_case: bool = False) -> dict | None:
    """Return a resultSummary or None (retain generic tool rendering)."""
    if not is_yuandian_case or tool_name not in TOOLS or not isinstance(output, str) or len(output) > 1_000_000:
        return None
    source = _text(source_name, 128)
    fetched = _text(fetched_at, 64)
    if not source or not fetched:
        return None
    try:
        data = _unwrap(output)
    except (ValueError, TypeError, RecursionError):
        return None
    if not data or type(data.get("code")) is not int or data["code"] not in (200, 201):
        return None
    vector = tool_name == "yuandian_case_vector_search"
    if vector:
        rows = data.get("extra", {}).get("wenshu") if isinstance(data.get("extra"), dict) else None
        total = None
    elif tool_name == "yuandian_rh_case_details":
        rows, total = data.get("data"), None
    else:
        container = data.get("data")
        rows = container.get("lst") if isinstance(container, dict) else None
        total = container.get("total") if isinstance(container, dict) else None
    if not isinstance(rows, list) or len(rows) > 1000:
        return None
    cases, seen = [], set()
    for row in rows[:20]:
        if not isinstance(row, dict):
            return None
        title = _text(row.get("title"), 120)
        if not title:
            return None
        number = _text(row.get("ah"), 64)
        identity = _text(row.get("scid" if vector else "id"), 128) or "unidentified-" + hashlib.sha256(json.dumps([title, number, _text(row.get("jbdw"), 128)], ensure_ascii=False).encode()).hexdigest()[:24]
        if identity in seen:
            continue
        seen.add(identity)
        cause = _text(row.get("ay"), 128) if not vector else None
        if vector and isinstance(row.get("anyou"), list) and len(row["anyou"]) <= 10 and all(_text(x, 64) for x in row["anyou"]):
            cause = _text("、".join(row["anyou"]), 128)
        judged = _text(row.get("cprq"), 32)
        if vector and type(row.get("jaDate")) is int:
            try:
                judged = datetime.strptime(str(row["jaDate"]), "%Y%m%d").date().isoformat()
            except ValueError:
                pass
        gist = None
        if isinstance(row.get("section"), list):
            for section in row["section"][:100]:
                if isinstance(section, dict) and section.get("name") == "裁判要旨":
                    gist = _text(section.get("value"), 600, clip=True)
                    break
        # '权威案例' is a vendor bucket, not a legal level. Never infer guidance.
        level = _text(row.get("authority"), 64) if vector else None
        cases.append({"sourceId": identity, "title": title, "caseNumber": number,
                      "court": _text(row.get("jbdw"), 128), "cause": cause,
                      "judgedAt": judged, "gist": gist,
                      "excerpt": _text(row.get("content"), 600, clip=True), "level": level})
    count = total if type(total) is int and len(rows) <= total <= 1_000_000_000 else len(rows)
    return {"kind": "case_results", "cases": cases, "count": count,
            "truncated": count > len(rows) or len(rows) > 20,
            "sourceName": source, "fetchedAt": fetched}
