from __future__ import annotations

import builtins
import socket

import html5lib
import pytest

from verilex_runtime.tools.registry import ToolError
from verilex_runtime.web.client import FetchedPage
from verilex_runtime.web.extract import ExtractedPage, extract_web_text


def fetched_page(
    content_type: str,
    body: str | bytes,
    *,
    final_url: str = "https://example.test/case",
) -> FetchedPage:
    return FetchedPage(
        final_url=final_url,
        content_type=content_type,
        body=body.encode("utf-8") if isinstance(body, str) else body,
        fetched_at="2026-08-27T00:00:00+00:00",
    )


def test_html_extracts_title_and_visible_text_only() -> None:
    page = fetched_page(
        "text/html; charset=UTF-8",
        "<html><head><title> 判决 &amp; 裁定 </title><style>x</style></head>"
        "<body><script>ignore()</script><noscript>fallback</noscript>"
        "<svg><text>vector</text></svg><main>可见 <b>正文</b></main></body></html>",
    )

    extracted = extract_web_text(page)

    assert extracted == ExtractedPage(
        title="判决 & 裁定",
        text="可见 正文",
        character_count=5,
        truncated=False,
    )


def test_html_collapses_whitespace_and_ignores_hidden_elements() -> None:
    page = fetched_page(
        "text/html",
        "<main>  第一段\n\n <span hidden>隐藏</span>"
        "<div aria-hidden='true'>也隐藏</div><p>第二段</p></main>",
    )

    extracted = extract_web_text(page)

    assert extracted.text == "第一段 第二段"


def test_template_content_is_not_visible_text() -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<main>Visible<template><p>INVISIBLE</p></template>Text</main>",
        )
    )

    assert extracted.text == "Visible Text"


@pytest.mark.parametrize(
    "self_closing_hidden",
    ["<div hidden/>", "<template/>", "<script/>"],
)
def test_non_void_self_closing_syntax_keeps_following_text_hidden(
    self_closing_hidden: str,
) -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            f"<main>Visible {self_closing_hidden}INVISIBLE</main>",
        )
    )

    assert extracted.text == "Visible"


def test_void_self_closing_br_keeps_a_visible_word_boundary() -> None:
    extracted = extract_web_text(
        fetched_page("text/html", "<main>First<br/>Second</main>")
    )

    assert extracted.text == "First Second"


@pytest.mark.parametrize("attribute", ["hidden", "aria-hidden='true'"])
def test_malformed_end_tag_does_not_escape_hidden_subtree(attribute: str) -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            f"<main>Visible<div {attribute}></span>INVISIBLE</div>Text</main>",
        )
    )

    assert extracted.text == "Visible Text"


@pytest.mark.parametrize(
    "html",
    [
        "<main>Visible<form><div hidden></form>INVISIBLE</div>Text</main>",
        "<main>Visible<b><div hidden></b>INVISIBLE</div>Text</main>",
    ],
)
def test_html5_misnested_end_tag_does_not_escape_hidden_subtree(html: str) -> None:
    extracted = extract_web_text(fetched_page("text/html", html))

    assert extracted.text == "Visible Text"


@pytest.mark.parametrize(
    "comment",
    [
        "<!-- Verify you are human with CAPTCHA -->",
        "<!Verify you are human with CAPTCHA>",
    ],
)
def test_comment_content_is_hidden_without_losing_its_tail(comment: str) -> None:
    extracted = extract_web_text(
        fetched_page("text/html", f"<main>Visible{comment}Tail</main>")
    )

    assert extracted.text == "VisibleTail"


def test_foreign_namespace_title_is_not_used_as_the_page_title() -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<svg><title>SVG diagram</title></svg>"
            "<math><title>Math formula</title></math><main>Case text</main>",
        )
    )

    assert extracted.title == "example.test"
    assert extracted.text == "Case text"


@pytest.mark.parametrize(
    "ignored_title",
    [
        "<title hidden>Verify you are human</title>",
        "<title aria-hidden='true'>Verify you are human</title>",
        "<title hidden></title>",
        "<noscript><title>Verify you are human</title></noscript>",
    ],
)
def test_ignored_head_title_does_not_block_the_first_valid_title(
    ignored_title: str,
) -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            f"<head>{ignored_title}<title>Case title</title></head>"
            "<body><main>Case text</main></body>",
        )
    )

    assert extracted.title == "Case title"
    assert extracted.text == "Case text"


