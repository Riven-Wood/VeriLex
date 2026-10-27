"""Bundled office document runtime boundaries."""

from verilex_runtime.documents.comments import CommentSpec, CommentOutcome, write_verified_comments
from verilex_runtime.documents.replace import TextReplacement, ProcessOutcome, process_copy
from verilex_runtime.documents.errors import DocumentDomainError
from verilex_runtime.documents.v1_creator import V1ValidationReport, create_v1_docx
from verilex_runtime.documents.v1_markdown import MarkdownBlock, parse_v1_markdown
from verilex_runtime.documents.v1_service import V1CreateRequest, V1DocumentService


__all__ = [
    "DocumentDomainError",
    "CommentSpec",
    "CommentOutcome",
    "write_verified_comments",
    "TextReplacement",
    "ProcessOutcome",
    "process_copy",
    "MarkdownBlock",
    "V1ValidationReport",
    "V1CreateRequest",
    "V1DocumentService",
    "create_v1_docx",
    "parse_v1_markdown",
]
