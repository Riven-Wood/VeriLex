import { searchServerIds } from "../mcp/providerGroups";
import {EditorDiscussionProvider} from "../components/editor/DiscussionContext";
import {ContractEditorProvider} from "../components/contractEditor/ContractEditorContext";
import { ScheduledTasksView } from "../views/ScheduledTasksView";
import { CaseCalendarView } from "../views/CaseCalendarView";
import { PluginCenterView } from "../views/PluginCenterView";
import { FileRedactionView } from "../views/FileRedactionView";
import { KnowledgeLibraryView } from "../views/KnowledgeLibraryView";
import { LitigationWorkbench } from "../views/LitigationWorkbench";
import { ProjectFactsEmpty, ProjectFactsView } from '../views/ProjectFactsView';
import { PreviewRail } from '../components/PreviewRail';
import {useCallback,useEffect,useState,type ReactNode} from 'react';
import { ContractReviewTasks } from "../views/ContractReviewTasks";
import type { StartReviewOptions } from "../runtime/contractReviewClient";
import type { PreviewTarget } from "../components/PreviewRail";
import type { WorkspaceRecord } from "../runtime/contracts";
import { CaseSearchView } from "../views/CaseSearchView";
import type { SearchSettings } from "../views/searchSettings";
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
import type { ConversationSettingsProps } from "../settings/ConversationSettings";
import type { SettingsCategoryId } from "../settings/categories";
import type { WebAccessApi } from "../runtime/webAccessClient";
import type { WebAccessHandoff } from "../hooks/useWebAccess";
import type { DraftThreadInput } from "../runtime/contracts";
import type { SelectDraftMcp, ThreadMcpApi } from "../hooks/useThreadMcp";
import {LitigationMaterialsContext} from '../components/editor/DiscussionContext';

interface WorkspaceRouterProps {
  conversationSettings?: ConversationSettingsProps;
  projectMode?: boolean;
  projectWorkThread?: ThreadSummary | null;
  projectWorkBusy?: boolean;
  projectWorkError?: string | null;
  projectSelectedFiles?: string[];
  onCancelProjectPending?: () => void;
  onStopProjectWork?: () => void;
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
  onSearchSettingsChange: (settings: SearchSettings | null) => void;
  onNewLegalSearch: () => void;
  onSelectLegalHistory: (threadId: string) => void;
  onDeleteLegalHistory: (threadId: string) => void;
  onOpenMcpSettingsForLegal: () => void;
  onStartContractReview: (file: File, options: StartReviewOptions) => void | Promise<void>;
  onSelectContractReviewThread: (threadId: string) => void;
  onRegisterContractLeaveGuard?: (guard: (() => Promise<boolean>) | null) => void;
}

