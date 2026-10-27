import pytest

from verilex_runtime.agent.events import (
    attachment_user_content,
    user_message_parts,
)
from verilex_runtime.attachments.errors import AttachmentDomainError


def _blob_missing(content):
    with pytest.raises(AttachmentDomainError) as captured:
        user_message_parts(content)
    assert captured.value.code == "attachment_blob_missing"


def test_user_message_parts_keeps_legacy_string_and_block_list_compatible():
    assert user_message_parts("plain") == ("plain", ())
    assert user_message_parts(
        [
            {"type": "text", "text": "Hello "},
            {"type": "reasoning", "text": "private"},
            {"type": "text", "text": "world"},
        ]
    ) == ("Hello world", ())


def test_attachment_user_content_round_trips_text_and_ordered_ids():
    content = attachment_user_content("Compare", ("att-2", "att-1"))

    assert content == {
        "version": 1,
        "text": "Compare",
        "attachmentIds": ["att-2", "att-1"],
    }
    assert user_message_parts(content) == ("Compare", ("att-2", "att-1"))


@pytest.mark.parametrize(
    "content",
    [
        None,
        {},
        {"version": True, "text": "x", "attachmentIds": ["att-1"]},
        {"version": 1.0, "text": "x", "attachmentIds": ["att-1"]},
        {"version": 2, "text": "x", "attachmentIds": ["att-1"]},
        {"version": 1, "text": 3, "attachmentIds": ["att-1"]},
        {"version": 1, "text": "x", "attachmentIds": "att-1"},
        {"version": 1, "text": "x", "attachmentIds": [1]},
        {"version": 1, "text": "x", "attachmentIds": [""]},
        {"version": 1, "text": "x", "attachmentIds": ["att-1", "att-1"]},
        {"version": 1, "text": "x", "attachmentIds": []},
        {"version": 1, "text": "x", "attachmentIds": [], "extra": True},
    ],
)
def test_user_message_parts_rejects_malformed_structured_content(content):
    _blob_missing(content)


@pytest.mark.parametrize(
    "ids",
    [(), ("",), ("   ",), ("att-1", "att-1"), ("att-1", 3)],
)
def test_attachment_user_content_rejects_invalid_or_duplicate_ids(ids):
    with pytest.raises(AttachmentDomainError) as captured:
        attachment_user_content("x", ids)
    assert captured.value.code == "attachment_blob_missing"


def test_structured_content_accepts_empty_body_when_an_attachment_exists():
    content = attachment_user_content("", ("att-1",))
    assert user_message_parts(content) == ("", ("att-1",))
