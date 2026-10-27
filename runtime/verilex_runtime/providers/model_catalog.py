from __future__ import annotations

from dataclasses import dataclass

from verilex_runtime.storage.models import ProviderKind


@dataclass(frozen=True, slots=True)
class CuratedContextWindow:
    tokens: int
    source_url: str
    verified_on: str


_VERIFIED_ON = "2026-08-24"
_BIGMODEL_API = "https://open.bigmodel.cn/api/paas/v4"
_BIGMODEL_CODING_API = "https://open.bigmodel.cn/api/coding/paas/v4"


def _official(tokens: int, model_page: str) -> CuratedContextWindow:
    return CuratedContextWindow(
        tokens=tokens,
        source_url=f"https://docs.bigmodel.cn/cn/guide/models/text/{model_page}",
        verified_on=_VERIFIED_ON,
    )


# This is deliberately a small exact table. It contains no model-family
# matching and is never populated from third-party catalogs at runtime.
_CATALOG: dict[tuple[ProviderKind, str, str], CuratedContextWindow] = {
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-5.2"): _official(
        1_000_000, "glm-5.2"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-5.1"): _official(
        200_000, "glm-5.1"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-5"): _official(
        200_000, "glm-5"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-5-turbo"): _official(
        200_000, "glm-5-turbo"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-4.7"): _official(
        200_000, "glm-4.7"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-4.6"): _official(
        200_000, "glm-4.6"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-4.5-air"): _official(
        128_000, "glm-4.5"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-4.5-airx"): _official(
        128_000, "glm-4.5"
    ),
    (ProviderKind.OPENAI_COMPATIBLE, _BIGMODEL_API, "glm-4-long"): _official(
        1_000_000, "glm-4-long"
    ),
    (
        ProviderKind.OPENAI_COMPATIBLE,
        _BIGMODEL_CODING_API,
        "glm-5.2",
    ): CuratedContextWindow(
        tokens=1_000_000,
        source_url="https://docs.bigmodel.cn/cn/guide/develop/cline",
        verified_on=_VERIFIED_ON,
    ),
}


def curated_context_window(
    kind: ProviderKind, base_url: str, model: str
) -> CuratedContextWindow | None:
    if not isinstance(base_url, str) or not isinstance(model, str):
        return None
    return _CATALOG.get((kind, base_url.rstrip("/"), model))
