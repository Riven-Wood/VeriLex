import pytest

from verilex_runtime.providers.errors import ProviderDomainError
from verilex_runtime.providers.urls import normalize_provider_url
from verilex_runtime.storage.models import ProviderKind


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:8000/v1",
        "http://127.42.0.9:8000/v1",
        "http://[::1]:11434",
        "https://models.example.com/v1",
    ],
)
def test_openai_compatible_accepts_https_or_loopback_http(url):
    assert normalize_provider_url(ProviderKind.OPENAI_COMPATIBLE, url)


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example.com/v1",
        "ftp://models.example.com/v1",
        "https://user:secret@models.example.com/v1",
        "https://models.example.com/v1?api_key=secret",
        "https://models.example.com:99999/v1",
    ],
)
def test_rejects_unsafe_provider_urls(url):
    with pytest.raises(ProviderDomainError):
        normalize_provider_url(ProviderKind.OPENAI_COMPATIBLE, url)


@pytest.mark.parametrize(
    "url",
    [
        "https://models.example.com/v1?",
        "https://models.example.com/v1#",
        " https://models.example.com/v1",
        "https://models.example.com/v1\n",
        "https://models.example.com:",
    ],
)
def test_rejects_ambiguous_provider_urls(url):
    with pytest.raises(ProviderDomainError) as captured:
        normalize_provider_url(ProviderKind.OPENAI_COMPATIBLE, url)

    assert captured.value.code == "invalid_provider_url"


def test_ollama_rejects_plaintext_non_loopback():
    with pytest.raises(ProviderDomainError) as captured:
        normalize_provider_url(
            ProviderKind.OLLAMA, "http://192.168.1.10:11434"
        )
    assert captured.value.code == "insecure_remote_url"


def test_plaintext_localhost_is_pinned_to_a_numeric_loopback_address():
    normalized = normalize_provider_url(
        ProviderKind.OPENAI_COMPATIBLE,
        "http://localhost:11434/v1/",
    )

    assert normalized == "http://127.0.0.1:11434/v1"
