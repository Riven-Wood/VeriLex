import base64
import hashlib
import logging
import os
import re
import stat
from collections.abc import AsyncIterator, Sequence

import anyio
import httpx
import openai

from verilex_runtime.attachments.errors import (
    AttachmentDomainError,
    attachment_error,
)
from verilex_runtime.providers.errors import ProviderDomainError, provider_error
from verilex_runtime.providers.http_limits import (
    MAX_PROVIDER_OUTPUT_BYTES,
    ProviderResponseTooLarge,
    caused_by_oversized_response,
    install_provider_response_limit,
)
from verilex_runtime.providers.models import (
    ChatDelta,
    ChatMessage,
    ChatRequestOptions,
    ChatUsage,
    DiscoveredContextWindow,
    ImagePart,
    ModelContent,
    ProviderDraft,
    ProbeResult,
    TextPart,
    ToolCallDelta,
)
from verilex_runtime.storage.models import ReasoningEffort


_PROBE_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_STREAM_TIMEOUT = httpx.Timeout(1800.0, connect=5.0)
_NO_KEY_PLACEHOLDER = "verilex-no-key"
logger = logging.getLogger(__name__)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
_CONTEXT_WINDOW_FIELDS = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_model_len",
)


def context_window_value(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not 1 <= value <= 10_000_000:
        return None
    return value


def _context_window_hint(model: object) -> DiscoveredContextWindow | None:
    model_id = getattr(model, "id", None)
    if not isinstance(model_id, str) or not model_id:
        return None
    extras = getattr(model, "model_extra", None)
    if not isinstance(extras, dict):
        return None

    for field in _CONTEXT_WINDOW_FIELDS:
        tokens = context_window_value(extras.get(field))
        if tokens is not None:
            return DiscoveredContextWindow(model_id, tokens, "provider")

    top_provider = extras.get("top_provider")
    if isinstance(top_provider, dict):
        tokens = context_window_value(top_provider.get("context_length"))
        if tokens is not None:
            return DiscoveredContextWindow(model_id, tokens, "provider")
    return None


def _secure_image_descriptor(part: ImagePart) -> int:
    expected = (
        part.expected_sha256,
        part.expected_size_bytes,
        part.storage_root,
    )
    if all(value is None for value in expected):
        nofollow = getattr(os, "O_NOFOLLOW", None)
        if nofollow is None:
            raise attachment_error("attachment_blob_missing")
        return os.open(
            part.blob_path,
            os.O_RDONLY
            | nofollow
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    if any(value is None for value in expected):
        raise attachment_error("attachment_blob_missing")

    assert part.expected_sha256 is not None
    assert part.expected_size_bytes is not None
    assert part.storage_root is not None
    if (
        re.fullmatch(r"[0-9a-f]{64}", part.expected_sha256) is None
        or isinstance(part.expected_size_bytes, bool)
        or not isinstance(part.expected_size_bytes, int)
        or not 0 <= part.expected_size_bytes <= _MAX_IMAGE_BYTES
    ):
        raise attachment_error("attachment_blob_missing")
    root = part.storage_root
    path = part.blob_path
    if not root.is_absolute() or not path.is_absolute() or ".." in root.parts:
        raise attachment_error("attachment_blob_missing")
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise attachment_error("attachment_blob_missing") from None
    if not relative.parts or ".." in relative.parts:
        raise attachment_error("attachment_blob_missing")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None or os.open not in os.supports_dir_fd:
        raise attachment_error("attachment_blob_missing")
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | nofollow
        | getattr(os, "O_CLOEXEC", 0)
    )
    current = os.open(
        os.sep,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        for component in root.parts[1:]:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=current,
            )
            os.close(current)
            current = next_descriptor
        for component in relative.parts[:-1]:
            next_descriptor = os.open(
                component,
                directory_flags,
                dir_fd=current,
            )
            os.close(current)
            current = next_descriptor
        return os.open(
            relative.parts[-1],
            os.O_RDONLY
            | nofollow
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=current,
        )
    finally:
        os.close(current)


class OpenAICompatibleProbe:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client
        install_provider_response_limit(http_client)

    async def probe(
        self,
        draft: ProviderDraft,
        api_key: str | None,
    ) -> ProbeResult:
        client = openai.AsyncOpenAI(
            api_key=api_key or _NO_KEY_PLACEHOLDER,
            base_url=draft.base_url,
            http_client=self._http_client,
            timeout=_PROBE_TIMEOUT,
            max_retries=0,
        )

        try:
            try:
                page = await client.models.list()
            except openai.APIStatusError as exc:
                if exc.status_code not in (404, 405):
                    raise
                if not draft.default_model:
                    raise provider_error("provider_model_not_found") from None
                return await self._probe_chat(client, draft)

            models = self._model_ids(page.data)
            if draft.default_model and draft.default_model not in models:
                raise provider_error("provider_model_not_found")
            selected = draft.default_model or (models[0] if models else "")
            context_windows = tuple(
                hint
                for model in page.data
                if (hint := _context_window_hint(model)) is not None
            )
            return ProbeResult(
                models=models,
                selected_model=selected,
                context_windows=context_windows,
            )
        except ProviderDomainError:
            raise
        except (openai.AuthenticationError, openai.PermissionDeniedError):
            raise provider_error("provider_auth_failed") from None
        except openai.RateLimitError:
            raise provider_error("provider_rate_limited") from None
        except openai.APITimeoutError:
            raise provider_error("provider_timeout") from None
        except openai.APIConnectionError as error:
            if caused_by_oversized_response(error):
                raise provider_error("provider_protocol_error") from None
            raise provider_error("provider_unreachable") from None
        except Exception:
            raise provider_error("provider_protocol_error") from None

    @staticmethod
    def _model_ids(data: object) -> tuple[str, ...]:
        if not isinstance(data, list):
            raise provider_error("provider_protocol_error")

        model_ids: list[str] = []
        for model in data:
            model_id = getattr(model, "id", None)
            if not isinstance(model_id, str) or not model_id:
                raise provider_error("provider_protocol_error")
            model_ids.append(model_id)
        return tuple(model_ids)

    @staticmethod
    async def _probe_chat(
        client: openai.AsyncOpenAI,
        draft: ProviderDraft,
    ) -> ProbeResult:
        response = await client.chat.completions.create(
            model=draft.default_model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            max_tokens=1,
            stream=False,
        )
        if not response.choices:
            raise provider_error("provider_protocol_error")
        return ProbeResult(
            models=(draft.default_model,),
            selected_model=draft.default_model,
        )


def _reasoning_delta(delta: object) -> str | None:
    for attribute in ("reasoning_content", "reasoning", "thinking"):
        value = getattr(delta, attribute, None)
        if isinstance(value, str) and value:
            return value
    return None


def _read_image(part: ImagePart) -> bytearray:
    descriptor: int | None = None
    try:
        descriptor = _secure_image_descriptor(part)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise attachment_error("attachment_blob_missing")
        if metadata.st_size > _MAX_IMAGE_BYTES:
            raise attachment_error("attachment_blob_missing")
        if (
            part.expected_size_bytes is not None
            and metadata.st_size != part.expected_size_bytes
        ):
            raise attachment_error("attachment_blob_missing")

        contents = bytearray()
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, _MAX_IMAGE_BYTES + 1 - total),
            )
            if not chunk:
                break
            contents.extend(chunk)
            digest.update(chunk)
            total += len(chunk)
            if total > _MAX_IMAGE_BYTES:
                raise attachment_error("attachment_blob_missing")

        final_metadata = os.fstat(descriptor)
        if (
            final_metadata.st_dev,
            final_metadata.st_ino,
            final_metadata.st_mode,
            final_metadata.st_size,
            final_metadata.st_mtime_ns,
            final_metadata.st_ctime_ns,
        ) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ):
            raise attachment_error("attachment_blob_missing")
        if (
            part.expected_size_bytes is not None
            and total != part.expected_size_bytes
        ) or (
            part.expected_sha256 is not None
            and digest.hexdigest() != part.expected_sha256
        ):
            raise attachment_error("attachment_blob_missing")
        return contents
    except AttachmentDomainError:
        raise
    except (OSError, ValueError):
        raise attachment_error("attachment_blob_missing") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _openai_content(content: ModelContent) -> str | list[dict]:
    if isinstance(content, str):
        return content

    mapped: list[dict] = []
    for part in content:
        if isinstance(part, TextPart):
            mapped.append({"type": "text", "text": part.text})
            continue
        if not isinstance(part, ImagePart):
            raise attachment_error("attachment_media_type_mismatch")
        if part.media_type not in _IMAGE_MEDIA_TYPES:
            raise attachment_error("attachment_media_type_mismatch")
        image_bytes = _read_image(part)
        encoded_bytes = base64.b64encode(image_bytes)
        del image_bytes
        encoded = encoded_bytes.decode("ascii")
        del encoded_bytes
        url = f"data:{part.media_type};base64,{encoded}"
        del encoded
        mapped.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": url,
                },
            }
        )
    return mapped


