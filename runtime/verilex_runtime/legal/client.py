"""Serialized, whitelisted HTTP client for the official law database.

The official National Laws and Regulations Database (flk.npc.gov.cn) has no
documented API, so this client treats its current JSON endpoints as
observed-but-fragile facts. Access discipline mirrors the controlled web
package: at most one in-flight request, a minimum interval between requests,
a host whitelist enforced before any connection is opened, bounded response
sizes, fixed Chinese error messages, and exactly one retry when the site
reports its transient rate-limit response. The site's bulk-download endpoint
is intentionally not implemented anywhere in this module.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from time import monotonic
from typing import Any
from urllib import error as _urlerror
from urllib import parse as _urlparse
from urllib import request as _urlrequest


DEFAULT_BASE_URL = "https://flk.npc.gov.cn"
DEFAULT_ALLOWED_HOSTS = frozenset(
    {"flk.npc.gov.cn", "flkoss.obs-bj2.cucloud.cn"}
)

_ERROR_MESSAGES = {
    "law_network_error": "网络暂时不可用，无法访问国家法律法规数据库。",
    "law_rate_limited": "国家法律法规数据库暂时限流，请稍后再试。",
    "law_site_changed": "国家法律法规数据库返回了无法识别的内容，可能已改版。",
    "law_timeout": "访问国家法律法规数据库超时，请稍后再试。",
    "law_not_found": "未能核实对应的法规内容。",
    # Raised by the retrieval service layer (never by the HTTP client
    # itself) when the local cache cannot be read or written.
    "law_cache_error": "本地法律缓存读写失败。",
}

_SEARCH_LIST_PATH = "/law-search/search/list"
_DETAIL_PATH = "/law-search/search/flfgDetails"
_DOWNLOAD_LINK_PATH = "/law-search/download/pc"

_MAX_PAGE_SIZE = 20
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_DOCX_BYTES = 8 * 1024 * 1024
_MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_JSON_TIMEOUT_SECONDS = 30.0
_DOCX_TIMEOUT_SECONDS = 120.0
_OK_CODE = 200
_RATE_LIMITED_MESSAGE = "系统异常"

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_HIGHLIGHT_TAGS = re.compile(r"<[^>]+>")


class LawClientError(Exception):
    """Fixed-code, fixed-message error for the official law database."""

    def __init__(self, code: str) -> None:
        try:
            message = _ERROR_MESSAGES[code]
        except KeyError:
            raise ValueError("Unknown law client error code") from None
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class LawSearchRow:
    bbbs: str
    title: str
    flxz: str
    zdjg_name: str
    sxx: int
    gbrq: str
    sxrq: str


@dataclass(frozen=True)
class LawDetail:
    bbbs: str
    title: str
    flxz: str
    zdjg_name: str
    sxx: int
    gbrq: str
    sxrq: str
    chapter_tree: dict
    docx_url: str | None


def _clean_title(raw: object) -> str | None:
    """Strip the site's highlight tags from a result title."""
    if type(raw) is not str:
        return None
    cleaned = _HIGHLIGHT_TAGS.sub("", raw).strip()
    return cleaned or None


def _parse_row(item: object) -> LawSearchRow | None:
    if not isinstance(item, dict):
        return None
    bbbs = item.get("bbbs")
    flxz = item.get("flxz")
    zdjg_name = item.get("zdjgName")
    sxx = item.get("sxx")
    gbrq = item.get("gbrq")
    sxrq = item.get("sxrq")
    title = _clean_title(item.get("title"))
    if title is None or type(bbbs) is not str or not bbbs:
        return None
    if type(sxx) is not int:
        return None
    if any(type(value) is not str for value in (flxz, zdjg_name, gbrq, sxrq)):
        return None
    return LawSearchRow(
        bbbs=bbbs,
        title=title,
        flxz=flxz,
        zdjg_name=zdjg_name,
        sxx=sxx,
        gbrq=gbrq,
        sxrq=sxrq,
    )


def _parse_search_payload(payload: dict) -> tuple[list[LawSearchRow], int]:
    rows_value = payload.get("rows")
    total = payload.get("total")
    if not isinstance(rows_value, list) or type(total) is not int:
        raise LawClientError("law_site_changed")
    rows: list[LawSearchRow] = []
    for item in rows_value:
        row = _parse_row(item)
        if row is None:
            raise LawClientError("law_site_changed")
        rows.append(row)
    return rows, total


