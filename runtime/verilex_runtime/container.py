from verilex_runtime.file_redaction.service import RedactionService
from verilex_runtime.case_calendar.store import CaseCalendarStore
from verilex_runtime.storage.compaction_repository import CompactionRepository
from verilex_runtime.storage.contract_review_repository import ContractReviewRepository
import asyncio
import hashlib
from datetime import datetime, timezone
import logging
import os
import sys
from dataclasses import dataclass

import httpx
from verilex_runtime.knowledge.service import KnowledgeService
from verilex_runtime.knowledge.ima import ImaService
from verilex_runtime.knowledge.turn_sources import TurnSources
from verilex_runtime.tools.knowledge import register_knowledge_tools

from verilex_runtime.scheduling.store import ScheduleStore
from verilex_runtime.scheduling.scheduler import SchedulerService
from verilex_runtime.agent.engine import AgentEngine
from verilex_runtime.context_usage.service import ContextUsageService
from verilex_runtime.attachments.blob_store import AttachmentBlobStore
from verilex_runtime.attachments.service import AttachmentService
from verilex_runtime.artifacts.blob_store import ArtifactBlobStore
from verilex_runtime.artifacts.publisher import ArtifactPublisher
from verilex_runtime.artifacts.service import ArtifactService
from verilex_runtime.documents.v1_service import V1DocumentService
from verilex_runtime.litigation.service import LitigationService
from verilex_runtime.litigation.exporter import LitigationExporter
from verilex_runtime.litigation.tools import register_litigation_tools
from verilex_runtime.contract_review.service import ContractReviewService
from verilex_runtime.contract_review.tools import register_contract_review_tools
from verilex_runtime.providers.base import ChatStream
from verilex_runtime.providers.capabilities import ModelCapabilityService
from verilex_runtime.providers.model_context_windows import ModelContextWindowService
from verilex_runtime.providers.ollama import OllamaProbe
from verilex_runtime.providers.openai_compatible import (
    OpenAICompatibleChatStream,
    OpenAICompatibleProbe,
)
from verilex_runtime.providers.probe_router import ProviderProbeRouter
from verilex_runtime.mcp.client import MCPConnectionPool, MCPManager
from verilex_runtime.mcp.config import (
    LegacyMigrationResult,
    migrate_legacy_mcp_config,
)
from verilex_runtime.mcp.secrets import MCPSecretJournal, MCPSecretManager
from verilex_runtime.mcp.receipts import TestReceiptStore
from verilex_runtime.mcp.service import MCPService
from verilex_runtime.legal.cache import LawCacheService
from verilex_runtime.legal.client import OfficialLawClient
from verilex_runtime.legal.tools import LawTurnBudget, register_law_tools
from verilex_runtime.skills import SkillService
from verilex_runtime.providers.service import ProviderService
from verilex_runtime.secrets.local_store import LocalSecretStore
from verilex_runtime.sessions.thread_service import ThreadService
from verilex_runtime.sessions.turn_service import TurnService
from verilex_runtime.sessions.workspace_service import WorkspaceService
from verilex_runtime.settings import RuntimeSettings
from verilex_runtime.storage.database import Database
from verilex_runtime.storage.context_usage_repository import ContextUsageRepository
from verilex_runtime.storage.attachment_repository import AttachmentRepository
from verilex_runtime.storage.artifact_repository import ArtifactRepository
from verilex_runtime.storage.migrations import run_migrations
from verilex_runtime.storage.mcp_repository import MCPRepository
from verilex_runtime.storage.model_capability_repository import (
    ModelCapabilityRepository,
)
from verilex_runtime.storage.model_context_window_repository import (
    ModelContextWindowRepository,
)
from verilex_runtime.storage.models import ProviderKind, ProviderRecord
from verilex_runtime.storage.law_cache_repository import LawCacheRepository
from verilex_runtime.storage.preferences import PreferenceRepository
from verilex_runtime.storage.provider_repository import ProviderRepository
from verilex_runtime.storage.thread_repository import (
    EventRepository,
    MessageRepository,
    ThreadRepository,
    TurnRepository,
)
from verilex_runtime.storage.tool_repository import (
    ApprovalRepository,
    ToolCallRepository,
)
from verilex_runtime.storage.workspace_repository import WorkspaceRepository
from verilex_runtime.storage.web_access_repository import WebAccessRepository
from verilex_runtime.storage.audit_repository import AuditRepository
from verilex_runtime.storage.skill_state_repository import SkillStateRepository
from verilex_runtime.sessions.audit_recorder import AuditRecorder
from verilex_runtime.tools import (
    ToolRegistry,
    register_background_tools,
    register_document_tools,
    register_file_tools,
    register_skill_tools,
    register_terminal_tools,
)
from verilex_runtime.tools.background import BackgroundTaskManager
from verilex_runtime.terminal import TerminalManager
from verilex_runtime.web.access import WebAccessService
from verilex_runtime.web.budget import WebTurnBudget
from verilex_runtime.web.client import PublicWebClient
from verilex_runtime.web.provider import DuckDuckGoSearchProvider
from verilex_runtime.web.tools import register_web_tools


logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _chat_base_url(record: ProviderRecord) -> str:
    """Normalize a provider base URL for OpenAI-compatible chat.

    Ollama stores its base as the root (e.g. ``http://127.0.0.1:11434``) while
    chat is served from the ``/v1`` OpenAI-compatible endpoint. OpenAI-compatible
    providers already point at ``.../v1``.
    """
    base = record.base_url.rstrip("/")
    if record.kind is ProviderKind.OLLAMA and not base.endswith("/v1"):
        return f"{base}/v1"
    return base


def _build_chat_stream_factory(http_client: httpx.AsyncClient):
    def factory(
        record: ProviderRecord,
        api_key: str | None,
        model: str,
    ) -> ChatStream:
        return OpenAICompatibleChatStream(
            http_client,
            _chat_base_url(record),
            api_key,
            model,
        )

    return factory


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    provider_service: ProviderService
    http_client: httpx.AsyncClient | None = None
    threads_service: ThreadService | None = None
    turn_service: TurnService | None = None
    workspace_service: WorkspaceService | None = None
    agent_engine: AgentEngine | None = None
    audit_recorder: AuditRecorder | None = None
    audit_repo: AuditRepository | None = None
    approvals_repo: ApprovalRepository | None = None
    tool_calls_repo: ToolCallRepository | None = None
    model_capability_service: ModelCapabilityService | None = None
    model_context_window_service: ModelContextWindowService | None = None
    context_usage_service: ContextUsageService | None = None
    attachment_service: AttachmentService | None = None
    attachment_store: AttachmentBlobStore | None = None
    artifact_service: ArtifactService | None = None
    artifact_publisher: ArtifactPublisher | None = None
    document_service: V1DocumentService | None = None
    litigation_service: LitigationService | None = None
    artifact_reconcile_report: object | None = None
    mcp_manager: MCPManager | None = None
    mcp_pool: MCPConnectionPool | None = None
    mcp_receipts: TestReceiptStore | None = None
    mcp_service: MCPService | None = None
    mcp_repository: MCPRepository | None = None
    mcp_secret_manager: MCPSecretManager | None = None
    mcp_legacy_migration: LegacyMigrationResult | None = None
    tool_registry: ToolRegistry | None = None
    skill_service: SkillService | None = None
    preferences: PreferenceRepository | None = None
    background_manager: BackgroundTaskManager | None = None
    terminal_manager: TerminalManager | None = None
    web_access_service: WebAccessService | None = None
    web_turn_budget: WebTurnBudget | None = None
    law_turn_budget: LawTurnBudget | None = None
    law_cache_service: LawCacheService | None = None
    scheduler_service: SchedulerService | None = None
    case_calendar_store: CaseCalendarStore | None = None
    knowledge_service: KnowledgeService | None = None
    redaction_service: RedactionService | None = None
    contract_review_service: ContractReviewService | None = None
    contract_review_repository: ContractReviewRepository | None = None

    async def aclose(self) -> None:
        if self.terminal_manager is not None:
            await self.terminal_manager.shutdown()
        if self.scheduler_service is not None:
            await self.scheduler_service.close()
        if self.http_client is not None and not self.http_client.is_closed:
            await self.http_client.aclose()
        mcp_closers = tuple(
            closer
            for closer in (
                None if self.mcp_service is None else self.mcp_service.aclose(),
                None if self.mcp_pool is None else self.mcp_pool.aclose(),
            )
            if closer is not None
        )
        if mcp_closers:
            await asyncio.gather(*mcp_closers)
        if self.mcp_manager is not None:
            await self.mcp_manager.aclose()
        if self.background_manager is not None:
            await self.background_manager.shutdown_all()


def _skill_audit_event(recorder: AuditRecorder):
    """Bridge SkillService events into the audit log (action and name only)."""

    def on_event(action: str, skill_name: str) -> None:
        recorder.record_skill(
            skill_name=skill_name,
            outcome=action.removeprefix("skill_"),
        )

    return on_event


