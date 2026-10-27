import json
import os
import stat

import pytest

from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.secrets.local_store import LocalSecretStore


def assert_domain_error(captured, *, code, secret="sentinel-secret"):
    error = captured.value
    assert isinstance(error, ProviderDomainError)
    assert error.code == code
    assert secret not in str(error)


def test_local_store_survives_restart_and_uses_private_permissions(tmp_path):
    secret_path = tmp_path / "private" / "secrets.json"
    store = LocalSecretStore(secret_path)

    store.ensure_available()
    store.put("provider:provider-1", "sentinel-secret")

    restarted = LocalSecretStore(secret_path)
    assert restarted.get("provider:provider-1") == "sentinel-secret"
    assert json.loads(secret_path.read_text(encoding="utf-8")) == {
        "provider:provider-1": "sentinel-secret"
    }

    if os.name != "nt":
        assert stat.S_IMODE(secret_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600


def test_local_store_delete_is_idempotent_and_persists(tmp_path):
    secret_path = tmp_path / "secrets.json"
    store = LocalSecretStore(secret_path)
    store.put("mcp:server:header:Authorization:v1", "sentinel-secret")

    store.delete("mcp:server:header:Authorization:v1")
    store.delete("mcp:server:header:Authorization:v1")

    assert LocalSecretStore(secret_path).get(
        "mcp:server:header:Authorization:v1"
    ) is None


def test_corrupt_secret_file_fails_closed_without_exposing_content(tmp_path):
    secret_path = tmp_path / "secrets.json"
    secret_path.write_text('{"secret":"sentinel-secret"', encoding="utf-8")
    store = LocalSecretStore(secret_path)

    with pytest.raises(ProviderDomainError) as captured:
        store.get("provider:provider-1")

    assert_domain_error(captured, code="secret_store_unavailable")


def test_secret_file_symlink_is_rejected(tmp_path):
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    secret_path = tmp_path / "secrets.json"
    secret_path.symlink_to(target)
    store = LocalSecretStore(secret_path)

    with pytest.raises(ProviderDomainError) as captured:
        store.put("provider:provider-1", "sentinel-secret")

    assert_domain_error(captured, code="secret_store_write_failed")
    assert target.read_text(encoding="utf-8") == "{}"


def test_non_string_secret_map_fails_closed(tmp_path):
    secret_path = tmp_path / "secrets.json"
    secret_path.write_text('{"provider:provider-1":42}', encoding="utf-8")
    store = LocalSecretStore(secret_path)

    with pytest.raises(ProviderDomainError) as captured:
        store.get("provider:provider-1")

    assert_domain_error(captured, code="secret_store_unavailable")