def _to_openai_message(message: ChatMessage) -> dict:
    payload: dict = {"role": message.role}
    if message.content is not None:
        payload["content"] = _openai_content(message.content)
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    return payload


def _to_openai_messages(messages: Sequence[ChatMessage]) -> list[dict]:
    return [_to_openai_message(message) for message in messages]


def _error_detail(error: openai.APIStatusError, field: str) -> object:
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return None
    detail = body.get("error", body)
    return detail.get(field) if isinstance(detail, dict) else None


def _unsupported_parameter(error: openai.APIStatusError, parameter: str) -> bool:
    param = _error_detail(error, "param")
    code = _error_detail(error, "code")
    if param == parameter and code in {
        "unsupported_parameter",
        "unknown_parameter",
        "invalid_parameter",
    }:
        return True
    message = _error_detail(error, "message")
    if not isinstance(message, str) or len(message) > 1000:
        return False
    lowered = message.lower()
    return parameter in lowered and (
        "unsupported parameter" in lowered or "unknown parameter" in lowered
    )


def _vision_rejection(error: openai.APIStatusError) -> bool:
    """True when the provider explicitly refused image input.

    Matches messages like "This model does not support image" (DeepSeek) or
    "image input is not supported" (other OpenAI-compatible providers). Only
    this specific failure is treated as a vision rejection so unrelated 400s
    are never mis-recorded as an image-capability verdict.
    """
    message = _error_detail(error, "message")
    if not isinstance(message, str) or len(message) > 1000:
        return False
    lowered = message.lower()
    if "image" not in lowered and "vision" not in lowered:
        return False
    return (
        "does not support image" in lowered
        or "does not support vision" in lowered
        or "not support image" in lowered
        or "doesn't support image" in lowered
        or "image is not supported" in lowered
        or "image input is not supported" in lowered
        or "does not accept image" in lowered
        or "modality" in lowered and "image" in lowered
    )