def build_services(settings: RuntimeSettings) -> RuntimeServices:
    database = Database(settings.state_dir / "verilex.sqlite3")
    run_migrations(database)
    repository = ProviderRepository(database)
    secrets = LocalSecretStore(settings.state_dir / "secrets.json")
    mcp_secret_manager = MCPSecretManager(
        secrets,
        MCPSecretJournal(database),
    )
    mcp_secret_manager.reconcile_pending_operations()
    mcp_repository = MCPRepository(database, secrets=mcp_secret_manager)
    mcp_legacy_migration = migrate_legacy_mcp_config(
        settings.state_dir,
        mcp_repository,
        mcp_secret_manager,
        environment=os.environ,
    )
    http_client = httpx.AsyncClient(
        headers={"Accept-Encoding": "identity"},
        follow_redirects=False,
        trust_env=False,
    )
    probes = ProviderProbeRouter(
        OpenAICompatibleProbe(http_client),
        OllamaProbe(http_client),
    )

    threads = ThreadRepository(database)
    turns = TurnRepository(database)
    messages = MessageRepository(database)
    events = EventRepository(database)
    workspaces = WorkspaceRepository(database)
    tool_calls = ToolCallRepository(database)
    approvals = ApprovalRepository(database)
    audit_repo = AuditRepository(database)
    audit_recorder = AuditRecorder(audit_repo)
    model_context_window_service = ModelContextWindowService(
        repository,
        ModelContextWindowRepository(database),
    )
    model_capability_service = ModelCapabilityService(
        repository,
        ModelCapabilityRepository(database),
        context_windows=model_context_window_service,
        audit=audit_recorder,
    )

    threads_service = ThreadService(
        threads,
        repository,
        turns=turns,
        workspaces=workspaces,
        model_validator=model_capability_service.validate_model,
        model_capability_resolver=model_capability_service.resolve,
        audit=audit_recorder,
    )
    web_access_service = WebAccessService(
        threads_service,
        WebAccessRepository(database),
    )
    attachment_repository = AttachmentRepository(database)
    turn_service = TurnService(
        threads, turns, messages, events, attachments=attachment_repository
    )
    terminal_manager = TerminalManager(workspaces, audit=audit_recorder)
    workspace_service = WorkspaceService(workspaces, audit=audit_recorder,
                                         terminal_active=terminal_manager.has_active)
    attachment_store = AttachmentBlobStore(settings.state_dir / "attachments")
    attachment_service = AttachmentService(
        threads_service,
        attachment_repository,
        attachment_store,
        audit=audit_recorder,
    )
    artifact_repository = ArtifactRepository(
        database,
        cursor_secret=hashlib.sha256(
            (str(settings.state_dir) + "\0artifact-cursor").encode("utf-8")
        ).digest(),
    )
    artifact_store = ArtifactBlobStore(settings.state_dir / "artifacts")
    artifact_publisher = ArtifactPublisher(
        artifact_repository,
        artifact_store,
        now=_utc_now,
    )
    # Cross-filesystem publication is reconciled before Agent/API services are
    # exposed, so callers never observe a stale partial batch after restart.
    artifact_reconcile_report = artifact_publisher.reconcile_all()
    logger.info(
        "Artifact publish recovery completed: completed=%d conflicted=%d cleaned=%d",
        artifact_reconcile_report.completed_batches,
        artifact_reconcile_report.conflicted_batches,
        artifact_reconcile_report.cleaned_batches,
    )
    artifact_service = ArtifactService(
        artifact_repository,
        artifact_store,
        now=_utc_now,
    )
    artifact_service.recover_publish_tool_calls(
        artifact_reconcile_report, tool_calls
    )
    document_service = V1DocumentService(
        artifact_repository,
        artifact_publisher,
        temporary_root=settings.state_dir / "document-v1-temp",
        now=_utc_now,
    )
    knowledge_service = KnowledgeService(database, ImaService(secrets))
    knowledge_sources = TurnSources(knowledge_service)
    tool_registry = ToolRegistry()
    register_knowledge_tools(tool_registry, knowledge_sources)
    register_file_tools(tool_registry)
    if sys.platform == "darwin":
        from verilex_runtime.ppt_plugin.tools import register_ppt_plugin
        register_ppt_plugin(tool_registry, artifact_publisher, now=_utc_now)
    register_terminal_tools(tool_registry)
    if sys.platform == "darwin":
        # The DOCX V1 loop opens on macOS only; Windows keeps the registry
        # without the tool so nothing can surface a fake entry point.
        register_document_tools(tool_registry, document_service)
    web_turn_budget = WebTurnBudget()
    register_web_tools(
        tool_registry,
        DuckDuckGoSearchProvider(use_environment_proxy=True),
        PublicWebClient(use_environment_proxy=True),
        web_turn_budget,
    )
    law_turn_budget = LawTurnBudget()
    law_cache_service = LawCacheService(LawCacheRepository(database))
    litigation_service = LitigationService(workspace_service, law_cache=law_cache_service, law_reads=tool_calls, messages=messages, threads=threads)
    litigation_exporter = LitigationExporter(artifact_publisher,
        temporary_root=settings.state_dir / "litigation-temp", now=_utc_now)
    register_litigation_tools(tool_registry, litigation_service, litigation_exporter)
    register_law_tools(
        tool_registry,
        OfficialLawClient(),
        law_cache_service,
        law_turn_budget,
    )
    contract_review_repository = ContractReviewRepository(database)
    contract_review_service = ContractReviewService(
        threads=threads_service,
        attachments=attachment_service,
        repository=contract_review_repository,
        tool_calls=tool_calls,
        turns=turns,
        now=_utc_now,
    )
    register_contract_review_tools(tool_registry, contract_review_service)
    preferences = PreferenceRepository(database, audit=audit_recorder)
    background_manager = BackgroundTaskManager(database, preferences)
    register_background_tools(tool_registry, background_manager, preferences)
    skills_root = settings.state_dir / "skills"
    skill_service = SkillService(
        skills_root,
        settings.state_dir / "skills-trash",
        SkillStateRepository(database),
        on_event=_skill_audit_event(audit_recorder),
    )
    register_skill_tools(tool_registry, skill_service)
    # Legacy JSON is never connected directly.  It is either migrated into the
    # reviewed SQLite model above or remains safely disabled for a later retry.
    mcp_manager = MCPManager([])
    # The management pool is intentionally cold. Selecting a reviewed server
    # in a later request is the only path that may establish a connection.
    mcp_pool = MCPConnectionPool()
    mcp_receipts = TestReceiptStore()
    mcp_service = MCPService(
        database=database,
        repository=mcp_repository,
        threads=threads_service,
        secrets=mcp_secret_manager,
        receipts=mcp_receipts,
        pool=mcp_pool,
    )
    provider_service = ProviderService(
        repository,
        secrets,
        probes,
        chat_stream_factory=_build_chat_stream_factory(http_client),
        audit=audit_recorder,
    )
    agent_engine = AgentEngine(
        threads_service,
        turn_service,
        provider_service.chat_stream_for,
        tool_registry=tool_registry,
        workspaces=workspace_service,
        tool_calls=tool_calls,
        approvals=approvals,
        audit=audit_recorder,
        attachments=attachment_service,
        model_capabilities=model_capability_service,
        skills_provider=skill_service.effective_skills,
        preferences=preferences,
        mcp_service=mcp_service,
        context_usage=ContextUsageRepository(database),
        compactions=CompactionRepository(database),
        web_access=web_access_service,
        web_budget=web_turn_budget,
        law_budget=law_turn_budget,
        knowledge_sources=knowledge_sources,
        contract_reviews=contract_review_repository,
        contract_review_service=contract_review_service,
        litigation_service=litigation_service,
    )
    contract_review_service.bind_cancel(agent_engine.cancel)
    context_usage_service = ContextUsageService(
        threads_service,
        model_capability_service,
        ContextUsageRepository(database),
        agent_engine.context_segments_for_thread,
    )

    return RuntimeServices(
        case_calendar_store=CaseCalendarStore(database),
        scheduler_service=SchedulerService(ScheduleStore(database), threads_service, agent_engine, audit=audit_recorder, knowledge=knowledge_sources, workspaces=workspace_service),
        redaction_service=RedactionService(database),
        contract_review_service=contract_review_service,
        contract_review_repository=contract_review_repository,
        knowledge_service=knowledge_service,
        provider_service=provider_service,
        model_capability_service=model_capability_service,
        model_context_window_service=model_context_window_service,
        context_usage_service=context_usage_service,
        http_client=http_client,
        threads_service=threads_service,
        turn_service=turn_service,
        workspace_service=workspace_service,
        agent_engine=agent_engine,
        audit_recorder=audit_recorder,
        audit_repo=audit_repo,
        approvals_repo=approvals,
        tool_calls_repo=tool_calls,
        attachment_service=attachment_service,
        attachment_store=attachment_store,
        artifact_service=artifact_service,
        artifact_publisher=artifact_publisher,
        document_service=document_service,
        artifact_reconcile_report=artifact_reconcile_report,
        mcp_manager=mcp_manager,
        mcp_pool=mcp_pool,
        mcp_receipts=mcp_receipts,
        mcp_service=mcp_service,
        mcp_repository=mcp_repository,
        mcp_secret_manager=mcp_secret_manager,
        mcp_legacy_migration=mcp_legacy_migration,
        tool_registry=tool_registry,
        skill_service=skill_service,
        preferences=preferences,
        background_manager=background_manager,
        terminal_manager=terminal_manager,
        web_access_service=web_access_service,
        web_turn_budget=web_turn_budget,
        law_turn_budget=law_turn_budget,
        law_cache_service=law_cache_service,
        litigation_service=litigation_service,
    )