def _direct_docx_url(oss_file: object) -> str | None:
    """Keep only absolute docx links; relative OSS paths need download/pc."""
    if not isinstance(oss_file, dict):
        return None
    path = oss_file.get("ossWordPath")
    if type(path) is not str:
        return None
    if path.startswith(("http://", "https://")):
        return path
    return None


def _parse_detail_payload(payload: dict) -> LawDetail:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LawClientError("law_site_changed")
    row = _parse_row(data)
    if row is None:
        raise LawClientError("law_site_changed")
    content = data.get("content", {})
    if not isinstance(content, dict):
        raise LawClientError("law_site_changed")
    return LawDetail(
        bbbs=row.bbbs,
        title=row.title,
        flxz=row.flxz,
        zdjg_name=row.zdjg_name,
        sxx=row.sxx,
        gbrq=row.gbrq,
        sxrq=row.sxrq,
        chapter_tree=content,
        docx_url=_direct_docx_url(data.get("ossFile")),
    )


def _parse_download_payload(payload: dict) -> str:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LawClientError("law_site_changed")
    url = data.get("url")
    if type(url) is not str or not url:
        raise LawClientError("law_site_changed")
    return url


def _decode_json_object(raw: bytes) -> dict:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise LawClientError("law_site_changed") from None
    if not isinstance(payload, dict):
        raise LawClientError("law_site_changed")
    return payload


def _is_rate_limited(payload: dict) -> bool:
    return (
        payload.get("code") != _OK_CODE
        and payload.get("msg") == _RATE_LIMITED_MESSAGE
    )


def _allowed_absolute_url(url: object, allowed_hosts: frozenset[str]) -> str:
    """Validate scheme and host before any connection is opened."""
    if type(url) is not str or not url:
        raise LawClientError("law_network_error")
    parts = _urlparse.urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise LawClientError("law_network_error")
    if parts.hostname not in allowed_hosts:
        raise LawClientError("law_network_error")
    return url


def _reject_oversized_content_length(value: object, limit: int) -> None:
    if value is None:
        return
    try:
        length = int(value)
    except (TypeError, ValueError):
        raise LawClientError("law_network_error") from None
    if length < 0:
        raise LawClientError("law_network_error")
    if length > limit:
        raise LawClientError("law_site_changed")


def _read_bounded(response: Any, limit: int) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise LawClientError("law_site_changed")
    return body


class _NoRedirectHandler(_urlrequest.HTTPRedirectHandler):
    """Prevent urllib from following redirects so every hop is whitelisted."""

    def redirect_request(
        self, req, fp, code, msg, headers, newurl
    ):  # noqa: ANN001
        return None


