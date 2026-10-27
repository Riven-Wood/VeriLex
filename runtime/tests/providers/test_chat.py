import gc
import hashlib
import json
import os
import threading
import tracemalloc
import weakref
from base64 import b64encode
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import anyio
import httpx
import pytest

from verilex_runtime.attachments.errors import AttachmentDomainError
from verilex_runtime.providers import openai_compatible as openai_compatible_module
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import (
    ChatRequestOptions,
    ChatUsage,
    ChatMessage,
    ImagePart,
    TextPart,
    ToolCallMessage,
)
from verilex_runtime.providers.openai_compatible import (
    OpenAICompatibleChatStream,
    _reasoning_delta,
    _to_openai_message,
)
from verilex_runtime.container import _chat_base_url
from verilex_runtime.storage.models import (
    AuthMode,
    ProviderKind,
    ProviderRecord,
    ReasoningEffort,
)


def record(kind: ProviderKind = ProviderKind.OPENAI_COMPATIBLE) -> ProviderRecord:
    return ProviderRecord(
        id="provider-1",
        name="Compat",
        kind=kind,
        base_url="https://models.example.test/v1",
        default_model="model-1",
        auth_mode=AuthMode.API_KEY,
        secret_ref="provider:provider-1",
        enabled=True,
        created_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:00:00Z",
    )


def _chunk(delta: dict) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "model-1",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }


def _sse_body(chunks: list[dict]) -> str:
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}")
        lines.append("")
    lines.append("data: [DONE]")
    lines.append("")
    return "\n".join(lines)


def test_openai_message_preserves_legacy_string_none_and_tool_payloads():
    assert _to_openai_message(ChatMessage(role="user", content="hi")) == {
        "role": "user",
        "content": "hi",
    }
    assert _to_openai_message(
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=(ToolCallMessage("call-1", "read_file", '{"path":"a"}'),),
        )
    ) == {
        "role": "assistant",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"a"}'},
            }
        ],
    }
    assert _to_openai_message(
        ChatMessage(role="tool", content="result", tool_call_id="call-1")
    ) == {"role": "tool", "content": "result", "tool_call_id": "call-1"}
    assert _to_openai_message(ChatMessage(role="user", content="")) == {
        "role": "user",
        "content": "",
    }
    assert _to_openai_message(ChatMessage(role="user", content=())) == {
        "role": "user",
        "content": [],
    }
    assert _to_openai_message(
        ChatMessage(role="user", content=(TextPart(""),))
    ) == {"role": "user", "content": [{"type": "text", "text": ""}]}


def test_openai_message_maps_immutable_model_parts_in_order(tmp_path):
    binary = b"\x89PNG\r\n\x1a\n\x00\xfffixture"
    image = tmp_path / "private-image.png"
    image.write_bytes(binary)
    second_binary = b"\xff\xd8second-image\xff\xd9"
    second_image = tmp_path / "private-image.jpeg"
    second_image.write_bytes(second_binary)
    content = (
        TextPart("Read this image."),
        ImagePart("image/png", image),
        TextPart("Then summarize it."),
        ImagePart("image/jpeg", second_image),
    )
    message = ChatMessage(role="user", content=content)

    payload = _to_openai_message(message)

    assert payload == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Read this image."},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{b64encode(binary).decode('ascii')}"
                },
            },
            {"type": "text", "text": "Then summarize it."},
            {
                "type": "image_url",
                "image_url": {
                    "url": (
                        "data:image/jpeg;base64,"
                        f"{b64encode(second_binary).decode('ascii')}"
                    )
                },
            },
        ],
    }
    assert message.content is content
    assert b64encode(binary).decode("ascii") not in repr(message)
    assert str(image) not in repr(message)
    assert not hasattr(content[0], "__dict__")
    assert not hasattr(content[1], "__dict__")
    with pytest.raises(FrozenInstanceError):
        content[0].text = "changed"
    with pytest.raises(FrozenInstanceError):
        content[1].media_type = "image/jpeg"


