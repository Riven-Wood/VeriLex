import {EditorDiscussionProvider} from "../components/editor/DiscussionContext";
import {ContractEditorProvider} from "../components/contractEditor/ContractEditorContext";
import { ScheduledTasksView } from "../views/ScheduledTasksView";
import { CaseCalendarView } from "../views/CaseCalendarView";
import { PluginCenterView } from "../views/PluginCenterView";
import { FileRedactionView } from "../views/FileRedactionView";
import { KnowledgeLibraryView } from "../views/KnowledgeLibraryView";
import { LitigationWorkbench } from "../views/LitigationWorkbench";
import { ContractReviewTasks } from "../views/ContractReviewTasks";
import type { StartReviewOptions } from "../runtime/contractReviewClient";
import type { PreviewTarget } from "../components/PreviewRail";
import type { WorkspaceRecord } from "../runtime/contracts";
import { CaseSearchView } from "../views/CaseSearchView";
import type { WorkViewId, ThreadSummary } from "./navigation";
import {
  AgentWorkspaceView,
  type PendingAgentMessage,
} from "../views/AgentWorkspaceView";
import { SettingsView } from "../views/SettingsView";
import { LegalSearchView } from "../views/LegalSearchView";
import type { McpServerRecord } from "../runtime/contracts";
import type { ConfiguredCapabilities, ProviderRecord } from "../runtime/contracts";
import type { ComposerContext } from "../composer/state";
import type { ComposerSubmission } from "../hooks/useAgentChat";
import type { ProviderApi } from "../runtime/providerClient";
import type { UpdateController } from "../updater/contracts";
import type { SettingsCategoryId } from "../settings/categories";
import type { WebAccessApi } from "../runtime/webAccessClient";
import type { WebAccessHandoff } from "../hooks/useWebAccess";
import type { DraftThreadInput } from "../runtime/contracts";
import type { SelectDraftMcp, ThreadMcpApi } from "../hooks/useThreadMcp";

interface WorkspaceRouterProps {
  onOpenCapabilities?: () => void;
  onWorkspaceChanged?: (id:string,revoked:boolean)=>void;
  calendarDate?: string;
  onCalendarEventsChanged?: () => void;
  onOpenScheduledRun: (id: string) => Promise<void>;
  onCloseScheduledRun: () => void;
  litigationThreads: ThreadSummary[];
  litigationFilesCollapsed: boolean;
  onExitLitigationCase: () => void;
  onSelectLitigationThread: (id: string) => void;
  onNewLitigationDiscussion: () => void;
  onRenameLitigationThread?: (id:string,title:string) => Promise<void>;
  litigationWorkspaces: WorkspaceRecord[];
  litigationWorkspaceId: string | null;
  litigationStarting: boolean;
  onSelectLitigationWorkspace: (id: string) => void;
  onChooseLitigationWorkspace: () => void;
  onStartLitigation: (message: string | import("../views/LitigationClient").WorkRequest) => void;
  activeView: WorkViewId;
  hasModel: boolean;
  configured: ConfiguredCapabilities;
  runtimeVersion: string;
  reduceMotion: boolean;
  thread: ThreadSummary | null;
  composerContext: ComposerContext | null;
  providers: readonly ProviderRecord[];
  draft: string;
  onDraftChange: (value: string) => void;
  draftWebEnabled: boolean;
  draftWebError: string | null;
  onDraftWebEnabledChange: (enabled: boolean) => void;
  webAccessApi: WebAccessApi;
  webAccessHandoff: WebAccessHandoff | null;
  onWebAccessHandoffConsumed: (operationId: number) => void;
  pendingAgentMessage: PendingAgentMessage | null;
  draftSendPreparing: boolean;
  onPendingAgentMessageSent: (operationId: number) => void;
  onSendWithoutThread: (submission: ComposerSubmission) => void;
  onOpenSettings: () => void;
  onThreadsRefresh: () => void;
  previewOpen: boolean;
  onPreviewOpenChange: (open: boolean) => void;
  onUserTurnAccepted: (threadId: string, submission: ComposerSubmission) => void;
  onEnsureDraftThread: () => Promise<string | null>;
  onComposerContextChange: (context: ComposerContext) => Promise<void>;
  onReduceMotionChange: (value: boolean) => void;
  providerApi?: ProviderApi;
  runtimeStatusError?: string;
  onProvidersChanged: () => Promise<void>;
  onModelMetadataChanged: () => void;
  modelMetadataRevision: number;
  updates: UpdateController;
  selectedCategory: SettingsCategoryId;
  onSelectCategory: (value: SettingsCategoryId) => void;
  query: string;
  onQueryChange: (value: string) => void;
  mcpApi: ThreadMcpApi;
  mcpDraftThread: DraftThreadInput | null;
  onSelectDraftMcp: SelectDraftMcp;
  onOpenMcpSettings: () => void;
  mcpTaskGeneration: number;
  legalMcpServers: readonly McpServerRecord[];
  legalHistory: { id: string; keyword: string }[];
  legalStarting: boolean;
  onStartLegalSearch: (question: string, serverId: string | null, skills: string[], knowledgeLibraryIds?: string[]) => void;
  onSelectLegalHistory: (threadId: string) => void;
  onDeleteLegalHistory: (threadId: string) => void;
  onOpenMcpSettingsForLegal: () => void;
  onStartContractReview: (file: File, options: StartReviewOptions) => void | Promise<void>;
  onSelectContractReviewThread: (threadId: string) => void;
  onRegisterContractLeaveGuard?: (guard: (() => Promise<boolean>) | null) => void;
}

