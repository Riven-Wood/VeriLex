from __future__ import annotations


class DocumentDomainError(Exception):
    def __init__(self, code: str, detail: str | None = None):
        self.code = code
        # Optional model-facing explanation (e.g. which line tripped which
        # markdown rule). Never shown instead of the stable user copy.
        self.detail = detail
        super().__init__(code)
