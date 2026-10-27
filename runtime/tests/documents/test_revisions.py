# runtime/tests/documents/test_revisions.py
import io

from lxml import etree

from verilex_runtime.documents.revisions import accept_revisions

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _tree(xml: str) -> etree._ElementTree:
    return etree.parse(io.BytesIO(xml.encode()))


BODY = """<w:body xmlns:w="{w}">
<w:p>
  <w:ins w:id="1"><w:r><w:t>保留的文字</w:t></w:r></w:ins>
  <w:del w:id="2"><w:r><w:t>要删的文字</w:t></w:r></w:del>
  <w:r><w:rPr><w:b/><w:rPrChange w:id="3"><w:rPr/></w:rPrChange></w:rPr><w:t>正文</w:t></w:r>
</w:p>
</w:body>""".format(w=W)


def test_accept_keeps_insertions_and_drops_deletions():
    tree = _tree(BODY)
    count = accept_revisions(tree)
    assert count == 3
    text = "".join(t.text or "" for t in tree.iter(f"{{{W}}}t"))
    assert text == "保留的文字正文"
    assert not tree.getroot().xpath(".//w:ins | .//w:del | .//w:rPrChange", namespaces={"w": W})


def test_idempotent_on_clean_document():
    tree = _tree(BODY.replace('<w:ins w:id="1">', '').replace('</w:ins>', '').replace('<w:del w:id="2">', '').replace('</w:del>', ''))
    assert accept_revisions(tree) == 1  # property revision remains in this fixture
    assert accept_revisions(tree) == 0
