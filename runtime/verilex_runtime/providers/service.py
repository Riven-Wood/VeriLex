from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
import uuid

from verilex_runtime.providers.base import ChatStream
from verilex_runtime.providers.blocking import run_provider_blocking
from verilex_runtime.providers.errors import provider_error
from verilex_runtime.providers.models import (
    ProbeResult,
    ProviderConnectionResult,
    ProviderDraft,
    ProviderUpdate,
    ProviderView,
    SecretAction,
)
from verilex_runtime.providers.probe_router import ProviderProbeRouter
from verilex_runtime.providers.update_lock import ProviderUpdateLock
from verilex_runtime.providers.urls import normalize_provider_url
from verilex_runtime.secrets.base import SecretStore
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.storage.models import AuthMode, ProviderKind, ProviderRecord
from verilex_runtime.storage.provider_repository import ProviderRepository


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ProviderService:
    def __init__(
        self,
        repository: ProviderRepository,
        secrets: SecretStore,
        probes: ProviderProbeRouter,
        *,
        id_factory: Callable[[], str] | None = None,
        secret_ref_factory: Callable[[str], str] | None = None,
        chat_stream_factory: Callable[[ProviderRecord, str | None, str], ChatStream]
        | None = None,
        now: Callable[[], str] | None = None,
        audit: AuditRecorder | None = None,
    ) -> None:
        self._repository = repository
        self._secrets = secrets
        self._probes = probes
        self._chat_stream_factory = chat_stream_factory
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._secret_ref_factory = secret_ref_factory or (
            lambda provider_id: f"provider:{provider_id}:{uuid.uuid4()}"
        )
        self._now = now or _utc_now
        self._audit = audit
        self._update_lock = ProviderUpdateLock(
            getattr(repository, "update_lock_path", None)
        )

    def list(self) -> list[ProviderView]:
        return [self._view(record) for record in self._repository.list()]

    def get(self, provider_id: str) -> ProviderView:
        return self._view(self._get_record(provider_id))

    async def test(self, draft: ProviderDraft) -> ProbeResult:
        normalized = self._normalize_draft(
            draft,
            allow_ollama_discovery=True,
            allow_model_discovery=True,
        )
        if normalized.auth_mode is AuthMode.API_KEY:
            await run_provider_blocking(self._secrets.ensure_available)
        return await self._probes.probe(normalized, normalized.api_key)

    async def test_saved(self, provider_id: str) -> ProbeResult:
        record = self._get_record(provider_id)
        return await self._probe_record(record)

    async def create(self, draft: ProviderDraft) -> ProviderView:
        return (await self.create_with_probe(draft)).provider

    async def create_with_probe(
        self, draft: ProviderDraft
    ) -> ProviderConnectionResult:
        normalized = self._normalize_draft(draft)
        provider_id = self._id_factory()
        timestamp = self._now()
        secret_ref = (
            f"provider:{provider_id}"
            if normalized.auth_mode is AuthMode.API_KEY
            else None
        )
        record = ProviderRecord(
            id=provider_id,
            name=normalized.name,
            kind=normalized.kind,
            base_url=normalized.base_url,
            default_model=normalized.default_model,
            auth_mode=normalized.auth_mode,
            secret_ref=secret_ref,
            enabled=True,
            created_at=timestamp,
            updated_at=timestamp,
        )

        if normalized.auth_mode is AuthMode.NONE:
            probe = await self._probes.probe(normalized, None)
            self._repository.create(record)
            self._audit_provider(record.name, "created", {"kind": record.kind.value})
            return ProviderConnectionResult(
                provider=self._view_known(record, has_secret=False),
                probe=probe,
            )

        await run_provider_blocking(self._secrets.ensure_available)
        probe = await self._probes.probe(normalized, normalized.api_key)
        await run_provider_blocking(
            self._secrets.put, secret_ref, normalized.api_key
        )
        try:
            self._repository.create(record)
        except Exception as repository_error:
            try:
                await run_provider_blocking(self._secrets.delete, secret_ref)
            except Exception:
                raise provider_error("secret_store_write_failed") from repository_error
            raise
        self._audit_provider(record.name, "created", {"kind": record.kind.value})
        return ProviderConnectionResult(
            provider=self._view_known(record, has_secret=True),
            probe=probe,
        )

    async def update(
        self,
        provider_id: str,
        change: ProviderUpdate,
    ) -> ProviderView:
        async with self._update_lock.hold():
            return await self._update(provider_id, change)

    async def _update(
        self,
        provider_id: str,
        change: ProviderUpdate,
    ) -> ProviderView:
        current = self._get_record(provider_id)
        updated, probe_secret, old_secret = await run_provider_blocking(
            self._prepare_update, current, change
        )
        should_probe = self._should_probe(current, updated, change.secret_action)

        if (
            updated.auth_mode is AuthMode.API_KEY
            and current.auth_mode is AuthMode.NONE
        ):
            await run_provider_blocking(self._secrets.ensure_available)

        if should_probe:
            await self._probes.probe(self._draft_from_record(updated), probe_secret)

        secret_mutation: str | None = None
        if change.secret_action is SecretAction.REPLACE:
            await run_provider_blocking(
                self._secrets.put, updated.secret_ref, change.api_key
            )
            secret_mutation = "replace"

        try:
            record_updated = self._repository.update(updated)
        except Exception as repository_error:
            if secret_mutation is not None:
                await run_provider_blocking(
                    self._compensate_update, updated, repository_error
                )
            raise

        if not record_updated:
            missing_error = provider_error("provider_not_found")
            if secret_mutation == "replace":
                try:
                    await run_provider_blocking(
                        self._secrets.delete, updated.secret_ref
                    )
                    if (
                        current.secret_ref is not None
                        and current.secret_ref != updated.secret_ref
                    ):
                        await run_provider_blocking(
                            self._secrets.delete, current.secret_ref
                        )
                except Exception:
                    raise provider_error("secret_store_write_failed") from missing_error
            raise missing_error

        if (
            secret_mutation == "replace"
            and current.secret_ref is not None
            and current.secret_ref != updated.secret_ref
        ):
            await run_provider_blocking(
                self._secrets.delete, current.secret_ref
            )
        elif (
            change.secret_action is SecretAction.REMOVE
            and current.auth_mode is AuthMode.API_KEY
        ):
            await run_provider_blocking(
                self._secrets.delete, current.secret_ref
            )

        has_secret = (
            updated.auth_mode is AuthMode.API_KEY
            and (
                change.secret_action is SecretAction.REPLACE
                or old_secret is not None
            )
        )
        self._audit_provider(updated.name, "updated", {"kind": updated.kind.value})
        return self._view_known(updated, has_secret=has_secret)

    def delete(self, provider_id: str) -> None:
        record = self._get_record(provider_id)
        if record.auth_mode is AuthMode.NONE:
            self._delete_record(provider_id)
            self._audit_provider(record.name, "deleted", {"kind": record.kind.value})
            return

        old_secret = self._secrets.get(record.secret_ref)
        self._secrets.delete(record.secret_ref)
        try:
            self._delete_record(provider_id)
        except Exception as repository_error:
            try:
                if old_secret is None:
                    self._secrets.delete(record.secret_ref)
                else:
                    self._secrets.put(record.secret_ref, old_secret)
            except Exception:
                raise provider_error("secret_store_write_failed") from repository_error
            raise
        self._audit_provider(record.name, "deleted", {"kind": record.kind.value})

    def _delete_record(self, provider_id: str) -> None:
        self._repository.delete(provider_id)

    def _audit_provider(self, name: str, outcome: str, details: dict) -> None:
        if self._audit is not None:
            self._audit.record_provider(
                provider_name=name, outcome=outcome, details=details
            )

    async def discover_models(self, provider_id: str) -> ProbeResult:
        record = self._get_record(provider_id)
        return await self._probe_record(record)

    def count_enabled(self) -> int:
        return self._repository.count_enabled()

    def chat_stream_for(self, provider_id: str, model: str) -> ChatStream:
        if self._chat_stream_factory is None:
            raise provider_error("provider_protocol_error")
        record = self._get_record(provider_id)
        secret = None
        if record.auth_mode is AuthMode.API_KEY:
            secret = self._secrets.get(record.secret_ref)
        return self._chat_stream_factory(record, secret, model)

    def _get_record(self, provider_id: str) -> ProviderRecord:
        record = self._repository.get(provider_id)
        if record is None:
            raise provider_error("provider_not_found")
        return record

    def _view(self, record: ProviderRecord) -> ProviderView:
        has_secret = (
            record.auth_mode is AuthMode.API_KEY and record.secret_ref is not None
        )
        return self._view_known(record, has_secret=has_secret)

    @staticmethod
    def _view_known(record: ProviderRecord, *, has_secret: bool) -> ProviderView:
        return ProviderView(
            id=record.id,
            name=record.name,
            kind=record.kind,
            base_url=record.base_url,
            default_model=record.default_model,
            auth_mode=record.auth_mode,
            has_secret=has_secret,
            enabled=record.enabled,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def _probe_record(self, record: ProviderRecord) -> ProbeResult:
        secret = None
        if record.auth_mode is AuthMode.API_KEY:
            secret = await run_provider_blocking(
                self._secrets.get, record.secret_ref
            )
        return await self._probes.probe(self._draft_from_record(record), secret)

    @staticmethod
    def _draft_from_record(record: ProviderRecord) -> ProviderDraft:
        return ProviderDraft(
            name=record.name,
            kind=record.kind,
            base_url=record.base_url,
            default_model=record.default_model,
            auth_mode=record.auth_mode,
        )

    @staticmethod
    def _required_text(value: str, field_name: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be empty")
        return normalized

    def _normalize_draft(
        self,
        draft: ProviderDraft,
        *,
        allow_ollama_discovery: bool = False,
        allow_model_discovery: bool = False,
    ) -> ProviderDraft:
        name = self._required_text(draft.name, "name")
        if (
            allow_ollama_discovery
            and draft.kind is ProviderKind.OLLAMA
            and not draft.default_model.strip()
        ):
            model = ""
        elif (
            allow_model_discovery
            and draft.kind is ProviderKind.OPENAI_COMPATIBLE
            and not draft.default_model.strip()
        ):
            model = ""
        else:
            model = self._required_text(draft.default_model, "default_model")
        base_url = normalize_provider_url(draft.kind, draft.base_url)

        if draft.kind is ProviderKind.OLLAMA:
            if draft.api_key is not None:
                raise ValueError("Ollama does not accept an API key")
            auth_mode = AuthMode.NONE
        else:
            auth_mode = draft.auth_mode
            if auth_mode is AuthMode.API_KEY:
                if draft.api_key is None or not draft.api_key.strip():
                    raise ValueError("api_key must not be empty")
            elif draft.api_key is not None:
                raise ValueError("no-key mode does not accept an API key")

        return replace(
            draft,
            name=name,
            base_url=base_url,
            default_model=model,
            auth_mode=auth_mode,
        )

    def _prepare_update(
        self,
        current: ProviderRecord,
        change: ProviderUpdate,
    ) -> tuple[ProviderRecord, str | None, str | None]:
        name = (
            current.name
            if change.name is None
            else self._required_text(change.name, "name")
        )
        model = (
            current.default_model
            if change.default_model is None
            else self._required_text(change.default_model, "default_model")
        )
        base_url = (
            current.base_url
            if change.base_url is None
            else normalize_provider_url(current.kind, change.base_url)
        )
        auth_mode = change.auth_mode or current.auth_mode

        if current.kind is ProviderKind.OLLAMA:
            if change.api_key is not None or change.secret_action is not SecretAction.KEEP:
                raise ValueError("Ollama does not accept secret changes")
            auth_mode = AuthMode.NONE
        elif change.secret_action is SecretAction.KEEP:
            if change.api_key is not None:
                raise ValueError("keeping a secret does not accept an API key")
            if auth_mode is not current.auth_mode:
                raise ValueError("auth mode changes require an explicit secret action")
        elif change.secret_action is SecretAction.REPLACE:
            if change.api_key is None or not change.api_key.strip():
                raise ValueError("api_key must not be empty")
            if change.auth_mode is AuthMode.NONE:
                raise ValueError("a replacement key requires api_key auth mode")
            auth_mode = AuthMode.API_KEY
        else:
            if change.api_key is not None:
                raise ValueError("removing a secret does not accept an API key")
            if change.auth_mode is AuthMode.API_KEY:
                raise ValueError("removing a secret requires no-key auth mode")
            auth_mode = AuthMode.NONE

        old_secret = None
        if current.auth_mode is AuthMode.API_KEY:
            old_secret = self._secrets.get(current.secret_ref)

        if auth_mode is AuthMode.API_KEY:
            secret_ref = (
                self._secret_ref_factory(current.id)
                if change.secret_action is SecretAction.REPLACE
                else current.secret_ref or f"provider:{current.id}"
            )
        else:
            secret_ref = None
        probe_secret = (
            change.api_key
            if change.secret_action is SecretAction.REPLACE
            else old_secret if auth_mode is AuthMode.API_KEY else None
        )
        updated = replace(
            current,
            name=name,
            base_url=base_url,
            default_model=model,
            auth_mode=auth_mode,
            secret_ref=secret_ref,
            enabled=current.enabled if change.enabled is None else change.enabled,
            updated_at=self._now(),
        )
        return updated, probe_secret, old_secret

    @staticmethod
    def _should_probe(
        current: ProviderRecord,
        updated: ProviderRecord,
        secret_action: SecretAction,
    ) -> bool:
        return (
            current.base_url != updated.base_url
            or current.default_model != updated.default_model
            or current.auth_mode is not updated.auth_mode
            or secret_action is not SecretAction.KEEP
        )

    def _compensate_update(
        self,
        updated: ProviderRecord,
        repository_error: Exception,
    ) -> None:
        try:
            self._secrets.delete(updated.secret_ref)
        except Exception:
            raise provider_error("secret_store_write_failed") from repository_error
