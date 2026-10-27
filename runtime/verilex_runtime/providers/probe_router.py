from verilex_runtime.providers.base import ProviderProbe
from verilex_runtime.providers.models import ProviderDraft, ProbeResult
from verilex_runtime.storage.models import ProviderKind


class ProviderProbeRouter:
    def __init__(
        self,
        openai_probe: ProviderProbe,
        ollama_probe: ProviderProbe,
    ) -> None:
        self._openai = openai_probe
        self._ollama = ollama_probe

    async def probe(
        self,
        draft: ProviderDraft,
        api_key: str | None,
    ) -> ProbeResult:
        target = self._ollama if draft.kind is ProviderKind.OLLAMA else self._openai
        return await target.probe(draft, api_key)