def test_openai_message_secure_image_contract_verifies_root_size_and_sha(tmp_path):
    root = tmp_path / "attachments"
    image = root / "blobs" / "aa" / "image"
    image.parent.mkdir(parents=True)
    binary = b"original-image"
    image.write_bytes(binary)
    part = ImagePart(
        "image/png",
        image,
        expected_sha256=hashlib.sha256(binary).hexdigest(),
        expected_size_bytes=len(binary),
        storage_root=root,
    )
    assert str(root) not in repr(part)
    assert part.expected_sha256 not in repr(part)
    assert str(part.expected_size_bytes) not in repr(part)

    payload = _to_openai_message(ChatMessage(role="user", content=(part,)))

    assert payload["content"][0]["image_url"]["url"] == (
        f"data:image/png;base64,{b64encode(binary).decode('ascii')}"
    )


def test_openai_message_secure_image_contract_rejects_same_size_replacement(tmp_path):
    root = tmp_path / "attachments"
    image = root / "blobs" / "aa" / "image"
    image.parent.mkdir(parents=True)
    original = b"original-image"
    image.write_bytes(original)
    part = ImagePart(
        "image/png",
        image,
        expected_sha256=hashlib.sha256(original).hexdigest(),
        expected_size_bytes=len(original),
        storage_root=root,
    )
    image.write_bytes(b"replaced-image")
    assert image.stat().st_size == len(original)

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(ChatMessage(role="user", content=(part,)))

    assert captured.value.code == "attachment_blob_missing"


def test_openai_message_secure_image_contract_rejects_replaced_root_ancestor(
    tmp_path,
):
    base = tmp_path / "guarded"
    root = base / "attachments"
    image = root / "blobs" / "aa" / "image"
    image.parent.mkdir(parents=True)
    binary = b"original-image"
    image.write_bytes(binary)
    part = ImagePart(
        "image/png",
        image,
        expected_sha256=hashlib.sha256(binary).hexdigest(),
        expected_size_bytes=len(binary),
        storage_root=root,
    )

    outside = tmp_path / "outside"
    outside_image = outside / "attachments" / "blobs" / "aa" / "image"
    outside_image.parent.mkdir(parents=True)
    outside_image.write_bytes(binary)

    base.rename(tmp_path / "moved-away")
    base.symlink_to(outside, target_is_directory=True)

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(ChatMessage(role="user", content=(part,)))

    assert captured.value.code == "attachment_blob_missing"
    assert b64encode(binary).decode("ascii") not in str(captured.value)


@pytest.mark.parametrize("damage", ["outside-root", "symlink-component", "partial-contract"])
def test_openai_message_secure_image_contract_fails_closed_for_invalid_identity(
    tmp_path, damage
):
    root = tmp_path / "attachments"
    root.mkdir()
    binary = b"image-content"
    if damage == "outside-root":
        image = tmp_path / "outside.png"
        image.write_bytes(binary)
        kwargs = {
            "expected_sha256": hashlib.sha256(binary).hexdigest(),
            "expected_size_bytes": len(binary),
            "storage_root": root,
        }
    elif damage == "symlink-component":
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "image").write_bytes(binary)
        (root / "blobs").symlink_to(outside, target_is_directory=True)
        image = root / "blobs" / "image"
        kwargs = {
            "expected_sha256": hashlib.sha256(binary).hexdigest(),
            "expected_size_bytes": len(binary),
            "storage_root": root,
        }
    else:
        image = root / "image"
        image.write_bytes(binary)
        kwargs = {"expected_sha256": hashlib.sha256(binary).hexdigest()}

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(role="user", content=(ImagePart("image/png", image, **kwargs),))
        )

    assert captured.value.code == "attachment_blob_missing"


