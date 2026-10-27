"""HTML5 visible-text extraction for bounded web responses."""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element

import html5lib

from verilex_runtime.tools.registry import ToolError
from verilex_runtime.web.client import FetchedPage


_MAX_TEXT_CHARACTERS = 20_000
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# The client already caps raw responses at 2 MiB. These additional structural
# limits keep browser-grade error recovery from turning a small hostile input
# into an unbounded DOM or a long synchronous traversal. Twenty thousand nodes
# is ample for the at-most-20,000-character result; 256 levels exceeds normal
# document nesting while staying well below Python's recursion boundary.
_MAX_HTML_TREE_NODES = 20_000
_MAX_HTML_TREE_DEPTH = 256
_HTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
_SKIPPED_ELEMENTS = {
    "head",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
    "title",
}
_BLOCK_ELEMENTS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "dd",
    "div",
    "dl",
    "dt",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
}
_BOUNDARY_ELEMENTS = _BLOCK_ELEMENTS | {
    "noscript",
    "script",
    "style",
    "svg",
    "template",
}
_CHARSET = re.compile(r"(?:^|;)\s*charset\s*=\s*[\"']?([^;\"'\s]+)", re.I)
_EtreeTreeBuilder = html5lib.getTreeBuilder("etree")
_EtreeElement = _EtreeTreeBuilder.elementClass
_EtreeComment = _EtreeTreeBuilder.commentClass
_EtreeDocument = _EtreeTreeBuilder.documentClass
_EtreeDocumentType = _EtreeTreeBuilder.doctypeClass
_EtreeDocumentFragment = _EtreeTreeBuilder.fragmentClass


class _HtmlTreeBudgetExceeded(Exception):
    pass


@dataclass(slots=True)
class _HtmlTreeBudget:
    allocated_nodes: int = 0

    def reserve_node(self) -> None:
        if self.allocated_nodes >= _MAX_HTML_TREE_NODES:
            raise _HtmlTreeBudgetExceeded
        self.allocated_nodes += 1


class _BudgetedEtreeTreeBuilder(_EtreeTreeBuilder):
    """Stop hostile structures while html5lib is still allocating the DOM."""

    def __init__(self, namespaceHTMLElements: bool) -> None:
        budget = _HtmlTreeBudget()

        class BudgetedElement(_EtreeElement):
            def __init__(self, name, namespace=None):  # noqa: ANN001
                budget.reserve_node()
                super().__init__(name, namespace)

        class BudgetedComment(_EtreeComment):
            def __init__(self, data):  # noqa: ANN001
                budget.reserve_node()
                super().__init__(data)

        class BudgetedDocument(_EtreeDocument):
            def __init__(self) -> None:
                budget.reserve_node()
                super().__init__()

        class BudgetedDocumentType(_EtreeDocumentType):
            def __init__(self, name, publicId, systemId):  # noqa: N803, ANN001
                budget.reserve_node()
                super().__init__(name, publicId, systemId)

        class BudgetedDocumentFragment(_EtreeDocumentFragment):
            def __init__(self) -> None:
                budget.reserve_node()
                super().__init__()

        self.elementClass = BudgetedElement
        self.commentClass = BudgetedComment
        self.documentClass = BudgetedDocument
        self.doctypeClass = BudgetedDocumentType
        self.fragmentClass = BudgetedDocumentFragment
        self._budget = budget
        super().__init__(namespaceHTMLElements)

    def createElement(self, token):  # noqa: N802, ANN001, ANN201
        self._check_depth(len(self.openElements) + 1)
        return super().createElement(token)

    def insertElementNormal(self, token):  # noqa: N802, ANN001, ANN201
        self._check_depth(len(self.openElements) + 1)
        return super().insertElementNormal(token)

    @staticmethod
    def _check_depth(depth: int) -> None:
        if depth > _MAX_HTML_TREE_DEPTH:
            raise _HtmlTreeBudgetExceeded


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    title: str
    text: str
    character_count: int
    truncated: bool


