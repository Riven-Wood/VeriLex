import { useCallback, useEffect, useRef, useState } from "react";

import { mcpErrorCopy } from "../mcp/errorCopy";
import { yuandianServiceFromServer } from "../mcp/yuandian";
import {
  activateMcpView,
  createAtomicThreadMcpSelection,
  deactivateMcpView,
  deselectThreadMcpServer,
  getThreadMcpSnapshot,
  listMcpServers,
  RuntimeApiError,
  selectThreadMcpServer,
} from "../runtime/api";
import type {
  AtomicThreadSelectionRequest,
  AtomicThreadSelectionResult,
  CreateMcpServerInput,
  DraftThreadInput,
  McpActiveViewRecord,
  McpSelectionRecord,
  McpServerRecord,
  McpThreadServerRecord,
  McpThreadSnapshot,
} from "../runtime/contracts";

export interface ThreadMcpApi {
  listServers: (signal?: AbortSignal) => Promise<McpServerRecord[]>;
  getSnapshot: (threadId: string, signal?: AbortSignal) => Promise<McpThreadSnapshot>;
  selectServer: (
    threadId: string,
    serverId: string,
    signal?: AbortSignal,
  ) => Promise<McpSelectionRecord>;
  deselectServer: (
    threadId: string,
    serverId: string,
    signal?: AbortSignal,
  ) => Promise<McpSelectionRecord>;
  atomicSelection: (
    input: AtomicThreadSelectionRequest,
    signal?: AbortSignal,
  ) => Promise<AtomicThreadSelectionResult>;
  activateView: (
    viewId: string,
    threadId: string,
    signal?: AbortSignal,
  ) => Promise<McpActiveViewRecord>;
  deactivateView: (viewId: string, signal?: AbortSignal) => Promise<McpActiveViewRecord>;
}

export const runtimeThreadMcpApi: ThreadMcpApi = {
  listServers: listMcpServers,
  getSnapshot: getThreadMcpSnapshot,
  selectServer: selectThreadMcpServer,
  deselectServer: deselectThreadMcpServer,
  atomicSelection: createAtomicThreadMcpSelection,
  activateView: activateMcpView,
  deactivateView: deactivateMcpView,
};

export type McpSelectionTarget = string | CreateMcpServerInput;

export interface DraftMcpSelectionOutcome {
  threadId: string;
  result: AtomicThreadSelectionResult | null;
}

export type SelectDraftMcp = (
  target: McpSelectionTarget,
  draftThread: DraftThreadInput,
) => Promise<DraftMcpSelectionOutcome | null>;

interface UseThreadMcpOptions {
  threadId: string | null;
  draftThread: DraftThreadInput | null;
  draftGeneration?: number;
  api?: ThreadMcpApi;
  selectDraftMcp?: SelectDraftMcp;
}

interface SnapshotState {
  targetKey: string;
  snapshot: McpThreadSnapshot | null;
  savedServers: McpServerRecord[];
}

interface TargetFlagState {
  targetKey: string;
  value: boolean;
}

interface TargetMessageState {
  targetKey: string;
  message: string;
}

function newViewId(): string {
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  return `mcp-view-${Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("")}`;
}

function codeFromError(error: unknown): unknown {
  return error instanceof RuntimeApiError ? error.code : null;
}

function draftKey(draft: DraftThreadInput | null): string {
  return draft === null
    ? "none"
    : JSON.stringify([
        draft.title,
        draft.providerId,
        draft.model,
        draft.workspaceId,
        draft.reasoningEffort,
      ]);
}

function mergeSnapshot(
  threadId: string,
  servers: readonly McpServerRecord[],
  threadSnapshot: McpThreadSnapshot | null,
): McpThreadSnapshot {
  const byId = new Map(
    (threadSnapshot?.servers ?? []).map((record) => [record.serverId, record]),
  );
  return {
    threadId,
    servers: servers.map((record): McpThreadServerRecord => {
      const selected = byId.get(record.id);
      const presetServiceId = yuandianServiceFromServer(record);
      return {
        serverId: record.id,
        displayName: record.displayName,
        selected: selected?.selected ?? false,
        connectionStatus: selected?.selected ? selected.connectionStatus : "idle",
        toolCount: selected?.selected ? selected.toolCount : 0,
        errorCode: selected?.selected ? selected.errorCode : null,
        retryable: selected?.selected ? selected.retryable : false,
        ...(presetServiceId ? { presetId: "yuandian" as const, presetServiceId } : {}),
      };
    }),
  };
}

