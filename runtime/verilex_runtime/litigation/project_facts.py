"""Project-local fact views, kept independently from source documents."""
import json
import time
import uuid
from typing import Literal

from pydantic import Field, ValidationError

from .models import Citation, StrictRecord
from .service import _LOCK, _failure, safe_path


class Entry(StrictRecord):
    title: str = Field(min_length=1, max_length=300)
    detail: str = Field(default='', max_length=4000)
    date: str = Field(default='', max_length=100)
    other: str = Field(default='', max_length=4000)
    pending: list[str] = Field(default_factory=list, max_length=30)
    sources: list[Citation] = Field(min_length=1, max_length=20)


class ProjectFacts(StrictRecord):
    kind: Literal['timeline', 'chronicle', 'relationship', 'confrontation']
    title: str = Field(min_length=1, max_length=200)
    entries: list[Entry] = Field(min_length=1, max_length=500)
    gaps: list[str] = Field(default_factory=list, max_length=100)


def _source_context(text: str, quote: str) -> str | None:
    """Expand a uniquely located excerpt using only contiguous original text."""
    if not quote or text.count(quote) != 1:
        return None
    start = text.index(quote)
    end = start + len(quote)
    boundaries = '。！？!?；;\n'
    left = max((text.rfind(mark, 0, start) for mark in boundaries), default=-1) + 1
    right_candidates = [index for mark in boundaries if (index := text.find(mark, end)) >= 0]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    # Keep the preceding sentence where possible: it may name the speaker of
    # a sentence beginning with a pronoun. Never cross the located text block.
    previous = max((text.rfind(mark, 0, max(0, left - 1)) for mark in boundaries), default=-1) + 1
    for begin in (previous, left):
        context = text[begin:right].strip()
        if len(context) <= 4000 and quote in context:
            return context
    return None


def save(service, context, payload):
    try:
        result = ProjectFacts.model_validate(payload)
    except ValidationError:
        raise _failure('project_facts_invalid', '成果格式不完整，请检查内容和出处。') from None
    with _LOCK:
        root = service._root(context, write=True)
        service.verify_sources(context, [source for entry in result.entries for source in entry.sources])
        for entry in result.entries:
            for source in entry.sources:
                located = service.locate_source(context, source)
                expanded = _source_context(located.get('text', ''), source.quote) if located.get('status') == 'located' else None
                if expanded and expanded != source.quote:
                    source.quote = expanded
                    service.verify_sources(context, [source])
                elif expanded is None:
                    reason = '位置不唯一' if located.get('status') == 'ambiguous' else '上下文无法完整保留'
                    notice = f'{source.path} 的引文{reason}，请打开原文核对。'
                    if notice not in entry.pending and notice not in result.gaps:
                        if len(entry.pending) < 30:
                            entry.pending.append(notice)
                        elif len(result.gaps) < 100:
                            result.gaps.append(notice)
                        else:
                            raise _failure('project_facts_context', '无法记录引文定位提示，请检查出处后重试。')
        name = f'文书起草/项目梳理-{uuid.uuid4().hex}.json'
        path = safe_path(root, name)
        data = {'version': 1, 'created_ns': time.time_ns(), **result.model_dump()}
        service.products._publish(context, path, json.dumps(data, ensure_ascii=False).encode('utf8'), internal=True)
        return {'path': name, **data}


def list_results(service, context):
    root = service._root(context)
    directory = safe_path(root, '文书起草')
    results, errors = [], []
    if not directory.is_dir():
        return {'results': results, 'errors': errors}
    for path in directory.glob('项目梳理-*.json'):
        try:
            safe = safe_path(root, str(path.relative_to(root)))
            if not safe.is_file() or safe.stat().st_size > 4_000_000:
                raise ValueError('invalid file')
            raw = json.loads(safe.read_text(encoding='utf8'))
            if raw.get('version') != 1 or not isinstance(raw.get('created_ns'), int):
                raise ValueError('invalid version')
            value = ProjectFacts.model_validate({key: raw[key] for key in ('kind', 'title', 'entries', 'gaps')})
            results.append({'path': str(safe.relative_to(root)), 'created_ns': raw['created_ns'], **value.model_dump()})
        except (ValueError, OSError, KeyError, ValidationError):
            errors.append({'path': path.name, 'notice': '成果文件无法读取。'})
    results.sort(key=lambda item: item['created_ns'], reverse=True)
    return {'results': results, 'errors': errors}