class OfficialLawClient:
    """Single-flight, rate-limited client for the official law database."""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        allowed_hosts: frozenset[str] = DEFAULT_ALLOWED_HOSTS,
        min_interval: float = 1.0,
        request_timeout_seconds: float = _JSON_TIMEOUT_SECONDS,
        docx_timeout_seconds: float = _DOCX_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._allowed_hosts = frozenset(allowed_hosts)
        self._min_interval = float(min_interval)
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._docx_timeout_seconds = float(docx_timeout_seconds)
        self._gate = asyncio.Lock()
        self._idle_after = 0.0
        self._opener = _urlrequest.build_opener(
            _urlrequest.ProxyHandler({}), _NoRedirectHandler()
        )

    async def search(
        self,
        keyword: str,
        *,
        search_type: int = 1,
        sxx: list[int] | tuple[int, ...] | int | None = None,
        page: int = 1,
        size: int = 10,
    ) -> tuple[list[LawSearchRow], int]:
        """Search laws by keyword with optional effectivity filter."""
        if not isinstance(keyword, str):
            raise ValueError("keyword must be a string")
        if sxx is None:
            sxx_values: list[int] = []
        elif isinstance(sxx, (list, tuple)):
            sxx_values = [int(value) for value in sxx]
        else:
            sxx_values = [int(sxx)]
        body = {
            "searchContent": keyword,
            "searchType": int(search_type),
            "searchRange": 1,
            "sxrq": [],
            "sxx": sxx_values,
            "gbrq": [],
            "gbrqYear": [],
            "flfgCodeId": [],
            "zdjgCodeId": [],
            "orderByParam": {"condition": "sxrq", "sort": "desc"},
            "pageNum": max(1, int(page)),
            "pageSize": min(_MAX_PAGE_SIZE, max(1, int(size))),
        }
        payload = await self._law_search_json("POST", _SEARCH_LIST_PATH, body=body)
        return _parse_search_payload(payload)

    async def detail(self, bbbs: str) -> LawDetail:
        """Fetch one law's metadata and chapter tree by its version id."""
        if not isinstance(bbbs, str) or not bbbs:
            raise ValueError("bbbs must be a non-empty string")
        payload = await self._law_search_json(
            "GET", _DETAIL_PATH, params={"bbbs": bbbs}
        )
        return _parse_detail_payload(payload)

    async def fetch_docx(self, detail: LawDetail) -> bytes:
        """Download the law's official docx body as bounded raw bytes."""
        url = detail.docx_url
        if url is None:
            url = await self._resolve_docx_url(detail.bbbs)
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "application/octet-stream, */*",
            "Accept-Encoding": "identity",
        }
        return await self._gated_request(
            "GET",
            url,
            headers=headers,
            body=None,
            timeout_seconds=self._docx_timeout_seconds,
            limit=_MAX_DOCX_BYTES,
        )

    async def _resolve_docx_url(self, bbbs: str) -> str:
        payload = await self._law_search_json(
            "GET", _DOWNLOAD_LINK_PATH, params={"format": "docx", "bbbs": bbbs}
        )
        return _parse_download_payload(payload)

    async def _law_search_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict:
        """Call one law-search endpoint, retrying exactly once when limited."""
        url = self._base_url + path
        if params:
            url = f"{url}?{_urlparse.urlencode(params)}"
        headers = {
            "User-Agent": _USER_AGENT,
            "Referer": f"{self._base_url}/",
            "Origin": self._base_url,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "identity",
        }
        data: bytes | None = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        payload = _decode_json_object(
            await self._gated_request(
                method,
                url,
                headers=headers,
                body=data,
                timeout_seconds=self._request_timeout_seconds,
                limit=_MAX_JSON_BYTES,
            )
        )
        if _is_rate_limited(payload):
            payload = _decode_json_object(
                await self._gated_request(
                    method,
                    url,
                    headers=headers,
                    body=data,
                    timeout_seconds=self._request_timeout_seconds,
                    limit=_MAX_JSON_BYTES,
                )
            )
        if _is_rate_limited(payload):
            raise LawClientError("law_rate_limited")
        if payload.get("code") != _OK_CODE:
            raise LawClientError("law_site_changed")
        return payload

    async def _gated_request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
        limit: int,
    ) -> bytes:
        """Serialize requests and keep at least min_interval between them."""
        _allowed_absolute_url(url, self._allowed_hosts)
        async with self._gate:
            delay = self._idle_after - monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                # Total deadline mirrors the web client: a slow-drip response
                # must not hold the rate-limit gate past the socket timeout.
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        self._request_sync,
                        method,
                        url,
                        headers,
                        body,
                        timeout_seconds,
                        limit,
                    ),
                    timeout=timeout_seconds,
                )
            except LawClientError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                raise LawClientError("law_timeout") from None
            except Exception:
                raise LawClientError("law_network_error") from None
            finally:
                self._idle_after = monotonic() + self._min_interval

    def _request_sync(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout_seconds: float,
        limit: int,
    ) -> bytes:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            _allowed_absolute_url(current, self._allowed_hosts)
            request = _urlrequest.Request(
                current, data=body, headers=headers, method=method
            )
            try:
                with self._opener.open(request, timeout=timeout_seconds) as response:
                    _reject_oversized_content_length(
                        response.headers.get("Content-Length"), limit
                    )
                    return _read_bounded(response, limit)
            except _urlerror.HTTPError as error:
                try:
                    if error.code in _REDIRECT_STATUSES:
                        location = (
                            error.headers.get("Location") if error.headers else None
                        )
                        if not location:
                            raise LawClientError("law_network_error") from None
                        current = _urlparse.urljoin(current, location)
                        continue
                    raise LawClientError("law_site_changed") from None
                finally:
                    error.close()
            except _urlerror.URLError as error:
                if isinstance(error.reason, TimeoutError):
                    raise LawClientError("law_timeout") from None
                raise LawClientError("law_network_error") from None
        raise LawClientError("law_network_error")


__all__ = [
    "DEFAULT_ALLOWED_HOSTS",
    "DEFAULT_BASE_URL",
    "LawClientError",
    "LawDetail",
    "LawSearchRow",
    "OfficialLawClient",
]