export function WorkspaceRouter({
  onRegisterContractLeaveGuard,
  onOpenCapabilities, onWorkspaceChanged,
  calendarDate, onCalendarEventsChanged,
  onOpenScheduledRun, onCloseScheduledRun,
  litigationThreads, litigationFilesCollapsed, onExitLitigationCase, onSelectLitigationThread, onNewLitigationDiscussion, onRenameLitigationThread,
  litigationWorkspaces, litigationWorkspaceId, litigationStarting,
  onSelectLitigationWorkspace, onChooseLitigationWorkspace, onStartLitigation,
  activeView,
  hasModel,
  configured,
  runtimeVersion,
  reduceMotion,
  thread,
  composerContext,
  providers,
  draft,
  onDraftChange,
  draftWebEnabled,
  draftWebError,
  onDraftWebEnabledChange,
  webAccessApi,
  webAccessHandoff,
  onWebAccessHandoffConsumed,
  pendingAgentMessage,
  draftSendPreparing,
  onPendingAgentMessageSent,
  onSendWithoutThread,
  onOpenSettings,
  onThreadsRefresh,
  previewOpen,
  onPreviewOpenChange,
  onUserTurnAccepted,
  onEnsureDraftThread,
  onComposerContextChange,
  onReduceMotionChange,
  providerApi,
  runtimeStatusError,
  onProvidersChanged,
  onModelMetadataChanged,
  modelMetadataRevision,
  updates,
  selectedCategory,
  onSelectCategory,
  query,
  onQueryChange,
  mcpApi,
  mcpDraftThread,
  onSelectDraftMcp,
  onOpenMcpSettings,
  mcpTaskGeneration,
  legalMcpServers,
  legalHistory,
  legalStarting,
  onStartLegalSearch,
  onSelectLegalHistory,
  onDeleteLegalHistory,
  onOpenMcpSettingsForLegal,
  onStartContractReview,
  onSelectContractReviewThread,
}: WorkspaceRouterProps) {
  if (activeView === "case-calendar") return <CaseCalendarView initialDate={calendarDate} onEventsChanged={onCalendarEventsChanged} />;
  if (activeView === "plugins") return <PluginCenterView />;
  if (activeView === "redact") return <FileRedactionView />;
  if (activeView === "knowledge") return <KnowledgeLibraryView />;
  const renderWorkspace = (onPreviewTarget?: (target: PreviewTarget) => void, quote = "", clearQuote?: () => void, editingContract = false, ordinaryDiscussion = false) => (
    <AgentWorkspaceView
      ordinaryDiscussion={ordinaryDiscussion}
      hideConversationHeader={activeView === "litigation"}
      disableContractOpinionConversion={editingContract}
      readOnly={activeView === "scheduled"}
      onPreviewTarget={onPreviewTarget}
      hasModel={hasModel}
      thread={(activeView === "agent" && thread?.scene !== "general") || (activeView === "legal-search" && thread?.scene !== "legal-retrieval") || (activeView === "case-search" && thread?.scene !== "case-retrieval") || (activeView === "contract-review" && thread?.scene !== "contract-review") || (activeView === "litigation" && (thread?.scene !== "litigation-work" || thread.workspaceId !== litigationWorkspaceId)) ? null : thread}
      composerContext={activeView === 'litigation' && thread?.scene === 'litigation-work' && thread.providerId
        ? {providerId:thread.providerId, model:thread.model, workspaceId:thread.workspaceId, reasoningEffort:thread.reasoningEffort}
        : activeView === 'litigation' && composerContext ? {...composerContext,workspaceId:litigationWorkspaceId} : composerContext}
      providers={providers}
      workspaceName="本地工作区"
      draft={draft}
      composerQuote={quote}
      onComposerQuoteClear={clearQuote}
      onDraftChange={onDraftChange}
      draftWebEnabled={draftWebEnabled}
      draftWebError={draftWebError}
      onDraftWebEnabledChange={onDraftWebEnabledChange}
      webAccessApi={webAccessApi}
      webAccessHandoff={webAccessHandoff}
      onWebAccessHandoffConsumed={onWebAccessHandoffConsumed}
      pendingAgentMessage={pendingAgentMessage}
      draftSendPreparing={draftSendPreparing}
      onPendingAgentMessageSent={onPendingAgentMessageSent}
      onSendWithoutThread={onSendWithoutThread}
      onOpenSettings={onOpenSettings}
      onThreadsRefresh={onThreadsRefresh}
      previewOpen={previewOpen}
      onPreviewOpenChange={onPreviewOpenChange}
      onUserTurnAccepted={onUserTurnAccepted}
      onEnsureDraftThread={onEnsureDraftThread}
      onComposerContextChange={onComposerContextChange}
      modelMetadataRevision={modelMetadataRevision}
      mcpApi={mcpApi}
      mcpDraftThread={mcpDraftThread}
      onSelectDraftMcp={onSelectDraftMcp}
      onOpenMcpSettings={onOpenMcpSettings}
      mcpTaskGeneration={mcpTaskGeneration}
      variant={activeView === "litigation" ? "litigation" : activeView === "legal-search" || activeView === "case-search" ? "legal" : "chat"}
    />
  );

  if (activeView === "scheduled") return <ScheduledTasksView
    defaultProvider={composerContext?.providerId ?? providers.find(p=>p.enabled)?.id ?? ''}
    defaultModel={composerContext?.model ?? providers.find(p=>p.enabled)?.default_model ?? ''}
    onOpenRun={onOpenScheduledRun} onCloseRun={onCloseScheduledRun}
    runView={thread?.scene === 'scheduled' ? renderWorkspace() : undefined} />;
  const workspace = renderWorkspace();

  if (activeView === "contract-review") {
    return (
      <ContractEditorProvider>
      <ContractReviewTasks
        thread={thread?.scene === "contract-review" ? thread : null}
        hasModel={hasModel}
        renderAssistant={(quote, clearQuote, editingCopy) => renderWorkspace(undefined, quote, clearQuote, editingCopy)}
        onStartReview={onStartContractReview}
        onSelectReview={onSelectContractReviewThread}
        onRegisterLeaveGuard={onRegisterContractLeaveGuard}
      />
      </ContractEditorProvider>
    );
  }

  if (activeView === "legal-search") {
    return (
      <div className="legal-search-shell">
        <LegalSearchView
          activeSearch={thread?.scene === "legal-retrieval" ? { id: thread.id, channel: thread.legalChannel, skills: thread.legalSkills } : undefined}
          mcpServers={legalMcpServers}
          history={legalHistory}
          starting={legalStarting}
          onStartSearch={onStartLegalSearch}
          onSelectHistory={onSelectLegalHistory}
          onDeleteHistory={onDeleteLegalHistory}
          onOpenMcpSettings={onOpenMcpSettingsForLegal}
        />
        {workspace}
      </div>
    );
  }

  if (activeView === "case-search") {
    return <div className="legal-search-shell">
      <CaseSearchView
        activeSearch={thread?.scene === "case-retrieval" ? { id: thread.id, channel: thread.legalChannel === "plugin" ? "plugin" : null, skills: thread.legalSkills } : undefined}
        mcpServers={legalMcpServers} history={legalHistory} starting={legalStarting}
        onStartSearch={onStartLegalSearch} onSelectHistory={onSelectLegalHistory}
        onDeleteHistory={onDeleteLegalHistory} onOpenMcpSettings={onOpenMcpSettingsForLegal}
      />
      {workspace}
    </div>;
  }

  if (activeView === "litigation") {
    const selected = litigationWorkspaces.find(w => w.id === litigationWorkspaceId && w.allow_read);
    const caseThread = thread?.scene === 'litigation-work' && thread.workspaceId === litigationWorkspaceId ? thread : null;
    return <EditorDiscussionProvider key={selected?.id ?? 'no-workspace'} threadId={caseThread?.id??null} workspaceId={selected?.id??''}><LitigationWorkbench key={selected?.id ?? 'no-workspace'} workspace={selected ?? null}
      onSelectWorkspace={onSelectLitigationWorkspace} onChooseWorkspace={onChooseLitigationWorkspace} workspaces={litigationWorkspaces}
      thread={caseThread} threads={selected ? litigationThreads.filter(t => t.workspaceId === selected.id) : []}
      busy={litigationStarting || caseThread?.status === 'running' || caseThread?.status === 'awaiting_approval'}
      filesCollapsed={litigationFilesCollapsed} onBack={onExitLitigationCase}
      onStartWorkflow={onStartLitigation} onStartWork={onStartLitigation} onSelectThread={onSelectLitigationThread}
      onNewDiscussion={onNewLitigationDiscussion} onRenameThread={onRenameLitigationThread} renderAssistant={(preview,ordinary)=>renderWorkspace(preview,"",undefined,false,ordinary)}
      draft={draft} onDraftChange={onDraftChange} hasModel={hasModel} onOpenSettings={onOpenSettings}/></EditorDiscussionProvider>;

  }

  if (activeView === "settings") {
    return (
      <SettingsView
        onOpenCapabilities={onOpenCapabilities}
        onWorkspaceChanged={onWorkspaceChanged}
        configured={configured}
        runtimeVersion={runtimeVersion}
        reduceMotion={reduceMotion}
        onReduceMotionChange={onReduceMotionChange}
        providerApi={providerApi}
        runtimeStatusError={runtimeStatusError}
        onProvidersChanged={onProvidersChanged}
        onModelMetadataChanged={onModelMetadataChanged}
        updates={updates}
        selectedCategory={selectedCategory}
        onSelectCategory={onSelectCategory}
        query={query}
        onQueryChange={onQueryChange}
      />
    );
  }

  return workspace;
}