@pytest.mark.parametrize("failure", ["missing", "directory", "unreadable"])
def test_openai_message_fails_closed_for_unavailable_image(
    tmp_path, monkeypatch, failure
):
    secret_path = tmp_path / "do-not-disclose-private-image.png"
    secret_content = "do-not-disclose-image-content"
    if failure == "directory":
        secret_path.mkdir()
    elif failure == "unreadable":
        secret_path.write_bytes(secret_content.encode())
        original_open = os.open
        opened_descriptors = []

        def record_selected_descriptor(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            if Path(path) == secret_path:
                opened_descriptors.append(descriptor)
            return descriptor

        def deny_read(descriptor, count):
            if descriptor in opened_descriptors:
                raise PermissionError(secret_content)
            return original_read(descriptor, count)

        original_read = os.read
        monkeypatch.setattr(os, "open", record_selected_descriptor)
        monkeypatch.setattr(os, "read", deny_read)

    message = ChatMessage(
        role="user", content=(ImagePart("image/png", secret_path),)
    )

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(message)

    assert captured.value.code == "attachment_blob_missing"
    assert str(secret_path) not in str(captured.value)
    assert secret_content not in str(captured.value)
    if failure == "unreadable":
        assert len(opened_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(opened_descriptors[0])


def test_openai_message_rejects_symlink_without_following_it(tmp_path):
    target = tmp_path / "private-target.png"
    target.write_bytes(b"private-image-content")
    link = tmp_path / "image.png"
    link.symlink_to(target)

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(
                role="user", content=(ImagePart("image/png", link),)
            )
        )

    assert captured.value.code == "attachment_blob_missing"
    assert str(target) not in str(captured.value)
    assert b64encode(target.read_bytes()).decode("ascii") not in str(captured.value)


def test_openai_message_rejects_fifo_without_blocking(tmp_path):
    fifo = tmp_path / "image.png"
    os.mkfifo(fifo)

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(
                role="user", content=(ImagePart("image/png", fifo),)
            )
        )

    assert captured.value.code == "attachment_blob_missing"


def test_openai_message_rejects_image_larger_than_ten_mib(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"x" * (10 * 1024 * 1024 + 1))

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(
                role="user", content=(ImagePart("image/png", image),)
            )
        )

    assert captured.value.code == "attachment_blob_missing"


def test_openai_message_accepts_image_at_exactly_ten_mib(tmp_path):
    image = tmp_path / "image.png"
    binary = b"x" * (10 * 1024 * 1024)
    image.write_bytes(binary)

    payload = _to_openai_message(
        ChatMessage(role="user", content=(ImagePart("image/png", image),))
    )

    url = payload["content"][0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert len(url.removeprefix("data:image/png;base64,")) == len(b64encode(binary))


def test_image_reader_does_not_duplicate_the_complete_image_buffer(tmp_path):
    image = tmp_path / "image.png"
    binary = b"x" * (10 * 1024 * 1024)
    image.write_bytes(binary)
    gc.collect()

    tracemalloc.start()
    try:
        data = openai_compatible_module._read_image(ImagePart("image/png", image))
        current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert data == binary
    assert peak - current < 4 * 1024 * 1024


def test_openai_message_releases_each_base64_intermediate_before_next_image(
    tmp_path, monkeypatch
):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first-image")
    second.write_bytes(b"second-image")
    encoded_refs = []
    released_before_next_encoding = []
    real_b64encode = b64encode

    class TrackableText(str):
        __slots__ = ("__weakref__",)

    class TrackableEncodedBytes(bytes):
        def decode(self, encoding="utf-8", errors="strict"):
            value = TrackableText(super().decode(encoding, errors))
            encoded_refs.append(weakref.ref(value))
            return value

    def tracked_b64encode(data):
        if encoded_refs:
            gc.collect()
            released_before_next_encoding.append(encoded_refs[-1]() is None)
        return TrackableEncodedBytes(real_b64encode(data))

    monkeypatch.setattr(
        openai_compatible_module.base64, "b64encode", tracked_b64encode
    )

    payload = _to_openai_message(
        ChatMessage(
            role="user",
            content=(
                ImagePart("image/png", first),
                ImagePart("image/png", second),
            ),
        )
    )

    assert len(payload["content"]) == 2
    assert released_before_next_encoding == [True]


def test_openai_message_rejects_image_that_grows_during_bounded_read(
    tmp_path, monkeypatch
):
    image = tmp_path / "image.png"
    image.write_bytes(b"x" * (10 * 1024 * 1024))
    original_read = os.read
    grew = False

    def grow_then_read(fd, count):
        nonlocal grew
        if not grew:
            grew = True
            with image.open("ab") as handle:
                handle.write(b"y")
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", grow_then_read)

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(
                role="user", content=(ImagePart("image/png", image),)
            )
        )

    assert captured.value.code == "attachment_blob_missing"


