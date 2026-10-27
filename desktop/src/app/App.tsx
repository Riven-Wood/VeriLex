import {ShortcutSettings} from "../commands/GlobalShortcut";
import { HelpCenter } from "../help/HelpCenter";
import {CommandController} from "../commands/CommandController";
import {PanelCommandContext} from "../commands/PanelCommandContext";
import {preferredWorkspaceId} from "../settings/StoragePreferences";
import { AppearanceEffect } from "../settings/AppearanceEffect";
import { useUiPreferences, saveUiPreferences } from "../settings/preferences";
import {litigationWorkClient, type WorkRequest} from "../views/LitigationClient";
import { useAgentDraft, moveDraft, clearDraft, readDraft } from "./agentDrafts";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";

import { RuntimeBanner } from "../components/RuntimeBanner";
import { AppToolbar } from "../components/AppToolbar";
import { Sidebar } from "../components/Sidebar";
import { SettingsNavigation } from "../components/SettingsNavigation";
import type { SettingsCategoryId } from "../settings/categories";
import {
  getHealth,
  listProviders,
  listWorkspaces,
  createWorkspace,
  RuntimeApiError,
} from "../runtime/api";
import { contractReviewApi } from "../runtime/contractReviewClient";
import type {
  AtomicThreadSelectionResult,
  DraftThreadInput,
  HealthResponse,
  ProviderRecord,
  ThreadRecord,
  WorkspaceRecord,
} from "../runtime/contracts";
import type { ProviderApi } from "../runtime/providerClient";
import { pickWorkspaceDirectory } from "../platform/directoryPicker";
import { directoryName } from "../components/ComposerContextMenus";
import {
  clampSidebarWidth,
  loadSidebarLayout,
  saveSidebarCollapsed,
  saveSidebarWidth,
} from "./sidebarLayout";
import { runtimeThreadApi } from "../runtime/threadClient";
import type { ThreadApi } from "../runtime/threadClient";
import { waitForRuntime } from "../runtime/startup";
import type { UpdaterClient } from "../updater/contracts";
import { tauriUpdaterClient } from "../updater/tauriUpdaterClient";
import { useUpdateController } from "../updater/useUpdateController";
import {
  beginNewTask,
  bindHiddenDraft,
  initialComposerState,
  materializeComposerTask,
  type ComposerContext,
  type ComposerState,
} from "../composer/state";
import {
  chooseComposerContext,
  parseStoredModelPreference,
  serializeModelPreference,
} from "../composer/context";
import {
  derivedThreadTitle,
  isDefaultThreadTitle,
  type ThreadSummary,
  type WorkViewId,
} from "./navigation";
import { WorkspaceRouter } from "./WorkspaceRouter";
import { CaseCalendarReminders } from "../components/CaseCalendarReminders";
import type { ComposerSubmission } from "../hooks/useAgentChat";
import type { PendingAgentMessage } from "../views/AgentWorkspaceView";
import {
  runtimeWebAccessApi,
  type WebAccessApi,
  type WebAccessRecord,
} from "../runtime/webAccessClient";
import type { WebAccessHandoff } from "../hooks/useWebAccess";
import {
  runtimeThreadMcpApi,
  type DraftMcpSelectionOutcome,
  type McpSelectionTarget,
  type ThreadMcpApi,
} from "../hooks/useThreadMcp";
import type { McpServerRecord } from "../runtime/contracts";

interface AppProps {
  initialHealth?: HealthResponse;
  providerApi?: ProviderApi;
  threadApi?: ThreadApi;
  refreshHealth?: () => Promise<HealthResponse>;
  updaterClient?: UpdaterClient;
  webAccessApi?: WebAccessApi;
  mcpApi?: ThreadMcpApi;
}

interface HiddenDraftCreationOutcome {
  threadId: string;
  atomicResult: AtomicThreadSelectionResult | null;
}

interface HiddenDraftCreationFlight {
  promise: Promise<HiddenDraftCreationOutcome | null>;
  atomicTarget: McpSelectionTarget | null;
}

function snapshotComposerSubmission(
  submission: ComposerSubmission,
): ComposerSubmission {
  const knowledgeLibraryIds = [...(submission.knowledgeLibraryIds ?? [])];
  Object.freeze(knowledgeLibraryIds);
  const attachmentUploadIds = [...submission.attachmentUploadIds];
  const attachmentNames = [...submission.attachmentNames];
  const attachments = submission.attachments?.map((attachment) => {
    const snapshot = { ...attachment };
    Object.freeze(snapshot);
    return snapshot;
  });
  Object.freeze(attachmentUploadIds);
  Object.freeze(attachmentNames);
  if (attachments) Object.freeze(attachments);
  const snapshot: ComposerSubmission = {
    text: submission.text,
    preserveDraft: submission.preserveDraft,
    ...(submission.litigationMaterials?{litigationMaterials:structuredClone(submission.litigationMaterials)}:{}),
    ...(knowledgeLibraryIds.length ? { knowledgeLibraryIds } : {}),
    attachmentUploadIds,
    attachmentNames,
    ...(attachments ? { attachments } : {}),
    approvalMode: submission.approvalMode,
    reasoningEffort: submission.reasoningEffort,
  };
  Object.freeze(snapshot);
  return snapshot;
}

const PROVIDERS_ERROR_PREFIX = "无法加载模型配置：";
const THREADS_ERROR_PREFIX = "无法加载会话列表：";
const LAST_THREAD_STORAGE_KEY = "verilex:last-thread:v1";
const LAST_MODEL_STORAGE_KEY = "verilex:last-model:v1";

function toSummary(record: ThreadRecord): ThreadSummary {
  return {
    id: record.id,
    backendId: record.id,
    title: record.title,
    providerId: record.provider_id,
    model: record.model,
    workspaceId: record.workspace_id,
    createdAt: record.created_at,
    updatedAt: record.updated_at,
    status: record.status,
    isDraft: record.is_draft,
    reasoningEffort: record.reasoning_effort ?? "auto",
    scene: record.scene ?? "general",
    legalChannel: record.legal_channel,
    legalSkills: record.legal_skills,
  };
}

function draftContextMatches(
  context: ComposerContext | null,
  draft: DraftThreadInput,
): boolean {
  return context !== null
    && context.providerId === draft.providerId
    && context.model === draft.model
    && context.workspaceId === draft.workspaceId
    && context.reasoningEffort === draft.reasoningEffort;
}