def test_template_title_is_not_a_page_title_or_challenge_signal() -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<body><template><title>Verify you are human</title></template>"
            "<main>Case text</main></body>",
        )
    )

    assert extracted.title == "example.test"
    assert extracted.text == "Case text"


def test_html_title_inside_svg_foreign_object_is_not_a_page_title() -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<body><svg><foreignObject><title>Verify you are human</title>"
            "</foreignObject></svg><main>Case text</main></body>",
        )
    )

    assert extracted.title == "example.test"
    assert extracted.text == "Case text"


def test_html_parser_never_loads_external_or_local_resources(monkeypatch) -> None:
    def fail_resource_access(*_args, **_kwargs):
        raise AssertionError("HTML parsing attempted external resource access")

    monkeypatch.setattr(socket, "socket", fail_resource_access)
    monkeypatch.setattr(builtins, "open", fail_resource_access)

    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<!DOCTYPE html SYSTEM 'file:///private/etc/passwd'>"
            "<link rel='stylesheet' href='https://example.invalid/style.css'>"
            "<script src='https://example.invalid/app.js'></script>"
            "<main>Case text<img src='file:///private/etc/passwd'>"
            "<iframe src='https://example.invalid/frame'></iframe></main>",
        )
    )

    assert extracted.text == "Case text"


def test_excessively_deep_html_fails_with_a_stable_tool_error() -> None:
    html = "<main>" + "<div>" * 1_200 + "Text" + "</div>" * 1_200 + "</main>"

    with pytest.raises(ToolError) as captured:
        extract_web_text(fetched_page("text/html", html))

    assert captured.value.code == "web_fetch_failed"
    assert str(captured.value) == "The web page could not be read."


def test_html_node_flood_fails_with_a_stable_tool_error() -> None:
    html = "<main>" + "<span>x</span>" * 20_001 + "</main>"

    with pytest.raises(ToolError) as captured:
        extract_web_text(fetched_page("text/html", html))

    assert captured.value.code == "web_fetch_failed"
    assert str(captured.value) == "The web page could not be read."


def test_adoption_agency_clones_consume_the_build_time_node_budget(
    monkeypatch,
) -> None:
    base_element = html5lib.getTreeBuilder("etree").elementClass
    original_init = base_element.__init__
    constructed_elements = 0

    def counting_init(element, *args, **kwargs):
        nonlocal constructed_elements
        constructed_elements += 1
        original_init(element, *args, **kwargs)

    monkeypatch.setattr(base_element, "__init__", counting_init)
    # Adoption-agency recovery creates six real elements per malformed group,
    # while the old insertion-hook budget observed only three. This therefore
    # crosses 20,000 actual elements after roughly 10,000 observed insertions.
    html = "<main>" + "<b><i><div></b></i></div>" * 3_334 + "</main>"

    with pytest.raises(ToolError) as captured:
        extract_web_text(fetched_page("text/html", html))

    assert captured.value.code == "web_fetch_failed"
    assert constructed_elements <= 20_000


def test_html_larger_than_response_limit_fails_before_parsing() -> None:
    body = b"x" * (2 * 1024 * 1024 + 1)

    with pytest.raises(ToolError) as captured:
        extract_web_text(fetched_page("text/html", body))

    assert captured.value.code == "web_fetch_failed"


@pytest.mark.parametrize(
    ("attributes", "expected_text"),
    [
        ("aria-hidden='true' aria-hidden='false'", "Visible Text"),
        ("aria-hidden='false' aria-hidden='true'", "Visible Kept Text"),
    ],
)
def test_duplicate_aria_hidden_uses_first_attribute(
    attributes: str,
    expected_text: str,
) -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            f"<main>Visible<div {attributes}>Kept</div>Text</main>",
        )
    )

    assert extracted.text == expected_text





def test_duplicate_input_type_uses_first_text_attribute() -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<main>Public<input type='text' type='password'></main>",
        )
    )

    assert extracted.text == "Public"