@pytest.mark.parametrize("mutation", ["shrink", "same_size"])
def test_openai_message_rejects_image_mutated_during_read(
    tmp_path, monkeypatch, mutation
):
    image = tmp_path / "image.png"
    image.write_bytes(b"original-image-content")
    original_size = image.stat().st_size
    original_read = os.read
    mutated = False

    def mutate_then_read(fd, count):
        nonlocal mutated
        if not mutated:
            mutated = True
            replacement = b"short" if mutation == "shrink" else b"changed-image-content!"
            if mutation == "same_size":
                assert len(replacement) == original_size
            else:
                assert len(replacement) < original_size
            image.write_bytes(replacement)
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", mutate_then_read)

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(
                role="user", content=(ImagePart("image/png", image),)
            )
        )

    assert captured.value.code == "attachment_blob_missing"


def test_openai_message_rejects_path_replaced_during_read(tmp_path, monkeypatch):
    image = tmp_path / "image.png"
    image.write_bytes(b"original-image-content")
    displaced = tmp_path / "displaced.png"
    original_read = os.read
    replaced = False

    def replace_then_read(fd, count):
        nonlocal replaced
        if not replaced:
            replaced = True
            image.replace(displaced)
            image.write_bytes(b"replacement-image-data")
        return original_read(fd, count)

    monkeypatch.setattr(os, "read", replace_then_read)

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(
                role="user", content=(ImagePart("image/png", image),)
            )
        )

    assert captured.value.code == "attachment_blob_missing"


@pytest.mark.parametrize(
    "media_type",
    [
        "image/gif",
        "image/png;charset=utf-8",
        "image/png;base64",
        "image/png\r\nX-Injected: true",
    ],
)
def test_openai_message_rejects_noncanonical_image_media_type(tmp_path, media_type):
    image = tmp_path / "image.png"
    image.write_bytes(b"image-content")

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(role="user", content=(ImagePart(media_type, image),))
        )

    assert captured.value.code == "attachment_media_type_mismatch"
    assert media_type not in str(captured.value)


def test_openai_message_rejects_unknown_content_part_without_leaking_repr():
    class SecretPart:
        def __repr__(self):
            return "do-not-disclose-unknown-part"

    with pytest.raises(AttachmentDomainError) as captured:
        _to_openai_message(
            ChatMessage(role="user", content=(SecretPart(),))  # type: ignore[arg-type]
        )

    assert captured.value.code == "attachment_media_type_mismatch"
    assert "do-not-disclose-unknown-part" not in str(captured.value)


@pytest.mark.asyncio
async def test_chat_stream_builds_all_messages_in_one_worker_call_before_sdk(
    tmp_path, monkeypatch
):
    image = tmp_path / "image.webp"
    image.write_bytes(b"RIFF\x04\x00\x00\x00WEBP")
    worker_calls = 0
    original_run_sync = anyio.to_thread.run_sync

    async def counted_run_sync(function, *args, **kwargs):
        nonlocal worker_calls
        worker_calls += 1
        return await original_run_sync(function, *args, **kwargs)

    monkeypatch.setattr(anyio.to_thread, "run_sync", counted_run_sync)

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["messages"] == [
            {"role": "user", "content": "first"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "second"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/webp;base64,UklGRgQAAABXRUJQ"
                        },
                    },
                ],
            },
        ]
        return httpx.Response(
            200,
            content=_sse_body([]),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        assert [
            delta
            async for delta in stream.stream(
                [
                    ChatMessage(role="user", content="first"),
                    ChatMessage(
                        role="user",
                        content=(
                            TextPart("second"),
                            ImagePart("image/webp", image),
                        ),
                    ),
                ]
            )
        ] == []

    assert worker_calls == 1


@pytest.mark.asyncio
async def test_chat_stream_does_not_block_event_loop_while_reading_large_image(
    tmp_path, monkeypatch
):
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * (10 * 1024 * 1024))
    read_started = threading.Event()
    allow_read = threading.Event()

    def controlled_read(part):
        assert part.blob_path == image
        read_started.set()
        assert allow_read.wait(timeout=2)
        return b"x" * (10 * 1024 * 1024)

    monkeypatch.setattr(openai_compatible_module, "_read_image", controlled_read)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse_body([]),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )

        async def consume():
            return [
                delta
                async for delta in stream.stream(
                    [
                        ChatMessage(
                            role="user",
                            content=(ImagePart("image/png", image),),
                        )
                    ]
                )
            ]

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(consume)
            with anyio.fail_after(2):
                while not read_started.is_set():
                    await anyio.sleep(0.001)
            allow_read.set()


