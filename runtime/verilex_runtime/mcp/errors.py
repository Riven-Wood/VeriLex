from __future__ import annotations

from dataclasses import FrozenInstanceError


class MCPDomainError(Exception):
    __slots__ = ("_code", "_retryable")

    def __init__(self, code: str, *, retryable: bool = False):
        super().__init__(code)
        object.__setattr__(self, "_code", code)
        object.__setattr__(self, "_retryable", retryable)

    @property
    def code(self) -> str:
        return self._code

    @property
    def retryable(self) -> bool:
        return self._retryable

    def __setattr__(self, name: str, value: object) -> None:
        if name in {"code", "retryable", "_code", "_retryable"}:
            raise FrozenInstanceError(f"cannot assign to field {name!r}")
        super().__setattr__(name, value)
