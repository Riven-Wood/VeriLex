import pytest

from verilex_runtime.api.limits import RequestBodyLimitMiddleware


async def _run(scope):
    sent = []

    async def app(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"x", "more_body": False}

    async def send(message):
        sent.append(message)

    await RequestBodyLimitMiddleware(app)(scope, receive, send)
    return sent[0]["status"]


def _scope(*, path="/v1/threads/t1/attachment-uploads", method="POST", headers=()):
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": list(headers),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        ((b"content-length", b"-1"),),
        ((b"content-length", b"not-a-number"),),
        ((b"content-length", b"1"), (b"content-length", b"2")),
        ((b"content-length", b"1"), (b"transfer-encoding", b"chunked")),
    ],
)
async def test_malformed_or_ambiguous_attachment_lengths_fail_closed(headers):
    assert await _run(_scope(headers=headers)) == 413


@pytest.mark.asyncio
async def test_exact_attachment_length_boundary_is_accepted_and_overflow_rejected():
    exact = ((b"content-length", str(25 * 1024 * 1024).encode()),)
    overflow = ((b"content-length", str(25 * 1024 * 1024 + 1).encode()),)
    assert await _run(_scope(headers=exact)) == 204
    assert await _run(_scope(headers=overflow)) == 413