@pytest.mark.asyncio
async def test_chat_stream_snapshots_mutable_message_sequence_before_worker(
    monkeypatch,
):
    mapping_started = threading.Event()
    allow_mapping = threading.Event()
    original_mapper = openai_compatible_module._to_openai_messages

    def controlled_mapping(messages):
        mapping_started.set()
        assert allow_mapping.wait(timeout=2)
        return original_mapper(messages)

    monkeypatch.setattr(
        openai_compatible_module, "_to_openai_messages", controlled_mapping
    )
    messages = [ChatMessage(role="user", content="first")]

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["messages"] == [
            {"role": "user", "content": "first"}
        ]
        return httpx.Response(
            200,
            content=_sse_body([]),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )

        async def consume():
            return [delta async for delta in stream.stream(messages)]

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(consume)
            with anyio.fail_after(2):
                while not mapping_started.is_set():
                    await anyio.sleep(0.001)
            messages.append(ChatMessage(role="user", content="late"))
            allow_mapping.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("missing", "attachment_blob_missing"),
        ("directory", "attachment_blob_missing"),
        ("symlink", "attachment_blob_missing"),
        ("fifo", "attachment_blob_missing"),
        ("oversize", "attachment_blob_missing"),
        ("unreadable", "attachment_blob_missing"),
        ("identity", "attachment_blob_missing"),
        ("media_type", "attachment_media_type_mismatch"),
    ],
)
async def test_chat_stream_never_calls_sdk_when_image_mapping_fails(
    tmp_path, monkeypatch, failure, expected_code
):
    image = tmp_path / "private-image.png"
    media_type = "image/png"
    image_kwargs = {}
    if failure == "directory":
        image.mkdir()
    elif failure == "symlink":
        target = tmp_path / "private-target.png"
        target.write_bytes(b"private-image-content")
        image.symlink_to(target)
    elif failure == "fifo":
        os.mkfifo(image)
    elif failure == "oversize":
        image.write_bytes(b"x" * (10 * 1024 * 1024 + 1))
    elif failure == "unreadable":
        image.write_bytes(b"private-image-content")
        original_open = os.open

        def deny_selected_path(path, flags, *args, **kwargs):
            if Path(path) == image:
                raise PermissionError("private-read-failure")
            return original_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(os, "open", deny_selected_path)
    elif failure == "identity":
        root = tmp_path / "attachments"
        image = root / "blobs" / "aa" / "image"
        image.parent.mkdir(parents=True)
        original = b"original-image"
        image.write_bytes(b"replaced-image")
        assert image.stat().st_size == len(original)
        image_kwargs = {
            "expected_sha256": hashlib.sha256(original).hexdigest(),
            "expected_size_bytes": len(original),
            "storage_root": root,
        }
    elif failure == "media_type":
        image.write_bytes(b"private-image-content")
        media_type = "image/png\r\nX-Private: value"

    sdk_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal sdk_calls
        sdk_calls += 1
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        with pytest.raises(AttachmentDomainError) as captured:
            async for _ in stream.stream(
                [
                    ChatMessage(
                        role="user",
                        content=(ImagePart(media_type, image, **image_kwargs),),
                    )
                ]
            ):
                pass

    assert captured.value.code == expected_code
    assert str(image) not in str(captured.value)
    assert "private" not in str(captured.value).lower()
    assert sdk_calls == 0


