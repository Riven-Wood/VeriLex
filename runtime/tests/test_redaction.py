import pytest

from verilex_runtime.redaction import REDACTED_TEXT, redact_sensitive_text


@pytest.mark.parametrize(
    "sensitive",
    [
        "API密钥：普通短值",
        "密码：abc123",
        "密码:abc123",
        "?password=abc123",
        "凭证：correct-horse-battery-staple",
        "访问令牌：long-sensitive-token-value",
        "认证令牌：long-sensitive-token-value",
        "参考https://example.com/guide，密码：abc123",
        "https://user:abc123@example.com/private",
        "https://user@example.com/private",
        "https://example.com/callback?password=abc123",
        "https://example.com/callback?pass%77ord=abc123",
        "参考https://example.com/guide,password: abc123",
        "参考https://example.com/guide?密码：abc123",
        '参考"https://example.com/guide" password: abc123',
        "参考(https://example.com/guide) password: abc123",
        "password is sk-live-sensitive-token-123456789",
        "Authorization is Bearer eyJhbGciOiJIUzI1NiJ9.sensitive.payload",
        "password is swordfish",
        "password is administered centrally",
        "secret is confidential information under the agreement",
        "密码 is 剑鱼口令",
        "password: abc123 do not share",
        "password is abc123",
        'password: "abc123"',
        '密码：“abc123”',
        "https://example.com/callback#token=abc123",
        "https://example.com/callback#to%6ben=abc123",
        "postgresql://user:pass@db.example.test/app",
        "ssh://user@example.test/private",
        "https://[2001:db8::1]?pass%77ord=abc123",
        "https://[2001:db8::1]#to%6ben=abc123",
        "https://[2001:db8::1]?secr%65t=abc123",
        "postgresql://us%65r:p%61ss@[2001:db8::1]",
        "https://example.com/docs_(guide)?pass%77ord=abc123",
        "https://example.com/a;b?to%6ben=abc123",
        "https://example.com/docs_(guide)?password%3Dabc123",
        "https://[2001:db8::1 /secret.txt",
        "https://[not-an-ipv6]/secret.txt",
        # Explicit colon assignments are fail-closed even when their value
        # could also be read as prose.
        "Authorization: documentation is available",
        "API key: configuration is documented",
    ],
)
def test_redacts_structurally_assigned_credentials(sensitive):
    assert redact_sensitive_text(sensitive) == REDACTED_TEXT


@pytest.mark.parametrize(
    "safe",
    [
        "postgresql://db.example.test/app",
        "ssh://example.test/private",
        "https://[2001:db8::1]/guide",
        "https://[2001:db8::1]/guide?section=overview",
        "https://example.com/docs_(guide)/private",
        "https://example.com/docs_(guide)/private?view=compact",
        "ordinary [bracketed] explanation",
    ],
)
def test_preserves_explanatory_credential_language(safe):
    assert redact_sensitive_text(safe) == safe


def test_english_colon_with_a_single_value_is_redacted():
    assert redact_sensitive_text("password: abc123") == REDACTED_TEXT
