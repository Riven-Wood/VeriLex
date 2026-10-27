import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from verilex_runtime.container import build_services
from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.models import ProviderDraft
from verilex_runtime.providers.openai_compatible import OpenAICompatibleProbe
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.models import AuthMode, ProviderKind


class RecordingProxyHandler(BaseHTTPRequestHandler):
    saw_authorization = False

    def do_GET(self):
        type(self).saw_authorization = "authorization" in {
            name.lower() for name in self.headers
        }
        body = json.dumps(
            {
                "object": "list",
                "data": [
                    {
                        "id": "model-1",
                        "object": "model",
                        "created": 0,
                        "owned_by": "test",
                    }
                ],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        del format, args


@pytest.mark.asyncio
async def test_provider_requests_do_not_inherit_environment_proxies(
    tmp_path,
    monkeypatch,
):
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), RecordingProxyHandler)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    RecordingProxyHandler.saw_authorization = False
    for name in (
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")

    services = build_services(
        RuntimeSettings(
            host="127.0.0.1",
            port=0,
            state_dir=tmp_path,
            api_token="proxy-isolation-token",
        )
    )
    probe = OpenAICompatibleProbe(services.http_client)
    draft = ProviderDraft(
        name="Loopback Provider",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:1/v1",
        default_model="model-1",
        auth_mode=AuthMode.API_KEY,
    )
    try:
        with pytest.raises(ProviderDomainError) as captured:
            await probe.probe(draft, "proxy-isolation-sentinel")
    finally:
        await services.aclose()
        proxy.shutdown()
        proxy.server_close()
        thread.join(timeout=5)

    assert captured.value.code == "provider_unreachable"
    assert RecordingProxyHandler.saw_authorization is False
