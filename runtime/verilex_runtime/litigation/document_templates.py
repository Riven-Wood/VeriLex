"""Official court-form catalogue. Reviewed XML templates, no model layout."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from verilex_runtime.tools.registry import ToolError

ASSETS = Path(__file__).with_name('templates')


def catalogue() -> list[dict]:
    return json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))['templates']


def template(template_id: str) -> tuple[dict, bytes]:
    item = next((item for item in catalogue() if item['id'] == template_id), None)
    if item is None:
        raise ToolError('litigation_template_unavailable', '该文书样式尚未适配，请选择已支持的样式。')
    content = (ASSETS / (template_id + '.xml')).read_bytes()
    if hashlib.sha256(content).hexdigest() != item['sha256']:
        raise ToolError('litigation_template_invalid', '文书样式校验失败，未出稿。请重新安装完整应用。')
    return item, content


def template_index() -> list[dict]:
    """Small model-facing catalogue; field rules are read for one chosen form."""
    return [{key: value for key, value in item.items() if key != 'fields'} for item in catalogue()]