def test_duplicate_class_uses_first_article_attribute() -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<main>Public<div class='article' class='h-captcha'>"
            "Ordinary discussion</div></main>",
        )
    )

    assert extracted.text == "Public Ordinary discussion"


def test_adjacent_block_elements_keep_a_word_boundary() -> None:
    extracted = extract_web_text(
        fetched_page("text/html", "<main><p>第一段</p><p>第二段</p></main>")
    )

    assert extracted.text == "第一段 第二段"


def test_plain_utf8_text_is_normalized() -> None:
    page = fetched_page("text/plain; charset=utf-8", " 第一行\r\n\r\n第二行 ")

    extracted = extract_web_text(page)

    assert extracted.title == "example.test"
    assert extracted.text == "第一行 第二行"
    assert extracted.character_count == 7
    assert extracted.truncated is False


def test_utf8_byte_order_mark_is_not_returned_as_content() -> None:
    extracted = extract_web_text(
        fetched_page("text/plain; charset=utf-8", b"\xef\xbb\xbfcontent")
    )

    assert extracted.text == "content"


@pytest.mark.parametrize(
    "content_type",
    [
        "application/pdf",
        "image/png",
        "application/zip",
        "application/octet-stream",
        "text/css; charset=utf-8",
        "text/html; charset=gb18030",
        "text/plain; charset=iso-8859-1",
    ],
)
def test_non_html_or_non_utf8_plain_content_is_unsupported(
    content_type: str,
) -> None:
    with pytest.raises(ToolError) as captured:
        extract_web_text(fetched_page(content_type, b"content"))

    assert captured.value.code == "web_content_unsupported"


@pytest.mark.parametrize("body", [b"\xff\xfe", b"valid\x00binary"])
def test_invalid_utf8_or_nul_bytes_are_unsupported(body: bytes) -> None:
    with pytest.raises(ToolError) as captured:
        extract_web_text(fetched_page("text/plain", body))

    assert captured.value.code == "web_content_unsupported"


def test_empty_or_script_only_html_is_not_a_successful_source() -> None:
    with pytest.raises(ToolError) as captured:
        extract_web_text(
            fetched_page("text/html", "<html><script>only()</script></html>")
        )

    assert captured.value.code == "web_fetch_failed"

















def test_page_text_is_not_rejected_for_discussing_human_verification():
    text = 'Verify you are human to continue. This article explains verification systems.'
    page = fetched_page('text/html', f'<title>Verification</title><main>{text}</main>')
    assert extract_web_text(page).text == text


@pytest.mark.parametrize("widget", [
    "<aside><input type='password'></aside>",
    "<form><div class='h-captcha'>Complete verification</div></form>",
    "<section title='captcha research'>Related topic</section>",
])
def test_public_body_is_retained_despite_login_or_challenge_widgets(widget):
    extracted = extract_web_text(fetched_page("text/html", "<article>Complete public article</article>" + widget))
    assert "Complete public article" in extracted.text


def test_login_only_page_returns_its_actual_text_without_claiming_article_access():
    extracted = extract_web_text(fetched_page("text/html", "<title>Login</title><form><input type='password'>Sign in</form>"))
    assert extracted.title == "Login"
    assert extracted.text == "Sign in"



def test_article_discussing_login_and_captcha_is_not_falsely_blocked() -> None:
    extracted = extract_web_text(
        fetched_page(
            "text/html",
            "<title>Authentication terminology</title><article>"
            "This article compares login design and CAPTCHA challenge usability."
            " It does not contain an account form.</article>",
        )
    )

    assert "login design" in extracted.text


def test_visible_text_is_truncated_at_twenty_thousand_characters() -> None:
    extracted = extract_web_text(
        fetched_page("text/html", f"<title>Long</title><main>{'甲' * 20_001}</main>")
    )

    assert extracted.title == "Long"
    assert extracted.text == "甲" * 20_000
    assert extracted.character_count == 20_000
    assert extracted.truncated is True


def test_exact_character_limit_is_not_marked_truncated() -> None:
    extracted = extract_web_text(
        fetched_page("text/plain", "甲" * 20_000)
    )

    assert extracted.character_count == 20_000
    assert extracted.truncated is False
