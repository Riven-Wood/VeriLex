import {useLitigationMaterials} from "../components/editor/DiscussionContext";
import { BROWSER_PANEL_EVENT, openBrowserPanel } from "../platform/browserPanel";
import { contractOpinionConversion } from "./contractOpinionConversion";
import { exportFilename, exportMarkdown } from "../timeline/exportMarkdown";
import { saveConversation } from "../platform/saveFileDialog";
import { UserMessageEdit } from "../components/timeline/UserMessageEdit";
import { MessageActions } from "../components/timeline/MessageActions";
import { memo, useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type SetStateAction } from "react";

import { AgentComposer } from "../components/AgentComposer";
import { McpThreadPicker } from "../components/McpThreadPicker";
import { ComposerContextMenus } from "../components/ComposerContextMenus";
import { MarkdownBody } from "../components/timeline/MarkdownBody";
import { SourcesBlock } from "../components/timeline/SourcesBlock";
import { ReasoningDisclosure } from "../components/timeline/ReasoningDisclosure";
import { TimelineNotice } from "../components/timeline/TimelineNotice";
import { PreviewRail } from "../components/PreviewRail";
import type { PreviewTarget } from "../components/PreviewRail";
import { TimelineAttachments } from "../components/timeline/TimelineAttachments";
import type { ToolItem } from "../timeline/items";
import { ApprovalCard } from "../components/timeline/ApprovalCard";
import { ToolCallCard } from "../components/timeline/ToolCallCard";
import { LegalStatuteCard } from "../components/LegalStatuteCard";
import { lawResultSummary } from "../timeline/lawResults";
import { LegalEscapeHatch } from "../components/LegalEscapeHatch";
import { DeliverablesCard } from "../components/timeline/DeliverablesCard";
import { TurnGroup } from "../components/timeline/TurnGroup";
import type { ThreadArtifactRecord } from "../runtime/contracts";
import { fetchAttachmentContent } from "../runtime/api";
import { BackgroundTaskCard } from "../components/timeline/BackgroundTaskCard";
import { WebAccessOffer } from "../components/timeline/WebAccessOffer";
import { useAgentChat, type ApprovalMode, type ComposerSubmission } from "../hooks/useAgentChat";
import { useBackgroundTasks } from "../hooks/useBackgroundTasks";
import { useComposerAttachments } from "../hooks/useComposerAttachments";
import { useContextUsage } from "../hooks/useContextUsage";
import {
  useWebAccess,
  type WebAccessHandoff,
} from "../hooks/useWebAccess";
import {
  listBackgroundTasks,
  listModelCapabilities,
  listThreadArtifacts,
  listWorkspaces,
} from "../runtime/api";
import type {
  BackgroundTaskRecord,
  DraftThreadInput,
  ProviderRecord,
  ReasoningEffort,
  WorkspaceRecord,
} from "../runtime/contracts";
import {
  runtimeThreadMcpApi,
  useThreadMcp,
  type SelectDraftMcp,
  type ThreadMcpApi,
} from "../hooks/useThreadMcp";
import type { ComposerContext } from "../composer/state";
import {
  editQueue,
  emptyComposerQueue,
  nextQueueAction,
  queueSubmission,
  type ComposerQueueMode,
} from "../composer/queue";
import type { ComposerAttachment } from "../hooks/useComposerAttachments";
import { formatRelativeTime } from "../app/navigation";
import type { ThreadSummary } from "../app/navigation";
import type {
  NoticeItem,
  TimelineAttachment,
  TimelineItem,
} from "../timeline/items";
import { groupTurns } from "../timeline/turnGroups";
import {
  runtimeWebAccessApi,
  type WebAccessApi,
} from "../runtime/webAccessClient";

export interface PendingAgentMessage {
  threadId: string;
  operationId: number;
  submission: ComposerSubmission;
}

interface AgentWorkspaceViewProps {
  searchControls?: ReactNode;
  searchSelection?: ComposerSubmission["searchSelection"];
  searchKnowledgeIds?: string[];
  hasModel: boolean;
  thread: ThreadSummary | null;
  workspaceName: string;
  /** "legal" embeds this view inside the legal search page: no conversation
   * header, no working process — results only (product decision
   * 2026-09-05). */
  readOnly?: boolean;
  disableContractOpinionConversion?: boolean;
  variant?: "chat" | "legal" | "litigation";
  searchBlockedReason?: string;
  ordinaryDiscussion?: boolean;
  hideConversationHeader?: boolean;
  onPreviewTarget?: (target: PreviewTarget) => void;
  composerContext?: ComposerContext | null;
  providers?: readonly ProviderRecord[];
  draft: string;
  composerQuote?: string;
  onComposerQuoteClear?: () => void;
  beforeContractSend?: () => Promise<void>;
  onDraftChange: (value: string) => void;
  draftWebEnabled?: boolean;
  draftWebError?: string | null;
  onDraftWebEnabledChange?: (enabled: boolean) => void;
  webAccessApi?: WebAccessApi;
  webAccessHandoff?: WebAccessHandoff | null;
  onWebAccessHandoffConsumed?: (operationId: number) => void;
  pendingAgentMessage: PendingAgentMessage | null;
  draftSendPreparing?: boolean;
  onPendingAgentMessageSent: (operationId: number) => void;
  onSendWithoutThread: (submission: ComposerSubmission) => void;
  onOpenSettings: () => void;
  onThreadsRefresh: () => void;
  onUserTurnAccepted: (threadId: string, submission: ComposerSubmission) => void;
  onEnsureDraftThread?: () => Promise<string | null>;
  onComposerContextChange?: (context: ComposerContext) => Promise<void>;
  modelMetadataRevision?: number;
  mcpApi?: ThreadMcpApi;
  mcpDraftThread?: DraftThreadInput | null;
  onSelectDraftMcp?: SelectDraftMcp;
  onOpenMcpSettings?: () => void;
  mcpTaskGeneration?: number;
  /** Controlled right-rail visibility when the App toolbar owns the toggle. */
  previewOpen?: boolean;
  onPreviewOpenChange?: (open: boolean) => void;
}

// Distance from the bottom (px) within which the view keeps following the
// stream; scrolling further up pauses following until "回到最新" is used.
const FOLLOW_THRESHOLD_PX = 48;
const RECOVERY_BLOCKED_MESSAGE = "输入框已有内容，请先处理后再恢复。";

/** Which workspace file a successful tool call makes previewable, if any. */
function artifactPreviewPath(item: Extract<TimelineItem, { kind: "tool" }>): string | null {
  const args = item.argumentsSummary;
  if (typeof args !== "object" || args === null) return null;
  if (item.name === "copy_path" || item.name === "move_path") {
    const destination = (args as Record<string, unknown>).destination;
    return typeof destination === "string" && destination.length > 0
      ? destination
      : null;
  }
  if (
    item.name === "write_file" ||
    item.name === "apply_file_patch" ||
    item.name === "read_file"
  ) {
    const path = (args as Record<string, unknown>).path;
    return typeof path === "string" && path.length > 0 ? path : null;
  }
  return null;
}

