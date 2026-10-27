"""Validated litigation records; model proposals cannot confer human review."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

DRAFT_NOTICE = 'AI 草稿，须律师审查'

class StrictRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

class SourceLocator(StrictRecord):
    version: Literal[1] = 1
    file_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    kind: Literal['docx','pdf','text']
    part: str = Field(default='',max_length=100)
    key: str = Field(max_length=1000)
    index: int = Field(ge=0,le=1000000)

class Citation(StrictRecord):
    locator: SourceLocator | None = None
    path: str = Field(min_length=1, max_length=500)
    page: int | None = Field(default=None, ge=1, le=10000)
    quote: str = Field(min_length=1, max_length=4000)

class Fact(StrictRecord):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    date: str = Field(default='', max_length=80)
    text: str = Field(min_length=1, max_length=4000)
    sources: list[Citation] = Field(default_factory=list, max_length=20)
    importance: Literal['high', 'medium', 'low'] = 'low'
    pending: list[str] = Field(default_factory=list, max_length=30)
    status: Literal['pending', 'supported', 'confirmed'] = 'pending'
    user_note: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def missing_information(self):
        if not self.date.strip() and '日期待确认' not in self.pending:
            self.pending.append('日期待确认')
        if not self.sources and not self.user_note and '事实来源待确认' not in self.pending:
            self.pending.append('事实来源待确认')
        if self.status != 'confirmed':
            self.status = 'pending' if self.pending else 'supported'
        return self

class FactsProposal(StrictRecord):
    facts: list[Fact] = Field(max_length=500)
    gaps: list[str] = Field(default_factory=list, max_length=100)
    relationships: str = Field(default='', max_length=16000)
    summary: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def unique_facts(self):
        if len({f.id for f in self.facts}) != len(self.facts):
            raise ValueError('duplicate fact id')
        return self

class Evidence(StrictRecord):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    group: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=300)
    kind: str = Field(min_length=1, max_length=100)
    purpose: str = Field(min_length=1, max_length=2000)
    sources: list[Citation] = Field(min_length=1, max_length=20)
    strength: Literal['strong', 'medium', 'weak', 'missing'] = 'weak'
    assessment: str = Field(default='', max_length=4000)

class EvidenceProposal(StrictRecord):
    evidence: list[Evidence] = Field(min_length=1, max_length=500)
    gaps: list[str] = Field(default_factory=list, max_length=100)
    contradictions: list[str] = Field(default_factory=list, max_length=100)
    suggestions: list[str] = Field(default_factory=list, max_length=100)
    summary: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def unique_evidence(self):
        if len({item.id for item in self.evidence}) != len(self.evidence):
            raise ValueError('duplicate evidence id')
        return self


class CaseInfo(StrictRecord):
    title: str = Field(default='', max_length=300)
    cause: str = Field(default='', max_length=200)
    plaintiff: str = Field(default='', max_length=1000)
    defendant: str = Field(default='', max_length=1000)
    position: str = Field(default='原告', max_length=1000)
    theory: str = Field(default='', max_length=4000)


class CaseSuggestionField(StrictRecord):
    key: Literal['title', 'cause', 'plaintiff', 'defendant']
    value: str = Field(min_length=1, max_length=1000)
    sources: list[Citation] = Field(min_length=1, max_length=20)
    reason: str = Field(default='', max_length=2000)


class CaseSuggestion(StrictRecord):
    fields: list[CaseSuggestionField] = Field(max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def unique_fields(self):
        if len({field.key for field in self.fields}) != len(self.fields):
            raise ValueError('duplicate case field')
        limits = {'title': 300, 'cause': 200, 'plaintiff': 1000, 'defendant': 1000}
        if any(not field.value.strip() or len(field.value) > limits[field.key] for field in self.fields):
            raise ValueError('invalid case field')
        return self


def merge_facts(previous: list[Fact], proposed: list[Fact]) -> tuple[list[Fact], list[dict]]:
    """Keep prior IDs and human decisions; record conflicts without accepting them."""
    merged = {fact.id: fact.model_copy(deep=True) for fact in previous}
    conflicts = []
    for candidate in proposed:
        item = candidate.model_copy(deep=True)
        # Only the explicit review endpoint may create a human-confirmed fact.
        item.status = 'pending' if item.pending else 'supported'
        item.user_note = ''
        old = merged.get(item.id)
        if old is None:
            merged[item.id] = item
        elif (old.text, old.date) != (item.text, item.date):
            conflicts.append({'id': old.id, 'previous': old.model_dump(), 'proposed': item.model_dump()})
        elif old.status != 'confirmed':
            # Union sources and pending flags; never clear an unresolved flag by generation.
            old.sources.extend(s for s in item.sources if s not in old.sources)
            old.pending = list(dict.fromkeys([*old.pending, *item.pending]))
            old.status = 'pending' if old.pending else 'supported'
    return sorted(merged.values(), key=lambda f: (not bool(f.date), f.date, f.id)), conflicts

class LawCitation(StrictRecord):
    bbbs: str = Field(min_length=1, max_length=100)
    clause: str = Field(min_length=1, max_length=32)
    quote: str = Field(min_length=1, max_length=8000)

class Element(StrictRecord):
    name: str = Field(min_length=1, max_length=300)
    supporting: list[Citation] = Field(default_factory=list, max_length=20)
    opposing: list[Citation] = Field(default_factory=list, max_length=20)
    strength: Literal['strong', 'medium', 'weak', 'missing'] = 'missing'
    gap: str = Field(default='', max_length=2000)

class Issue(StrictRecord):
    title: str = Field(min_length=1, max_length=300)
    elements: list[Element] = Field(min_length=1, max_length=30)
    laws: list[LawCitation] = Field(min_length=1, max_length=20)

class IssuesProposal(StrictRecord):
    issues: list[Issue] = Field(min_length=1, max_length=30)
    summary: str = Field(min_length=1, max_length=1000)

class StrategyProposal(StrictRecord):
    favorable: list[str] = Field(max_length=50)
    unfavorable: list[str] = Field(min_length=1, max_length=50)
    defenses: list[str] = Field(min_length=1, max_length=50)
    actions: list[str] = Field(min_length=1, max_length=50)
    opposing_counsel: list[str] = Field(min_length=1, max_length=50)
    summary: str = Field(min_length=1, max_length=1000)


class ElementOutline(StrictRecord):
    title: str = Field(min_length=1, max_length=300)
    elements: list[str] = Field(min_length=1, max_length=30)

    @model_validator(mode='after')
    def valid_names(self):
        if any(not name.strip() or len(name) > 300 for name in self.elements) or len(set(self.elements)) != len(self.elements):
            raise ValueError('invalid element names')
        return self


class ElementsProposal(StrictRecord):
    issues: list[ElementOutline] = Field(min_length=1, max_length=30)
    summary: str = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def unique_titles(self):
        if len({item.title for item in self.issues}) != len(self.issues):
            raise ValueError('duplicate issue title')
        return self


class FactSupplement(StrictRecord):
    fact_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    user_quote: str = Field(min_length=2, max_length=4000)
    date: str = Field(default='', max_length=80)