def extract_web_text(page: FetchedPage) -> ExtractedPage:
    if len(page.body) > _MAX_RESPONSE_BYTES:
        raise _fetch_failed()
    media_type, charset = _parse_content_type(page.content_type)
    if media_type not in {"text/html", "text/plain"} or charset not in {
        None,
        "utf-8",
        "utf8",
    }:
        raise _unsupported()
    if b"\x00" in page.body:
        raise _unsupported()
    try:
        decoded = page.body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _unsupported() from None

    if media_type == "text/html":
        try:
            parser = html5lib.HTMLParser(
                tree=_BudgetedEtreeTreeBuilder,
                namespaceHTMLElements=False,
            )
            root = parser.parse(decoded, scripting=True)
            extracted_html = _extract_html_tree(root)
            title = _collapse(extracted_html.title_parts)
            text = _collapse(extracted_html.text_parts)
        except Exception:
            raise _fetch_failed() from None
    else:
        title = ""
        text = " ".join(decoded.split())

    if not text:
        raise _fetch_failed()
    if not title:
        title = urlsplit(page.final_url).hostname or page.final_url
    truncated = len(text) > _MAX_TEXT_CHARACTERS
    if truncated:
        text = text[:_MAX_TEXT_CHARACTERS]
    return ExtractedPage(
        title=title[:512],
        text=text,
        character_count=len(text),
        truncated=truncated,
    )


@dataclass(slots=True)
class _HtmlTreeExtraction:
    text_parts: list[str]
    title_parts: list[str]


def _extract_html_tree(root: Element) -> _HtmlTreeExtraction:
    extraction = _HtmlTreeExtraction(text_parts=[], title_parts=[])
    title = _first_visible_html_title(root)
    if title is not None:
        extraction.title_parts.append(title)
    _walk_visible_tree(root, extraction)
    return extraction


def _walk_visible_tree(
    root: Element,
    extraction: _HtmlTreeExtraction,
) -> None:
    # Events are (phase, element, depth, parent_blocked). A separate exit/tail
    # event preserves browser tree order without relying on Python recursion.
    stack: list[tuple[str, Element, int, bool]] = [("enter", root, 1, False)]
    visited_nodes = 0
    while stack:
        phase, element, depth, parent_blocked = stack.pop()
        if phase == "tail":
            if not parent_blocked and element.tail:
                extraction.text_parts.append(element.tail)
            continue

        tag = _tag_name(element)
        if phase == "exit":
            if not parent_blocked and tag in _BOUNDARY_ELEMENTS:
                extraction.text_parts.append(" ")
            continue

        visited_nodes += 1
        if (
            visited_nodes > _MAX_HTML_TREE_NODES
            or depth > _MAX_HTML_TREE_DEPTH
        ):
            raise _HtmlTreeBudgetExceeded

        is_element = isinstance(element.tag, str)
        attributes = (
            {
                _tag_name_from_value(name): value
                for name, value in element.attrib.items()
            }
            if is_element
            else {}
        )
        aria_hidden = (
            (attributes.get("aria-hidden") or "").strip().casefold() == "true"
        )
        blocked = bool(
            parent_blocked
            or not is_element
            or tag in _SKIPPED_ELEMENTS
            or "hidden" in attributes
            or aria_hidden
        )

        if not parent_blocked and tag in _BOUNDARY_ELEMENTS:
            extraction.text_parts.append(" ")
        if not blocked:
            if element.text:
                extraction.text_parts.append(element.text)

        stack.append(("exit", element, depth, blocked))
        for child in reversed(element):
            stack.append(("tail", child, depth + 1, blocked))
            stack.append(("enter", child, depth + 1, blocked))


def _tag_name(element: Element) -> str:
    return _tag_name_from_value(element.tag) if isinstance(element.tag, str) else ""


def _is_html_title(element: Element) -> bool:
    return isinstance(element.tag, str) and element.tag in {
        "title",
        f"{{{_HTML_NAMESPACE}}}title",
    }


def _first_visible_html_title(root: Element) -> str | None:
    for element in root:
        if not _is_html_element(element, "head"):
            continue
        if _element_is_hidden(element):
            return None
        for child in element:
            if _is_html_title(child) and not _element_is_hidden(child):
                return child.text or ""
        return None
    return None


def _is_html_element(element: Element, name: str) -> bool:
    return isinstance(element.tag, str) and element.tag in {
        name,
        f"{{{_HTML_NAMESPACE}}}{name}",
    }


def _element_is_hidden(element: Element) -> bool:
    attributes = {
        _tag_name_from_value(name): value for name, value in element.attrib.items()
    }
    return bool(
        "hidden" in attributes
        or (attributes.get("aria-hidden") or "").strip().casefold() == "true"
    )


def _tag_name_from_value(value: str) -> str:
    return value.rsplit("}", 1)[-1].casefold()


def _collapse(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def _parse_content_type(value: str) -> tuple[str, str | None]:
    media_type = value.split(";", 1)[0].strip().lower()
    match = _CHARSET.search(value)
    charset = match.group(1).lower() if match else None
    return media_type, charset


def _unsupported() -> ToolError:
    return ToolError(
        "web_content_unsupported", "This web content type is not supported."
    )


def _fetch_failed() -> ToolError:
    return ToolError("web_fetch_failed", "The web page could not be read.")