export function App({
  initialHealth,
  providerApi,
  threadApi = runtimeThreadApi,
  refreshHealth = getHealth,
  updaterClient,
  webAccessApi = runtimeWebAccessApi,
  mcpApi = runtimeThreadMcpApi,
}: AppProps) {
  const updates = useUpdateController(updaterClient ?? tauriUpdaterClient);
  const [activeView, setActiveView] = useState<WorkViewId>("agent");
  const activeViewRef = useRef(activeView);
  activeViewRef.current = activeView;
  const [settingsCategory, setSettingsCategory] = useState<SettingsCategoryId>("general");
  const [settingsQuery, setSettingsQuery] = useState("");
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [scheduledThread, setScheduledThread] = useState<ThreadSummary | null>(null);
  const [calendarDate, setCalendarDate] = useState<string>();
  const [calendarRefresh, setCalendarRefresh] = useState(0);
  const [contractThreads, setContractThreads] = useState<ThreadSummary[]>([]);
  const [archivedThreads, setArchivedThreads] = useState<ThreadSummary[]>([]);
  const [trashedThreads, setTrashedThreads] = useState<ThreadSummary[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [agentDraft, setAgentDraft, draftStorageFailed] = useAgentDraft(activeThreadId ?? `__new__:${activeView === "settings" ? "agent" : activeView}`);
  const [draftWebEnabled, setDraftWebEnabled] = useState(false);
  const [draftWebError, setDraftWebError] = useState<string | null>(null);
  const [webAccessHandoff, setWebAccessHandoff] = useState<WebAccessHandoff | null>(null);
  const { reduceMotion } = useUiPreferences();
  const setReduceMotion = (value: boolean) => saveUiPreferences({ reduceMotion: value });
  const [health, setHealth] = useState<HealthResponse | null>(initialHealth ?? null);
  const [healthRefreshing, setHealthRefreshing] = useState(false);
  const [runtimeError, setRuntimeError] = useState("");
  const contractLeaveGuard = useRef<(() => Promise<boolean>) | null>(null);
  const navigationPending = useRef(false);
  const registerContractLeaveGuard = useCallback((guard: (() => Promise<boolean>) | null) => { contractLeaveGuard.current = guard; }, []);
  // Guard the whole user action so a failed Word save cannot change either
  // the page or the selected thread and unmount the only pending input copy.
  const navigateSafely = useCallback((action: () => void | Promise<void>) => {
    const guard = contractLeaveGuard.current;
    if (activeViewRef.current !== "contract-review" || !guard) { void action(); return; }
    if (navigationPending.current) return;
    navigationPending.current = true;
    void guard().then(allowed => {
      if (allowed && contractLeaveGuard.current === guard) return action();
    }).catch(() => setRuntimeError("当前合同未能保存，已留在合同审查页。"))
      .finally(() => { navigationPending.current = false; });
  }, []);
  const [litigationError, setLitigationError] = useState("");
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [modelMetadataRevision, setModelMetadataRevision] = useState(0);
  const [pendingAgentMessage, setPendingAgentMessage] = useState<PendingAgentMessage | null>(null);
  const [draftSendPreparing, setDraftSendPreparing] = useState(false);
  const [composerState, setComposerState] = useState<ComposerState | null>(null);
  const [hiddenDraftThread, setHiddenDraftThread] = useState<ThreadSummary | null>(null);
  const hiddenDraftCreationRef = useRef<HiddenDraftCreationFlight | null>(null);
  const hiddenDraftCreationGenerationRef = useRef(0);
  const activeThreadIdRef = useRef<string | null>(null);
  const hiddenDraftThreadIdRef = useRef<string | null>(null);
  const composerContextRef = useRef<ComposerContext | null>(null);
  const draftSendOperationIdRef = useRef(0);
  const draftSendOperationRef = useRef<{
    readonly operationId: number;
    readonly generation: number;
    readonly threadId: string | null;
    readonly submission: ComposerSubmission;
  } | null>(null);
  const healthRefreshPromiseRef = useRef<Promise<void> | null>(null);
  const startupControllerRef = useRef<AbortController | null>(null);
  // Restore-once guard: only the first successful load may reselect the
  // remembered session; later refreshes must never yank the user away from
  // the session they deliberately switched to.
  const threadRestoreDoneRef = useRef(false);
  const [contractNavOpen, setContractNavOpen] = useState(false);
  useEffect(() => { if (activeView === "contract-review") setContractNavOpen(false); }, [activeView]);
  const [sidebarLayout, setSidebarLayout] = useState(() =>
    loadSidebarLayout(window.localStorage),
  );
  activeThreadIdRef.current = activeThreadId;
  hiddenDraftThreadIdRef.current = hiddenDraftThread?.id ?? null;
  composerContextRef.current = composerState?.context ?? null;

  const invalidateDraftSend = useCallback(() => {
    hiddenDraftCreationGenerationRef.current += 1;
    hiddenDraftCreationRef.current = null;
    draftSendOperationRef.current = null;
    setDraftSendPreparing(false);
    setPendingAgentMessage(null);
    setWebAccessHandoff(null);
  }, []);
  const consumeWebAccessHandoff = useCallback((operationId: number) => {
    setWebAccessHandoff((current) =>
      current?.operationId === operationId ? null : current,
    );
  }, []);

  const handleSidebarWidthChange = useCallback((width: number) => {
    setSidebarLayout((current) => ({ ...current, width }));
  }, []);

  const handleSidebarWidthCommit = useCallback((width: number) => {
    const clamped = clampSidebarWidth(width);
    saveSidebarWidth(clamped, window.localStorage);
    setSidebarLayout((current) => ({ ...current, width: clamped }));
  }, []);

  const handleToggleSidebarCollapsed = useCallback(() => {
    setSidebarLayout((current) => {
      const next = !current.collapsed;
      saveSidebarCollapsed(next, window.localStorage);
      return { ...current, collapsed: next };
    });
  }, []);

  const checkRuntimeStartup = useCallback((): Promise<void> => {
    // Reuse only a pending request whose controller is still live. An aborted
    // attempt (e.g. StrictMode's simulated unmount) must be replaced: its
    // rejection is intentionally swallowed, so returning it would leave the
    // startup spinner forever without a retry.
    if (
      healthRefreshPromiseRef.current &&
      !startupControllerRef.current?.signal.aborted
    ) {
      return healthRefreshPromiseRef.current;
    }
    const controller = new AbortController();
    startupControllerRef.current?.abort();
    startupControllerRef.current = controller;
    setHealthRefreshing(true);
    setRuntimeError("");
    const request = waitForRuntime(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setHealth(value);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setRuntimeError(error instanceof Error ? error.message : "本地 Runtime 无法启动");
        }
        throw error;
      })
      .finally(() => {
        if (startupControllerRef.current === controller) {
          startupControllerRef.current = null;
        }
        if (healthRefreshPromiseRef.current === request) {
          healthRefreshPromiseRef.current = null;
          if (!controller.signal.aborted) setHealthRefreshing(false);
        }
      });
    healthRefreshPromiseRef.current = request;
    return request;
  }, []);

  useEffect(() => {
    if (initialHealth) return;
    void checkRuntimeStartup().catch(() => undefined);
    return () => {
      startupControllerRef.current?.abort();
    };
  }, [checkRuntimeStartup, initialHealth]);

  useEffect(() => {
    if (!health) return;
    let cancelled = false;
    const fetchProviders = providerApi?.listProviders ?? listProviders;
    fetchProviders()
      .then(async (records) => {
        const defaultWorkspaceId=await preferredWorkspaceId().catch(()=>null);
        if (cancelled) return;
        const enabled = records.filter((provider) => provider.enabled);
        setProviders(enabled);
        setComposerState((current) => {
          if (current && enabled.some((provider) => provider.id === current.context.providerId)) return current;
          const context = chooseComposerContext(
            enabled,
            parseStoredModelPreference(
              window.localStorage.getItem(LAST_MODEL_STORAGE_KEY),
            ),
          );
          return context
            ? current
              ? { ...current, context: { ...context, workspaceId: current.context.workspaceId } }
              : initialComposerState({...context,workspaceId:defaultWorkspaceId})
            : null;
        });
        // A recovered listing clears the error it previously caused, but never
        // clobbers an unrelated runtime error.
        setRuntimeError((current) =>
          current.startsWith(PROVIDERS_ERROR_PREFIX) ? "" : current,
        );
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setRuntimeError(
          `${PROVIDERS_ERROR_PREFIX}${
            error instanceof Error ? error.message : "未知错误"
          }`,
        );
      });
    return () => {
      cancelled = true;
    };
  }, [health, providerApi, modelMetadataRevision]);

  const refreshThreads = useCallback((): Promise<void> => {
    return Promise.all([
      threadApi.listThreads(undefined, undefined, "general"),
      threadApi.listThreads("archived", undefined, "general"),
      threadApi.listThreads("trash", undefined, "general"),
      threadApi.listThreads(undefined, undefined, "legal-retrieval"),
      threadApi.listThreads(undefined, undefined, "case-retrieval"),
      threadApi.listThreads(undefined, undefined, "litigation-work"),
      threadApi.listThreads(undefined, undefined, "contract-review"),
    ])
      .then(([active, archived, trashed, legal, cases, litigation, contracts]) => {
        if (
          !Array.isArray(active) ||
          !Array.isArray(archived) ||
          !Array.isArray(trashed) ||
          !Array.isArray(legal) || !Array.isArray(cases)
        ) {
          throw new Error("会话列表响应格式不正确");
        }
        const summaries = active.map(toSummary);
        const legalSummaries = [...legal, ...cases].map(toSummary);
        setThreads(summaries);
        setLegalThreads(legalSummaries);
        setLitigationThreads(litigation.map(toSummary));
        setContractThreads(contracts.map(toSummary));
        setArchivedThreads(archived.map(toSummary));
        setTrashedThreads(trashed.map(toSummary));
        setRuntimeError((current) =>
          current.startsWith(THREADS_ERROR_PREFIX) ? "" : current,
        );
        if (!threadRestoreDoneRef.current) {
          threadRestoreDoneRef.current = true;
          const remembered = window.localStorage.getItem(LAST_THREAD_STORAGE_KEY);
          const rememberedThread =
            summaries.find((thread) => thread.id === remembered) ??
            legalSummaries.find((thread) => thread.id === remembered) ??
            litigation.map(toSummary).find((thread) => thread.id === remembered) ??
            contracts.map(toSummary).find((thread) => thread.id === remembered);
          const rememberedCase = window.localStorage.getItem('verilex:litigation-last-workspace:v1');
          if (!rememberedThread && rememberedCase) {
            setLitigationWorkspaceId(rememberedCase); setActiveView('litigation');
          }
          if (rememberedThread) {
            setActiveThreadId(remembered);
            if (rememberedThread.scene === "contract-review") setActiveView("contract-review");
            if (rememberedThread.scene === "litigation-work") {setActiveView("litigation");setLitigationWorkspaceId(rememberedThread.workspaceId);}
            if (["legal-retrieval", "case-retrieval"].includes(rememberedThread.scene)) {
              setActiveView(rememberedThread.scene === "case-retrieval" ? "case-search" : "legal-search");
            }
          }
        }
      })
      .catch((error: unknown) => {
        setRuntimeError(
          `${THREADS_ERROR_PREFIX}${
            error instanceof Error ? error.message : "未知错误"
          }`,
        );
      });
  }, [threadApi]);

  // Load the real thread list as soon as the Runtime is healthy.
  useEffect(() => {
    if (!health) return;
    void refreshThreads();
  }, [health, refreshThreads, modelMetadataRevision]);

  // Refresh ordering and status when the window regains focus.
  useEffect(() => {
    const onFocus = () => {
      void refreshThreads();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
    };
  }, [refreshThreads]);

  const selectThread = useCallback((threadId: string) => {
    invalidateDraftSend();
    const selected = [...threads, ...archivedThreads, ...contractThreads].find((thread) => thread.id === threadId);
    if (!selected) {
      void threadApi.getThread(threadId).then(record => {
        if (activeThreadIdRef.current !== threadId) return;
        const restored = toSummary(record);
        setThreads(current => [...current.filter(t => t.id !== threadId), restored]);
        if (restored.providerId) setComposerState(materializeComposerTask(initialComposerState({providerId: restored.providerId, model: restored.model, workspaceId: restored.workspaceId, reasoningEffort: restored.reasoningEffort})));
      }).catch(() => setRuntimeError("无法打开搜索结果，请重试。"));
    }
    const abandonedDraftId = hiddenDraftThread?.id;
    if (abandonedDraftId && abandonedDraftId !== threadId) {
      void threadApi.discardDraftThread(abandonedDraftId)
        .then(() => {
          moveDraft(abandonedDraftId, `__new__:agent`);
          setHiddenDraftThread((current) =>
            current?.id === abandonedDraftId ? null : current,
          );
        })
        .catch((error: unknown) => {
          // The first turn may have materialized between the click and this
          // cleanup request. Keep the hidden record until its queued ack
          // arrives so it can still enter the sidebar exactly once.
          setRuntimeError(
            error instanceof Error ? error.message : "无法清理未发送的新任务",
          );
        });
    } else {
      setHiddenDraftThread(null);
    }
    hiddenDraftThreadIdRef.current = null;
    setDraftWebEnabled(false);
    setDraftWebError(null);
    if (selected?.providerId) {
      const context: ComposerContext = {
        providerId: selected.providerId,
        model: selected.model,
        workspaceId: selected.workspaceId,
        reasoningEffort: selected.reasoningEffort,
      };
      setComposerState(materializeComposerTask(initialComposerState(context)));
      window.localStorage.setItem(
        LAST_MODEL_STORAGE_KEY,
        serializeModelPreference({
          providerId: context.providerId,
          model: context.model,
        }),
      );
    }
    activeThreadIdRef.current = threadId;
    setActiveThreadId(threadId);
    window.localStorage.setItem(LAST_THREAD_STORAGE_KEY, threadId);
  }, [hiddenDraftThread, invalidateDraftSend, threadApi, threads, archivedThreads, contractThreads]);

  // Right preview rail lives at the App toolbar (product layout decision
  // 2026-08-31); AgentWorkspaceView receives it as controlled state.
  const [rightRailOpen, setRightRailOpen] = useState(false);
  const [commandsOpen, setCommandsOpen] = useState(false);
  const [searchRequest, setSearchRequest] = useState(0);
  const [panelCommand, setPanelCommand] = useState<{panel:'files'|'terminal';sequence:number}|null>(null);
  const openCommandPanel = (panel:'files'|'terminal') => {
    setPanelCommand(previous => ({panel,sequence:(previous?.sequence ?? 0)+1}));
    setRightRailOpen(true);
  };

  // Back/forward navigation over {view, thread} snapshots — every user-driven
  // view or thread change pushes an entry; the toolbar arrows walk the stack.
  const [navHistory, setNavHistory] = useState<{
    stack: { view: WorkViewId; threadId: string | null }[];
    index: number;
  }>(() => ({ stack: [], index: -1 }));
  const skipNavCaptureRef = useRef(false);

  useEffect(() => {
    if (skipNavCaptureRef.current) {
      skipNavCaptureRef.current = false;
      return;
    }
    const threadId = activeView === "agent" ? activeThreadId : null;
    setNavHistory((current) => {
      const entry = { view: activeView, threadId };
      const top = current.stack[current.index];
      if (
        top &&
        top.view === entry.view &&
        top.threadId === entry.threadId
      ) {
        return current;
      }
      if (current.index === -1) {
        return { stack: [entry], index: 0 };
      }
      const stack = current.stack
        .slice(0, current.index + 1)
        .concat(entry)
        .slice(-50);
      return { stack, index: stack.length - 1 };
    });
  }, [activeView, activeThreadId]);

  const applyNavSnapshot = useCallback(
    (target: {
      view: WorkViewId;
      threadId: string | null;
    }) => {
      let willChange = false;
      if (target.view !== activeView) {
        willChange = true;
        setActiveView(target.view);
      }
      if (
        target.view === "agent" &&
        target.threadId &&
        target.threadId !== activeThreadId &&
        threads.some((thread) => thread.id === target.threadId)
      ) {
        willChange = true;
        selectThread(target.threadId);
      }
      if (willChange) skipNavCaptureRef.current = true;
    },
    [activeThreadId, activeView, selectThread, threads],
  );

  const goBack = useCallback(() => {
    setNavHistory((current) => {
      if (current.index <= 0) return current;
      const target = current.stack[current.index - 1];
      applyNavSnapshot(target);
      return { ...current, index: current.index - 1 };
    });
  }, [applyNavSnapshot]);

  const goForward = useCallback(() => {
    setNavHistory((current) => {
      if (current.index >= current.stack.length - 1) return current;
      const target = current.stack[current.index + 1];
      applyNavSnapshot(target);
      return { ...current, index: current.index + 1 };
    });
  }, [applyNavSnapshot]);


  // Shared handler for sidebar lifecycle actions: refresh all scopes after
  // success, translate the Runtime's active-turn rejection, surface failures.
  const runThreadAction = useCallback(
    (action: () => Promise<unknown>, fallback: string): Promise<void> => {
      return action()
        .then(() => refreshThreads())
        .catch((error: unknown) => {
          if (
            error instanceof RuntimeApiError &&
            error.code === "thread_turn_active"
          ) {
            setRuntimeError("会话正在运行，请先等待完成或停止后再操作。");
            return;
          }
          setRuntimeError(
            error instanceof Error ? error.message : fallback,
          );
        });
    },
    [refreshThreads],
  );

  const renameThreadById = useCallback(
    (threadId: string, title: string) => {
      void runThreadAction(
        () => threadApi.renameThread(threadId, title),
        "无法重命名会话",
      );
    },
    [runThreadAction, threadApi],
  );

  const archiveThreadById = useCallback(
    (threadId: string) => {
      void runThreadAction(
        () => threadApi.archiveThread(threadId),
        "无法归档会话",
      );
    },
    [runThreadAction, threadApi],
  );

  const unarchiveThreadById = useCallback(
    (threadId: string) => {
      void runThreadAction(
        () => threadApi.unarchiveThread(threadId),
        "无法取消归档会话",
      );
    },
    [runThreadAction, threadApi],
  );

  const trashThreadById = useCallback(
    (threadId: string) => {
      void runThreadAction(
        () => threadApi.trashThread(threadId),
        "无法移入回收站",
      );
      if (threadId === activeThreadId) {
        setActiveThreadId(null);
        window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
      }
    },
    [runThreadAction, threadApi, activeThreadId],
  );

  const restoreThreadById = useCallback(
    (threadId: string) => {
      void runThreadAction(
        () => threadApi.restoreThread(threadId),
        "无法恢复会话",
      );
    },
    [runThreadAction, threadApi],
  );

  const deleteThreadById = useCallback(
    (threadId: string) => {
      void runThreadAction(
        () => threadApi.deleteThread(threadId),
        "无法彻底删除会话",
      );
      if (threadId === activeThreadId) {
        setActiveThreadId(null);
        window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
      }
    },
    [runThreadAction, threadApi, activeThreadId],
  );

  // First-message auto naming: after the Runtime durably bound the first
  // turn, replace a still-default title with the message lead-in. Manual
  // renames are never overwritten, and failures stay silent (cosmetic only).
  const handleUserTurnAccepted = useCallback(
    (threadId: string, submission: ComposerSubmission) => {
      // The chat owns live turn state; the workbench reads this summary.
      // Mark it at acceptance so another work action cannot start while the
      // model is running. The existing settled refresh restores final status.
      setLitigationThreads(current => current.map(thread =>
        thread.id === threadId ? {...thread, status: "running"} : thread,
      ));
      if (!submission.preserveDraft && readDraft(threadId) === submission.text) clearDraft(threadId);
      const derived = derivedThreadTitle(
        submission.text,
        submission.attachmentNames[0],
      );
      if (hiddenDraftThread?.id === threadId) {
        const formalThread: ThreadSummary = {
          ...hiddenDraftThread,
          title: derived || hiddenDraftThread.title,
          isDraft: false,
        };
        setHiddenDraftThread(null);
        setDraftWebEnabled(false);
        setDraftWebError(null);
        setThreads((current) => [
          formalThread,
          ...current.filter((thread) => thread.id !== threadId),
        ]);
        setComposerState((current) =>
          current ? materializeComposerTask(current) : current,
        );
        if (activeThreadId === threadId) {
          window.localStorage.setItem(LAST_THREAD_STORAGE_KEY, threadId);
        }
        if (formalThread.providerId) {
          window.localStorage.setItem(
            LAST_MODEL_STORAGE_KEY,
            serializeModelPreference({
              providerId: formalThread.providerId,
              model: formalThread.model,
            }),
          );
        }
        if (derived && isDefaultThreadTitle(hiddenDraftThread.title)) {
          void threadApi.renameThread(threadId, derived)
            .then((record) => {
              setThreads((current) =>
                current.map((thread) =>
                  thread.id === threadId
                    ? {
                        ...thread,
                        title: record.title || derived,
                        updatedAt: record.updated_at || thread.updatedAt,
                      }
                    : thread,
                ),
              );
            })
            .catch(() => undefined);
        }
        return;
      }
      setThreads((current) => {
        const target = current.find((thread) => thread.id === threadId);
        if (!target || !isDefaultThreadTitle(target.title)) return current;
        if (!derived) return current;
        void threadApi.renameThread(threadId, derived)
          .then((record) => {
            setThreads((latest) =>
              latest.map((thread) =>
                thread.id === threadId
                  ? { ...thread, title: record.title, updatedAt: record.updated_at }
                  : thread,
              ),
            );
          })
          .catch(() => undefined);
        return current;
      });
    },
    [activeThreadId, hiddenDraftThread, threadApi],
  );

  const refreshRuntimeHealth = useCallback((): Promise<void> => {
    if (healthRefreshPromiseRef.current) return healthRefreshPromiseRef.current;
    setHealthRefreshing(true);
    const request = refreshHealth()
      .then((updatedHealth) => {
        setHealth(updatedHealth);
        setRuntimeError("");
      })
      .catch((error: unknown) => {
        setRuntimeError(
          error instanceof Error ? error.message : "无法确认最新 Runtime 状态",
        );
        throw error;
      })
      .finally(() => {
        healthRefreshPromiseRef.current = null;
        setHealthRefreshing(false);
      });
    healthRefreshPromiseRef.current = request;
    return request;
  }, [refreshHealth]);

  const resolveComposerContext = useCallback(async (reportUnavailable: (message: string) => void = setRuntimeError): Promise<ComposerContext | null> => {
    let enabled = providers;
    let context = chooseComposerContext(
      enabled,
      parseStoredModelPreference(window.localStorage.getItem(LAST_MODEL_STORAGE_KEY)),
    );
    if (!context) {
      // The cached list may be stale (initial fetch failed, or a provider was
      // just configured); refresh once before declaring nothing usable.
      try {
        const records = await (providerApi?.listProviders ?? listProviders)();
        enabled = records.filter((provider) => provider.enabled);
        setProviders(enabled);
        context = chooseComposerContext(
          enabled,
          parseStoredModelPreference(window.localStorage.getItem(LAST_MODEL_STORAGE_KEY)),
        );
      } catch {
        // Fall through to the explicit guidance below.
      }
    }
    if (!context) {
      reportUnavailable("尚未配置可用模型，请先在设置页连接模型。");
      return null;
    }
    return context;
  }, [providerApi, providers]);

  // 📁 新建项目：选择文件夹 → 复用或创建工作区 → 建立绑定该工作区的会话
  // 并直接打开。项目即「带工作区的会话」，在侧栏项目区展示。
  const createProject = useCallback(async (): Promise<void> => {
    let directoryPath: string | null = null;
    try {
      directoryPath = await pickWorkspaceDirectory();
    } catch (error) {
      setRuntimeError(
        error instanceof Error ? error.message : "无法选择项目文件夹",
      );
      return;
    }
    if (!directoryPath) return;
    try {
      const workspaces = await listWorkspaces();
      const existing = Array.isArray(workspaces)
        ? workspaces.find((workspace) => workspace.root_path === directoryPath)
        : undefined;
      const workspace = existing ?? await createWorkspace({
        root_path: directoryPath,
        display_name: directoryName(directoryPath),
        allow_read: true,
        allow_write: true,
      });
      const context = await resolveComposerContext();
      if (!context) return;
      const record = await threadApi.createThread({
        title: workspace.display_name,
        model: context.model,
        provider_id: context.providerId,
        workspace_id: workspace.id,
      });
      setComposerState(
        materializeComposerTask(
          initialComposerState({
            providerId: context.providerId,
            model: context.model,
            workspaceId: workspace.id,
            reasoningEffort: context.reasoningEffort,
          }),
        ),
      );
        setDraftWebEnabled(false);
      setDraftWebError(null);
      setPendingAgentMessage(null);
      setActiveView("agent");
      setRuntimeError("");
      selectThread(record.id);
      await refreshThreads();
    } catch (error) {
      setRuntimeError(error instanceof Error ? error.message : "无法创建项目");
    }
  }, [refreshThreads, resolveComposerContext, selectThread, threadApi]);

  const startNewTask = useCallback(async (): Promise<void> => {
    invalidateDraftSend();
    const context = await resolveComposerContext();
    if (!context) return;
    try {context.workspaceId=await preferredWorkspaceId();}
    catch {setRuntimeError("无法读取默认工作目录，请重试。");return;}
    const abandonedDraftId = hiddenDraftThread?.id;
    if (abandonedDraftId) {
      try {
        await threadApi.discardDraftThread(abandonedDraftId);
        moveDraft(abandonedDraftId, `__new__:agent`);
      } catch (error) {
        setRuntimeError(
          error instanceof Error ? error.message : "无法清理未发送的新任务",
        );
        return;
      }
    }
    const current = composerState ?? initialComposerState(context);
    setComposerState(beginNewTask(current, context));
    hiddenDraftThreadIdRef.current = null;
    setHiddenDraftThread(null);
    activeThreadIdRef.current = null;
    setActiveThreadId(null);
    setDraftWebEnabled(false);
    setDraftWebError(null);
    window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
    setActiveView("agent");
    setRuntimeError("");
  }, [composerState, hiddenDraftThread, invalidateDraftSend, resolveComposerContext, threadApi]);

  // Legal search page (plugin channel keeps the original scene-thread flow,
  // but the app stays on the page; the first message is queued as a pending
  // agent message and fires when the user opens the research thread).
  const [legalMcpServers, setLegalMcpServers] = useState<McpServerRecord[]>([]);

  useEffect(() => {
    if (activeView !== "legal-search" && activeView !== "case-search") return;
    const controller = new AbortController();
    mcpApi
      .listServers(controller.signal)
      .then((servers) => setLegalMcpServers(servers))
      .catch(() => setLegalMcpServers([]));
    return () => controller.abort();
  }, [activeView, mcpApi]);

  const [legalThreads, setLegalThreads] = useState<ThreadSummary[]>([]);
  const [legalStarting, setLegalStarting] = useState(false);
  const legalStartInFlightRef = useRef(false);
  useEffect(() => {
    const selected = legalThreads.find((t) => t.id === activeThreadId);
    if (!selected || !["legal-retrieval", "case-retrieval"].includes(selected.scene) || (selected.scene === "legal-retrieval" && activeView === "legal-search") || (selected.scene === "case-retrieval" && activeView === "case-search")) return;
    activeThreadIdRef.current = null;
    setActiveThreadId(null);
    setPendingAgentMessage(null);
    window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
  }, [activeView, activeThreadId, legalThreads]);

  const refreshLegalHistory = useCallback((): Promise<void> => {
    return Promise.all([
      threadApi.listThreads(undefined, undefined, "legal-retrieval"),
      threadApi.listThreads(undefined, undefined, "case-retrieval"),
    ])
      .then(([legal, cases]) => setLegalThreads([...legal, ...cases].map(toSummary)))
      .catch(() => undefined);
  }, [threadApi]);

  useEffect(() => {
    if (activeView !== "legal-search" && activeView !== "case-search") return;
    void refreshLegalHistory();
  }, [activeView, refreshLegalHistory]);

  const startLegalSearch = useCallback(async (
    question: string,
    serverId: string | null,
    skills: string[],
    knowledgeLibraryIds: string[] = [],
  ): Promise<void> => {
    if (legalStartInFlightRef.current) return;
    const startView = activeView;
    const caseSearch = activeView === "case-search";
    legalStartInFlightRef.current = true;
    setLegalStarting(true);
    setRuntimeError("");
    try {
      const context = composerState?.context ?? await resolveComposerContext();
      if (!context) {
        setRuntimeError("还没有可用的模型，请先在设置中完成连接。");
        return;
      }
      const record = await threadApi.createThread({
        title: question.slice(0, 30),
        model: context.model,
        provider_id: context.providerId,
        workspace_id: context.workspaceId,
        scene: caseSearch ? "case-retrieval" : "legal-retrieval",
        ...(serverId ? {legal_channel: "plugin" as const} : knowledgeLibraryIds.length ? {} : {legal_channel: "web" as const}),
        legal_skills: skills,
      });
      if (activeViewRef.current !== startView) return;
      if (serverId) {
        await mcpApi.selectServer(record.id, serverId);
      }
      if (activeViewRef.current !== startView) return;
      const summary = toSummary(record);
      activeThreadIdRef.current = record.id;
      setActiveThreadId(record.id);
      setLegalThreads((current) => [
        summary,
        ...current.filter((thread) => thread.id !== record.id),
      ]);
      setComposerState(
        materializeComposerTask(
          initialComposerState({
            providerId: context.providerId,
            model: context.model,
            workspaceId: context.workspaceId,
            reasoningEffort: context.reasoningEffort,
          }),
        ),
      );
      setPendingAgentMessage({
        threadId: record.id,
        operationId: ++draftSendOperationIdRef.current,
        submission: {
          text: question,
          knowledgeLibraryIds: [...knowledgeLibraryIds],
          attachmentUploadIds: [],
          attachmentNames: [],
        },
      });
      window.localStorage.setItem(LAST_THREAD_STORAGE_KEY, record.id);
    } catch (error) {
      setRuntimeError(
        error instanceof Error ? error.message : "无法创建检索，请重试。",
      );
    } finally {
      legalStartInFlightRef.current = false;
      setLegalStarting(false);
    }
  }, [activeView, composerState, legalStarting, mcpApi, resolveComposerContext, threadApi]);

  const selectLegalHistory = useCallback((threadId: string) => {
    activeThreadIdRef.current = threadId;
    setActiveThreadId(threadId);
    window.localStorage.setItem(LAST_THREAD_STORAGE_KEY, threadId);
  }, []);

  const deleteLegalHistory = useCallback((threadId: string) => {
    threadApi
      .trashThread(threadId)
      .then(() => {
        setLegalThreads((current) =>
          current.filter((thread) => thread.id !== threadId),
        );
        if (activeThreadIdRef.current === threadId) {
          activeThreadIdRef.current = null;
          setActiveThreadId(null);
          window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
        }
      })
      .catch(() => {
        setRuntimeError("删除检索记录失败，请重试。");
      });
  }, [threadApi]);

  const [litigationThreads, setLitigationThreads] = useState<ThreadSummary[]>([]);
  const [litigationWorkspaces, setLitigationWorkspaces] = useState<WorkspaceRecord[]>([]);
  const [litigationWorkspaceId, setLitigationWorkspaceId] = useState<string | null>(null);
  const litigationWorkspaceRef = useRef(litigationWorkspaceId);
  litigationWorkspaceRef.current = litigationWorkspaceId;
  const [litigationFilesCollapsed, setLitigationFilesCollapsed] = useState(false);
  const litigationDrafts = useRef<Record<string, string>>({});
  const [litigationStarting, setLitigationStarting] = useState(false);
  const litigationStartRef = useRef(false);
  useEffect(() => {
    if (activeView !== 'litigation' && threadRestoreDoneRef.current) {
      window.localStorage.removeItem('verilex:litigation-last-workspace:v1');
    }
  }, [activeView]);
  useEffect(() => {
    if (activeView !== "litigation") return;
    let current = true;
    listWorkspaces().then(rows => { if(current) setLitigationWorkspaces(rows); }).catch(() => {if(current)setRuntimeError("无法读取案件文件夹，请重试。");});
    return () => {current=false;};
  }, [activeView]);
  useEffect(() => {
    if (activeView === "litigation" || !litigationThreads.some(t => t.id === activeThreadId && t.scene === "litigation-work")) return;
    activeThreadIdRef.current = null; setActiveThreadId(null); setPendingAgentMessage(null);
    window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
  }, [activeView, activeThreadId, litigationThreads]);
  const activateLitigationThread = useCallback((id: string | null, workspaceId: string | null) => {
    const previousKey = `${litigationWorkspaceRef.current}:${activeThreadIdRef.current ?? 'new'}`;
    litigationDrafts.current[previousKey] = agentDraft;
    const selected = litigationThreads.find(t => t.id === id && t.workspaceId === workspaceId);
    activeThreadIdRef.current = selected?.id ?? null;
    setActiveThreadId(selected?.id ?? null);
    setPendingAgentMessage(null);
    setAgentDraft(litigationDrafts.current[`${workspaceId}:${selected?.id ?? 'new'}`] ?? '');
    setDraftWebEnabled(false); setDraftWebError(null);
    if (selected?.providerId) {
      setComposerState(materializeComposerTask(initialComposerState({
        providerId:selected.providerId, model:selected.model, workspaceId:selected.workspaceId,
        reasoningEffort:selected.reasoningEffort,
      })));
    }
    if (selected) window.localStorage.setItem(LAST_THREAD_STORAGE_KEY, selected.id);
    else window.localStorage.removeItem(LAST_THREAD_STORAGE_KEY);
    if (workspaceId) {
      window.localStorage.setItem(`verilex:litigation-discussion:v1:${workspaceId}`, selected?.id ?? '');
    }
  }, [agentDraft, litigationThreads]);
  const selectLitigationWorkspace = useCallback((id: string) => {
    const remembered = window.localStorage.getItem(`verilex:litigation-discussion:v1:${id}`);
    const existing = litigationThreads.find(t => t.workspaceId === id && t.id === remembered)
      ?? (remembered === '' ? undefined : litigationThreads.find(t => t.workspaceId === id));
    activateLitigationThread(existing?.id ?? null, id || null);
    setLitigationWorkspaceId(id || null);
    if (id) window.localStorage.setItem('verilex:litigation-last-workspace:v1', id);
    else window.localStorage.removeItem('verilex:litigation-last-workspace:v1');
  }, [litigationThreads, activateLitigationThread]);
  const chooseLitigationWorkspace = useCallback(async () => {
    try {
      const path = await pickWorkspaceDirectory(); if(!path)return;
      const rows = await listWorkspaces();
      const selected = rows.find(w => w.root_path === path) ?? await createWorkspace({root_path:path, display_name:directoryName(path), allow_read:true, allow_write:true});
      setLitigationWorkspaces([...rows.filter(w=>w.id!==selected.id),selected]);
      selectLitigationWorkspace(selected.id);
    }catch(error){setRuntimeError(error instanceof Error ? error.message : "无法选择案件文件夹。");}
  }, [selectLitigationWorkspace]);
  const startLitigation = useCallback(async (input: string | WorkRequest | ComposerSubmission): Promise<string|null> => {
    const work = typeof input !== "string" && 'kind' in input ? input : null;
    const submission = typeof input !== "string" && 'text' in input ? snapshotComposerSubmission(input) : null;
    const text = typeof input === "string" ? input : work ? work.message : submission?.text;
    const workspaceId = litigationWorkspaceId;
    if(!workspaceId || litigationStartRef.current || activeViewRef.current!=="litigation" || litigationWorkspaceRef.current!==workspaceId)return null;
    litigationStartRef.current=true;setLitigationStarting(true);setRuntimeError("");setLitigationError("");
    let unboundThread: string | null = null;
    const startingThreadId = activeThreadIdRef.current;
    litigationDrafts.current[`${workspaceId}:${startingThreadId ?? 'new'}`] = agentDraft;
    try {
      const context = composerState?.context ?? await resolveComposerContext(setLitigationError); if(!context)return null;
      let summary = work ? undefined : litigationThreads.find(t=>t.id===startingThreadId && t.workspaceId===workspaceId);
      if(!summary){
        const record=await threadApi.createThread({title:work?.title ?? (text?.split('\n')[0] || "新会话").slice(0,40),model:context.model,provider_id:context.providerId,workspace_id:workspaceId,scene:"litigation-work"});
        unboundThread=record.id;
        if(work) {
          const {title: _title, message: _message, editorContext: _editorContext, ...binding}=work;
          await litigationWorkClient.bind(workspaceId,record.id,binding);
        }
        summary=toSummary(record);setLitigationThreads(current=>[summary!,...current]);
        unboundThread=null;
      }
      if(activeViewRef.current!=="litigation" || litigationWorkspaceRef.current!==workspaceId || activeThreadIdRef.current!==startingThreadId)return null;
      if(!work && draftWebEnabled){
        const record=await webAccessApi.put(summary.id,true);
        if(activeViewRef.current!=="litigation" || litigationWorkspaceRef.current!==workspaceId || activeThreadIdRef.current!==startingThreadId)return null;
        setWebAccessHandoff({threadId:summary.id,operationId:draftSendOperationIdRef.current+1,record});
        setDraftWebEnabled(false);setDraftWebError(null);
      }
      activeThreadIdRef.current=summary.id;setActiveThreadId(summary.id);
      setComposerState(materializeComposerTask(initialComposerState({providerId:summary.providerId ?? context.providerId,model:summary.model,workspaceId,reasoningEffort:summary.reasoningEffort})));
      setPendingAgentMessage(text || submission?.attachmentUploadIds.length ? {threadId:summary.id,operationId:++draftSendOperationIdRef.current,submission:submission??{text:text??"",attachmentUploadIds:[],attachmentNames:[],...(work?{litigationMaterials:{workspaceId,paths:[...work.materials]}}:{}),...(work?.editorContext?{editorContext:work.editorContext}:{})}} : null);
      window.localStorage.setItem(LAST_THREAD_STORAGE_KEY,summary.id);
      window.localStorage.setItem(`verilex:litigation-discussion:v1:${workspaceId}`,summary.id);
      if(text || submission?.attachmentUploadIds.length){litigationDrafts.current[`${workspaceId}:new`] = '';setAgentDraft(current => work || current.trim() === text?.trim() ? '' : current);}
      return summary.id;
    }catch(error){if(unboundThread) {try {await threadApi.trashThread(unboundThread);} catch { /* Keep failed setup visible in history; never send it. */ }} setLitigationError(error instanceof Error ? error.message : "无法开始诉讼工作。");}
    finally{litigationStartRef.current=false;setLitigationStarting(false);}
    return null;
  }, [litigationWorkspaceId,litigationThreads,litigationWorkspaces,composerState,resolveComposerContext,threadApi,agentDraft,draftWebEnabled,webAccessApi]);
  const litigationDraftCreation=useRef<Promise<string|null>|null>(null);
  const ensureLitigationDiscussion=useCallback(async()=>{
    const current=litigationThreads.find(row=>row.id===activeThreadIdRef.current&&row.workspaceId===litigationWorkspaceRef.current);
    if(current)return current.id;
    if(!litigationDraftCreation.current)litigationDraftCreation.current=startLitigation('').finally(()=>{litigationDraftCreation.current=null;});
    return litigationDraftCreation.current;
  },[litigationThreads,startLitigation]);

  const startContractReview = useCallback(async (file: File, options: import("../runtime/contractReviewClient").StartReviewOptions) => {
    setRuntimeError("");
    let unstartedReviewId: string | null = null;
    try {
      const context = composerState?.context ?? await resolveComposerContext((message: string) => setRuntimeError(message));
      if (!context) throw new Error("还没有可用的模型，请先在设置中完成连接。");
      const handle = await contractReviewApi.create(file, {
        ...options,
        providerId: context.providerId,
        model: context.model,
      });
      unstartedReviewId = handle.review_id;
      const created = toSummary(await threadApi.getThread(handle.thread_id));
      setContractThreads(current => [created, ...current.filter(row => row.id !== created.id)]);
      if (activeViewRef.current !== "contract-review") {
        await contractReviewApi.stop(handle.review_id);
        unstartedReviewId = null;
        return;
      }
      const partyText = options.party === "party_a" ? "甲方" : options.party === "party_b" ? "乙方" : "中立";
      const strictnessText = options.strictness === "loose" ? "宽松（只报重大风险）" : options.strictness === "strict" ? "严格（事无巨细）" : "常规";
      const text = [
        `请审查这份合同。审查技能：${options.skill}；代表方：${partyText}；审查口径：${strictnessText}；批注署名：${options.signature}。`,
        "请先通读合同，再逐条通过 contract_opinion 工具提交审查意见（锚定文本逐字摘自合同原文）。",
        options.retrieval
          ? "涉及法条依据时先用 law_search/law_read 检索，只检索法律概念词；援引必须来自本轮真实检索结果。"
          : "本次未开启法条检索：不要输出任何规范性文件援引（提法律概念可以），需要依据时如实说明。",
        ...(options.requirements?.trim() ? [`本次审查要求：\n${options.requirements.trim()}`] : []),
      ].join("\n");
      activeThreadIdRef.current = handle.thread_id;
      setActiveThreadId(handle.thread_id);
      setComposerState(materializeComposerTask(initialComposerState({ providerId: context.providerId, model: context.model, workspaceId: null, reasoningEffort: "auto" })));
      setPendingAgentMessage({
        threadId: handle.thread_id,
        operationId: ++draftSendOperationIdRef.current,
        submission: { text, attachmentUploadIds: [handle.upload_id], attachmentNames: [file.name] },
      });
      unstartedReviewId = null;
      window.localStorage.setItem(LAST_THREAD_STORAGE_KEY, handle.thread_id);
    } catch (error) {
      if (unstartedReviewId) {
        try { await contractReviewApi.stop(unstartedReviewId); }
        catch { setRuntimeError("审查未能启动，自动停止也未成功；请返回审查历史点“停止审查”后重试。"); throw error; }
      }
      setRuntimeError(error instanceof Error ? error.message : "无法开始合同审查。");
      throw error;
    }
  }, [composerState, resolveComposerContext, threadApi]);

  const selectContractReviewThread = useCallback((threadId: string) => {
    setActiveView("contract-review");
    selectThread(threadId);
  }, [selectThread]);

  const createHiddenDraft = useCallback(async (): Promise<string | null> => {
    const alreadyBound = hiddenDraftThreadIdRef.current;
    if (alreadyBound) return alreadyBound;
    if (hiddenDraftCreationRef.current) {
      try {
        return (await hiddenDraftCreationRef.current.promise)?.threadId ?? null;
      } catch {
        // MCP owns the original failure and renders fixed, non-sensitive copy.
        // Other callers sharing the same draft creation only need a safe null.
        return null;
      }
    }
    const generation = hiddenDraftCreationGenerationRef.current;
    const creation = (async (): Promise<HiddenDraftCreationOutcome | null> => {
      const context = composerState?.context ?? await resolveComposerContext();
      if (!context) return null;
      const threadNumber = threads.length + 1;
      const title = threadNumber === 1 ? "新会话" : `新会话 ${threadNumber}`;
      try {
        const record = await threadApi.createThread({
          title,
          model: context.model,
          provider_id: context.providerId,
          workspace_id: context.workspaceId,
          is_draft: true,
        });
        if (hiddenDraftCreationGenerationRef.current !== generation) {
          try {
            await threadApi.discardDraftThread(record.id);
          } catch {
            // Best effort only: the Runtime's 24-hour stale-draft cleanup is
            // the fallback if the local task was abandoned during creation.
          }
          return null;
        }
        const thread = toSummary(record);
        hiddenDraftThreadIdRef.current = thread.id;
        setHiddenDraftThread(thread);
        moveDraft(`__new__:${activeViewRef.current}`, thread.id);
      activeThreadIdRef.current = thread.id;
        setActiveThreadId(thread.id);
        setComposerState((current) =>
          bindHiddenDraft(current ?? initialComposerState(context), thread.id),
        );
        setActiveView("agent");
        return { threadId: thread.id, atomicResult: null };
      } catch (error) {
        if (hiddenDraftCreationGenerationRef.current === generation) {
          setRuntimeError(error instanceof Error ? error.message : "无法创建任务");
        }
        return null;
      }
    })();
    const flight: HiddenDraftCreationFlight = {
      promise: creation,
      atomicTarget: null,
    };
    hiddenDraftCreationRef.current = flight;
    try {
      return (await creation)?.threadId ?? null;
    } finally {
      if (hiddenDraftCreationRef.current === flight) {
        hiddenDraftCreationRef.current = null;
      }
    }
  }, [composerState, resolveComposerContext, threadApi, threads.length]);

  const selectDraftMcp = useCallback(async (
    target: McpSelectionTarget,
    draftThread: DraftThreadInput,
  ): Promise<DraftMcpSelectionOutcome | null> => {
    const selectOnExisting = async (threadId: string): Promise<DraftMcpSelectionOutcome> => {
      if (typeof target === "string") {
        await mcpApi.selectServer(threadId, target);
        return { threadId, result: null };
      }
      const result = await mcpApi.atomicSelection({
        existingThreadId: threadId,
        draftThread: null,
        existingServerId: null,
        createServer: target,
      });
      return { threadId, result };
    };

    if(activeViewRef.current==='litigation'){
      const id=await ensureLitigationDiscussion();return id?selectOnExisting(id):null;
    }
    const alreadyBound = hiddenDraftThreadIdRef.current;
    if (alreadyBound) return selectOnExisting(alreadyBound);

    const currentFlight = hiddenDraftCreationRef.current;
    if (currentFlight) {
      const outcome = await currentFlight.promise;
      if (!outcome) return null;
      if (currentFlight.atomicTarget === target && outcome.atomicResult) {
        return { threadId: outcome.threadId, result: outcome.atomicResult };
      }
      return selectOnExisting(outcome.threadId);
    }

    const generation = hiddenDraftCreationGenerationRef.current;
    const creation = (async (): Promise<HiddenDraftCreationOutcome | null> => {
      const result = await mcpApi.atomicSelection(typeof target === "string"
        ? {
            existingThreadId: null,
            draftThread,
            existingServerId: target,
            createServer: null,
          }
        : {
            existingThreadId: null,
            draftThread,
            existingServerId: null,
            createServer: target,
          });
      if (
        hiddenDraftCreationGenerationRef.current !== generation
        || !draftContextMatches(composerContextRef.current, draftThread)
      ) {
        try {
          await threadApi.discardDraftThread(result.thread.id);
        } catch {
          // Best effort only; Runtime stale-draft cleanup remains the fallback.
        }
        return null;
      }
      const context: ComposerContext = {
        providerId: result.thread.providerId ?? draftThread.providerId,
        model: result.thread.model,
        workspaceId: result.thread.workspaceId,
        reasoningEffort: result.thread.reasoningEffort,
      };
      const thread: ThreadSummary = {
        id: result.thread.id,
        backendId: result.thread.id,
        title: result.thread.title,
        providerId: context.providerId,
        model: context.model,
        workspaceId: context.workspaceId,
        createdAt: result.thread.createdAt,
        updatedAt: result.thread.updatedAt,
        status: result.thread.status,
        isDraft: result.thread.isDraft,
        reasoningEffort: context.reasoningEffort,
        scene: result.thread.scene ?? "general",
      };
      hiddenDraftThreadIdRef.current = thread.id;
      moveDraft(`__new__:${activeViewRef.current}`, thread.id);
      activeThreadIdRef.current = thread.id;
      setHiddenDraftThread(thread);
      setActiveThreadId(thread.id);
      setComposerState((current) =>
        bindHiddenDraft(current ?? initialComposerState(context), thread.id),
      );
      return { threadId: thread.id, atomicResult: result };
    })();
    const flight: HiddenDraftCreationFlight = {
      promise: creation,
      atomicTarget: target,
    };
    hiddenDraftCreationRef.current = flight;
    try {
      const outcome = await creation;
      return outcome
        ? { threadId: outcome.threadId, result: outcome.atomicResult }
        : null;
    } finally {
      if (hiddenDraftCreationRef.current === flight) {
        hiddenDraftCreationRef.current = null;
      }
    }
  }, [mcpApi, threadApi, ensureLitigationDiscussion]);

  // The default view is the agent chat with no thread yet; sending from there
  // must create the session first and deliver the message once it exists.
  const sendWithoutThread = useCallback(async (
    input: ComposerSubmission,
  ): Promise<void> => {
    if (draftSendOperationRef.current !== null) return;
    const submission = snapshotComposerSubmission(input);
    const requested = {
      operationId: ++draftSendOperationIdRef.current,
      generation: hiddenDraftCreationGenerationRef.current,
      threadId: null,
      submission,
    } as const;
    draftSendOperationRef.current = requested;
    setDraftSendPreparing(true);
    let handedOff = false;
    try {
      const threadId = await createHiddenDraft();
      const bound = { ...requested, threadId };
      if (
        !threadId ||
        draftSendOperationRef.current?.operationId !== requested.operationId ||
        hiddenDraftCreationGenerationRef.current !== requested.generation
      ) return;
      draftSendOperationRef.current = bound;
      const stillOwnsDraft = () =>
        draftSendOperationRef.current?.operationId === bound.operationId &&
        hiddenDraftCreationGenerationRef.current === bound.generation &&
        hiddenDraftThreadIdRef.current === bound.threadId &&
        activeThreadIdRef.current === bound.threadId;
      if (draftWebEnabled) {
        let record: WebAccessRecord;
        try {
          record = await webAccessApi.put(threadId, true);
        } catch (error) {
          if (!stillOwnsDraft()) return;
          const message = error instanceof Error
            ? error.message
            : "无法保存联网状态，请重试。";
          setDraftWebError(message);
          setRuntimeError(message);
          return;
        }
        if (!stillOwnsDraft()) return;
        setDraftWebError(null);
        if (draftWebError) {
          setRuntimeError((current) => current === draftWebError ? "" : current);
        }
        setWebAccessHandoff({
          threadId,
          operationId: bound.operationId,
          record,
        });
      }
      if (!stillOwnsDraft()) return;
      setPendingAgentMessage({
        threadId,
        operationId: bound.operationId,
        submission: bound.submission,
      });
      handedOff = true;
    } finally {
      if (
        !handedOff &&
        draftSendOperationRef.current?.operationId === requested.operationId
      ) {
        draftSendOperationRef.current = null;
        setDraftSendPreparing(false);
      }
    }
  }, [createHiddenDraft, draftWebEnabled, draftWebError, webAccessApi]);

  const changeComposerContext = useCallback(async (
    context: ComposerContext,
  ): Promise<void> => {
    const target =
      threads.find((thread) => thread.id === activeThreadId) ??
      archivedThreads.find((thread) => thread.id === activeThreadId) ??
      legalThreads.find((thread) => thread.id === activeThreadId) ??
      litigationThreads.find((thread) => thread.id === activeThreadId) ??
      contractThreads.find((thread) => thread.id === activeThreadId) ??
      (hiddenDraftThread?.id === activeThreadId ? hiddenDraftThread : null);
    if (target?.backendId) {
      try {
        const record = await threadApi.updateThreadContext(target.backendId, {
          provider_id: context.providerId,
          model: context.model,
          workspace_id: context.workspaceId,
          reasoning_effort: context.reasoningEffort,
        });
        const updated = toSummary(record);
        if (hiddenDraftThread?.id === updated.id) setHiddenDraftThread(updated);
        setLitigationThreads(current => current.map(t => t.id === updated.id ? updated : t));
        setContractThreads(current => current.map(t => t.id === updated.id ? updated : t));
        setLegalThreads(current => current.map(t => t.id === updated.id ? updated : t));
        setArchivedThreads(current => current.map(t => t.id === updated.id ? updated : t));
        setThreads((current) =>
          current.map((thread) => thread.id === updated.id ? updated : thread),
        );
      } catch (error) {
        setRuntimeError(
          error instanceof Error ? error.message : "无法切换任务设置",
        );
        throw error;
      }
    }
    setComposerState((current) =>
      current ? { ...current, context } : initialComposerState(context),
    );
    window.localStorage.setItem(
      LAST_MODEL_STORAGE_KEY,
      serializeModelPreference({
        providerId: context.providerId,
        model: context.model,
      }),
    );
    setRuntimeError("");
  }, [activeThreadId, hiddenDraftThread, threadApi, threads, archivedThreads, legalThreads, litigationThreads, contractThreads]);

  const activeThread =
    (activeView === "scheduled" ? scheduledThread : null) ??
    threads.find((thread) => thread.id === activeThreadId) ??
    archivedThreads.find((thread) => thread.id === activeThreadId) ??
    legalThreads.find((thread) => thread.id === activeThreadId) ??
    contractThreads.find((thread) => thread.id === activeThreadId) ??
    litigationThreads.find((thread) => thread.id === activeThreadId) ??
    (hiddenDraftThread?.id === activeThreadId ? hiddenDraftThread : null);

  let content;
  if (!health && activeView === "settings" && settingsCategory === "help") {
    content = <section className="settings-view page-view settings-redesigned"><div className="settings-content"><header className="settings-page-heading"><h1>帮助与反馈</h1><p>使用说明与问题反馈</p></header><HelpCenter appVersion={updates.state.currentVersion} runtimeConnected={false}/></div></section>;
  } else if (!health && activeView === "settings" && settingsCategory === "general") {
    content = <section className="settings-view page-view settings-redesigned"><div className="settings-content"><header className="settings-page-heading"><h1>通用</h1><p>后台恢复后显示其他通用设置。</p></header><ShortcutSettings/></div></section>;
  } else if (!health && runtimeError) {
    content = (
      <section className="startup-state" role="alert">
        <h1>本地 Runtime 未能启动</h1>
        <p>{runtimeError}</p>
      </section>
    );
  } else if (!health) {
    content = (
      <section className="startup-state" aria-live="polite">
        <span className="startup-pulse" aria-hidden="true" />
        <h1>正在启动本地 Runtime</h1>
        <p>VeriLex 正在建立仅限本机的安全连接。</p>
      </section>
    );
  } else {
    content = (
      <WorkspaceRouter
        calendarDate={calendarDate}
        onCalendarEventsChanged={() => setCalendarRefresh(value => value + 1)}
        onOpenScheduledRun={async (id) => {const record=await threadApi.getThread(id);setScheduledThread(toSummary(record));}}
        onCloseScheduledRun={() => setScheduledThread(null)}
        litigationThreads={litigationThreads} litigationFilesCollapsed={litigationFilesCollapsed}
        onExitLitigationCase={() => {selectLitigationWorkspace('');setActiveView('agent');}}
        onSelectLitigationThread={(id) => activateLitigationThread(id, litigationWorkspaceId)}
        onNewLitigationDiscussion={() => activateLitigationThread(null, litigationWorkspaceId)}
        onRenameLitigationThread={async(id,title)=>{await threadApi.renameThread(id,title);await refreshThreads();}}
        litigationWorkspaces={litigationWorkspaces} litigationWorkspaceId={litigationWorkspaceId}
        litigationStarting={litigationStarting} onSelectLitigationWorkspace={selectLitigationWorkspace}
        onChooseLitigationWorkspace={() => {void chooseLitigationWorkspace();}}
        onStartLitigation={(message) => {void startLitigation(message);}}
        onStartContractReview={startContractReview}
        onSelectContractReviewThread={selectContractReviewThread}
        onRegisterContractLeaveGuard={registerContractLeaveGuard}
        activeView={activeView}
        hasModel={health.configured.models > 0}
        configured={health.configured}
        runtimeVersion={health.version}
        previewOpen={rightRailOpen}
        onPreviewOpenChange={setRightRailOpen}
        reduceMotion={reduceMotion}
        thread={activeThread}
        composerContext={composerState
          ? activeThread && activeThread.providerId === null
            ? { ...composerState.context, workspaceId: activeThread.workspaceId, reasoningEffort: "auto" }
            : composerState.context
          : null}
        providers={providers}
        draft={agentDraft}
        onDraftChange={setAgentDraft}
        draftWebEnabled={draftWebEnabled}
        draftWebError={draftWebError}
        onDraftWebEnabledChange={(enabled) => {
          setDraftWebEnabled(enabled);
          setDraftWebError(null);
        }}
        webAccessApi={webAccessApi}
        webAccessHandoff={webAccessHandoff}
        onWebAccessHandoffConsumed={consumeWebAccessHandoff}
        pendingAgentMessage={pendingAgentMessage}
        draftSendPreparing={draftSendPreparing || (activeView === "litigation" && litigationStarting)}
        onPendingAgentMessageSent={(operationId) => {
          const operation = draftSendOperationRef.current;
          setPendingAgentMessage((current) =>
            current?.operationId === operationId ? null : current,
          );
          consumeWebAccessHandoff(operationId);
          if (operation?.operationId === operationId) {
            draftSendOperationRef.current = null;
            setDraftSendPreparing(false);

          }
        }}
        onSendWithoutThread={activeView === "litigation" ? input=>{void startLitigation(input);} : sendWithoutThread}
        onReduceMotionChange={setReduceMotion}
        providerApi={providerApi}
        runtimeStatusError={runtimeError}
        onProvidersChanged={refreshRuntimeHealth}
        onModelMetadataChanged={() => setModelMetadataRevision((revision) => revision + 1)}
        modelMetadataRevision={modelMetadataRevision}
        updates={updates}
        selectedCategory={settingsCategory}
        onSelectCategory={setSettingsCategory}
        query={settingsQuery}
        onQueryChange={setSettingsQuery}
        onWorkspaceChanged={(id,revoked) => {
          void refreshThreads();
          void listWorkspaces().then(setLitigationWorkspaces).catch(()=>setRuntimeError("无法刷新授权文件夹，请重新打开设置。"));
          if(revoked) {
            if(litigationWorkspaceId===id)selectLitigationWorkspace("");
            setComposerState(current=>current?.context.workspaceId===id?{...current,context:{...current.context,workspaceId:null}}:current);
          }
        }}
        onOpenCapabilities={() => navigateSafely(() => setActiveView("plugins"))}
        onOpenSettings={() => navigateSafely(() => {setSettingsCategory("models");setSettingsQuery("");setActiveView("settings");})}
        onThreadsRefresh={refreshThreads}
        onUserTurnAccepted={handleUserTurnAccepted}
        onEnsureDraftThread={activeView === "litigation" ? ensureLitigationDiscussion : () => createHiddenDraft()}
        onComposerContextChange={changeComposerContext}
        mcpApi={mcpApi}
        mcpDraftThread={composerState ? {
          title: threads.length === 0 ? "新会话" : `新会话 ${threads.length + 1}`,
          providerId: composerState.context.providerId,
          model: composerState.context.model,
          workspaceId: composerState.context.workspaceId,
          reasoningEffort: composerState.context.reasoningEffort,
        } : null}
        onSelectDraftMcp={selectDraftMcp}
        onOpenMcpSettings={() => navigateSafely(() => {
          setActiveView("plugins");
        })}
        mcpTaskGeneration={hiddenDraftCreationGenerationRef.current}
        legalMcpServers={legalMcpServers}
        legalHistory={legalThreads.filter((thread) => thread.scene === (activeView === "case-search" ? "case-retrieval" : "legal-retrieval")).map((thread) => ({
          id: thread.id,
          keyword: thread.title,
        }))}
        legalStarting={legalStarting}
        onStartLegalSearch={(question, serverId, skills, knowledgeLibraryIds) => {
          void startLegalSearch(question, serverId, skills, knowledgeLibraryIds);
        }}
        onSelectLegalHistory={selectLegalHistory}
        onDeleteLegalHistory={deleteLegalHistory}
        onOpenMcpSettingsForLegal={() => navigateSafely(() => {
          setActiveView("plugins");
        })}
      />
    );
  }

  return (
    <PanelCommandContext.Provider value={panelCommand}>
    <div
      className="app-frame"
      data-reduce-motion={reduceMotion}
      data-view={activeView}
      data-litigation-desk={activeView === "litigation"}
      data-contract-focus={activeView === "contract-review" && !contractNavOpen}
      style={{
        "--vl-sidebar-width": sidebarLayout.collapsed
          ? "56px"
          : `${sidebarLayout.width}px`,
      } as CSSProperties}
    >
      <AppearanceEffect />
      <CommandController open={commandsOpen} onOpen={() => setCommandsOpen(true)} onClose={() => setCommandsOpen(false)} handlers={{
        'new-thread': health && providers.length ? () => navigateSafely(startNewTask) : undefined,
        'search-threads': () => navigateSafely(() => {setActiveView('agent');saveSidebarCollapsed(false,window.localStorage);setSidebarLayout(current=>({...current,collapsed:false}));setSearchRequest(value=>value+1);}),
        'settings': () => navigateSafely(() => {setSettingsQuery('');setActiveView('settings');}),
        'shortcut-settings': () => navigateSafely(() => {setSettingsCategory('general');setSettingsQuery('');setActiveView('settings');requestAnimationFrame(()=>document.getElementById('setting-shortcuts')?.scrollIntoView?.({block:'center'}));}),
        'help': () => navigateSafely(() => {setSettingsCategory('help');setSettingsQuery('');setActiveView('settings');}),
        'files': activeView === 'agent' ? () => openCommandPanel('files') : undefined,
        'terminal': activeView === 'agent' && !['archived','trash'].includes(activeThread?.status ?? '') ? () => openCommandPanel('terminal') : undefined,
        'sidebar': activeView === 'settings' ? undefined : activeView === 'contract-review' ? () => setContractNavOpen(v=>!v) : activeView === 'litigation' ? () => setLitigationFilesCollapsed(v=>!v) : handleToggleSidebarCollapsed,
        'plugins': () => navigateSafely(() => setActiveView('plugins')),
        'knowledge': () => navigateSafely(() => setActiveView('knowledge')),
        'calendar': () => navigateSafely(() => {setCalendarDate(undefined);setActiveView('case-calendar');}),
        'scheduled': () => navigateSafely(() => {setScheduledThread(null);setActiveView('scheduled');}),
      }}/>
      <AppToolbar
        onOpenCommands={() => setCommandsOpen(true)}
        showSidebarToggle={activeView !== "settings"}
        sidebarCollapsed={activeView === "contract-review" ? !contractNavOpen : activeView === "litigation" ? litigationFilesCollapsed : sidebarLayout.collapsed}
        onToggleSidebar={activeView === "contract-review" ? () => setContractNavOpen(v => !v) : activeView === "litigation" ? () => setLitigationFilesCollapsed(v => !v) : handleToggleSidebarCollapsed}
        canGoBack={navHistory.index > 0}
        onBack={() => navigateSafely(goBack)}
        canGoForward={navHistory.index < navHistory.stack.length - 1}
        onForward={() => navigateSafely(goForward)}
        rightRail={
          activeView === "agent"
            ? { open: rightRailOpen, onToggle: () => setRightRailOpen((open) => !open) }
            : undefined
        }
      />
      <div className="app-body">
      {activeView === "litigation" || (activeView === "contract-review" && !contractNavOpen) ? null : activeView === "settings" ? (
        <aside className="sidebar settings-rail" aria-label="设置导航">
          <SettingsNavigation
            selected={settingsCategory}
            query={settingsQuery}
            onQueryChange={setSettingsQuery}
            onSelect={setSettingsCategory}
            onBack={() => setActiveView("agent")}
          />
        </aside>
      ) : (
      <Sidebar
        searchRequest={searchRequest}
        threads={threads}
        archivedThreads={archivedThreads}
        trashedThreads={trashedThreads}
        activeThreadId={activeThreadId}
        updates={updates}
        onNewThread={() => navigateSafely(startNewTask)}
        onSelectThread={(threadId) => navigateSafely(() => {
          setActiveView("agent");
          selectThread(threadId);
        })}
        onOpenSettings={() => navigateSafely(() => setActiveView("settings"))}
        onOpenLegalSearch={() => navigateSafely(() => setActiveView("legal-search"))}
        onOpenRedaction={() => navigateSafely(() => setActiveView("redact"))}
        onOpenKnowledge={() => navigateSafely(() => setActiveView("knowledge"))}
        onOpenCaseSearch={() => navigateSafely(() => setActiveView("case-search"))}
        onOpenLitigation={() => navigateSafely(() => setActiveView("litigation"))}
        onOpenContractReview={() => setActiveView("contract-review")}
        onOpenScheduled={() => navigateSafely(() => {setScheduledThread(null);setActiveView("scheduled");})}
        onOpenCaseCalendar={() => navigateSafely(() => {setCalendarDate(undefined);setActiveView("case-calendar");})}
        onOpenPlugins={() => navigateSafely(() => setActiveView("plugins"))}
        onRenameThread={renameThreadById}
        onArchiveThread={(id) => navigateSafely(() => archiveThreadById(id))}
        onUnarchiveThread={unarchiveThreadById}
        onTrashThread={(id) => navigateSafely(() => trashThreadById(id))}
        onRestoreThread={restoreThreadById}
        onDeleteThread={(id) => navigateSafely(() => deleteThreadById(id))}
        onCreateProject={() => navigateSafely(createProject)}
        collapsed={sidebarLayout.collapsed}
        onSidebarWidthChange={handleSidebarWidthChange}
        onSidebarWidthCommit={handleSidebarWidthCommit}
      />
      )}
      <section className="app-stage">
        {activeView === "litigation" && litigationError && <div className="runtime-banner" role="alert"><span>{litigationError}</span><button type="button" onClick={()=>{setSettingsCategory("models");setSettingsQuery("");setActiveView("settings");}}>打开模型设置</button><button type="button" onClick={()=>setLitigationError("")}>关闭提示</button></div>}
        <RuntimeBanner
          state={runtimeError ? "offline" : health ? "healthy" : "starting"}
          message={runtimeError}
          onRetry={runtimeError
            ? () => void (health ? refreshRuntimeHealth() : checkRuntimeStartup())
              .catch(() => undefined)
            : undefined}
          retrying={healthRefreshing}
        />
        <main className="workspace">{draftStorageFailed && <div className="message-error" role="alert">草稿暂时只能保留到本次退出，未能保存到本机。</div>}{content}</main>
        <CaseCalendarReminders enabled={Boolean(health)} refreshKey={calendarRefresh} onOpenDate={date => navigateSafely(() => {setCalendarDate(date);setActiveView("case-calendar");})} />
      </section>
      </div>
    </div>
    </PanelCommandContext.Provider>
  );
}