function updateSelection(
  snapshot: McpThreadSnapshot | null,
  selection: McpSelectionRecord,
  createdServer?: McpServerRecord,
): McpThreadSnapshot | null {
  if (!snapshot) return snapshot;
  const existing = snapshot.servers.some((item) => item.serverId === selection.serverId);
  const servers = snapshot.servers.map((item) => item.serverId === selection.serverId
    ? {
        ...item,
        selected: selection.selected,
        connectionStatus: selection.connectionStatus,
        toolCount: selection.selected ? selection.toolCount : 0,
        errorCode: selection.errorCode ?? null,
        retryable: selection.retryable,
      }
    : item);
  if (!existing && createdServer) {
    const presetServiceId = yuandianServiceFromServer(createdServer);
    servers.push({
      serverId: createdServer.id,
      displayName: createdServer.displayName,
      selected: selection.selected,
      connectionStatus: selection.connectionStatus,
      toolCount: selection.selected ? selection.toolCount : 0,
      errorCode: selection.errorCode ?? null,
      retryable: selection.retryable,
      ...(presetServiceId ? { presetId: "yuandian" as const, presetServiceId } : {}),
    });
  }
  return { ...snapshot, servers };
}

export function useThreadMcp({
  threadId,
  draftThread,
  draftGeneration = 0,
  api = runtimeThreadMcpApi,
  selectDraftMcp,
}: UseThreadMcpOptions) {
  const viewIdRef = useRef<string | null>(null);
  if (!viewIdRef.current) viewIdRef.current = newViewId();
  const viewId = viewIdRef.current;
  const targetKey = threadId
    ? `thread:${threadId}`
    : `draft:${draftGeneration}:${draftKey(draftThread)}`;
  const targetKeyRef = useRef(targetKey);
  const threadIdRef = useRef(threadId);
  const draftThreadRef = useRef(draftThread);
  const boundDraftThreadIdRef = useRef<string | null>(null);
  const loadGenerationRef = useRef(0);
  const loadControllerRef = useRef<AbortController | null>(null);
  const requestIdRef = useRef(0);
  const writesRef = useRef(new Map<string, Promise<void>>());
  const [requestId, setRequestId] = useState(0);
  const [state, setState] = useState<SnapshotState>({
    targetKey,
    snapshot: null,
    savedServers: [],
  });
  const [loadingState, setLoadingState] = useState<TargetFlagState>({
    targetKey,
    value: true,
  });
  const [errorState, setErrorState] = useState<TargetMessageState>({
    targetKey,
    message: "",
  });
  const [viewErrorState, setViewErrorState] = useState<TargetMessageState>({
    targetKey,
    message: "",
  });
  const [pendingByTarget, setPendingByTarget] = useState<Map<string, Set<string>>>(() =>
    new Map());
  const [windowActive, setWindowActive] = useState(() =>
    document.visibilityState !== "hidden" && document.hasFocus());
  const windowActiveRef = useRef(windowActive);

  targetKeyRef.current = targetKey;
  threadIdRef.current = threadId;
  draftThreadRef.current = draftThread;
  windowActiveRef.current = windowActive;
  if (threadId) boundDraftThreadIdRef.current = threadId;

  const load = useCallback(async (): Promise<void> => {
    const requestedKey = targetKeyRef.current;
    const requestedThreadId = threadIdRef.current ?? boundDraftThreadIdRef.current;
    const generation = ++loadGenerationRef.current;
    const nextRequestId = ++requestIdRef.current;
    setRequestId(nextRequestId);
    setState({ targetKey: requestedKey, snapshot: null, savedServers: [] });
    setLoadingState({ targetKey: requestedKey, value: true });
    setErrorState({ targetKey: requestedKey, message: "" });
    loadControllerRef.current?.abort();
    const controller = new AbortController();
    loadControllerRef.current = controller;
    try {
      const serversPromise = api.listServers(controller.signal);
      const threadPromise = requestedThreadId
        ? api.getSnapshot(requestedThreadId, controller.signal)
        : Promise.resolve(null);
      const [servers, threadSnapshot] = await Promise.all([serversPromise, threadPromise]);
      if (
        controller.signal.aborted
        || generation !== loadGenerationRef.current
        || requestedKey !== targetKeyRef.current
        || requestedThreadId !== (threadIdRef.current ?? boundDraftThreadIdRef.current)
      ) return;
      setState({
        targetKey: requestedKey,
        snapshot: mergeSnapshot(requestedThreadId ?? "", servers, threadSnapshot),
        savedServers: servers,
      });
    } catch (loadError) {
      if (
        !controller.signal.aborted
        && generation === loadGenerationRef.current
        && requestedKey === targetKeyRef.current
      ) {
        setErrorState({
          targetKey: requestedKey,
          message: mcpErrorCopy(codeFromError(loadError)),
        });
      }
    } finally {
      if (
        !controller.signal.aborted
        && generation === loadGenerationRef.current
        && requestedKey === targetKeyRef.current
      ) setLoadingState({ targetKey: requestedKey, value: false });
      if (loadControllerRef.current === controller) {
        loadControllerRef.current = null;
      }
    }
  }, [api]);

  useEffect(() => {
    boundDraftThreadIdRef.current = threadId;
    void load();
    return () => {
      loadGenerationRef.current += 1;
      loadControllerRef.current?.abort();
    };
  }, [load, targetKey, threadId]);

  useEffect(() => {
    const onBlur = () => setWindowActive(false);
    const onFocus = () => setWindowActive(document.visibilityState !== "hidden");
    const onVisibility = () => setWindowActive(
      document.visibilityState !== "hidden" && document.hasFocus(),
    );
    window.addEventListener("blur", onBlur);
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("blur", onBlur);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const desiredViewRef = useRef({
    version: 0,
    threadId: null as string | null,
    targetKey,
  });
  const viewRunRef = useRef<Promise<void> | null>(null);
  const viewMountedRef = useRef(true);
  const apiRef = useRef(api);
  apiRef.current = api;

  const reconcileView = useCallback((): Promise<void> => {
    if (viewRunRef.current) return viewRunRef.current;
    let processedVersion = -1;
    const run = (async () => {
      while (true) {
        const desired = desiredViewRef.current;
        processedVersion = desired.version;
        try {
          if (desired.threadId) {
            await apiRef.current.activateView(viewId, desired.threadId);
          } else {
            await apiRef.current.deactivateView(viewId);
          }
          if (viewMountedRef.current && desired.version === desiredViewRef.current.version) {
            setViewErrorState({ targetKey: desired.targetKey, message: "" });
            if (desired.threadId) await load();
          }
        } catch (viewRequestError) {
          if (viewMountedRef.current && desired.version === desiredViewRef.current.version) {
            setViewErrorState({
              targetKey: desired.targetKey,
              message: mcpErrorCopy(codeFromError(viewRequestError)),
            });
          }
        }
        if (desired.version === desiredViewRef.current.version) break;
      }
    })().finally(() => {
      viewRunRef.current = null;
      if (processedVersion !== desiredViewRef.current.version) {
        void reconcileView();
      }
    });
    viewRunRef.current = run;
    return run;
  }, [load, viewId]);

  useEffect(() => {
    desiredViewRef.current = {
      version: desiredViewRef.current.version + 1,
      threadId: windowActive ? threadId : null,
      targetKey,
    };
    void reconcileView();
  }, [reconcileView, targetKey, threadId, windowActive]);

  useEffect(() => {
    // StrictMode deliberately runs setup -> cleanup -> setup in development.
    // Restore the mounted flag so the real setup can report current errors.
    viewMountedRef.current = true;
    return () => {
      viewMountedRef.current = false;
      desiredViewRef.current = {
        version: desiredViewRef.current.version + 1,
        threadId: null,
        targetKey: targetKeyRef.current,
      };
      void reconcileView();
    };
  }, [reconcileView]);

  const setPending = useCallback((operationKey: string, key: string, pending: boolean) => {
    setPendingByTarget((current) => {
      const next = new Map(current);
      const targetPending = new Set(next.get(operationKey) ?? []);
      if (pending) targetPending.add(key);
      else targetPending.delete(key);
      if (targetPending.size > 0) next.set(operationKey, targetPending);
      else next.delete(operationKey);
      return next;
    });
  }, []);

  const runServerWrite = useCallback((
    operationKey: string,
    key: string,
    operation: () => Promise<void>,
  ) => {
    const writeKey = JSON.stringify([operationKey, key]);
    const current = writesRef.current.get(writeKey);
    if (current) return current;
    setPending(operationKey, key, true);
    const run = operation().finally(() => {
      if (writesRef.current.get(writeKey) === run) writesRef.current.delete(writeKey);
      setPending(operationKey, key, false);
    });
    writesRef.current.set(writeKey, run);
    return run;
  }, [setPending]);

  const select = useCallback((target: McpSelectionTarget): Promise<void> => {
    const operationKey = targetKeyRef.current;
    const key = typeof target === "string" ? target : `new:${target.draft.displayName}`;
    return runServerWrite(operationKey, key, async () => {
      const existingThreadId = threadIdRef.current ?? boundDraftThreadIdRef.current;
      if (operationKey === targetKeyRef.current) {
        setErrorState({ targetKey: operationKey, message: "" });
      }
      try {
        if (existingThreadId) {
          if (typeof target === "string") {
            const selection = await api.selectServer(existingThreadId, target);
            if (operationKey !== targetKeyRef.current) return;
            setState((current) => current.targetKey === operationKey
              ? { ...current, snapshot: updateSelection(current.snapshot, selection) }
              : current);
          } else {
            const result = await api.atomicSelection({
              existingThreadId,
              draftThread: null,
              existingServerId: null,
              createServer: target,
            });
            if (operationKey !== targetKeyRef.current) return;
            setState((current) => current.targetKey === operationKey
              ? {
                  ...current,
                  snapshot: updateSelection(current.snapshot, result.selection, result.server),
                  savedServers: current.savedServers.some((server) => server.id === result.server.id)
                    ? current.savedServers
                    : [...current.savedServers, result.server],
                }
              : current);
            desiredViewRef.current = {
              version: desiredViewRef.current.version + 1,
              threadId: windowActiveRef.current ? existingThreadId : null,
              targetKey: operationKey,
            };
            await reconcileView();
          }
          return;
        }

        const draft = draftThreadRef.current;
        if (!draft) throw new Error("missing draft context");
        let outcome: DraftMcpSelectionOutcome | null;
        if (selectDraftMcp) {
          outcome = await selectDraftMcp(target, draft);
        } else {
          const result = await api.atomicSelection(typeof target === "string"
            ? {
                existingThreadId: null,
                draftThread: draft,
                existingServerId: target,
                createServer: null,
              }
            : {
                existingThreadId: null,
                draftThread: draft,
                existingServerId: null,
                createServer: target,
              });
          outcome = { threadId: result.thread.id, result };
        }
        if (!outcome) return;
        const reboundKey = `thread:${outcome.threadId}`;
        if (
          operationKey !== targetKeyRef.current
          && reboundKey !== targetKeyRef.current
        ) return;
        boundDraftThreadIdRef.current = outcome.threadId;
        if (reboundKey === targetKeyRef.current) {
          if (outcome.result?.createdServer) {
            desiredViewRef.current = {
              version: desiredViewRef.current.version + 1,
              threadId: windowActiveRef.current ? outcome.threadId : null,
              targetKey: reboundKey,
            };
            await reconcileView();
          } else {
            await load();
          }
          return;
        }
        const result = outcome.result;
        if (result) {
          setState((current) => current.targetKey === operationKey
            ? {
                ...current,
                snapshot: updateSelection(current.snapshot, result.selection, result.server),
                savedServers: result.server && !current.savedServers.some((server) =>
                  server.id === result.server.id)
                  ? [...current.savedServers, result.server]
                  : current.savedServers,
              }
            : current);
        } else {
          await load();
        }
      } catch (selectionError) {
        if (operationKey === targetKeyRef.current) {
          setErrorState({
            targetKey: operationKey,
            message: mcpErrorCopy(codeFromError(selectionError)),
          });
        }
        throw selectionError;
      }
    });
  }, [api, load, reconcileView, runServerWrite, selectDraftMcp]);

  const deselect = useCallback((serverId: string): Promise<void> => {
    const operationKey = targetKeyRef.current;
    return runServerWrite(operationKey, serverId, async () => {
      const currentThreadId = threadIdRef.current ?? boundDraftThreadIdRef.current;
      if (!currentThreadId) return;
      if (operationKey === targetKeyRef.current) {
        setErrorState({ targetKey: operationKey, message: "" });
      }
      try {
        const selection = await api.deselectServer(currentThreadId, serverId);
        if (operationKey !== targetKeyRef.current) return;
        setState((current) => current.targetKey === operationKey
          ? { ...current, snapshot: updateSelection(current.snapshot, selection) }
          : current);
      } catch (selectionError) {
        if (operationKey === targetKeyRef.current) {
          setErrorState({
            targetKey: operationKey,
            message: mcpErrorCopy(codeFromError(selectionError)),
          });
        }
        throw selectionError;
      }
    });
  }, [api, runServerWrite]);

  const retry = useCallback(async (serverId?: string): Promise<void> => {
    const selected = state.targetKey === targetKey
      ? state.snapshot?.servers.find((item) => item.serverId === serverId)?.selected
      : false;
    if (serverId && selected) {
      await select(serverId);
      return;
    }
    desiredViewRef.current = {
      version: desiredViewRef.current.version + 1,
      threadId: windowActive ? threadIdRef.current : null,
      targetKey: targetKeyRef.current,
    };
    void reconcileView();
    await load();
  }, [load, reconcileView, select, state, targetKey, windowActive]);

  const visibleSnapshot = state.targetKey === targetKey ? state.snapshot : null;
  const savedServers = state.targetKey === targetKey ? state.savedServers : [];
  const loading = loadingState.targetKey === targetKey ? loadingState.value : true;
  const error = errorState.targetKey === targetKey ? errorState.message : "";
  const viewError = viewErrorState.targetKey === targetKey ? viewErrorState.message : "";
  const pendingServerIds = pendingByTarget.get(targetKey) ?? new Set<string>();
  return {
    viewId,
    snapshot: visibleSnapshot,
    savedServers,
    loading,
    error: error || viewError,
    pendingServerIds,
    select,
    deselect,
    retry,
    requestId,
  };
}