// Item components are memoized: the reducer preserves object identity for
// unchanged items, so only the item that actually changed re-renders per
// flush instead of the whole timeline.
const UserMessage = memo(function UserMessage({
  item,
  loadAttachmentContent,
  onOpenAttachment, onResend, onBeginEdit, onCancelEdit, running = false, editDisabled = false,
}: {
  item: Extract<TimelineItem, { kind: "user" }>;
  onResend?: (text: string) => boolean;
  onBeginEdit?: () => boolean;
  onCancelEdit?: () => void;
  running?: boolean; editDisabled?: boolean;
  loadAttachmentContent: (attachment: TimelineAttachment) => Promise<Blob>;
  onOpenAttachment: (attachment: TimelineAttachment) => void;
}) {
  return (
    <div className="message message-user">
      <div className="message-role">你</div>
      <div className="message-text">{item.text}</div>
      {onResend && <UserMessageEdit text={item.text} createdAt={item.createdAt} hasAttachments={!!(item.attachments?.length || item.attachmentCount || item.attachmentNames?.length)} running={running} disabled={editDisabled} onBegin={onBeginEdit!} onCancel={onCancelEdit!} onSend={onResend} />}
      {item.attachments && item.attachments.length > 0 ? (
        <TimelineAttachments
          attachments={item.attachments}
          loadContent={loadAttachmentContent}
          onOpen={onOpenAttachment}
        />
      ) : (item.attachmentNames?.length || item.attachmentCount) ? (
        <div className="message-attachments">
          已附加：
          {item.attachmentNames?.length
            ? item.attachmentNames.join("、")
            : `${item.attachmentCount} 个文件`}
        </div>
      ) : null}
    </div>
  );
});

const AssistantMessage = memo(function AssistantMessage({
  item,
  offerDisabled,
  onEnableWebAccess,
  onSendWebFollowUp,
}: {
  item: Extract<TimelineItem, { kind: "assistant" }>;
  offerDisabled: boolean;
  onEnableWebAccess: () => Promise<boolean>;
  onSendWebFollowUp: (text: string) => void;
}) {
  return (
    <div className="message message-assistant">
      <div className="message-avatar" aria-hidden="true">
        <img src="/verilex-logo.png" alt="" />
      </div>
      <div className="message-role">VeriLex</div>
      <div className="message-body">
        {item.segments.map((segment, index) =>
          segment.type === "reasoning" ? (
            <ReasoningDisclosure key={`reasoning-${index}`} segment={segment} />
          ) : (
            <div key={`text-${index}`} className="message-text">
              <MarkdownBody
                text={segment.text}
                sources={item.sources}
                streaming={item.isStreaming}
              />
              {segment.isStreaming && (
                <span className="streaming-cursor" aria-hidden="true">▋</span>
              )}
            </div>
          ),
        )}
        <SourcesBlock
          sources={item.sources ?? []}
          citationWarning={item.citationWarning}
        />
        {item.webAccessOffer ? (
          <WebAccessOffer
            disabled={offerDisabled}
            onEnable={onEnableWebAccess}
            onSend={onSendWebFollowUp}
          />
        ) : null}
      </div>
    </div>
  );
});

