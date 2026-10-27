from dataclasses import FrozenInstanceError

import pytest

from verilex_runtime.providers.errors import provider_error
from verilex_runtime.providers.models import ProviderDraft, ProviderUpdate, SecretAction
from verilex_runtime.storage.models import AuthMode, ProviderKind


def draft_with_key(api_key: str = "sentinel-key") -> ProviderDraft:
    return ProviderDraft(
        name="Compat",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        default_model="test-model",
        auth_mode=AuthMode.API_KEY,
        api_key=api_key,
    )


def update_with_key(api_key: str = "sentinel-key") -> ProviderUpdate:
    return ProviderUpdate(api_key=api_key)


@pytest.mark.parametrize(
    ("code", "retryable", "http_status"),
    [
        ("invalid_provider_url", False, 422),
        ("insecure_remote_url", False, 422),
        ("secret_store_unavailable", True, 503),
        ("secret_store_write_failed", True, 503),
        ("provider_unreachable", True, 502),
        ("provider_timeout", True, 504),
        ("provider_auth_failed", False, 401),
        ("provider_rate_limited", True, 429),
        ("provider_model_not_found", False, 404),
        ("provider_protocol_error", False, 502),
        ("provider_response_too_large", False, 502),
        ("provider_reasoning_effort_unsupported", False, 422),
        ("provider_has_no_models", False, 409),
        ("provider_not_found", False, 404),
        ("provider_in_use", False, 409),
    ],
)
def test_provider_errors_have_stable_metadata(code, retryable, http_status):
    error = provider_error(code)

    assert error.code == code
    assert error.retryable is retryable
    assert error.http_status == http_status


def test_provider_auth_error_has_approved_public_message():
    error = provider_error("provider_auth_failed")

    assert error.message == "Provider rejected the configured credentials."
    assert str(error) == error.message


def test_provider_draft_hides_api_key_from_repr():
    draft = draft_with_key()

    assert "sentinel-key" not in repr(draft)


def test_provider_draft_api_key_does_not_participate_in_comparison():
    left_marker = "VERILEX_SENTINEL_LEFT"
    right_marker = "VERILEX_SENTINEL_RIGHT"
    left = ProviderDraft(
        name="Compat",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        default_model="test-model",
        auth_mode=AuthMode.API_KEY,
        api_key=left_marker,
    )
    right = ProviderDraft(
        name="Compat",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        default_model="test-model",
        auth_mode=AuthMode.API_KEY,
        api_key=right_marker,
    )

    assert left == right
    assert left_marker not in repr(left)
    assert right_marker not in repr(right)


def test_provider_update_hides_api_key_from_repr():
    change = update_with_key()

    assert "sentinel-key" not in repr(change)


def test_provider_update_api_key_does_not_participate_in_comparison():
    left_marker = "VERILEX_SENTINEL_LEFT"
    right_marker = "VERILEX_SENTINEL_RIGHT"
    left = ProviderUpdate(api_key=left_marker)
    right = ProviderUpdate(api_key=right_marker)

    assert left == right
    assert left_marker not in repr(left)
    assert right_marker not in repr(right)


def test_provider_update_keeps_secret_by_default():
    assert ProviderUpdate().secret_action is SecretAction.KEEP


def test_provider_domain_records_are_immutable():
    draft = ProviderDraft(
        name="Compat",
        kind=ProviderKind.OPENAI_COMPATIBLE,
        base_url="https://models.example.test/v1",
        default_model="test-model",
        auth_mode=AuthMode.NONE,
    )

    with pytest.raises(FrozenInstanceError):
        draft.name = "Changed"
