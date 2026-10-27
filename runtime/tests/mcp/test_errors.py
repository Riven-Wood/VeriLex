from dataclasses import FrozenInstanceError

import pytest


def test_domain_error_exposes_stable_code_and_retryability():
    from verilex_runtime.mcp.errors import MCPDomainError

    error = MCPDomainError("mcp_keychain_unavailable", retryable=True)

    assert error.code == "mcp_keychain_unavailable"
    assert error.retryable is True
    assert str(error) == "mcp_keychain_unavailable"

    with pytest.raises(FrozenInstanceError):
        error.code = "changed"
