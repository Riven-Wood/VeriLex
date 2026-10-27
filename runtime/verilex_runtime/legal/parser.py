"""Lenient response parsing for the official law database.

``client.py`` is the strict protocol boundary: any unexpected shape there is
reported as ``law_site_changed``. This module is the forgiving layer above
it:

* malformed search rows are skipped and counted instead of failing the page;
* chapter trees are bounded (500 nodes / 500 nesting levels) before use;
* docx bodies are extracted with zipfile + xml.etree only, with zip-bomb
  checks on the declared member size before any decompression;
* clause lookup returns a fixed-size context window.

Everything that still cannot be interpreted raises the same fixed-code
``LawClientError`` the client uses. The site's bulk-download endpoint is not
referenced anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
import xml.etree.ElementTree as ET
import zipfile

from verilex_runtime.legal.client import (
    LawClientError,
    LawDetail,
    LawSearchRow,
)

__all__ = [
    "LocatedClause",
    "UNKNOWN_EFFECTIVITY",
    "clean_title",
    "effectivity_label",
    "extract_docx_text",
    "locate_clause",
    "parse_detail",
    "parse_search_rows",
    "parse_search_rows_with_skipped",
]


# ---------------------------------------------------------------------------
# Title cleaning and effectivity labeling
# ---------------------------------------------------------------------------

_HIGHLIGHT_TAG = re.compile(r"<[^>]+>")

UNKNOWN_EFFECTIVITY = "未能确认效力"

_EFFECTIVITY_LABELS = {1: "已废止", 2: "已修改", 3: "现行有效"}


def clean_title(raw: str) -> str:
    """Strip highlight markup such as ``<em class='highlight'>…</em>``.

    Any tag-looking fragment is removed and outer whitespace is stripped;
    whitespace between two removed tags is preserved as-is.
    """
    if not isinstance(raw, str):
        raise TypeError("raw title must be a string")
    return _HIGHLIGHT_TAG.sub("", raw).strip()


def effectivity_label(sxx: int) -> str:
    """Map the site's effectivity enum to its fixed Chinese label.

    The database uses 1=已废止, 2=已修改, 3=现行有效. Any other value —
    including ``bool`` and non-int input — is reported honestly as
    「未能确认效力」 instead of being guessed.
    """
    if type(sxx) is not int:
        return UNKNOWN_EFFECTIVITY
    return _EFFECTIVITY_LABELS.get(sxx, UNKNOWN_EFFECTIVITY)


# ---------------------------------------------------------------------------
# Search rows
# ---------------------------------------------------------------------------


def _parse_row_lenient(item: object) -> LawSearchRow | None:
    """Parse one search row, returning None only for unusable rows.

    A row is skipped when it is not a dict, when ``bbbs`` is missing or not
    a non-empty string, when ``title`` is missing/not a string or empty
    after highlight cleaning, or when ``sxx`` is missing/not a true int
    (``bool`` counts as invalid). The display-only fields ``flxz``,
    ``zdjgName``, ``gbrq`` and ``sxrq`` default to an empty string when
    absent or not a string, so a slightly incomplete row still surfaces as
    a search candidate.
    """
    if not isinstance(item, dict):
        return None
    bbbs = item.get("bbbs")
    if type(bbbs) is not str or not bbbs:
        return None
    raw_title = item.get("title")
    if type(raw_title) is not str:
        return None
    title = clean_title(raw_title)
    if not title:
        return None
    sxx = item.get("sxx")
    if type(sxx) is not int:
        return None

    def _optional(key: str) -> str:
        value = item.get(key)
        return value if type(value) is str else ""

    return LawSearchRow(
        bbbs=bbbs,
        title=title,
        flxz=_optional("flxz"),
        zdjg_name=_optional("zdjgName"),
        sxx=sxx,
        gbrq=_optional("gbrq"),
        sxrq=_optional("sxrq"),
    )


def parse_search_rows_with_skipped(
    payload: dict,
) -> tuple[list[LawSearchRow], int, int]:
    """Lenient search parser that also exposes the skip count.

    Returns ``(rows, total, skipped)`` where ``skipped`` counts rows dropped
    for being malformed. Out-of-bounds responses (``rows`` not a list or
    ``total`` not a true int) raise ``law_site_changed``.
    """
    if not isinstance(payload, dict):
        raise LawClientError("law_site_changed") from None
    rows_value = payload.get("rows")
    total = payload.get("total")
    if not isinstance(rows_value, list) or type(total) is not int:
        raise LawClientError("law_site_changed") from None
    rows: list[LawSearchRow] = []
    skipped = 0
    for item in rows_value:
        row = _parse_row_lenient(item)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return rows, total, skipped


def parse_search_rows(payload: dict) -> tuple[list[LawSearchRow], int]:
    """Parse a search response, skipping malformed rows instead of failing.

    Unlike the client's strict parser, one malformed row does not fail the
    whole page; use :func:`parse_search_rows_with_skipped` to observe how
    many rows were skipped. Structurally out-of-bounds responses (``rows``
    not a list or ``total`` not an int) still raise ``law_site_changed``.
    """
    rows, total, _ = parse_search_rows_with_skipped(payload)
    return rows, total


# ---------------------------------------------------------------------------
# Detail payloads and chapter-tree bounding
# ---------------------------------------------------------------------------

_MAX_TREE_NODES = 500
_MAX_TREE_DEPTH = 500
_TRUNCATED_FLAG = "_truncated"


def _bound_chapter_tree(tree: dict) -> dict:
    """Bound a chapter tree to at most 500 nodes and 500 nesting levels.

    Trimming rule: nodes are kept in document (pre-)order until either the
    node budget or the depth cap is hit; a non-dict child is dropped. When
    anything was pruned the returned root dict carries the extra key
    ``"_truncated": True``; an untouched tree is returned unchanged and
    carries no such key.
    """
    state = {"nodes": 0, "truncated": False}

    def walk(node: object, depth: int) -> dict | None:
        if not isinstance(node, dict) or depth > _MAX_TREE_DEPTH:
            state["truncated"] = True
            return None
        if state["nodes"] >= _MAX_TREE_NODES:
            state["truncated"] = True
            return None
        state["nodes"] += 1
        bounded = {key: value for key, value in node.items() if key != "children"}
        children = node.get("children")
        if isinstance(children, list):
            kept: list[dict] = []
            for child in children:
                bounded_child = walk(child, depth + 1)
                if bounded_child is not None:
                    kept.append(bounded_child)
            bounded["children"] = kept
        elif "children" in node:
            bounded["children"] = children
        return bounded

    bounded_root = walk(tree, 1)
    if not state["truncated"]:
        return tree
    assert bounded_root is not None
    bounded_root[_TRUNCATED_FLAG] = True
    return bounded_root


def _direct_docx_url(oss_file: object) -> str | None:
    """Keep only absolute docx links (mirrors the client's rule)."""
    if not isinstance(oss_file, dict):
        return None
    path = oss_file.get("ossWordPath")
    if type(path) is not str:
        return None
    if path.startswith(("http://", "https://")):
        return path
    return None


def parse_detail(payload: dict) -> LawDetail:
    """Parse a detail response into the client's ``LawDetail`` dataclass.

    The chapter tree is bounded to 500 nodes / 500 nesting levels; when
    trimming happened the returned root dict is marked with the extra key
    ``"_truncated": True`` (unpruned trees carry no such key and are passed
    through unchanged). Malformed detail payloads (missing ``data``, an
    unusable row, or a non-dict ``content``) raise ``law_site_changed``.
    """
    if not isinstance(payload, dict):
        raise LawClientError("law_site_changed") from None
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LawClientError("law_site_changed") from None
    row = _parse_row_lenient(data)
    if row is None:
        raise LawClientError("law_site_changed") from None
    content = data.get("content", {})
    if not isinstance(content, dict):
        raise LawClientError("law_site_changed") from None
    return LawDetail(
        bbbs=row.bbbs,
        title=row.title,
        flxz=row.flxz,
        zdjg_name=row.zdjg_name,
        sxx=row.sxx,
        gbrq=row.gbrq,
        sxrq=row.sxrq,
        chapter_tree=_bound_chapter_tree(content),
        docx_url=_direct_docx_url(data.get("ossFile")),
    )


# ---------------------------------------------------------------------------
# Docx body extraction
# ---------------------------------------------------------------------------

_DOCX_MAIN_MEMBER = "word/document.xml"
_MAX_DOCX_XML_BYTES = 8 * 1024 * 1024
_TAB_REPLACEMENT = "\u3000"  # w:tab renders as a full-width space


def _local_name(tag: object) -> str:
    """Return the tag's local name, ignoring namespace prefixes."""
    if not isinstance(tag, str):
        return ""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag.rsplit(":", 1)[-1]


def _paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for node in paragraph.iter():
        local = _local_name(node.tag)
        if local == "t":
            parts.append(node.text or "")
        elif local == "tab":
            parts.append(_TAB_REPLACEMENT)
    return "".join(parts)


def extract_docx_text(data: bytes) -> list[str]:
    """Extract paragraph texts from an official docx body.

    Only the ``word/document.xml`` member is read, only ``w:t`` (text) and
    ``w:tab`` (converted to the full-width space ``\\u3000``) contribute
    text, and elements are matched by local name so namespace-prefix
    changes do not matter. Zip-bomb defense: the member's declared
    uncompressed size (``ZipInfo.file_size``) is checked against the 8 MiB
    limit *before* anything is decompressed, and the actual read is bounded
    to the same limit as a second line of defense. Blank paragraphs are
    dropped; remaining paragraph texts are returned verbatim, indentation
    included. Anything unreadable raises ``law_site_changed``.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, ValueError):
        raise LawClientError("law_site_changed") from None
    with archive:
        try:
            info = archive.getinfo(_DOCX_MAIN_MEMBER)
        except KeyError:
            raise LawClientError("law_site_changed") from None
        if info.file_size > _MAX_DOCX_XML_BYTES:
            # Reject on the declared size before any decompression runs.
            raise LawClientError("law_site_changed")
        try:
            with archive.open(_DOCX_MAIN_MEMBER) as member:
                xml_bytes = member.read(_MAX_DOCX_XML_BYTES + 1)
        except (
            zipfile.BadZipFile,
            EOFError,
            NotImplementedError,
            OSError,
            RuntimeError,
            ValueError,
        ):
            raise LawClientError("law_site_changed") from None
    if len(xml_bytes) > _MAX_DOCX_XML_BYTES:
        raise LawClientError("law_site_changed")
    lowered = xml_bytes.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        # Official Word XML never carries a DTD; reject it before parsing so
        # entity expansion cannot even reach expat's defaults.
        raise LawClientError("law_site_changed")
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, LookupError, ValueError):
        raise LawClientError("law_site_changed") from None
    paragraphs: list[str] = []
    for element in root.iter():
        if _local_name(element.tag) == "p":
            text = _paragraph_text(element)
            if text.strip():
                paragraphs.append(text)
    return paragraphs


# ---------------------------------------------------------------------------
# Clause location
# ---------------------------------------------------------------------------

_CLAUSE_NUMBER = re.compile(
    r"^(第[零〇一二三四五六七八九十百千万两0-9]+条(?:之[零〇一二三四五六七八九十百千万两0-9]+)?)(?=\s|[【（(]|$)"
)
_HEADING_NUMBER = re.compile(r"^第[零〇一二三四五六七八九十百千万两0-9]+(?:编|章|节)(?=\s|$)")
_CONTEXT_LIMIT = 3


@dataclass(frozen=True)
class LocatedClause:
    """A located clause plus a bounded context window.

    ``heading`` is the nearest preceding chapter/section line
    (``第X编/章/节``), whitespace-stripped, or ``""`` when none precedes
    the hit. ``clause`` holds the hit paragraph plus any continuation
    paragraphs up to the next ``第X条`` paragraph, trimmed to the first 3.
    ``before``/``after`` keep at most the 3 paragraphs closest to the
    clause block. Paragraph text is preserved verbatim, indentation
    included.
    """

    heading: str
    before: list[str]
    clause: list[str]
    after: list[str]


def locate_clause(paragraphs: list[str], clause: str) -> LocatedClause | None:
    """Locate a clause such as 「第一条」 inside extracted paragraphs.

    Match the complete article marker (including an optional 条之一 suffix),
    then stop before the next article or structural heading. The argument's own
    leading/trailing whitespace — ASCII or full-width — is ignored. Returns
    ``None`` when no paragraph matches, which callers turn into their
    fixed ``law_not_found`` copy.
    """
    if not isinstance(paragraphs, list) or not isinstance(clause, str):
        return None
    needle = clause.strip()
    if _CLAUSE_NUMBER.fullmatch(needle) is None:
        return None
    items = [item for item in paragraphs if isinstance(item, str)]
    hit: int | None = None
    for index, paragraph in enumerate(items):
        marker = _CLAUSE_NUMBER.match(paragraph.strip())
        if marker is not None and marker.group(1) == needle:
            hit = index
            break
    if hit is None:
        return None
    end = len(items)
    for index in range(hit + 1, len(items)):
        text = items[index].strip()
        if _CLAUSE_NUMBER.match(text) or _HEADING_NUMBER.match(text):
            end = index
            break
    heading = ""
    for paragraph in reversed(items[:hit]):
        stripped = paragraph.strip()
        if _HEADING_NUMBER.match(stripped):
            heading = stripped
            break
    return LocatedClause(
        heading=heading,
        before=items[max(0, hit - _CONTEXT_LIMIT) : hit],
        clause=items[hit:end],
        after=items[end : end + _CONTEXT_LIMIT],
    )