export function WorkspaceRouter({
  projectMode = false,
  projectWorkThread = null,
  projectWorkBusy = false,
  projectWorkError = null,
  projectSelectedFiles = [],
  onCancelProjectPending,
  onStopProjectWork,
  onRegisterContractLeaveGuard,
  onOpenCapabilities, onWorkspaceChanged, conversationSettings,
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
  onSearchSettingsChange,
  onNewLegalSearch,
  onSelectLegalHistory,
  onDeleteLegalHistory,
  onOpenMcpSettingsForLegal,
  onStartContractReview,
  onSelectContractReviewThread,
}: WorkspaceRouterProps) {
  const [searchState, setSearchState] = useState<{view: WorkViewId; settings: SearchSettings} | null>(null);
  const searchSettings = searchState?.view === activeView ? searchState.settings : null;
  const updateSearchSettings = useCallback((settings: SearchSettings) => setSearchState({view: activeView, settings}), [activeView]);
  useEffect(() => { onSearchSettingsChange(searchSettings); }, [searchSettings, onSearchSettingsChange]);
  if (activeView === "case-calendar") return <CaseCalendarView initialDate={calendarDate} onEventsChanged={onCalendarEventsChanged} />;
  if (activeView === "plugins") return <PluginCenterView />;
  if (activeView === "redact") return <FileRedactionView />;
  if (activeView === "knowledge") return <KnowledgeLibraryView />;
  const renderWorkspace = (onPreviewTarget?: (target: PreviewTarget) => void, quote = "", clearQuote?: () => void, editingContract = false, ordinaryDiscussion = false, beforeContractSend?:()=>Promise<void>, renderThread = thread, searchControls?: ReactNode) => (
    <AgentWorkspaceView
      searchControls={searchControls}
      searchKnowledgeIds={searchSettings?.knowledgeIds}
      searchSelection={(activeView === "legal-search" || activeView === "case-search") && searchSettings?.valid ? {
        channel: searchSettings.kind === "online" ? "web" : searchSettings.kind,
        serverIds: searchSettings.kind === "plugin" && searchSettings.serverId ? searchServerIds(legalMcpServers, searchSettings.serverId) : [],
        skills: searchSettings.skills,
      } : undefined}
      ordinaryDiscussion={ordinaryDiscussion}
      hideConversationHeader={activeView === "litigation" && !projectMode}
      disableContractOpinionConversion={editingContract}
      readOnly={activeView === "scheduled"}
      onPreviewTarget={onPreviewTarget}
      hasModel={hasModel}
      thread={(activeView === "agent" && renderThread?.scene !== "general") || (activeView === "legal-search" && renderThread?.scene !== "legal-retrieval") || (activeView === "case-search" && renderThread?.scene !== "case-retrieval") || (activeView === "contract-review" && renderThread?.scene !== "contract-review") || (activeView === "litigation" && (!["general", "litigation-work"].includes(renderThread?.scene ?? "") || renderThread?.workspaceId !== litigationWorkspaceId)) ? null : renderThread}
      composerContext={activeView === 'litigation' && renderThread && ['general','litigation-work'].includes(renderThread.scene) && renderThread.providerId
        ? {providerId:renderThread.providerId, model:renderThread.model, workspaceId:renderThread.workspaceId, reasoningEffort:renderThread.reasoningEffort}
        : activeView === 'litigation' && composerContext ? {...composerContext,workspaceId:litigationWorkspaceId} : composerContext}
      providers={providers}
      workspaceName="本地工作区"
      draft={draft}
      composerQuote={quote}
      beforeContractSend={beforeContractSend}
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
      onSendWithoutThread={(input) => onSendWithoutThread(
        (activeView === "legal-search" || activeView === "case-search") && searchSettings?.kind === "knowledge"
          ? {...input, knowledgeLibraryIds: [...searchSettings.knowledgeIds]} : input)}
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
      variant={activeView === "litigation" && !projectMode ? "litigation" : activeView === "legal-search" || activeView === "case-search" ? "legal" : "chat"}
      searchBlockedReason={(activeView === "legal-search" || activeView === "case-search") && !searchSettings?.valid
        ? "请先选择检索渠道和具体资料。" : ""}
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
        renderAssistant={(quote, clearQuote, editingCopy, beforeSend) => renderWorkspace(undefined, quote, clearQuote, editingCopy, false, beforeSend)}
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
          renderWorkspace={controls => renderWorkspace(undefined, "", undefined, false, false, undefined, thread, controls)}
          draftSearchId={thread?.scene === "legal-retrieval" && thread.isDraft ? thread.id : undefined}
          activeSearch={thread?.scene === "legal-retrieval" && !thread.isDraft ? { id: thread.id, channel: thread.legalChannel, skills: thread.legalSkills } : undefined}
          mcpServers={legalMcpServers}
          history={legalHistory}
          starting={legalStarting}
          onSettingsChange={updateSearchSettings}
          onNewSearch={onNewLegalSearch}
          onSelectHistory={onSelectLegalHistory}
          onDeleteHistory={onDeleteLegalHistory}
          onOpenMcpSettings={onOpenMcpSettingsForLegal}
        />
      </div>
    );
  }

  if (activeView === "case-search") {
    return <div className="legal-search-shell">
      <CaseSearchView
        renderWorkspace={controls => renderWorkspace(undefined, "", undefined, false, false, undefined, thread, controls)}
        draftSearchId={thread?.scene === "case-retrieval" && thread.isDraft ? thread.id : undefined}
        activeSearch={thread?.scene === "case-retrieval" && !thread.isDraft ? { id: thread.id, channel: thread.legalChannel === "plugin" ? "plugin" : null, skills: thread.legalSkills } : undefined}
        mcpServers={legalMcpServers} history={legalHistory} starting={legalStarting}
        onSettingsChange={updateSearchSettings} onNewSearch={onNewLegalSearch} onSelectHistory={onSelectLegalHistory}
        onDeleteHistory={onDeleteLegalHistory} onOpenMcpSettings={onOpenMcpSettingsForLegal}
      />
    </div>;
  }

  if (activeView === "litigation") {
    const selected = litigationWorkspaces.find(w => w.id === litigationWorkspaceId && w.allow_read);
    const caseThread = thread && ['general','litigation-work'].includes(thread.scene) && thread.workspaceId === (selected?.id ?? null) ? thread : null;
    if (projectMode) return <ProjectShell workspace={selected ?? null} selectedFiles={projectSelectedFiles} busy={projectWorkBusy} error={projectWorkThread?.workspaceId===selected?.id ? projectWorkError : null} onCancelPending={onCancelProjectPending} onStop={onStopProjectWork} threadId={caseThread?.id ?? null} onStart={onStartLitigation} renderAssistant={(open)=><LitigationMaterialsContext.Provider value={selected ? {workspaceId:selected.id,paths:projectSelectedFiles} : null}>{renderWorkspace(open,'',undefined,false,true)}</LitigationMaterialsContext.Provider>} />;
    return <EditorDiscussionProvider key={selected?.id ?? 'no-workspace'} threadId={caseThread?.scene === 'litigation-work' ? caseThread.id : null} workspaceId={selected?.id??''}><LitigationWorkbench key={selected?.id ?? 'no-workspace'} workspace={selected ?? null}
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
        conversationSettings={conversationSettings}
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

export function ProjectShell({workspace,selectedFiles=[],busy,error,onCancelPending,onStop,threadId,onStart,renderAssistant}:{workspace:WorkspaceRecord|null;selectedFiles?:string[];busy:boolean;error:string|null;onCancelPending?:()=>void;onStop?:()=>void;threadId:string|null;onStart:(request:import('../views/LitigationClient').WorkRequest)=>void;renderAssistant:(open:(target:PreviewTarget)=>void)=>ReactNode}) {
  const [page,setPage]=useState<'home'|'facts'>('home');
  const [target,setTarget]=useState<PreviewTarget|null>(null);
  useEffect(()=>{setTarget(null);setPage('home');},[workspace?.id]);
  useEffect(()=>{const home=()=>{setTarget(null);setPage('home');};window.addEventListener('verilex:project-home',home);return()=>window.removeEventListener('verilex:project-home',home);},[]);
  useEffect(()=>{const open=(event:Event)=>{const detail=(event as CustomEvent<{workspaceId:string;path:string}>).detail;if(workspace && detail?.workspaceId===workspace.id)setTarget({source:'workspace',workspaceId:workspace.id,path:detail.path});};window.addEventListener('verilex:project-open-file',open);return()=>window.removeEventListener('verilex:project-open-file',open);},[workspace?.id]);
  return <section className="project-shell">
    <header className="project-shell-tabs"><button type="button" aria-pressed={page==='home'} onClick={()=>setPage('home')}>首页</button><button type="button" aria-pressed={page==='facts'} onClick={()=>setPage('facts')}>事实梳理</button></header>
    <div className="project-shell-body">
      <div className="project-shell-main">
        <div className="project-shell-content" hidden={page!=='home'}>{renderAssistant(setTarget)}</div>
        <div className="project-shell-content" hidden={page!=='facts'}>{workspace ? <ProjectFactsView key={workspace.id} workspaceId={workspace.id} selectedFiles={selectedFiles} busy={busy} taskError={error} onCancelPending={onCancelPending} onStop={onStop} canWrite={workspace.allow_write} onStart={request=>onStart({...request,projectWorkspaceId:workspace.id})} onOpenSource={source=>setTarget({source:'workspace',workspaceId:workspace.id,path:source.path,page:source.page??undefined,citation:source})}/> : <ProjectFactsEmpty/>}</div>
      </div>
      {workspace && target && <aside className="project-shell-preview" aria-label="项目文件预览"><PreviewRail embedded open target={target} threadId={threadId} workspaceId={workspace.id} artifactsRevision={0} onClose={()=>setTarget(null)} onOpenWorkspaceFile={(id,path)=>setTarget({source:'workspace',workspaceId:id,path})} onOpenCitation={source=>setTarget({source:'workspace',workspaceId:workspace.id,path:source.path,page:source.page??undefined,citation:source})} onOpenList={()=>setTarget({source:'artifacts-list'})}/></aside>}
    </div>
  </section>;
}
