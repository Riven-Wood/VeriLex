"""New pleading typography, measured from the owner's 2026-10-25 DOCX sample.

Only formatting values are reused. No case text or proprietary font is bundled.
Existing documents and contract formatting are not restyled.
"""
import re
from docx.shared import Pt, Twips, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def configure(doc):
    for name in ['Normal', *[f'Heading {i}' for i in range(1, 10)]]:
        style = doc.styles[name]
        title = name == 'Heading 1'
        style.font.name = '黑体' if title else 'Times New Roman'
        style.font.size = Pt(18 if title else 14)
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.font.bold = name != 'Normal' and not title
        style.font.italic = False
        fonts = style.element.get_or_add_rPr().rFonts
        for key in list(fonts.attrib):
            if 'theme' in key.lower(): del fonts.attrib[key]
        fonts.set(qn('w:eastAsia'), '黑体' if title else '仿宋')
        paragraph = style.paragraph_format
        paragraph.line_spacing = 1.5
        paragraph.space_before = paragraph.space_after = Pt(0)
        paragraph.first_line_indent = Pt(0 if title else 28)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if title else WD_ALIGN_PARAGRAPH.JUSTIFY
    section = doc.sections[0]
    section.page_width, section.page_height = Twips(11906), Twips(16838)
    section.top_margin, section.bottom_margin = Twips(1247), Twips(1134)
    section.left_margin = section.right_margin = Twips(1474)
    section.header_distance = section.footer_distance = Twips(720)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.first_line_indent = Pt(0)
    run = footer.add_run(); run.font.size = Pt(10.5)
    field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE')
    field.append(run._r); footer._p.append(field)


def paragraph_defaults(p, text, *, in_table=False, numbered=False):
    # Heading styles carry title typography. Ordinary body text uses two-character indent.
    if in_table or numbered or p.alignment in (WD_ALIGN_PARAGRAPH.CENTER, WD_ALIGN_PARAGRAPH.RIGHT):
        p.paragraph_format.first_line_indent = Pt(0)
    elif text.strip() == '此致':
        p.paragraph_format.first_line_indent = Pt(28)
    elif re.fullmatch(r'(?:[〇零一二三四五六七八九十\d]{4}年[〇零一二三四五六七八九十\d]{1,3}月[〇零一二三四五六七八九十\d]{1,3}日|[年月日\s＿_]+)', text.strip()):
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.first_line_indent = Pt(0)
