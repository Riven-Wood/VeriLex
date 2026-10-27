"""Conservative redaction for short persisted or displayed text fields.

The callers in this module intentionally replace the whole field when a
sensitive marker is present. Partial masking is too easy to get wrong and can
leave a usable credential or private path behind.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, unquote_plus, urlsplit


REDACTED_TEXT = "[内容已脱敏]"

# Provider tool arguments and approval reasons can be much larger than the
# snippets we persist. Scan a bounded prefix that comfortably covers every
# persisted field, then let each caller apply its own smaller output limit.
MAX_SENSITIVE_SCAN_CHARS = 8 * 1024

_URI_PATTERN = re.compile(
    r"(?i)[A-Z][A-Z0-9+.-]*://"
    r"(?:[^\s,，。;；!?！？、/@#\"'“”‘’<>\[\](){}]+@)?"
    r"(?:\[(?=[^\]]*:)[0-9A-F:.]+(?:%[0-9A-Z_.-]+)?\]|"
    r"[^\s,，。;；!?！？、/:@#\"'“”‘’<>\[\](){}]+)"
    r"(?::[0-9]+)?"
    r"(?:/[^\s,，。；!?！？、?#\"'“”‘’<>\[\]{}]*)?"
    r"(?:\?(?=[^\s,，。;；!?！？、#\"'“”‘’<>\[\](){}]*=)"
    r"[^\s,，。;；!?！？、#\"'“”‘’<>\[\](){}]+)?"
    r"(?:#[^\s,，。;；!?！？、\"'“”‘’<>\[\](){}]+)?"
)
_POSIX_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9:/])/(?!/)[^/\s]+(?:/[^/\s]+)*"
)
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Z]:\\(?!\\)[^\\\s]+(?:\\[^\\\s]+)*"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}")
_PREFIXED_KEY_PATTERNS = (
    re.compile(
        r"(?i)(?<![A-Za-z0-9])(?:sk|pk|ghp|github_pat|xox[baprs])[-_]"
        r"[A-Za-z0-9._-]{12,}(?![A-Za-z0-9])"
    ),
    re.compile(r"(?<![A-Za-z0-9])AKIA[A-Z0-9]{16}(?![A-Za-z0-9])"),
)
_ENGLISH_LABEL = (
    r"(?:api[\s_-]*key|access[\s_-]*token|authorization|auth|token|"
    r"password|passwd|secret)"
)
_CHINESE_LABEL = r"(?:API\s*密钥|访问令牌|认证令牌|密码|口令|令牌|凭证|密钥)"
_LABEL = rf"(?:(?<![\w-]){_ENGLISH_LABEL}(?![\w-])|{_CHINESE_LABEL})"
_EXPLICIT_ASSIGNMENT = re.compile(
    rf"(?i){_LABEL}\s*[:：=]\s*(?P<value>\S)"
)
_EQUALS_ASSIGNMENT = re.compile(
    rf"(?i){_LABEL}\s*=\s*(?P<value>\S)"
)
_NATURAL_IS_VALUE = re.compile(
    rf"(?i){_LABEL}\s+is\s+(?P<value>\S)"
)
_QUERY_CREDENTIAL_KEYS = {
    "apikey",
    "accesstoken",
    "authorization",
    "auth",
    "token",
    "password",
    "passwd",
    "secret",
    "api密钥",
    "密码",
    "口令",
    "令牌",
    "访问令牌",
    "认证令牌",
    "凭证",
    "密钥",
}


def _uri_contains_credential(uri: str) -> bool:
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return False
    if parsed.username or parsed.password:
        return True
    for component in (parsed.query, parsed.fragment):
        for key, value in parse_qsl(component, keep_blank_values=True):
            normalized_key = re.sub(r"[\s_-]", "", key).casefold()
            if normalized_key in _QUERY_CREDENTIAL_KEYS and bool(value):
                return True
    return False


def redact_sensitive_text(value: str) -> str:
    """Return a fixed placeholder when a bounded prefix contains sensitive data."""
    scanned = value[:MAX_SENSITIVE_SCAN_CHARS]

    if _BEARER_PATTERN.search(scanned):
        return REDACTED_TEXT
    if any(pattern.search(scanned) for pattern in _PREFIXED_KEY_PATTERNS):
        return REDACTED_TEXT

    # Decode once before URI masking so percent-encoded query or fragment
    # assignments cannot evade detection when punctuation shortens a URI
    # candidate. Only the unambiguous ``label=value`` form runs here: applying
    # colon rules to the decoded whole field would misclassify safe URI paths.
    try:
        decoded = unquote_plus(scanned)
    except (UnicodeError, ValueError):
        decoded = scanned
    if _EQUALS_ASSIGNMENT.search(decoded):
        return REDACTED_TEXT

    # URI paths can legitimately contain words such as ``API-key``. Check
    # authority userinfo plus query/fragment parameters, then mask safe URIs
    # before applying filesystem-path and labelled-value rules to prose.
    masked = list(scanned)
    for match in _URI_PATTERN.finditer(scanned):
        if _uri_contains_credential(match.group()):
            return REDACTED_TEXT
        masked[match.start() : match.end()] = " " * (match.end() - match.start())
    prose = "".join(masked)

    if _POSIX_PATH_PATTERN.search(prose) or _WINDOWS_PATH_PATTERN.search(prose):
        return REDACTED_TEXT
    if _EXPLICIT_ASSIGNMENT.search(prose):
        return REDACTED_TEXT
    if _NATURAL_IS_VALUE.search(prose):
        return REDACTED_TEXT
    return value