export function AgentWorkspaceView({
  searchControls,
  searchSelection,
  searchKnowledgeIds,
  hasModel,
  thread,
  workspaceName,
  composerContext = null,
  providers = [],
  draft,
  composerQuote = "",
  onComposerQuoteClear,
  beforeContractSend,
  onDraftChange,
  draftWebEnabled = false,
  draftWebError = null,
  onDraftWebEnabledChange = () => undefined,
  webAccessApi = runtimeWebAccessApi,
  webAccessHandoff = null,
  onWebAccessHandoffConsumed = () => undefined,
  pendingAgentMessage,
  draftSendPreparing = false,
  onPendingAgentMessageSent,
  onSendWithoutThread,
  onOpenSettings,
  onThreadsRefresh,
  onUserTurnAccepted,
  onEnsureDraftThread = async () => null,
  onComposerContextChange = async () => undefined,
  modelMetadataRevision = 0,
  mcpApi = runtimeThreadMcpApi,
  mcpDraftThread = null,
  onSelectDraftMcp,
  onOpenMcpSettings = onOpenSettings,
  mcpTaskGeneration = 0,
  previewOpen: previewOpenProp,
  onPreviewOpenChange,

  onPreviewTarget,
  readOnly = false,
  searchBlockedReason = "",
  disableContractOpinionConversion = false,
  ordinaryDiscussion = false, hideConversationHeader = false,
  variant = "chat",}: AgentWorkspaceViewProps) {
  const listRef = useRef<HTMLDivElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const followRef = useRef(true);
  const approvalPresenceRef = useRef<{threadId: string; pending: boolean} | null>(null);
  const [showJump, setShowJump] = useState(false);
  const [contextMenu, setContextMenu] = useState<"model" | "workspace" | null>(null);
  const [previewTarget, setPreviewTarget] = useState<PreviewTarget | null>(null);
  const [previewOpenState, setPreviewOpenState] = useState(false);
  // Controlled when the App toolbar owns the toggle (product layout decision
  // 2026-08-31); uncontrolled otherwise (direct component usage in tests).
  const previewOpen = previewOpenProp ?? previewOpenState;
  const previewOpenRef = useRef(previewOpen);
  previewOpenRef.current = previewOpen;
  const setPreviewOpen = useCallback(
    (value: SetStateAction<boolean>) => {
      const next =
        typeof value === "function"
          ? (value as (open: boolean) => boolean)(previewOpenRef.current)
          : value;
      if (onPreviewOpenChange) onPreviewOpenChange(next);
      else setPreviewOpenState(next);
    },
    [onPreviewOpenChange],
  );
  // The old toolbar-less toggle opened the rail with the artifacts list and
  // cleared the target on close; keep that semantics when the visibility
  // boolean is owned externally and opened without a specific target.
  useEffect(() => {
    if (previewOpen) {
      setPreviewTarget((current) => current ?? { source: "artifacts-list" });
    } else {
      setPreviewTarget(null);
    }
  }, [previewOpen]);
  useEffect(() => {
    if (variant !== 'chat' || readOnly || onPreviewTarget) return;
    const openBrowser = () => { setPreviewTarget(current => current ?? {source: 'artifacts-list'}); setPreviewOpen(true); };
    const followLink = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = (event.target as Element).closest?.('a[href]');
      if (!(link instanceof HTMLAnchorElement) || !link.closest('.markdown-body, .web-search-results') || !/^https?:/.test(link.href)) return;
      event.preventDefault(); openBrowserPanel(link.href);
    };
    window.addEventListener(BROWSER_PANEL_EVENT, openBrowser);
    document.addEventListener('click', followLink);
    return () => { window.removeEventListener(BROWSER_PANEL_EVENT, openBrowser); document.removeEventListener('click', followLink); };
  }, [variant, readOnly, onPreviewTarget, setPreviewOpen]);
  const [exportStatus, setExportStatus] = useState("");
  const [exporting, setExporting] = useState(false);
  const [contextError, setContextError] = useState("");
  const [recoveryError, setRecoveryError] = useState("");
  const [focusRequestToken, setFocusRequestToken] = useState(0);
  const [filePickerRequestToken, setFilePickerRequestToken] = useState(0);
  const [pluginFocusRequestToken, setPluginFocusRequestToken] = useState(0);
  const [knowledgeIds, setKnowledgeIds] = useState<string[]>([]);
  useEffect(() => setKnowledgeIds([]), [thread?.id, variant, mcpTaskGeneration]);
  useEffect(() => {
    if (pendingAgentMessage?.threadId === thread?.id) {
      setKnowledgeIds([...(pendingAgentMessage?.submission.knowledgeLibraryIds ?? [])]);
    }
  }, [thread?.id, variant, pendingAgentMessage?.operationId]);
  const [showPlugins, setShowPlugins] = useState(false);
  const pluginScopeRef = useRef(`${thread?.backendId ?? "draft"}:${mcpTaskGeneration}`);
  const [approvalMode, setApprovalMode] = useState<ApprovalMode>("ask");
  const [workspaceRecords, setWorkspaceRecords] = useState<WorkspaceRecord[]>([]);
  const [backgroundTasks, setBackgroundTasks] = useState<BackgroundTaskRecord[]>([]);
  const [stoppingTaskId, setStoppingTaskId] = useState<string | null>(null);
  const [visionSupported, setVisionSupported] = useState<boolean | undefined>(undefined);
  const [supportedReasoningEfforts, setSupportedReasoningEfforts] = useState<
    Exclude<ReasoningEffort, "auto">[] | null
  >(null);
  const lastPendingRef = useRef<string | null>(null);
  const previousThreadIdRef = useRef<string | null>(thread?.backendId ?? null);
  const deniedWebAccessRef = useRef<() => void>(() => undefined);
  const attachmentsEnabled = composerContext !== null;
  const threadMcp = useThreadMcp({
    threadId: thread?.backendId ?? null,
    draftThread: mcpDraftThread,
    draftGeneration: mcpTaskGeneration,
    api: mcpApi,
    selectDraftMcp: onSelectDraftMcp,
  });
  const closePlugins = useCallback(() => {
    setShowPlugins(false);
    setPluginFocusRequestToken((value) => value + 1);
  }, []);
  useEffect(() => {
    const scope = `${thread?.backendId ?? "draft"}:${mcpTaskGeneration}`;
    if (pluginScopeRef.current !== scope && showPlugins) closePlugins();
    pluginScopeRef.current = scope;
  }, [closePlugins, mcpTaskGeneration, showPlugins, thread?.backendId]);
  const attachments = useComposerAttachments({
    threadId: thread?.backendId ?? null,
    enabled: attachmentsEnabled,
    ensureDraftThread: onEnsureDraftThread,
  });
  const currentThreadId = thread?.backendId ?? null;
  const [threadArtifactRecords, setThreadArtifactRecords] = useState<
    ThreadArtifactRecord[]
  >([]);
  const [editingTurnId, setEditingTurnId] = useState<string | null>(null);
  const editLockRef = useRef<string | null>(null);
  const contractSendLock = useRef(false);
  const [queuesByThread, setQueuesByThread] = useState<Record<string, ReturnType<typeof emptyComposerQueue>>>({});
  const queuedAttachmentsByThreadRef = useRef(new Map<string, ComposerAttachment[]>());
  const inFlightQueuedAttachmentsByThreadRef = useRef(new Map<string, ComposerAttachment[]>());
  const queue = currentThreadId
    ? queuesByThread[currentThreadId] ?? emptyComposerQueue()
    : emptyComposerQueue();
  const currentThreadIdRef = useRef(currentThreadId);
  const draftRef = useRef(draft);
  const attachmentCountRef = useRef(attachments.attachments.length);
  const queueSubmissionRef = useRef(queue.submission);
  currentThreadIdRef.current = currentThreadId;
  draftRef.current = draft;
  attachmentCountRef.current = attachments.attachments.length;
  queueSubmissionRef.current = queue.submission;
  useEffect(() => {
    if (
      recoveryError === RECOVERY_BLOCKED_MESSAGE &&
      draft.trim() === "" &&
      attachments.attachments.length === 0 &&
      queue.submission === null
    ) {
      setRecoveryError("");
    }
  }, [attachments.attachments.length, draft, queue.submission, recoveryError]);
  const {
    items,
    isRunning,
    isThinking,
    error,
    connectionReady,
    runningTurnId,
    terminalToken,
    pendingAckToken,
    historyLoadedThreadId,
    historyToken,
    historyError,
    historyLoading,
    retryHistory,
    approvalSubmissions,
    turnTimes,
    sendMessage,
    resolveApproval,
    stop,
  } = useAgentChat(
    thread?.backendId ?? null,
    {
      onUserTurnAccepted: (_turnId, submission, acceptedThreadId) => {
        if (thread?.backendId === acceptedThreadId) {
          if (submission.replaceTurnId) { editLockRef.current = null; setEditingTurnId(null); }
          if (!submission.preserveDraft && draftRef.current === submission.text) onDraftChange("");
          if (composerQuote && submission.text === `引用内容：\n${composerQuote}\n\n问题：${draftRef.current}`) {
            onDraftChange("");
            onComposerQuoteClear?.();
          }
          attachments.consumeSubmitted(submission.attachmentUploadIds);
        }
        inFlightQueuedAttachmentsByThreadRef.current.delete(acceptedThreadId);
        onUserTurnAccepted(acceptedThreadId, submission);
      },
      onSubmissionRejected: (submission, _code, rejectedThreadId) => {
        const inFlight = inFlightQueuedAttachmentsByThreadRef.current
          .get(rejectedThreadId) ?? [];
        inFlightQueuedAttachmentsByThreadRef.current.delete(rejectedThreadId);
        if (thread?.backendId !== rejectedThreadId) {
          void attachments.discard(inFlight);
          return;
        }
        // A hidden-draft submission may be rejected after the user has
        // already started the next prompt. Restore the rejected text only
        // when its original handoff left the composer genuinely empty.
        if (!submission.preserveDraft && draftRef.current === "") onDraftChange(submission.text);
        if (inFlight.length > 0) {
          attachments.restore(inFlight);
        }
      },
      onTurnSettled: () => {
        // The terminal event already refreshes the parent; avoid a second
        // refresh when the same render clears its remaining approval cards.
        if (variant === "litigation" && currentThreadId) {
          approvalPresenceRef.current = {threadId: currentThreadId, pending: false};
        }
        onThreadsRefresh();
      },
      onWebAccessDenied: () => deniedWebAccessRef.current(),
    },
  );
  // Deliverables for the tail card: refreshed whenever the thread changes or
  // a turn settles (terminalToken bumps on completed/failed/cancelled).
  useEffect(() => {
    if (!currentThreadId) {
      setThreadArtifactRecords([]);
      return;
    }
    let cancelled = false;
    listThreadArtifacts(currentThreadId)
      .then((records) => {
        if (!cancelled) {
          setThreadArtifactRecords(Array.isArray(records) ? records : []);
        }
      })
      .catch(() => {
        if (!cancelled) setThreadArtifactRecords([]);
      });
    return () => {
      cancelled = true;
    };
  }, [currentThreadId, terminalToken]);
  const webAccess = useWebAccess({
    threadId: currentThreadId,
    isRunning,
    api: webAccessApi,
  });
  deniedWebAccessRef.current = webAccess.disableAfterDenied;
  useEffect(() => {
    if (
      !webAccessHandoff ||
      webAccessHandoff.threadId !== currentThreadId
    ) return;
    webAccess.acceptRecord(
      webAccessHandoff.threadId,
      webAccessHandoff.record,
    );
    onWebAccessHandoffConsumed(webAccessHandoff.operationId);
  }, [
    currentThreadId,
    onWebAccessHandoffConsumed,
    webAccess.acceptRecord,
    webAccessHandoff,
  ]);
  const backgroundThreadId = thread?.backendId ?? null;
  const backgroundToolState = items.flatMap((item) =>
    item.kind === "tool" && item.name === "run_background_command"
      ? [`${item.id}:${item.status}`] : [],
  ).join(",");
  // One instruction, one bubble: non-user items fold into per-turn groups.
  const turnEntries = useMemo(
    () => groupTurns(Array.isArray(items) ? items : [], runningTurnId),
    [items, runningTurnId],
  );
  // Deliverables attach to the turn that produced them, not the list tail.
  const artifactsByTurn = useMemo(() => {
    const byTurn: Record<string, ThreadArtifactRecord[]> = {};
    for (const record of Array.isArray(threadArtifactRecords)
      ? threadArtifactRecords
      : []) {
      if (record.kind !== "artifact" || !record.turn_id) continue;
      (byTurn[record.turn_id] ??= []).push(record);
    }
    return byTurn;
  }, [threadArtifactRecords]);
  useEffect(() => {
    if (!backgroundThreadId) {
      setBackgroundTasks([]);
      return undefined;
    }
    const controller = new AbortController();
    let cancelled = false;
    listBackgroundTasks(backgroundThreadId, controller.signal)
      .then((tasks) => {
        if (!cancelled && Array.isArray(tasks)) setBackgroundTasks(tasks);
      })
      .catch(() => {
        if (!cancelled) setBackgroundTasks([]);
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [backgroundThreadId, backgroundToolState]);
  const hasRunningBackgroundTask = backgroundTasks.some(
    (task) => task.status === "running",
  );
  const { states: backgroundStates, stopTask } = useBackgroundTasks(
    backgroundThreadId ?? "",
    backgroundTasks,
    hasRunningBackgroundTask,
  );
  const backgroundTaskByToolCall = new Map(
    backgroundTasks.map((task) => [task.toolCallId, task]),
  );
  const previewWorkspaceId = composerContext?.workspaceId ?? null;

  const loadAttachmentContent = useCallback(
    (attachment: TimelineAttachment) => {
      const threadIdForLoad = attachment.threadId ?? thread?.backendId;
      if (!threadIdForLoad) {
        return Promise.reject(new Error("会话尚未就绪。"));
      }
      return fetchAttachmentContent(threadIdForLoad, attachment.id);
    },
    [thread?.backendId],
  );

  const openAttachmentPreview = useCallback(
    (attachment: TimelineAttachment) => {
      const threadIdForPreview = attachment.threadId ?? thread?.backendId;
      if (!threadIdForPreview) return;
      if (onPreviewTarget) {
        onPreviewTarget({source: "attachment", threadId: threadIdForPreview, attachment});
        return;
      }
      setPreviewOpen(true);
      setPreviewTarget({
        source: "attachment",
        threadId: threadIdForPreview,
        attachment,
      });
    },
    [thread?.backendId, onPreviewTarget],
  );

  const openWorkspacePreview = useCallback(
    (workspaceId: string, path: string) => {
      if (onPreviewTarget) {onPreviewTarget({source: "workspace", workspaceId, path}); return;}
      setPreviewOpen(true);
      setPreviewTarget({ source: "workspace", workspaceId, path });
    },
    [onPreviewTarget],
  );

  const openArtifactsList = useCallback(() => {
    if (onPreviewTarget) {onPreviewTarget({source: "artifacts-list"}); return;}
    setPreviewOpen(true);
    setPreviewTarget({ source: "artifacts-list" });
  }, [onPreviewTarget]);

  const closePreview = useCallback(() => {
    setPreviewOpen(false);
    setPreviewTarget(null);
  }, []);

  const handleStopBackgroundTask = (taskId: string) => {
    setStoppingTaskId(taskId);
    void stopTask(taskId)
      .catch(() => undefined)
      .finally(() => setStoppingTaskId(null));
  };

  const contextUsage = useContextUsage(
    currentThreadId,
    `${terminalToken}:${composerContext?.providerId ?? ""}:${composerContext?.model ?? ""}:${modelMetadataRevision}`,
    Boolean(currentThreadId && composerContext),
  );

  useEffect(() => {
    let cancelled = false;
    listWorkspaces()
      .then((records) => {
        if (!cancelled && Array.isArray(records)) setWorkspaceRecords(records);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [composerContext?.workspaceId]);

  useEffect(() => {
    let cancelled = false;
    setVisionSupported(undefined);
    setSupportedReasoningEfforts(null);
    if (!composerContext) return () => undefined;
    listModelCapabilities(composerContext.providerId)
      .then((records) => {
        if (cancelled) return;
        const safeRecords = Array.isArray(records) ? records : [];
        setVisionSupported(
          safeRecords.find((record) => record.model === composerContext.model)
            ?.supports_vision,
        );
        setSupportedReasoningEfforts(
          safeRecords.find((record) => record.model === composerContext.model)
            ?.supported_reasoning_efforts ?? null,
        );
      })
      .catch(() => {
        if (!cancelled) setVisionSupported(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [composerContext]);

  useEffect(() => {
    const currentThreadId = thread?.backendId ?? null;
    const previousThreadId = previousThreadIdRef.current;
    if (previousThreadId && previousThreadId !== currentThreadId) {
      attachments.clear();
      editLockRef.current = null;
      setEditingTurnId(null);
      setQueuesByThread((current) => {
        const previous = current[previousThreadId];
        return previous?.editing
          ? { ...current, [previousThreadId]: { ...previous, editing: false } }
          : current;
      });
      setContextMenu(null);
      setContextError("");
      setRecoveryError("");
      setApprovalMode("ask");
    }
    previousThreadIdRef.current = currentThreadId;
  }, [attachments.clear, thread?.backendId]);

  useEffect(() => {
    const action = nextQueueAction({
      queue,
      runningTurnId,
      connectionReady,
      terminalToken,
      currentThreadId,
      historyLoadedThreadId,
      historyToken,
    });
    if (!action || !currentThreadId) return;
    if (sendMessage(action.submission)) {
      inFlightQueuedAttachmentsByThreadRef.current.set(
        currentThreadId,
        queuedAttachmentsByThreadRef.current.get(currentThreadId) ?? [],
      );
      queuedAttachmentsByThreadRef.current.delete(currentThreadId);
      setQueuesByThread((current) => {
        const next = { ...current };
        delete next[currentThreadId];
        return next;
      });
    }
  }, [
    connectionReady,
    currentThreadId,
    historyLoadedThreadId,
    historyToken,
    queue,
    runningTurnId,
    sendMessage,
    terminalToken,
  ]);

  useEffect(() => {
    // Instant scrolling: during a long stream this fires on every flushed
    // batch, and smooth-scroll animations would pile up and jank the UI.
    // jsdom lacks scrollIntoView; guard the method itself, not just the ref.
    if (followRef.current) {
      bottomRef.current?.scrollIntoView?.({ behavior: "auto", block: "end" });
    }
  }, [items, isThinking, error]);

  function handleScroll() {
    const element = listRef.current;
    if (!element) return;
    const nearBottom =
      element.scrollHeight - element.scrollTop - element.clientHeight <=
      FOLLOW_THRESHOLD_PX;
    followRef.current = nearBottom;
    setShowJump(!nearBottom);
  }

  function jumpToLatest() {
    const element = listRef.current;
    if (element) element.scrollTop = element.scrollHeight;
    followRef.current = true;
    setShowJump(false);
  }

  // A first message sent before any session existed is stashed in App state;
  // deliver it exactly once as soon as the freshly created thread arrives.
  useEffect(() => {
    if (
      !thread ||
      !pendingAgentMessage ||
      pendingAgentMessage.threadId !== thread.backendId
    ) return;
    const pendingKey = `${pendingAgentMessage.operationId}\u0000${pendingAgentMessage.threadId}`;
    if (lastPendingRef.current === pendingKey) return;
    if (sendMessage(pendingAgentMessage.submission)) {
      lastPendingRef.current = pendingKey;
      onPendingAgentMessageSent(pendingAgentMessage.operationId);
    }
  }, [
    connectionReady,
    thread,
    pendingAgentMessage,
    pendingAckToken,
    sendMessage,
    onPendingAgentMessageSent,
  ]);

  const needsThreadModel = Boolean(thread && thread.providerId === null);

  const litigationMaterials=useLitigationMaterials();

  async function handleSend() {
    if (editLockRef.current || needsThreadModel || contractSendLock.current || searchBlockedReason) return;
    if (!draft.trim() && attachments.ready.length === 0) return;
    if (beforeContractSend) {
      contractSendLock.current=true;
      try { await beforeContractSend(); }
      catch (error) { setContextError(error instanceof Error ? error.message : '合同未能保存，请重试。'); return; }
      finally { contractSendLock.current=false; }
      if(currentThreadIdRef.current!==currentThreadId || draftRef.current!==draft)return;
    }
    const submission: ComposerSubmission = {
      text: composerQuote ? `引用内容：\n${composerQuote}\n\n问题：${draft}` : draft,
      ...(litigationMaterials ? {litigationMaterials:structuredClone(litigationMaterials)} : {}),
      ...(searchSelection ? {searchSelection} : {}),
      knowledgeLibraryIds: searchSelection ? (searchSelection.channel === "knowledge" ? [...(searchKnowledgeIds ?? [])] : []) : [...knowledgeIds],
      attachmentUploadIds: attachments.ready
        .map((attachment) => attachment.uploadId)
        .filter((uploadId): uploadId is string => Boolean(uploadId)),
      attachmentNames: attachments.ready.map((attachment) => attachment.name),
      attachments: attachments.ready.flatMap((attachment) =>
        attachment.uploadId
          ? [{
              id: attachment.uploadId,
              name: attachment.name,
              mediaType: attachment.mediaType,
              sizeBytes: attachment.size,
            }]
          : [],
      ),
      approvalMode,
      reasoningEffort: composerContext?.reasoningEffort ?? "auto",
    };
    if (!thread || thread.isDraft) {
      // App creates the session and stashes the text; the draft is only
      // cleared when creation succeeded so failed attempts lose nothing.
      onSendWithoutThread(submission);
      return;
    }
    sendMessage(submission);
  }

  const handleEnableWebAccess = useCallback(
    () => webAccess.toggle(true),
    [webAccess.toggle],
  );
  const handleSendWebFollowUp = useCallback(
    (text: string) => {
      sendMessage(text);
    },
    [sendMessage],
  );

  function queueCurrentSubmission(mode: ComposerQueueMode) {
    if (editLockRef.current || searchBlockedReason) return;
    if (!currentThreadId) return;
    const submission: ComposerSubmission = {
      text: draft,
      ...(litigationMaterials ? {litigationMaterials:structuredClone(litigationMaterials)} : {}),
      ...(searchSelection ? {searchSelection: structuredClone(searchSelection)} : {}),
      knowledgeLibraryIds: searchSelection ? (searchSelection.channel === "knowledge" ? [...(searchKnowledgeIds ?? [])] : []) : [...knowledgeIds],
      attachmentUploadIds: attachments.ready
        .map((attachment) => attachment.uploadId)
        .filter((uploadId): uploadId is string => Boolean(uploadId)),
      attachmentNames: attachments.ready.map((attachment) => attachment.name),
      attachments: attachments.ready.flatMap((attachment) =>
        attachment.uploadId
          ? [{
              id: attachment.uploadId,
              name: attachment.name,
              mediaType: attachment.mediaType,
              sizeBytes: attachment.size,
            }]
          : [],
      ),
      approvalMode,
      reasoningEffort: composerContext?.reasoningEffort ?? "auto",
    };
    if (!submission.text.trim() && submission.attachmentUploadIds.length === 0) return;
    const detached = attachments.detach();
    if (queue.submission && !queue.editing) {
      void attachments.discard(
        queuedAttachmentsByThreadRef.current.get(currentThreadId) ?? [],
      );
    }
    queuedAttachmentsByThreadRef.current.set(currentThreadId, detached);
    setQueuesByThread((current) => ({
      ...current,
      [currentThreadId]: queueSubmission(queue, submission, mode, {
        threadId: currentThreadId,
        terminalToken,
        historyToken,
      }).state,
    }));
    onDraftChange("");
    if (mode === "after_stop") stop();
  }

  function editQueuedSubmission() {
    if (!queue.submission || !currentThreadId) return;
    onDraftChange(queue.submission.text);
    attachments.restore(
      queuedAttachmentsByThreadRef.current.get(currentThreadId) ?? [],
    );
    setQueuesByThread((current) => ({
      ...current,
      [currentThreadId]: editQueue(queue),
    }));
  }

  function cancelQueuedSubmission() {
    if (!currentThreadId) return;
    const discarded = queue.editing
      ? attachments.detach()
      : queuedAttachmentsByThreadRef.current.get(currentThreadId) ?? [];
    void attachments.discard(discarded);
    queuedAttachmentsByThreadRef.current.delete(currentThreadId);
    if (queue.editing) onDraftChange("");
    setQueuesByThread((current) => {
      const next = { ...current };
      delete next[currentThreadId];
      return next;
    });
  }

  function restoreNoticeText(item: NoticeItem, renderedThreadId: string | null) {
    if (
      !item.sourceText ||
      !renderedThreadId ||
      currentThreadIdRef.current !== renderedThreadId
    ) return;
    if (
      draftRef.current.trim() ||
      attachmentCountRef.current > 0 ||
      queueSubmissionRef.current !== null
    ) {
      setRecoveryError(RECOVERY_BLOCKED_MESSAGE);
      return;
    }
    onDraftChange(item.sourceText);
    setRecoveryError("");
    setFocusRequestToken((value) => value + 1);
  }

  function beginMessageEdit(item: Extract<TimelineItem, {kind: "user"}>): boolean {
    if (!item.turnId || editLockRef.current || queue.submission || !connectionReady || historyLoadedThreadId !== currentThreadId) return false;
    editLockRef.current = item.turnId;
    setEditingTurnId(item.turnId);
    if (isRunning) stop();
    return true;
  }
  function sendMessageEdit(item: Extract<TimelineItem, {kind: "user"}>, text: string): boolean {
    if (!item.turnId || editLockRef.current !== item.turnId || isRunning || !connectionReady || queue.submission) return false;
    return sendMessage({text, replaceTurnId: item.turnId, preserveDraft: true,
      attachmentUploadIds: [], attachmentNames: item.attachmentNames ?? item.attachments?.map(a => a.name) ?? [],
      attachments: item.attachments, ...(searchSelection ? {searchSelection} : {}), knowledgeLibraryIds: searchSelection ? (searchSelection.channel === "knowledge" ? searchKnowledgeIds : []) : knowledgeIds,
      approvalMode, reasoningEffort: composerContext?.reasoningEffort ?? "auto"});
  }
  function resendText(text: string, mode?: ComposerQueueMode): boolean {
    if (editLockRef.current || !currentThreadId || historyLoadedThreadId !== currentThreadId || !text.trim() || queue.submission) return false;
    const submission: ComposerSubmission = {text, preserveDraft: true, ...(searchSelection ? {searchSelection, knowledgeLibraryIds: searchSelection.channel === "knowledge" ? searchKnowledgeIds : []} : {}), attachmentUploadIds: [], attachmentNames: [], approvalMode, reasoningEffort: composerContext?.reasoningEffort ?? "auto"};
    if (isRunning) {
      if (!mode) return false;
      setQueuesByThread(current => ({...current, [currentThreadId]: queueSubmission(queue, submission, mode, {threadId: currentThreadId, terminalToken, historyToken}).state}));
      if (mode === "after_stop") stop();
      return true;
    }
    return sendMessage(submission);
  }
  const lastAnswer = [...items].reverse().find(item => item.kind === "assistant" && item.segments.some(s => s.type === "text" && s.text));
  const lastUser = [...items].reverse().find(item => item.kind === "user");
  const canRegenerate = (variant !== "litigation" || ordinaryDiscussion) && !editingTurnId && !isRunning && !queue.submission && historyLoadedThreadId === currentThreadId && lastUser?.kind === "user" && !!lastUser.text.trim() && lastUser.turnId === lastAnswer?.turnId;
  const hasItems = items.length > 0;
  const pendingApprovals = historyLoadedThreadId === currentThreadId
    ? items.filter((item): item is ToolItem => item.kind === "tool" && item.status === "awaiting_approval"
      && item.turnId === runningTurnId && Boolean(item.turnId && item.id && item.approvalId)) : [];
  const pendingApproval = pendingApprovals[0];
  const hasPendingApproval = pendingApprovals.length > 0;
  useEffect(() => {
    if (variant !== "litigation" || !currentThreadId || historyLoadedThreadId !== currentThreadId) {
      approvalPresenceRef.current = null;
      return;
    }
    const previous = approvalPresenceRef.current;
    const changed = previous?.threadId === currentThreadId
      ? previous.pending !== hasPendingApproval
      : hasPendingApproval;
    approvalPresenceRef.current = {threadId: currentThreadId, pending: hasPendingApproval};
    // Workbench headers use the parent's persisted thread status. Refresh
    // exactly on visible approval transitions, not on every streamed delta.
    if (changed) onThreadsRefresh();
  }, [variant, currentThreadId, historyLoadedThreadId, hasPendingApproval, onThreadsRefresh]);
  const metaTime = thread ? formatRelativeTime(thread.updatedAt) : "新任务";
  const selectedWorkspace = workspaceRecords.find(
    (workspace) => workspace.id === composerContext?.workspaceId,
  );
  const visibleWorkspaceName = selectedWorkspace?.display_name
    ?? (composerContext?.workspaceId ? workspaceName : "选择工作区");
  const visionBlocked = attachments.hasImages && visionSupported === false;
  const selectedProviderKind = providers.find(
    (provider) => provider.id === composerContext?.providerId,
  )?.kind;
  const attachmentBlockedReason = visionBlocked
    ? "该模型不支持图片，请更换模型。"
    : attachments.hasPending
      ? "附件正在上传，完成后即可发送。"
      : attachments.attachments.some((item) => item.status === "failed")
        ? "有附件上传失败，请重试或移除。"
        : "";
  const contextLocked = isRunning || editingTurnId !== null || queue.submission !== null;
  const queuedLabel = queue.submission
    ? `${queue.editing ? "正在编辑" : "已排队"}：${
        queue.submission.text.trim()
          || queue.submission.attachmentNames[0]
          || "附件消息"
      }`
    : undefined;
  const renderedThreadId = historyLoadedThreadId === currentThreadId
    ? currentThreadId
    : null;

  return (
    <section className="agent-workspace-frame">
    <div className="agent-workspace">
      {variant === "chat" && !hideConversationHeader ? (
        <header className="conversation-meta">
          <div className="conversation-meta-title">
            <strong>{thread?.title ?? "新任务"}</strong>
            <span>{visibleWorkspaceName} · Agent · {metaTime}</span>
          </div>
        </header>
      ) : null}
      {(variant !== "litigation" || ordinaryDiscussion) && thread && thread.scene !== "contract-review" && <div className="conversation-export">
        <button type="button" disabled={!renderedThreadId || historyLoading || exporting} onClick={async () => {
          setExporting(true); setExportStatus("");
          try { setExportStatus(await saveConversation(exportFilename(thread.title), exportMarkdown(thread.title, thread.createdAt, items, isRunning)) ? "对话已保存" : "已取消导出"); }
          catch { setExportStatus("保存失败，请重新选择位置。"); }
          finally { setExporting(false); }
        }}>导出对话</button>{exportStatus && <span role="status">{exportStatus}</span>}
      </div>}
      {hasItems ? (
        <div
          className="message-list"
          role="log"
          aria-label="对话时间线"
          ref={listRef}
          onScroll={handleScroll}
        >
          {turnEntries.map((entry) => {
            if (entry.kind === "user") {
              return (
                <UserMessage
                  key={entry.item.id}
                  item={entry.item}
                  running={isRunning}
                  editDisabled={!!queue.submission || !renderedThreadId || !entry.item.turnId || !connectionReady || (!!editingTurnId && editingTurnId !== entry.item.turnId)}
                  onBeginEdit={() => beginMessageEdit(entry.item)}
                  onCancelEdit={() => { editLockRef.current = null; setEditingTurnId(null); }}
                  onResend={!readOnly && (variant !== "litigation" || ordinaryDiscussion) && renderedThreadId ? text => sendMessageEdit(entry.item, text) : undefined}
                  loadAttachmentContent={loadAttachmentContent}
                  onOpenAttachment={openAttachmentPreview}
                />
              );
            }
            return (
              <TurnGroup
                key={entry.turnId}
                threadId={renderedThreadId ?? undefined}
                items={entry.items}
                actionsEnabled={!readOnly && (variant !== "litigation" || ordinaryDiscussion)}
                regenerateItemId={canRegenerate ? lastAnswer?.id : undefined}
                onRegenerate={!readOnly && canRegenerate && lastUser?.kind === "user" ? () => {resendText(lastUser.text);} : undefined}
                onConvertOpinion={!readOnly && !disableContractOpinionConversion && thread?.scene === "contract-review" && !isRunning && !queue.submission && historyLoadedThreadId === currentThreadId
                  ? text => resendText(contractOpinionConversion(text)) : undefined}
                caseContextItems={items}
                presentation={variant === "legal" ? "legal" : "chat"}
                active={entry.isActive}
                startedAt={turnTimes[entry.turnId]?.started}
                endedAt={turnTimes[entry.turnId]?.ended}
                artifacts={artifactsByTurn[entry.turnId] ?? []}
                renderDeliverables={(turnArtifacts) => (
                  <DeliverablesCard
                    artifacts={turnArtifacts}
                    onOpen={openWorkspacePreview}
                    onViewAll={openArtifactsList}
                  />
                )}
                renderItem={(item) => {
                  if (item.kind === "assistant") {
                    return (
                      <AssistantMessage
                        key={item.id}
                        item={item}
                        offerDisabled={
                          isRunning || webAccess.state.loading || webAccess.state.updating
                        }
                        onEnableWebAccess={handleEnableWebAccess}
                        onSendWebFollowUp={handleSendWebFollowUp}
                      />
                    );
                  }
                  if (item.kind === "tool") {
                    if (item.name === "run_background_command") {
                      const boundTask = backgroundTaskByToolCall.get(item.id);
                      if (boundTask) {
                        return (
                          <BackgroundTaskCard
                            key={item.id}
                            threadId={backgroundThreadId ?? ""}
                            task={boundTask}
                            live={backgroundStates[boundTask.id] ?? null}
                            onStop={handleStopBackgroundTask}
                            stopPending={stoppingTaskId === boundTask.id}
                          />
                        );
                      }
                    }
                    const previewPath = artifactPreviewPath(item);
                    return (
                      <div key={item.id} className="timeline-tool-entry">
                        <ToolCallCard
                          compactApproval
                          presentation={variant === "legal" ? "legal" : "chat"}
                          item={item}
                          connectionReady={connectionReady}
                          previewPath={
                            previewPath && previewWorkspaceId ? previewPath : undefined
                          }
                          onPreviewPath={
                            previewWorkspaceId
                              ? (path) => openWorkspacePreview(previewWorkspaceId, path)
                              : undefined
                          }
                        />
                        {item.name === "law_read" && item.status === "succeeded" ? (
                          (() => {
                            const summary = lawResultSummary(item);
                            return summary && summary.kind === "law_read" ? (
                              <LegalStatuteCard
                                summary={summary}
                                onRefresh={
                                  renderedThreadId && !isRunning
                                    ? (request) => {
                                        // 重读走一次新的智能体回合，让模型带
                                        // force_refresh 重新调用 law_read。
                                        sendMessage({
                                          text: `请重新读取并核对「${request.title}」${
                                            request.clause ? request.clause : ""
                                          }的最新原文（law_read 使用 force_refresh 重新联网读取，不要用缓存）。`,
                                          attachmentUploadIds: [],
                                          attachmentNames: [],
                                        });
                                      }
                                    : undefined
                                }
                              />
                            ) : null;
                          })()
                        ) : null}
                        {(item.name === "law_search" || item.name === "law_read") &&
                        item.status === "failed" ? (
                          <LegalEscapeHatch />
                        ) : null}
                      </div>
                    );
                  }
                  if (item.kind !== "notice") return null;
                  return (
                    <TimelineNotice
                      key={item.id}
                      item={item}
                      onRestoreText={!readOnly && renderedThreadId
                        ? () => restoreNoticeText(item, renderedThreadId)
                        : undefined}
                      onOpenSettings={renderedThreadId
                        ? () => {
                            if (currentThreadIdRef.current !== renderedThreadId) return;
                            setRecoveryError("");
                            onOpenSettings();
                          }
                        : undefined}
                      onOpenMcpSettings={renderedThreadId
                        ? () => {
                            if (currentThreadIdRef.current !== renderedThreadId) return;
                            onOpenMcpSettings();
                          }
                        : undefined}
                      onReselectAttachment={renderedThreadId
                        ? () => {
                            if (currentThreadIdRef.current !== renderedThreadId) return;
                            setRecoveryError("");
                            setFilePickerRequestToken((value) => value + 1);
                          }
                        : undefined}
                    />
                  );
                }}
              />
            );
          })}
          {isThinking && (
            <div className="thinking-indicator" role="status">正在深度思考…</div>
          )}
          {error && (
            <div className="message-error" role="alert">{error}</div>
          )}
          <div ref={bottomRef} />
        </div>
      ) : (
        <div className="agent-workspace-center">
          {variant === "chat" || ordinaryDiscussion ? (
            <>
              <img className="agent-empty-logo" src="/verilex-logo.png" alt="" />
              <h1>开启今天的法律工作</h1>
            </>
          ) : variant === "legal" ? (
            <h1>输入问题开始检索，结果会显示在这里</h1>
          ) : null}
          {isThinking && (
            <div className="thinking-indicator" role="status">正在深度思考…</div>
          )}
          {error && (
            <div className="message-error" role="alert">{error}</div>
          )}
        </div>
      )}
      {historyError && <div className="message-error" role="alert">
        <p>{historyError}</p>
        <button type="button" disabled={historyLoading} onClick={retryHistory}>
          {historyLoading ? '正在加载历史…' : '重新加载历史'}
        </button>
      </div>}
      {!readOnly && <div className="composer-context-layer">
        {pendingApproval && <div className="approval-dock">
          <ApprovalCard key={`${currentThreadId}:${pendingApproval.turnId}:${pendingApproval.id}:${pendingApproval.approvalId}`}
            item={pendingApproval} pendingCount={pendingApprovals.length}
            connectionReady={connectionReady}
            submission={approvalSubmissions[`${pendingApproval.turnId}:${pendingApproval.id}`]}
            onResolve={(decision, reason) => resolveApproval({
              turnId: pendingApproval.turnId!, toolCallId: pendingApproval.id, decision, reason,
            })} />
        </div>}
        <div hidden={hasPendingApproval}>
        {showJump && !editingTurnId && (
          <button type="button" className="jump-to-latest" onClick={jumpToLatest}>
            {hasPendingApproval ? "有操作等待确认 · 查看" : isRunning ? "回到最新（运行中）" : "回到最新"}
          </button>
        )}
        {contextMenu === "workspace" && composerContext && (
          <ComposerContextMenus
            kind="workspace"
            providers={providers}
            context={composerContext}
            onClose={() => setContextMenu(null)}
            onError={setContextError}
            onSelectModel={() => undefined}
            onSelectWorkspace={(workspace) => {
              if (workspace) {
                setWorkspaceRecords((current) => [
                  workspace,
                  ...current.filter((item) => item.id !== workspace.id),
                ]);
              }
              void onComposerContextChange({
                ...composerContext,
                workspaceId: workspace?.id ?? null,
              }).catch(() => undefined);
            }}
          />
        )}
        {showPlugins && (
          <McpThreadPicker
            snapshot={threadMcp.snapshot}
            savedServers={threadMcp.savedServers}
            loading={threadMcp.loading}
            error={threadMcp.error}
            pendingServerIds={threadMcp.pendingServerIds}
            onSelect={threadMcp.select}
            onDeselect={threadMcp.deselect}
            onRetry={threadMcp.retry}
            onAddServer={threadMcp.select}
            onOpenSettings={() => {
              setShowPlugins(false);
              onOpenMcpSettings();
            }}
            onClose={closePlugins}
          />
        )}
        {composerQuote && <div className="contract-review-quote" role="status">
          <span>已选中：『{composerQuote}』</span>
          <button aria-label="移除引用" onClick={onComposerQuoteClear}>×</button>
        </div>}
        <AgentComposer
        searchControls={searchControls}
        litigationSkillScope={variant === "litigation"
          ? `${previewWorkspaceId ?? "none"}:${currentThreadId ?? "new"}:${mcpTaskGeneration}` : undefined}
        knowledgeSelection={{selected: knowledgeIds, onChange: setKnowledgeIds, disabled: isRunning || draftSendPreparing || readOnly}}
        hasModel={hasModel}
        value={draft}
        isRunning={isRunning}
        modelLabel={needsThreadModel && hasModel ? "请选择模型" : composerContext?.model}
        contextUsage={contextUsage.usage}
        contextUsageLoading={contextUsage.loading}
        reasoningEffort={composerContext?.reasoningEffort ?? "auto"}
        providerKind={selectedProviderKind}
        supportedReasoningEfforts={supportedReasoningEfforts}
        workspaceLabel={visibleWorkspaceName}
        attachments={attachments.attachments}
        hasReadyAttachments={attachments.ready.length > 0}
        attachmentError={[recoveryError, contextError, attachments.error]
          .filter(Boolean)
          .join(" ")}
        sendBlockedReason={needsThreadModel ? "原模型连接已删除，会话内容已保留。请选择模型后继续。" : editingTurnId ? "请先完成或取消上方的指令编辑。" : attachmentBlockedReason || searchBlockedReason}
        submissionPending={draftSendPreparing}
        actionScopeKey={currentThreadId}
        focusRequestToken={focusRequestToken}
        pluginFocusRequestToken={pluginFocusRequestToken}
        filePickerRequestToken={filePickerRequestToken}
        onAddFiles={composerContext ? (files) => void attachments.addFiles(files) : undefined}
        attachmentsEnabled={attachmentsEnabled}
        onAttachmentsUnavailable={() => {
          setContextError("请先连接模型，再添加附件或选择工作区。");
        }}
        onRetryAttachment={(key) => void attachments.retry(key)}
        onRemoveAttachment={(key) => void attachments.remove(key)}
        onSelectModel={composerContext && !isRunning && !contextLocked
          ? () => setContextMenu("model")
          : undefined}
        onSelectWorkspace={variant !== "litigation" && composerContext && !contextLocked
          ? () => setContextMenu("workspace")
          : undefined}
        onReasoningEffortChange={composerContext && !contextLocked
          ? (reasoningEffort) => {
              void onComposerContextChange({
                ...composerContext,
                reasoningEffort,
              }).catch(() => undefined);
            }
          : undefined}
        onDismissContextMenus={() => setContextMenu(null)}
        approvalMode={approvalMode}
        onApprovalModeChange={setApprovalMode}
        webAccess={thread?.isDraft || !thread
          ? {
              enabled: draftWebEnabled,
              granted: false,
              loading: false,
              updating: false,
              error: draftWebError,
            }
          : webAccess.state}
        onWebAccessChange={thread?.isDraft || !thread
          ? onDraftWebEnabledChange
          : (enabled) => void webAccess.toggle(enabled)}
        onChange={onDraftChange}
        onOpenPlugins={() => setShowPlugins(true)}
        onOpenSettings={onOpenSettings}
        onSend={() => void handleSend()}
        onStop={stop}
        queuedLabel={queuedLabel}
        queueEditing={queue.editing}
        onQueue={() => queueCurrentSubmission("after_current")}
        onInterruptAndSend={() => queueCurrentSubmission("after_stop")}
        onEditQueue={editQueuedSubmission}
        onCancelQueue={cancelQueuedSubmission}
        modelMenuSlot={contextMenu === "model" && composerContext ? (
          <ComposerContextMenus
            kind="model"
            providers={providers}
            context={composerContext}
            onClose={() => setContextMenu(null)}
            onError={setContextError}
            onSelectModel={(providerId, model, supportsVision) => {
              void onComposerContextChange({
                providerId,
                model,
                workspaceId: composerContext.workspaceId,
                reasoningEffort:
                  providerId === composerContext.providerId
                    && model === composerContext.model
                    ? composerContext.reasoningEffort
                    : "auto",
              })
                .then(() => {
                  setVisionSupported(supportsVision);
                  setContextMenu(null);
                  setContextError("");
                })
                .catch(() => undefined);
            }}
            onSelectWorkspace={() => undefined}
          />
        ) : undefined}
      />
      </div>
      </div>}
    </div>
      {!onPreviewTarget && <PreviewRail
        terminalEnabled={variant === "chat" && !readOnly}
        open={previewOpen}
        target={previewTarget}
        threadId={currentThreadId}
        workspaceId={previewWorkspaceId}
        artifactsRevision={terminalToken}
        onClose={closePreview}
        onOpenWorkspaceFile={openWorkspacePreview}
        onOpenList={openArtifactsList}
      />}
    </section>
  );
}
