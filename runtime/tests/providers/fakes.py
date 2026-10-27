from collections.abc import Callable

from verilex_runtime.providers.errors import provider_error
from verilex_runtime.providers.models import ProbeResult, ProviderDraft
from verilex_runtime.storage.models import ProviderRecord


class MemoryProviderRepository:
    def __init__(self) -> None:
        self.records: dict[str, ProviderRecord] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_next_create = False
        self.fail_next_update = False
        self.fail_next_delete = False
        self.delete_error: Exception | None = None

    def create(self, record: ProviderRecord) -> None:
        self.calls.append(("create", record.id))
        if self.fail_next_create:
            self.fail_next_create = False
            raise RuntimeError("repository create failed")
        self.records[record.id] = record

    def get(self, provider_id: str) -> ProviderRecord | None:
        return self.records.get(provider_id)

    def list(self) -> list[ProviderRecord]:
        return sorted(self.records.values(), key=lambda record: (record.created_at, record.id))

    def update(self, record: ProviderRecord) -> bool:
        self.calls.append(("update", record.id))
        if self.fail_next_update:
            self.fail_next_update = False
            raise RuntimeError("repository update failed")
        if record.id not in self.records:
            return False
        self.records[record.id] = record
        return True

    def delete(self, provider_id: str) -> None:
        self.calls.append(("delete", provider_id))
        if self.delete_error is not None:
            raise self.delete_error
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise RuntimeError("repository delete failed")
        self.records.pop(provider_id, None)

    def count_enabled(self) -> int:
        return sum(record.enabled for record in self.records.values())


class MemorySecretStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.calls: list[tuple[str, str | None]] = []
        self.failures: set[str] = set()

    def ensure_available(self) -> None:
        self.calls.append(("ensure", None))
        if "ensure" in self.failures:
            raise provider_error("secret_store_unavailable")

    def get(self, secret_ref: str) -> str | None:
        self.calls.append(("get", secret_ref))
        if "get" in self.failures:
            raise provider_error("secret_store_unavailable")
        return self.values.get(secret_ref)

    def put(self, secret_ref: str, value: str) -> None:
        self.calls.append(("put", secret_ref))
        if "put" in self.failures:
            raise provider_error("secret_store_write_failed")
        self.values[secret_ref] = value

    def delete(self, secret_ref: str) -> None:
        self.calls.append(("delete", secret_ref))
        if "delete" in self.failures:
            raise provider_error("secret_store_write_failed")
        self.values.pop(secret_ref, None)


class StubProbeRouter:
    def __init__(self) -> None:
        self.result = ProbeResult(models=("model-1",), selected_model="model-1")
        self.error: Exception | None = None
        self.calls: list[tuple[ProviderDraft, str | None]] = []
        self.on_probe: Callable[[], None] | None = None

    async def probe(
        self,
        draft: ProviderDraft,
        api_key: str | None,
    ) -> ProbeResult:
        self.calls.append((draft, api_key))
        if self.on_probe is not None:
            self.on_probe()
        if self.error is not None:
            raise self.error
        return self.result


def build_service_fixture():
    from verilex_runtime.providers.service import ProviderService

    repository = MemoryProviderRepository()
    secrets = MemorySecretStore()
    probes = StubProbeRouter()
    service = ProviderService(
        repository,
        secrets,
        probes,
        id_factory=lambda: "provider-1",
        now=lambda: "2026-08-04T00:00:00Z",
    )
    return service, repository, secrets, probes
