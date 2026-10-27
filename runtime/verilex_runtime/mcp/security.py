from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Sequence
from urllib.parse import parse_qsl, urlsplit

if TYPE_CHECKING:
    from verilex_runtime.mcp.importer import MCPServerDraft


MAX_SCAN_BYTES = 256 * 1024

# These are explicit authentication fields, not fragments to search in names.
_SECRET_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"})
_CREDENTIAL_FIELDS = frozenset({
    "token", "accesstoken", "refreshtoken", "apitoken", "authtoken",
    "secret", "clientsecret", "password", "passwd", "key", "apikey",
    "auth", "credential", "signature", "sig", "xamzcredential", "xamzsignature",
})
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


@dataclass(frozen=True, slots=True)
class MCPRiskFinding:
    code: str
    severity: Literal["block", "warn"]


def is_sensitive_header_name(name: str) -> bool:
    return name.casefold() in _SECRET_HEADERS


def is_sensitive_query_name(name: str) -> bool:
    normalized = name.casefold().replace("-", "").replace("_", "")
    return normalized in _CREDENTIAL_FIELDS or normalized in {
        header.replace("-", "") for header in _SECRET_HEADERS
    }


def _has_percent_escape(value: str) -> bool:
    for index, character in enumerate(value):
        if (
            character == "%"
            and index + 2 < len(value)
            and value[index + 1] in _HEX_DIGITS
            and value[index + 2] in _HEX_DIGITS
        ):
            return True
    return False


def stable_query_pairs(query: str) -> tuple[tuple[str, str], ...] | None:
    try:
        pairs = parse_qsl(
            query,
            keep_blank_values=True,
            strict_parsing=False,
            encoding="utf-8",
            errors="strict",
            max_num_fields=512,
        )
    except (UnicodeError, ValueError):
        return None
    if any(_has_percent_escape(name) for name, _value in pairs):
        return None
    return tuple(pairs)


def secret_placement_codes(draft: MCPServerDraft) -> frozenset[str]:
    """Keep explicit authentication fields out of persisted public config.

    Process authorization belongs to configuration review. Command text,
    directory names and explicit environment variables do not prove behavior.
    """
    codes: set[str] = set()
    try:
        size = sum(len(value.encode("utf-8", "strict")) for value in
                   (draft.command or "", *draft.args, draft.cwd or ""))
        if size > MAX_SCAN_BYTES:
            codes.add("mcp_config_invalid")
    except UnicodeError:
        codes.add("mcp_config_invalid")
    for index, argument in enumerate(draft.args):
        if not argument.startswith(("-", "/")):
            continue
        option = argument.lstrip("-/").split("=", 1)[0].split(":", 1)[0]
        if is_sensitive_query_name(option):
            codes.add("mcp_secret_in_arguments")
        if option.casefold() in {"header", "h"}:
            payload = (argument.split("=", 1)[1] if "=" in argument else
                       draft.args[index + 1] if index + 1 < len(draft.args) else "")
            if is_sensitive_header_name(payload.split(":", 1)[0].strip()):
                codes.add("mcp_secret_in_arguments")
    if draft.url:
        try:
            parsed = urlsplit(draft.url)
            query = stable_query_pairs(parsed.query)
            if query is None:
                codes.add("mcp_config_invalid")
            elif any(is_sensitive_query_name(name) for name, _ in query):
                codes.add("mcp_secret_in_url")
            if parsed.username is not None or parsed.password is not None:
                codes.add("mcp_url_credentials")
        except ValueError:
            codes.add("mcp_config_invalid")
    return frozenset(codes)


def scan_mcp_draft(draft: MCPServerDraft) -> Sequence[MCPRiskFinding]:
    findings = [MCPRiskFinding(code, "block") for code in secret_placement_codes(draft)]
    if draft.url and draft.url.casefold().startswith("http:"):
        findings.append(MCPRiskFinding("mcp_insecure_http", "warn"))
    return tuple(sorted(findings, key=lambda finding: finding.code))
