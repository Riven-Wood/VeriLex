"""Bind references to real tool calls without guessing document titles or meaning.

The persisted call retains its arguments and exact result (including partial
ranges and versions). A receipt proves that return, not legal applicability or
that a search preview constitutes a complete source.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from verilex_runtime.contract_review.opinions import Opinion


@dataclass(frozen=True, slots=True)
class RetrievedSource:
    source_call_id: str
    source_kind: str
    source_label: str
    verified_at: str


class SourceIndex:
    def __init__(self, entries: Iterable[RetrievedSource] = ()):
        self._entries = {entry.source_call_id: entry for entry in entries}

    def __len__(self) -> int:
        return len(self._entries)

    def match(self, source_call_id: str) -> RetrievedSource | None:
        return self._entries.get(source_call_id)


class CitationGate:
    """Attach runtime-owned metadata only for the explicitly selected call."""

    def validate(self, opinions: Sequence[Opinion], thread_sources: SourceIndex) -> list[Opinion]:
        result = []
        for opinion in opinions:
            citations = []
            for citation in opinion.citations:
                if not citation.source_call_id:
                    # Old records remain readable, without inventing a receipt.
                    citations.append(replace(citation, source_label='未关联读取记录', verified_at=''))
                    continue
                source = thread_sources.match(citation.source_call_id)
                if source is not None:
                    citations.append(replace(citation, source_kind=source.source_kind,
                        source_label=source.source_label, verified_at=source.verified_at))
            result.append(replace(opinion, citations=tuple(citations)))
        return result


def source_index_from_tool_calls(records) -> SourceIndex:
    """Use the shared tool-call receipt, independent of provider result shapes."""
    from verilex_runtime.storage.models import ToolCallStatus

    entries = []
    for record in records:
        if record.status is not ToolCallStatus.SUCCEEDED:
            continue
        try:
            result = json.loads(record.result_json or '{}')
        except (TypeError, ValueError):
            continue
        if not isinstance(result, dict) or result.get('ok') is not True or not result.get('output'):
            continue
        name = record.name
        kind = ('mcp' if name.startswith('mcp__') else 'knowledge' if name.startswith('knowledge_')
                else 'web' if name.startswith(('law_', 'web_')) else 'tool')
        entries.append(RetrievedSource(record.id, kind, name, (record.finished_at or '')[:10]))
    return SourceIndex(entries)
