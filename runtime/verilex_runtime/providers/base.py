from collections.abc import AsyncIterator, Sequence
from typing import Protocol

from verilex_runtime.providers.models import (
    ChatDelta,
    ChatMessage,
    ChatRequestOptions,
    ProviderDraft,
    ProbeResult,
)


class ProviderProbe(Protocol):
    async def probe(
        self,
        draft: ProviderDraft,
        api_key: str | None,
    ) -> ProbeResult:
        raise NotImplementedError


class ChatStream(Protocol):
    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict] | None = None,
        options: ChatRequestOptions | None = None,
    ) -> AsyncIterator[ChatDelta]:
        raise NotImplementedError
