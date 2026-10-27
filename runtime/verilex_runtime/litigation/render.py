"""Readable litigation outputs generated deterministically from saved records."""
from .models import DRAFT_NOTICE


def source_label(source: dict) -> str:
    return source['path'] + (f" 第{source['page']}页" if source.get('page') else '（文件）')


def verification_summary(snapshot: dict) -> str:
    facts = (snapshot['facts'].get('data') or {}).get('facts', [])
    pending = sum(bool(f['pending']) or f['status'] == 'pending' for f in facts)
    conflicts = len((snapshot['facts'].get('data') or {}).get('conflicts', []))
    return f'{DRAFT_NOTICE}\n\n核验状态：待确认事实 {pending} 条；事实冲突 {conflicts} 条。法条须以已保存焦点的核验记录为准。\n'


def render_markdown(kind: str, snapshot: dict) -> str:
    data = snapshot[kind].get('data')
    if not data:
        raise ValueError('missing output')
    lines = [verification_summary(snapshot)]
    if kind == 'facts':
        lines += ['# 事实时间线']
        for fact in data['facts']:
            status = '用户已核对' if fact['status'] == 'confirmed' else ('待确认' if fact['pending'] else ('用户已补充，待律师复核' if fact.get('user_note') else '有材料支持，待律师复核'))
            lines += [f"\n## {fact['date'] or '日期待确认'} · {status}", fact['text'], f"重要性：{ {'high':'高','medium':'中','low':'低'}[fact['importance']]}"]
            lines += [f"- 出处：{source_label(s)}；原文：{s['quote']}" for s in fact['sources']]
            lines += [f'- 待确认：{p}' for p in fact['pending']]
            if fact['user_note']:
                lines += ['用户口述/核对说明：' + fact['user_note']]
        lines += ['\n# 缺口报告', *['- '+v for v in data['gaps']], '\n# 法律关系小结', data['relationships']]
        for conflict in data.get('conflicts', []):
            lines += ['\n# 待处理冲突 ' + conflict['id'], '已保留：'+conflict['previous']['text'], '新材料提议：'+conflict['proposed']['text']]
    elif kind == 'evidence':
        lines += ['# 内部证据评估']
        for item in data['evidence']:
            lines += [f"\n## {item['group']} · {item['name']}", f"证明目的：{item['purpose']}", f"证明力：{ {'strong':'强','medium':'中','weak':'弱','missing':'缺失'}[item['strength']]}，请律师复核", item['assessment']]
        for title, key in [('证据缺口','gaps'),('矛盾','contradictions'),('补证建议','suggestions')]:
            lines += ['\n# '+title, *['- '+v for v in data[key]]]
    elif kind == 'issues':
        lines += ['# 争议焦点与要件表', '\n## 缺口清单']
        for issue in data['issues']:
            lines += [f"- {issue['title']} / {e['name']}：{e['gap']}" for e in issue['elements'] if e['gap']]
        for issue in data['issues']:
            lines += ['\n## '+issue['title']]
            for element in issue['elements']:
                lines += ['\n### '+element['name'], '强弱：'+{'strong':'强','medium':'中','weak':'弱','missing':'缺失'}[element['strength']]+'，请律师复核']
                for label, key in [('支持','supporting'),('反驳','opposing')]:
                    lines += [f"- {label}：{source_label(s)}；原文：{s['quote']}" for s in element[key]]
            for law in issue['laws']:
                lines += [f"法条原文核验通过：{law['clause']}（{law['bbbs']}）", law['quote']]
    elif kind == 'strategy':
        lines += ['# 策略备忘']
        for title, key in [('有利因素','favorable'),('不利因素','unfavorable'),('对方可能的抗辩','defenses'),('补证与行动建议','actions'),('对方会怎么打我们','opposing_counsel')]:
            lines += ['\n## '+title, *['- '+v for v in data[key]]]
    return '\n\n'.join(lines)+'\n'


def render_evidence_docx(path, snapshot: dict) -> None:
    from docx import Document
    document = Document()
    document.add_heading('核验状态汇总', 0)
    document.add_paragraph(verification_summary(snapshot))
    document.add_page_break()
    document.add_heading('证据目录', 0)
    document.add_paragraph(DRAFT_NOTICE)
    table = document.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    headers = ['组别', '序号', '名称', '类型', '证明目的', '出处/页码']
    for cell, title in zip(table.rows[0].cells, headers):
        cell.text = title
    items = snapshot['evidence']['data']['evidence']
    for index, item in enumerate(items, start=1):
        values = [item['group'], str(index), item['name'], item['kind'], item['purpose'], '\n'.join(source_label(s) for s in item['sources'])]
        for cell, value in zip(table.add_row().cells, values):
            cell.text = value
    document.save(path)
    # Deterministic reopen: every submitted row and column must survive intact.
    reopened = Document(path)
    if len(reopened.tables) != 1 or len(reopened.tables[0].rows) != len(items)+1:
        raise ValueError('evidence document validation failed')
    if [c.text for c in reopened.tables[0].rows[0].cells] != headers:
        raise ValueError('evidence document validation failed')
