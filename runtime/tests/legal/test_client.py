"""Fake-server protocol tests for the official law-database client."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

import verilex_runtime.legal.client as law_client_module
from verilex_runtime.legal.client import (
    LawClientError,
    LawDetail,
    LawSearchRow,
    OfficialLawClient,
)

SEARCH_PATH = "/law-search/search/list"
DETAIL_PATH = "/law-search/search/flfgDetails"
DOWNLOAD_PATH = "/law-search/download/pc"
DOCX_PATH = "/files/civil-code.docx"

_ENDPOINTS = {
    SEARCH_PATH: "search",
    DETAIL_PATH: "detail",
    DOWNLOAD_PATH: "download",
    DOCX_PATH: "docx",
}

_BBBS = "ff808081729d1efe01729d50b5c500bf"
_DOCX_BYTES = b"PK\x03\x04verilex-test-docx"


class ScriptedResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        json_body: Any = None,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        delay: float = 0.0,
        drip_interval: float | None = None,
    ) -> None:
        self.status = status
        self.json_body = json_body
        self.body = body
        self.headers = headers or {}
        self.delay = delay
        self.drip_interval = drip_interval


class RequestRecord:
    def __init__(
        self,
        endpoint: str,
        method: str,
        path: str,
        headers: dict[str, str],
        raw_body: bytes,
        arrived_at: float,
    ) -> None:
        self.endpoint = endpoint
        self.method = method
        self.path = path
        self.query = parse_qs(urlsplit(path).query)
        self.headers = headers
        self.raw_body = raw_body
        self.arrived_at = arrived_at
        self.left_at: float | None = None
        try:
            self.json_body = json.loads(raw_body.decode("utf-8")) if raw_body else None
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.json_body = None


class FakeLawServer:
    """Scripted stand-in for flk.npc.gov.cn served from 127.0.0.1."""

    def __init__(self) -> None:
        self._scripts: dict[str, list[ScriptedResponse]] = {}
        self._records: list[RequestRecord] = []
        self._lock = threading.Lock()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_GET(self) -> None:
                self._dispatch("GET")

            def _dispatch(self, method: str) -> None:
                endpoint = _ENDPOINTS.get(urlsplit(self.path).path)
                if endpoint is None:
                    self._send(404, b"unknown path", "text/plain", None)
                    return
                raw_body = self._read_body()
                record = RequestRecord(
                    endpoint,
                    method,
                    self.path,
                    {key.lower(): value for key, value in self.headers.items()},
                    raw_body,
                    time.monotonic(),
                )
                with outer._lock:
                    outer._records.append(record)
                    queue = outer._scripts.get(endpoint)
                    script = queue.pop(0) if queue else None
                if script is None:
                    self._send(500, b"unscripted request", "text/plain", None)
                    record.left_at = time.monotonic()
                    return
                if script.delay:
                    time.sleep(script.delay)
                if script.json_body is not None:
                    payload = json.dumps(script.json_body, ensure_ascii=False).encode(
                        "utf-8"
                    )
                    self._send(
                        script.status,
                        payload,
                        "application/json",
                        script.headers,
                        script.drip_interval,
                    )
                else:
                    self._send(
                        script.status,
                        script.body,
                        "application/octet-stream",
                        script.headers,
                        script.drip_interval,
                    )
                record.left_at = time.monotonic()

            def _read_body(self) -> bytes:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    length = 0
                return self.rfile.read(length) if length > 0 else b""

            def _send(
                self,
                status: int,
                body: bytes,
                content_type: str,
                extra: dict[str, str] | None,
                drip_interval: float | None = None,
            ) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(body)))
                    for key, value in (extra or {}).items():
                        self.send_header(key, value)
                    self.end_headers()
                    if drip_interval and body:
                        for index in range(len(body)):
                            self.wfile.write(body[index : index + 1])
                            self.wfile.flush()
                            time.sleep(drip_interval)
                    else:
                        self.wfile.write(body)
                except OSError:
                    return

        self._handler_class = Handler

    def start(self) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        assert self._httpd is not None and self._thread is not None
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)

    @property
    def host(self) -> str:
        return "127.0.0.1"

    @property
    def base_url(self) -> str:
        assert self._httpd is not None
        return f"http://127.0.0.1:{self._httpd.server_port}"

    def docx_url(self) -> str:
        return self.base_url + DOCX_PATH

    def enqueue(
        self,
        endpoint: str,
        *,
        status: int = 200,
        json_body: Any = None,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        delay: float = 0.0,
        drip_interval: float | None = None,
    ) -> None:
        with self._lock:
            self._scripts.setdefault(endpoint, []).append(
                ScriptedResponse(
                    status=status,
                    json_body=json_body,
                    body=body,
                    headers=headers,
                    delay=delay,
                    drip_interval=drip_interval,
                )
            )

    def requests_for(self, endpoint: str) -> list[RequestRecord]:
        return [record for record in self._records if record.endpoint == endpoint]

    def request_count(self, endpoint: str) -> int:
        return len(self.requests_for(endpoint))


@pytest.fixture()
def fake_server() -> Iterator[FakeLawServer]:
    server = FakeLawServer()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def client(fake_server: FakeLawServer) -> OfficialLawClient:
    return OfficialLawClient(
        base_url=fake_server.base_url,
        allowed_hosts=frozenset({fake_server.host}),
        min_interval=0.0,
    )


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "bbbs": _BBBS,
        "title": "中华人民共和国民法典",
        "flxz": "法律",
        "zdjgName": "全国人民代表大会",
        "sxx": 3,
        "gbrq": "2020-05-28",
        "sxrq": "2021-01-01",
    }
    row.update(overrides)
    return row


def _search_payload(rows: list[dict[str, Any]] | None = None, total: int = 32) -> dict:
    return {
        "code": 200,
        "msg": "操作成功",
        "total": total,
        "rows": rows if rows is not None else [_row()],
    }


_CHAPTER_TREE: dict[str, Any] = {
    "id": 0,
    "parentId": None,
    "title": "中华人民共和国民法典",
    "index": 0,
    "children": [
        {"id": 1, "parentId": 0, "title": "第一编 总则", "index": 1, "children": []}
    ],
}


def _detail_payload(
    content: Any = _CHAPTER_TREE,
    oss_file: Any = {"ossWordPath": "law/2021/civil-code.docx"},
    **data_overrides: Any,
) -> dict:
    data: dict[str, Any] = {
        "bbbs": _BBBS,
        "title": "中华人民共和国民法典",
        "flxz": "法律",
        "zdjgName": "全国人民代表大会",
        "sxx": 3,
        "gbrq": "2020-05-28",
        "sxrq": "2021-01-01",
        "content": content,
        "ossFile": oss_file,
    }
    data.update(data_overrides)
    return {"code": 200, "msg": "操作成功", "data": data}


def _download_payload(url: str) -> dict:
    return {
        "code": 200,
        "msg": "操作成功",
        "data": {"url": url, "urlIn": "http://internal.example/file.docx"},
    }


@pytest.mark.asyncio
async def test_search_detail_and_docx_happy_path(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body=_search_payload())
    fake_server.enqueue("detail", json_body=_detail_payload())
    fake_server.enqueue("download", json_body=_download_payload(fake_server.docx_url()))
    fake_server.enqueue("docx", body=_DOCX_BYTES)

    rows, total = await client.search("民法典")

    assert rows == [
        LawSearchRow(
            bbbs=_BBBS,
            title="中华人民共和国民法典",
            flxz="法律",
            zdjg_name="全国人民代表大会",
            sxx=3,
            gbrq="2020-05-28",
            sxrq="2021-01-01",
        )
    ]
    assert total == 32

    detail = await client.detail(_BBBS)
    assert isinstance(detail, LawDetail)
    assert detail.bbbs == _BBBS
    assert detail.title == "中华人民共和国民法典"
    assert detail.flxz == "法律"
    assert detail.zdjg_name == "全国人民代表大会"
    assert detail.sxx == 3
    assert detail.gbrq == "2020-05-28"
    assert detail.sxrq == "2021-01-01"
    assert detail.chapter_tree == _CHAPTER_TREE
    assert detail.docx_url is None

    docx = await client.fetch_docx(detail)
    assert docx == _DOCX_BYTES

    search_request = fake_server.requests_for("search")[0]
    assert search_request.method == "POST"
    body = search_request.json_body
    assert isinstance(body, dict)
    assert body["searchContent"] == "民法典"
    assert body["searchType"] == 1
    assert body["searchRange"] == 1
    assert body["pageNum"] == 1
    assert body["pageSize"] == 10
    assert body["sxx"] == []
    assert body["orderByParam"]["condition"] == "sxrq"
    assert body["orderByParam"]["sort"] == "desc"

    detail_request = fake_server.requests_for("detail")[0]
    assert detail_request.method == "GET"
    assert detail_request.query == {"bbbs": [_BBBS]}

    download_request = fake_server.requests_for("download")[0]
    assert download_request.query == {"format": ["docx"], "bbbs": [_BBBS]}

    assert fake_server.request_count("docx") == 1


@pytest.mark.asyncio
async def test_search_cleans_highlight_tags_in_titles(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue(
        "search",
        json_body=_search_payload(
            rows=[
                _row(
                    title="<em class='highlight'>中华人民共和国</em>民法典"
                )
            ]
        ),
    )

    rows, _ = await client.search("民法典")

    assert rows[0].title == "中华人民共和国民法典"
    assert "<" not in rows[0].title
    assert "em" not in rows[0].title


@pytest.mark.asyncio
async def test_search_with_no_results_returns_empty_list(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body=_search_payload(rows=[], total=0))

    rows, total = await client.search("不存在的一部法律")

    assert rows == []
    assert total == 0


@pytest.mark.asyncio
async def test_rate_limited_retries_exactly_once_then_raises(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body={"code": 500, "msg": "系统异常"})
    fake_server.enqueue("search", json_body={"code": 500, "msg": "系统异常"})

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_rate_limited"
    assert captured.value.message == "国家法律法规数据库暂时限流，请稍后再试。"
    assert fake_server.request_count("search") == 2


@pytest.mark.asyncio
async def test_rate_limited_retry_recovers(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body={"code": 500, "msg": "系统异常"})
    fake_server.enqueue("search", json_body=_search_payload())

    rows, total = await client.search("民法典")

    assert total == 32
    assert len(rows) == 1
    assert fake_server.request_count("search") == 2


@pytest.mark.asyncio
async def test_other_business_error_does_not_retry(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body={"code": 401, "msg": "未授权"})

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_site_changed"
    assert fake_server.request_count("search") == 1


@pytest.mark.asyncio
async def test_page_size_is_clamped_to_twenty(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body=_search_payload())

    await client.search("民法典", size=50)

    body = fake_server.requests_for("search")[0].json_body
    assert isinstance(body, dict)
    assert body["pageSize"] == 20


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": 200, "msg": "操作成功", "total": 0},
        {"code": 200, "rows": [], "total": "many"},
        {"code": 200, "rows": "not-a-list", "total": 1},
        {"code": 200, "rows": ["not-an-object"], "total": 1},
        {"code": 200, "rows": [{k: v for k, v in _row().items() if k != "flxz"}], "total": 1},
        {"code": 200, "rows": [{k: v for k, v in _row().items() if k != "bbbs"}], "total": 1},
        {"code": 200, "rows": [_row(sxx="3")], "total": 1},
        {"code": 200, "rows": [_row(title="")], "total": 1},
    ],
)
async def test_malformed_search_payload_maps_to_site_changed(
    payload: dict, fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body=payload)

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_site_changed"
    assert fake_server.request_count("search") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": 200, "msg": "操作成功"},
        {"code": 200, "msg": "操作成功", "data": "not-an-object"},
        {
            "code": 200,
            "msg": "操作成功",
            "data": {k: v for k, v in _detail_payload()["data"].items() if k != "title"},
        },
        {
            "code": 200,
            "msg": "操作成功",
            "data": dict(_detail_payload()["data"], content="not-a-tree"),
        },
    ],
)
async def test_malformed_detail_payload_maps_to_site_changed(
    payload: dict, fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("detail", json_body=payload)

    with pytest.raises(LawClientError) as captured:
        await client.detail(_BBBS)

    assert captured.value.code == "law_site_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, b"Not Found"),
        (500, b"Internal Server Error"),
        (200, b"<html>not json</html>"),
        (200, b'{"code": 200, "rows": [], '),
        (200, b'["not", "an", "object"]'),
    ],
)
async def test_invalid_responses_map_to_site_changed(
    status: int,
    body: bytes,
    fake_server: FakeLawServer,
    client: OfficialLawClient,
) -> None:
    fake_server.enqueue("search", status=status, body=body)

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_site_changed"
    assert fake_server.request_count("search") == 1


@pytest.mark.asyncio
async def test_redirect_outside_whitelist_is_refused(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue(
        "search",
        status=302,
        headers={"Location": "http://non-whitelisted.example/steal"},
    )

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_network_error"
    assert "non-whitelisted.example" not in captured.value.message
    assert fake_server.request_count("search") == 1


@pytest.mark.asyncio
async def test_docx_link_outside_whitelist_is_refused_before_request(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("detail", json_body=_detail_payload())
    detail = await client.detail(_BBBS)
    fake_server.enqueue(
        "download", json_body=_download_payload("http://non-whitelisted.example/file.docx")
    )

    with pytest.raises(LawClientError) as captured:
        await client.fetch_docx(detail)

    assert captured.value.code == "law_network_error"
    assert fake_server.request_count("download") == 1
    assert fake_server.request_count("docx") == 0


@pytest.mark.asyncio
async def test_base_url_outside_whitelist_never_connects(
    fake_server: FakeLawServer,
) -> None:
    client = OfficialLawClient(
        base_url=fake_server.base_url,
        allowed_hosts=frozenset({"flk.npc.gov.cn"}),
        min_interval=0.0,
    )
    fake_server.enqueue("search", json_body=_search_payload())

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_network_error"
    assert fake_server.request_count("search") == 0


@pytest.mark.asyncio
async def test_concurrent_requests_are_serialized_with_min_interval(
    fake_server: FakeLawServer,
) -> None:
    client = OfficialLawClient(
        base_url=fake_server.base_url,
        allowed_hosts=frozenset({fake_server.host}),
        min_interval=0.05,
    )
    fake_server.enqueue("search", json_body=_search_payload(), delay=0.03)
    fake_server.enqueue("search", json_body=_search_payload(), delay=0.03)

    results = await asyncio.gather(
        client.search("民法典"),
        client.search("公司法"),
    )

    assert [total for _, total in results] == [32, 32]
    records = fake_server.requests_for("search")
    assert len(records) == 2
    gap = records[1].arrived_at - records[0].arrived_at
    assert gap >= 0.05 - 0.005
    assert records[0].left_at is not None
    assert records[1].arrived_at >= records[0].left_at


def test_client_exposes_no_bulk_surface() -> None:
    client = OfficialLawClient()

    assert not hasattr(client, "batch")
    for name in dir(client):
        assert "batch" not in name.lower()
    assert [name for name in vars(law_client_module) if "batch" in name.lower()] == []
    assert "batch" not in inspect.getsource(law_client_module).lower()


@pytest.mark.asyncio
async def test_requests_carry_browser_headers(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue("search", json_body=_search_payload())
    await client.search("民法典")

    search_headers = fake_server.requests_for("search")[0].headers
    assert search_headers["user-agent"].startswith("Mozilla/5.0")
    assert search_headers["referer"] == fake_server.base_url + "/"
    assert search_headers["origin"] == fake_server.base_url
    assert search_headers["content-type"].startswith("application/json")

    fake_server.enqueue("detail", json_body=_detail_payload())
    detail = await client.detail(_BBBS)

    detail_headers = fake_server.requests_for("detail")[0].headers
    assert detail_headers["user-agent"].startswith("Mozilla/5.0")
    assert detail_headers["referer"] == fake_server.base_url + "/"
    assert detail_headers["origin"] == fake_server.base_url

    fake_server.enqueue("download", json_body=_download_payload(fake_server.docx_url()))
    fake_server.enqueue("docx", body=_DOCX_BYTES)
    await client.fetch_docx(detail)

    docx_headers = fake_server.requests_for("docx")[0].headers
    assert docx_headers["user-agent"].startswith("Mozilla/5.0")


@pytest.mark.asyncio
async def test_oversized_responses_are_rejected(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    oversized_json = _search_payload()
    oversized_json["rows"][0]["title"] = "x" * (2 * 1024 * 1024 + 16)
    fake_server.enqueue("search", json_body=oversized_json)

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_site_changed"

    fake_server.enqueue("detail", json_body=_detail_payload())
    detail = await client.detail(_BBBS)
    fake_server.enqueue("download", json_body=_download_payload(fake_server.docx_url()))
    fake_server.enqueue("docx", body=b"P" * (8 * 1024 * 1024 + 1))

    with pytest.raises(LawClientError) as captured_docx:
        await client.fetch_docx(detail)

    assert captured_docx.value.code == "law_site_changed"


@pytest.mark.asyncio
async def test_timeout_maps_to_fixed_error(fake_server: FakeLawServer) -> None:
    client = OfficialLawClient(
        base_url=fake_server.base_url,
        allowed_hosts=frozenset({fake_server.host}),
        min_interval=0.0,
        request_timeout_seconds=0.25,
    )
    fake_server.enqueue("search", json_body=_search_payload(), delay=0.8)

    with pytest.raises(LawClientError) as captured:
        await client.search("民法典")

    assert captured.value.code == "law_timeout"


@pytest.mark.asyncio
async def test_slow_drip_body_hits_total_deadline(fake_server: FakeLawServer) -> None:
    client = OfficialLawClient(
        base_url=fake_server.base_url,
        allowed_hosts=frozenset({fake_server.host}),
        min_interval=0.0,
        docx_timeout_seconds=0.3,
    )
    detail = LawDetail(
        bbbs=_BBBS,
        title="中华人民共和国民法典",
        flxz="法律",
        zdjg_name="全国人民代表大会",
        sxx=3,
        gbrq="2020-05-28",
        sxrq="2021-01-01",
        chapter_tree={},
        docx_url=fake_server.docx_url(),
    )
    # Every byte arrives well within the per-read socket timeout, so only a
    # total deadline can cut the transfer short.
    fake_server.enqueue("docx", body=b"PK-drip!", drip_interval=0.12)

    started = time.monotonic()
    with pytest.raises(LawClientError) as captured:
        await client.fetch_docx(detail)
    elapsed = time.monotonic() - started

    assert captured.value.code == "law_timeout"
    assert elapsed < 0.8


@pytest.mark.asyncio
async def test_fetch_docx_uses_detail_docx_url_when_absolute(
    fake_server: FakeLawServer, client: OfficialLawClient
) -> None:
    fake_server.enqueue(
        "detail", json_body=_detail_payload(oss_file={"ossWordPath": fake_server.docx_url()})
    )
    detail = await client.detail(_BBBS)
    assert detail.docx_url == fake_server.docx_url()

    fake_server.enqueue("docx", body=_DOCX_BYTES)
    docx = await client.fetch_docx(detail)

    assert docx == _DOCX_BYTES
    assert fake_server.request_count("download") == 0


def test_result_dataclasses_are_immutable() -> None:
    row = LawSearchRow(
        bbbs="B",
        title="t",
        flxz="f",
        zdjg_name="z",
        sxx=3,
        gbrq="g",
        sxrq="s",
    )
    with pytest.raises(AttributeError):
        row.title = "changed"  # type: ignore[misc]

    detail = LawDetail(
        bbbs="B",
        title="t",
        flxz="f",
        zdjg_name="z",
        sxx=3,
        gbrq="g",
        sxrq="s",
        chapter_tree={},
        docx_url=None,
    )
    with pytest.raises(AttributeError):
        detail.docx_url = "http://example.test/x"  # type: ignore[misc]


def test_error_codes_are_limited_to_the_fixed_set() -> None:
    for code in (
        "law_network_error",
        "law_rate_limited",
        "law_site_changed",
        "law_timeout",
        "law_not_found",
    ):
        error = LawClientError(code)
        assert error.code == code
        assert error.message

    with pytest.raises(ValueError):
        LawClientError("law_backdoor")
