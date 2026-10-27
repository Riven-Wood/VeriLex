import asyncio

import httpx

from verilex_runtime.providers.errors import ProviderDomainError, provider_error
from verilex_runtime.providers.http_limits import (
    ProviderResponseTooLarge,
    install_provider_response_limit,
)
from verilex_runtime.providers.models import (
    DiscoveredContextWindow,
    ProviderDraft,
    ProbeResult,
)


_PROBE_TIMEOUT = httpx.Timeout(20.0, connect=5.0)


class OllamaProbe:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client
        install_provider_response_limit(http_client)

    async def probe(
        self,
        draft: ProviderDraft,
        api_key: str | None,
    ) -> ProbeResult:
        del api_key
        root = draft.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]

        try:
            response = await self._http_client.get(
                f"{root}/api/tags",
                timeout=_PROBE_TIMEOUT,
            )
            self._raise_for_status(response)
            models = self._parse_models(response)
        except ProviderDomainError:
            raise
        except httpx.TimeoutException:
            raise provider_error("provider_timeout") from None
        except httpx.RequestError:
            raise provider_error("provider_unreachable") from None
        except ProviderResponseTooLarge:
            raise provider_error("provider_protocol_error") from None
        except Exception:
            raise provider_error("provider_protocol_error") from None

        if not models:
            raise provider_error("provider_has_no_models")
        selected_model = draft.default_model or models[0]
        if selected_model not in models:
            raise provider_error("provider_model_not_found")
        context_windows = await self._read_context_windows(root, models)
        return ProbeResult(
            models=models,
            selected_model=selected_model,
            context_windows=context_windows,
        )

    async def _read_context_windows(
        self, root: str, models: tuple[str, ...]
    ) -> tuple[DiscoveredContextWindow, ...]:
        semaphore = asyncio.Semaphore(4)

        async def read(model: str) -> DiscoveredContextWindow | None:
            async with semaphore:
                try:
                    response = await self._http_client.post(
                        f"{root}/api/show",
                        json={"model": model},
                        timeout=_PROBE_TIMEOUT,
                    )
                    self._raise_for_status(response)
                    tokens = self._parse_context_window(response)
                except (
                    ProviderDomainError,
                    ProviderResponseTooLarge,
                    httpx.HTTPError,
                    ValueError,
                    TypeError,
                ):
                    return None
                if tokens is None:
                    return None
                return DiscoveredContextWindow(model, tokens, "provider")

        results = await asyncio.gather(*(read(model) for model in models))
        return tuple(result for result in results if result is not None)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code in (401, 403):
            raise provider_error("provider_auth_failed")
        if response.status_code == 429:
            raise provider_error("provider_rate_limited")
        if not 200 <= response.status_code < 300:
            raise provider_error("provider_protocol_error")

    @staticmethod
    def _parse_models(response: httpx.Response) -> tuple[str, ...]:
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(
            payload.get("models"), list
        ):
            raise provider_error("provider_protocol_error")

        names: list[str] = []
        for model in payload["models"]:
            if not isinstance(model, dict):
                raise provider_error("provider_protocol_error")
            name = model.get("name")
            if not isinstance(name, str) or not name.strip():
                raise provider_error("provider_protocol_error")
            names.append(name)
        return tuple(sorted(set(names)))

    @staticmethod
    def _parse_context_window(response: httpx.Response) -> int | None:
        payload = response.json()
        if not isinstance(payload, dict):
            return None
        model_info = payload.get("model_info")
        if not isinstance(model_info, dict):
            return None
        valid_values = {
            value
            for key, value in model_info.items()
            if isinstance(key, str)
            and key.endswith(".context_length")
            and not isinstance(value, bool)
            and isinstance(value, int)
            and 1 <= value <= 10_000_000
        }
        if len(valid_values) != 1:
            return None
        return next(iter(valid_values))