@pytest.mark.asyncio
async def test_chat_stream_emits_text_and_reasoning_deltas():
    chunks = [
        _chunk({"reasoning_content": "thinking "}),
        _chunk({"content": "Hello"}),
        _chunk({"content": " world"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        assert payload["model"] == "model-1"
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        return httpx.Response(
            200,
            content=_sse_body(chunks),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "sentinel-key", "model-1"
        )
        deltas = [
            (delta.kind, delta.text)
            async for delta in stream.stream(
                [ChatMessage(role="user", content="hi")]
            )
        ]

    assert deltas == [
        ("reasoning", "thinking "),
        ("text", "Hello"),
        ("text", " world"),
    ]


@pytest.mark.asyncio
async def test_chat_stream_sends_explicit_effort_and_normalizes_usage():
    usage_chunk = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "model-1",
        "choices": [],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 5,
            "total_tokens": 17,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 2},
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["reasoning_effort"] == "high"
        assert payload["stream_options"] == {"include_usage": True}
        return httpx.Response(
            200,
            content=_sse_body([usage_chunk]),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        deltas = [
            delta
            async for delta in stream.stream(
                [ChatMessage(role="user", content="hi")],
                options=ChatRequestOptions(ReasoningEffort.HIGH),
            )
        ]

    assert deltas[-1].kind == "usage"
    assert deltas[-1].usage == ChatUsage(
        input_tokens=12,
        cached_input_tokens=3,
        output_tokens=5,
        reasoning_output_tokens=2,
    )


@pytest.mark.asyncio
async def test_auto_effort_is_omitted_from_provider_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert "reasoning_effort" not in payload
        return httpx.Response(
            200,
            content=_sse_body([]),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        assert [
            delta
            async for delta in stream.stream(
                [ChatMessage(role="user", content="hi")],
                options=ChatRequestOptions(ReasoningEffort.AUTO),
            )
        ] == []


@pytest.mark.asyncio
async def test_explicit_unsupported_reasoning_parameter_has_safe_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: reasoning_effort",
                    "type": "invalid_request_error",
                    "param": "reasoning_effort",
                    "code": "unsupported_parameter",
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        with pytest.raises(ProviderDomainError) as captured:
            async for _ in stream.stream(
                [ChatMessage(role="user", content="hi")],
                options=ChatRequestOptions(ReasoningEffort.HIGH),
            ):
                pass

    assert captured.value.code == "provider_reasoning_effort_unsupported"
    assert "reasoning_effort" not in str(captured.value)


@pytest.mark.asyncio
async def test_negative_provider_usage_is_rejected():
    usage_chunk = {
        "id": "chatcmpl-1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "model-1",
        "choices": [],
        "usage": {"prompt_tokens": -1, "completion_tokens": 0, "total_tokens": -1},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse_body([usage_chunk]),
            headers={"content-type": "text/event-stream"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        with pytest.raises(ProviderDomainError) as captured:
            async for _ in stream.stream([ChatMessage(role="user", content="hi")]):
                pass

    assert captured.value.code == "provider_protocol_error"


@pytest.mark.asyncio
async def test_chat_stream_emits_tool_call_signal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse_body(
                [_chunk({"tool_calls": [{"id": "call-1", "type": "function"}]})]
            ),
            headers={"content-type": "text/event-stream"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        deltas = [
            delta
            async for delta in stream.stream([ChatMessage(role="user", content="hi")])
        ]

    assert [(d.kind, d.text) for d in deltas] == [("tool_call", "")]


@pytest.mark.asyncio
async def test_chat_stream_maps_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "sentinel-key", "model-1"
        )
        with pytest.raises(ProviderDomainError) as captured:
            async for _ in stream.stream([ChatMessage(role="user", content="hi")]):
                pass

    assert captured.value.code == "provider_auth_failed"


@pytest.mark.asyncio
async def test_chat_stream_maps_rate_limit_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stream = OpenAICompatibleChatStream(
            client, "https://models.example.test/v1", "key", "model-1"
        )
        with pytest.raises(ProviderDomainError) as captured:
            async for _ in stream.stream([ChatMessage(role="user", content="hi")]):
                pass

    assert captured.value.code == "provider_rate_limited"


def test_reasoning_delta_supports_common_field_names():
    class Delta:
        reasoning_content = "deep"

    assert _reasoning_delta(Delta()) == "deep"

    class AltDelta:
        reasoning = "alt"

    assert _reasoning_delta(AltDelta()) == "alt"

    class NoneDelta:
        content = "text"

    assert _reasoning_delta(NoneDelta()) is None


def test_chat_base_url_appends_v1_for_ollama():
    ollama = record(kind=ProviderKind.OLLAMA)
    ollama = replace(ollama, base_url="http://127.0.0.1:11434")
    assert _chat_base_url(ollama) == "http://127.0.0.1:11434/v1"

    already = replace(ollama, base_url="http://127.0.0.1:11434/v1")
    assert _chat_base_url(already) == "http://127.0.0.1:11434/v1"

    compat = record()
    assert _chat_base_url(compat) == "https://models.example.test/v1"