def _usage_value(value: object, attribute: str, default: int = 0) -> int:
    if value is None:
        return default
    result = value.get(attribute) if isinstance(value, dict) else getattr(value, attribute, None)
    if result is None:
        return default
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise ValueError("invalid provider usage")
    return result


def _normalized_usage(value: object) -> ChatUsage:
    prompt_details = (
        value.get("prompt_tokens_details")
        if isinstance(value, dict)
        else getattr(value, "prompt_tokens_details", None)
    )
    completion_details = (
        value.get("completion_tokens_details")
        if isinstance(value, dict)
        else getattr(value, "completion_tokens_details", None)
    )
    return ChatUsage(
        input_tokens=_usage_value(value, "prompt_tokens"),
        cached_input_tokens=_usage_value(prompt_details, "cached_tokens"),
        output_tokens=_usage_value(value, "completion_tokens"),
        reasoning_output_tokens=_usage_value(completion_details, "reasoning_tokens"),
    )


class OpenAICompatibleChatStream:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        base_url: str,
        api_key: str | None,
        model: str,
    ) -> None:
        install_provider_response_limit(http_client)
        self._client = openai.AsyncOpenAI(
            api_key=api_key or _NO_KEY_PLACEHOLDER,
            base_url=base_url,
            http_client=http_client,
            timeout=_STREAM_TIMEOUT,
            max_retries=0,
        )
        self._model = model

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[dict] | None = None,
        options: ChatRequestOptions | None = None,
    ) -> AsyncIterator[ChatDelta]:
        emitted = False
        output_bytes = 0
        response = None
        request_options = options or ChatRequestOptions()
        try:
            message_snapshot = tuple(messages)
            provider_messages = await anyio.to_thread.run_sync(
                _to_openai_messages, message_snapshot
            )
            request: dict = {
                "model": self._model,
                "messages": provider_messages,
                "stream": True,
                "tools": list(tools) if tools else None,
                "stream_options": {"include_usage": True},
            }
            if request_options.reasoning_effort is not ReasoningEffort.AUTO:
                request["reasoning_effort"] = request_options.reasoning_effort.value
            try:
                response = await self._client.chat.completions.create(**request)
            except openai.BadRequestError as error:
                if not _unsupported_parameter(error, "stream_options"):
                    raise
                request.pop("stream_options")
                response = await self._client.chat.completions.create(**request)
            tool_calls: dict[int, dict] = {}
            async for chunk in response:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    emitted = True
                    yield ChatDelta(kind="usage", usage=_normalized_usage(usage))
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                delta_tool_calls = getattr(delta, "tool_calls", None)
                # Include reasoning and function arguments, even while a tool
                # call is incomplete and has not yet been yielded to the Agent.
                fields = [getattr(delta, "content", None), _reasoning_delta(delta)]
                for call in delta_tool_calls or ():
                    function = getattr(call, "function", None)
                    fields.extend((getattr(call, "id", None),
                                   getattr(function, "name", None),
                                   getattr(function, "arguments", None)))
                output_bytes += sum(len(value.encode("utf-8")) for value in fields if isinstance(value, str))
                if output_bytes > MAX_PROVIDER_OUTPUT_BYTES:
                    raise ProviderResponseTooLarge("decoded provider output exceeded size limit")
                if delta_tool_calls:
                    for call in delta_tool_calls:
                        index = getattr(call, "index", 0)
                        entry = tool_calls.setdefault(
                            index, {"id": "", "name": "", "parts": []}
                        )
                        call_id = getattr(call, "id", None)
                        if call_id:
                            entry["id"] = call_id
                        function = getattr(call, "function", None)
                        if function is not None:
                            name = getattr(function, "name", None)
                            if name:
                                entry["name"] = name
                            arguments = getattr(function, "arguments", None)
                            if arguments:
                                entry["parts"].append(arguments)
                    continue
                reasoning = _reasoning_delta(delta)
                if reasoning:
                    emitted = True
                    yield ChatDelta(kind="reasoning", text=reasoning)
                content = getattr(delta, "content", None)
                if isinstance(content, str) and content:
                    emitted = True
                    yield ChatDelta(kind="text", text=content)

            for index in sorted(tool_calls):
                entry = tool_calls[index]
                emitted = True
                yield ChatDelta(
                    kind="tool_call",
                    tool_call=ToolCallDelta(
                        id=entry["id"],
                        name=entry["name"],
                        arguments="".join(entry["parts"]),
                    ),
                )
        except (ProviderDomainError, AttachmentDomainError):
            raise
        except openai.BadRequestError as error:
            if _error_detail(error, "code") in {"context_length_exceeded", "max_context_length_exceeded", "context_window_exceeded"}:
                raise provider_error("provider_context_exceeded") from None
            if (
                not emitted
                and request_options.reasoning_effort is not ReasoningEffort.AUTO
                and _unsupported_parameter(error, "reasoning_effort")
            ):
                raise provider_error("provider_reasoning_effort_unsupported") from None
            if _vision_rejection(error):
                raise provider_error("provider_vision_unsupported") from None
            logger.warning("provider stream rejected request: %r", error)
            raise provider_error("provider_protocol_error") from None
        except (openai.AuthenticationError, openai.PermissionDeniedError):
            raise provider_error("provider_auth_failed") from None
        except openai.RateLimitError:
            raise provider_error("provider_rate_limited") from None
        except openai.APITimeoutError as error:
            logger.warning("provider stream timed out: %r", error)
            raise provider_error("provider_timeout") from None
        except openai.APIConnectionError as error:
            if caused_by_oversized_response(error):
                logger.warning("provider stream failed (oversized response)")
                raise provider_error("provider_response_too_large"
                    if caused_by_oversized_response(error, actual_size_only=True)
                    else "provider_protocol_error") from None
            logger.warning("provider connection error: %r", error)
            raise provider_error(
                "provider_stream_interrupted" if emitted else "provider_unreachable"
            ) from None
        except httpx.TransportError as error:
            # Mid-body transport failures escape the SDK unwrapped; they are
            # transient network drops, not incompatible responses.
            logger.warning("provider stream transport error: %r", error)
            raise provider_error(
                "provider_stream_interrupted" if emitted else "provider_unreachable"
            ) from None
        except ProviderResponseTooLarge as error:
            logger.warning("provider stream exceeded safe response limit")
            raise provider_error("provider_response_too_large"
                if error.size_limit else "provider_protocol_error") from None
        except Exception as error:
            logger.warning("provider stream failed unexpectedly: %r", error)
            raise provider_error("provider_protocol_error") from None
        finally:
            if response is not None:
                await response.close()
