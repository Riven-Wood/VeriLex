import {useEditorDiscussion,useLitigationMaterials} from "../components/editor/DiscussionContext";
import {useContractEditorContext} from "../components/contractEditor/ContractEditorContext";
import { useCallback, useEffect, useRef, useState } from "react";

import { listMessages, listThreadAttachments, listThreadEvents, listTurns, wsToken, wsUrl } from "../runtime/api";
import type { AgentEvent, ReasoningEffort, ThreadAttachmentRecord } from "../runtime/contracts";
import { buildTimelineHistory } from "../timeline/history";
import type { TimelineAttachment } from "../timeline/items";
import {
  appendUserMessage,
  applyTimelineEvent,
  bindUserMessageToTurn,
  bufferDeltas,
  initialTimeline,
  type TimelineState,
} from "../timeline/reducer";

export type { TimelineState };
export { initialTimeline };

export type ApprovalMode = "ask" | "help" | "full";

export interface ComposerSubmission {
  litigationMaterials?: import("../components/editor/DiscussionContext").LitigationMaterialSelection;
  editorContext?: import("../components/editor/DiscussionContext").EditorTurnContext;
  text: string;
  preserveDraft?: boolean;
  /** Replace this turn and its descendants only once the Runtime accepts. */
  replaceTurnId?: string;
  knowledgeLibraryIds?: string[];
  attachmentUploadIds: string[];
  attachmentNames: string[];
  /** Preview metadata for the sent attachments, keyed by upload id. */
  attachments?: TimelineAttachment[];
  /** How high-risk tool calls are gated for this Turn. Defaults to "ask". */
  approvalMode?: ApprovalMode;
  /** Persisted model reasoning setting for this Thread. */
  reasoningEffort?: ReasoningEffort;
}

export interface ResolveApprovalRequest {
  turnId: string;
  toolCallId: string;
  decision: "allowed" | "denied";
  reason: string;
}

export interface ApprovalSubmissionState {
  status: "submitting" | "failed";
  code?: string;
  retryable?: boolean;
  error?: string;
}

export interface AgentChatOptions {
  /** Fired once the Runtime accepts a user turn (ack status queued). */
  onUserTurnAccepted?: (
    turnId: string,
    submission: ComposerSubmission,
    threadId: string,
  ) => void;
  /** Returns a submission to the composer when Runtime rejects turn.start. */
  onSubmissionRejected?: (
    submission: ComposerSubmission,
    code: string,
    threadId: string,
  ) => void;
  /** Fired when the running turn reaches a terminal status. */
  onTurnSettled?: () => void;
  /** Fired after Runtime durably resolves first-use web approval as denied. */
  onWebAccessDenied?: () => void;
}

const CONNECTION_NOT_READY_ERROR = "对话连接未就绪，请稍后重试。";
const NO_TOKEN_ERROR = "未获得本地 Runtime 访问令牌，无法建立对话连接。";
const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 5_000;
// A connection that never opened cannot heal by retrying (e.g. the launch
// token is rejected permanently); stop spinning after this many attempts.
const RECONNECT_MAX_ATTEMPTS_WITHOUT_OPEN = 6;
// Streaming providers can emit dozens of deltas per second; rendering each
// one synchronously turns into a render storm that freezes the UI. Buffer
// text and reasoning deltas and flush them in one state update per interval.
const STREAM_FLUSH_INTERVAL_MS = 80;

interface PendingDeltas {
  text: string;
  reasoning: string;
}

function approvalKey(turnId: string, toolCallId: string): string {
  return `${turnId}:${toolCallId}`;
}

export function useAgentChat(
  threadId: string | null,
  options: AgentChatOptions = {},
) {
  const contractEditor=useContractEditorContext();
  const contractEditorRef=useRef(contractEditor);
  contractEditorRef.current=contractEditor;
  const [state, setState] = useState<TimelineState>(initialTimeline);
  const [connectionReady, setConnectionReady] = useState(false);
  const [runningTurnId, setRunningTurnId] = useState<string | null>(null);
  const [terminalToken, setTerminalToken] = useState(0);
  const [pendingAckToken, setPendingAckToken] = useState(0);
  const [historyLoadedThreadId, setHistoryLoadedThreadId] = useState<string | null>(null);
  const [historyToken, setHistoryToken] = useState(0);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [approvalSubmissions, setApprovalSubmissions] = useState<
    Record<string, ApprovalSubmissionState>
  >({});
  // Callbacks stay optional and unstable-safe: latest copies are kept in refs.
  const acceptedCallbackRef = useRef(options.onUserTurnAccepted);
  acceptedCallbackRef.current = options.onUserTurnAccepted;
  const settledCallbackRef = useRef(options.onTurnSettled);
  settledCallbackRef.current = options.onTurnSettled;
  const rejectedCallbackRef = useRef(options.onSubmissionRejected);
  rejectedCallbackRef.current = options.onSubmissionRejected;
  const webAccessDeniedCallbackRef = useRef(options.onWebAccessDenied);
  webAccessDeniedCallbackRef.current = options.onWebAccessDenied;
  const pendingSubmissionRef = useRef<{
    submission: ComposerSubmission;
    userItemId: string;
    threadId: string;
    knownTurnIds: Set<string>;
    sentAt: number;
  } | null>(null);
  const knownTurnIdsRef = useRef(new Set<string>());
  const wsRef = useRef<WebSocket | null>(null);
  const runningTurnRef = useRef<string | null>(null);
  // The composer becomes stoppable as soon as turn.start is sent, before the
  // Runtime has replied with the turn id. Remember an early stop and deliver
  // it as soon as the queued ack arrives. Keep it latched until a terminal
  // event so a reconnect can safely retry the request as well.
  const stopRequestedRef = useRef(false);
  const supersededTurnIdsRef = useRef(new Set<string>());
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectAttemptsRef = useRef(0);
  const everOpenedRef = useRef(false);
  const disposedRef = useRef(false);
  // Sequence of the last event seen for the running turn; drives replay on
  // reconnect via turn.subscribe(after) and drops replay duplicates.
  const lastSequenceRef = useRef(0);
  const connectRef = useRef<() => void>(() => {});
  const pendingRef = useRef<PendingDeltas>({ text: "", reasoning: "" });
  const flushTimerRef = useRef<number | null>(null);
  // Guards history loads against a thread switch while requests are in
  // flight: a stale response must never clobber the freshly selected thread.
  const loadedThreadRef = useRef<string | null>(null);
  // A history request may complete after an optimistic message or live event.
  // Only the untouched baseline created by the current thread selection may
  // replace the whole timeline; later snapshots can still recover a durable
  // active turn without erasing newer UI state.
  const timelineRevisionRef = useRef(0);
  const historyBaselineRevisionRef = useRef(0);
  const historyRequestRef = useRef(0);
  // Updated during render, before passive cleanup. Event handlers and callbacks
  // captured by the previous screen can therefore reject work immediately
  // when the selected thread prop changes.
  const currentThreadRef = useRef(threadId);
  currentThreadRef.current = threadId;
  // A single socket can remain subscribed to an earlier turn after navigation.
  // Remember each live turn's owner so legacy frames without threadId can be
  // filtered whenever an ack or earlier wire event established ownership.
  const turnThreadByIdRef = useRef(new Map<string, string>());
  const stateRef = useRef(state);
  stateRef.current = state;
  const approvalSubmissionsRef = useRef<Record<string, ApprovalSubmissionState>>({});

  const replaceApprovalSubmissions = useCallback(
    (next: Record<string, ApprovalSubmissionState>) => {
      approvalSubmissionsRef.current = next;
      setApprovalSubmissions(next);
    },
    [],
  );

  const acceptPendingSubmission = useCallback((turnId: string) => {
    const pending = pendingSubmissionRef.current;
    pendingSubmissionRef.current = null;
    if (!pending) return;
    if (currentThreadRef.current === pending.threadId) {
      knownTurnIdsRef.current.add(turnId);
      setState((prev) => {
        let next = prev;
        if (pending.submission.replaceTurnId) {
          const boundary = prev.items.findIndex(item => item.kind === "user" && item.turnId === pending.submission.replaceTurnId);
          if (boundary >= 0) {
            for (const item of prev.items.slice(boundary)) {
              if (item.turnId && item.turnId !== turnId) supersededTurnIdsRef.current.add(item.turnId);
            }
            next = {...prev, items: [...prev.items.slice(0, boundary), ...prev.items.slice(boundary).filter(item => item.id === pending.userItemId || item.turnId === turnId)]};
          }
        }
        return bindUserMessageToTurn(next, pending.userItemId, turnId);
      });
    }
    setPendingAckToken((current) => current + 1);
    acceptedCallbackRef.current?.(
      turnId,
      pending.submission,
      pending.threadId,
    );
  }, []);

  const loadHistory = useCallback(async (id: string) => {
    const requestId = historyRequestRef.current + 1;
    historyRequestRef.current = requestId;
    const revisionAtStart = timelineRevisionRef.current;
    setHistoryLoading(true);
    try {
      const [messages, turns, events, attachments] = await Promise.all([
        listMessages(id),
        listTurns(id),
        listThreadEvents(id),
        // Attachment labels are an enrichment; missing metadata must not
        // fail the whole timeline.
        listThreadAttachments(id).catch(
          () => [] as ThreadAttachmentRecord[],
        ),
      ]);
      if (
        loadedThreadRef.current !== id ||
        historyRequestRef.current !== requestId
      ) return;
      const attachmentNamesById: Record<string, string> = {};
      const attachmentRecordsById: Record<string, TimelineAttachment> = {};
      for (const attachment of attachments) {
        attachmentNamesById[attachment.id] = attachment.display_name;
        // Partial records (missing type/size) keep the name-only fallback.
        if (
          typeof attachment.media_type !== "string" ||
          typeof attachment.size_bytes !== "number"
        ) {
          continue;
        }
        attachmentRecordsById[attachment.id] = {
          id: attachment.id,
          name: attachment.display_name,
          mediaType: attachment.media_type,
          sizeBytes: attachment.size_bytes,
          threadId: attachment.thread_id,
        };
      }
      const build = buildTimelineHistory({
        messages,
        turns,
        events,
        attachmentNamesById,
        attachmentRecordsById,
      });
      knownTurnIdsRef.current = new Set(turns.map((turn) => turn.id));
      const mayReplaceTimeline =
        revisionAtStart === historyBaselineRevisionRef.current &&
        timelineRevisionRef.current === revisionAtStart;
      if (mayReplaceTimeline) setState(prev => ({...build.state, error: prev.error ?? build.state.error}));
      const mayAdoptActiveTurn =
        mayReplaceTimeline || pendingSubmissionRef.current?.threadId === id;
      if (build.activeTurnId !== null && mayAdoptActiveTurn) {
        // A turn of this thread is still running server-side: adopt it and
        // resume its stream. Only advance to the persisted sequence when
        // that same snapshot was actually rendered. If an optimistic message
        // prevented replacement, replay from the last event this client saw
        // so approval cards and tool progress cannot be skipped.
        turnThreadByIdRef.current.set(build.activeTurnId, id);
        runningTurnRef.current = build.activeTurnId;
        setRunningTurnId(build.activeTurnId);
        if (mayReplaceTimeline) {
          lastSequenceRef.current =
            build.state.maxSequenceByTurn[build.activeTurnId] ?? 0;
        }
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(
            stopRequestedRef.current
              ? JSON.stringify({ type: "turn.stop", turnId: build.activeTurnId })
              : JSON.stringify({
                  type: "turn.subscribe",
                  turnId: build.activeTurnId,
                  after: lastSequenceRef.current,
                }),
          );
        }
        // If the connection dropped after turn.start but before its queued
        // ack, the active turn in persisted history is the durable ack. This
        // clears the optimistic submission and preserves draft promotion.
        if (pendingSubmissionRef.current?.threadId === id) {
          acceptPendingSubmission(build.activeTurnId);
        }
      } else if (build.activeTurnId === null && timelineRevisionRef.current === revisionAtStart) {
        // The queued ack can be lost even when the server finishes before
        // reconnection. Match a newly persisted turn, never an older identical
        // prompt, and settle it without resending turn.start or its side effects.
        const pending = pendingSubmissionRef.current;
        if (pending?.threadId === id) {
          const matches = turns.filter((turn) =>
            !pending.knownTurnIds.has(turn.id) &&
            Date.parse(turn.created_at) >= pending.sentAt &&
            ["completed", "failed", "cancelled", "interrupted"].includes(turn.status) &&
            build.state.items.some((item) => item.kind === "user" &&
              item.turnId === turn.id && item.text === pending.submission.text),
          );
          if (matches.length === 1) {
            acceptPendingSubmission(matches[0].id);
            runningTurnRef.current = null;
            stopRequestedRef.current = false;
            setRunningTurnId(null);
            setState(build.state);
            setTerminalToken((current) => current + 1);
            settledCallbackRef.current?.();
          }
        }
      }
      setHistoryError(null);
      setHistoryLoadedThreadId(id);
      setHistoryToken((current) => current + 1);
    } catch (error) {
      if (
        loadedThreadRef.current !== id ||
        historyRequestRef.current !== requestId
      ) return;
      setHistoryError("历史消息未能加载，请重试。已保存的对话不会因此删除。");
    } finally {
      if (loadedThreadRef.current === id && historyRequestRef.current === requestId) {
        setHistoryLoading(false);
      }
    }
  }, [acceptPendingSubmission]);

  const retryHistory = useCallback(() => {
    if (threadId && !historyLoading) void loadHistory(threadId);
  }, [threadId, historyLoading, loadHistory]);

  useEffect(() => {
    loadedThreadRef.current = threadId;
    knownTurnIdsRef.current = new Set();
    supersededTurnIdsRef.current = new Set();
    historyRequestRef.current += 1;
    timelineRevisionRef.current += 1;
    historyBaselineRevisionRef.current = timelineRevisionRef.current;
    replaceApprovalSubmissions({});
    setHistoryLoadedThreadId(null);
    setHistoryError(null);
    setHistoryLoading(false);
    if (!threadId) {
      setState(initialTimeline());
      runningTurnRef.current = null;
      stopRequestedRef.current = false;
      setRunningTurnId(null);
      lastSequenceRef.current = 0;
      pendingRef.current = { text: "", reasoning: "" };
      return;
    }
    setState(initialTimeline());
    runningTurnRef.current = null;
    stopRequestedRef.current = false;
    setRunningTurnId(null);
    lastSequenceRef.current = 0;
    pendingRef.current = { text: "", reasoning: "" };
    void loadHistory(threadId);
  }, [threadId, loadHistory, replaceApprovalSubmissions]);

  const scheduleReconnect = useCallback(() => {
    if (disposedRef.current) return;
    if (reconnectTimerRef.current !== null) return;
    if (!everOpenedRef.current && reconnectAttemptsRef.current >= RECONNECT_MAX_ATTEMPTS_WITHOUT_OPEN) {
      setState((prev) => ({ ...prev, error: "无法建立对话连接，请重启应用后重试。" }));
      return;
    }
    const attempt = reconnectAttemptsRef.current;
    reconnectAttemptsRef.current = attempt + 1;
    const delay = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** attempt,
      RECONNECT_MAX_DELAY_MS,
    );
    reconnectTimerRef.current = globalThis.setTimeout(() => {
      reconnectTimerRef.current = null;
      connectRef.current();
    }, delay);
  }, []);

  const connect = useCallback(() => {
    const token = wsToken();
    if (!token) {
      setState((prev) => (prev.error ? prev : { ...prev, error: NO_TOKEN_ERROR }));
      return;
    }
    const url = wsUrl();
    const ws = new WebSocket(url, token);
    wsRef.current = ws;

    ws.onopen = () => {
      if (wsRef.current !== ws) return;
      everOpenedRef.current = true;
      reconnectAttemptsRef.current = 0;
      setConnectionReady(true);
      setState((prev) =>
        prev.error === CONNECTION_NOT_READY_ERROR ? { ...prev, error: null } : prev,
      );
      // Turns keep running server-side across a disconnect; resume the
      // stream exactly after the last event this client has seen.
      const turnId = runningTurnRef.current;
      if (turnId) {
        ws.send(
          stopRequestedRef.current
            ? JSON.stringify({ type: "turn.stop", turnId })
            : JSON.stringify({
                type: "turn.subscribe",
                turnId,
                after: lastSequenceRef.current,
          }),
        );
      } else {
        const pending = pendingSubmissionRef.current;
        if (pending && loadedThreadRef.current === pending.threadId) {
          // The prior socket may have lost the queued ack. Re-read durable
          // turn state to recover its id, then subscribe or deliver stop.
          void loadHistory(pending.threadId);
        }
      }
    };

    ws.onmessage = (event) => {
      if (wsRef.current !== ws) return;
      try {
        const message = JSON.parse(event.data as string) as {
          type: string;
          turnId?: string;
          event?: AgentEvent;
          code?: string;
          message?: string;
          status?: string;
          requestType?: string;
          toolCallId?: string;
          retryable?: boolean;
        };

        if (message.requestType === "turn.resolve") {
          if (message.turnId && message.toolCallId) {
            const key = approvalKey(message.turnId, message.toolCallId);
            const hasActiveSubmission =
              approvalSubmissionsRef.current[key]?.status === "submitting";
            const belongsToCurrentThread =
              turnThreadByIdRef.current.get(message.turnId) ===
              currentThreadRef.current;
            const stillVisible = stateRef.current.items.some(
              (item) =>
                item.kind === "tool" &&
                item.turnId === message.turnId &&
                item.id === message.toolCallId &&
                item.status === "awaiting_approval",
            );
            if (
              message.type === "error" &&
              hasActiveSubmission &&
              belongsToCurrentThread &&
              stillVisible
            ) {
              replaceApprovalSubmissions({
                ...approvalSubmissionsRef.current,
                [key]: {
                  status: "failed",
                  code: message.code ?? "approval_submit_failed",
                  retryable: message.retryable === true,
                },
              });
            }
          }
          return;
        }

        if (message.type === "event" && message.event) {
          const agentEvent = message.event;
          if (supersededTurnIdsRef.current.has(agentEvent.turnId)) return;
          const eventThreadId =
            agentEvent.threadId ??
            turnThreadByIdRef.current.get(agentEvent.turnId);
          if (
            eventThreadId !== undefined &&
            eventThreadId !== currentThreadRef.current
          ) {
            return;
          }
          if (
            runningTurnRef.current !== null &&
            agentEvent.turnId !== runningTurnRef.current
          ) {
            return;
          }
          if (agentEvent.threadId !== undefined) {
            turnThreadByIdRef.current.set(
              agentEvent.turnId,
              agentEvent.threadId,
            );
          } else if (
            agentEvent.type === "turn_started" &&
            currentThreadRef.current !== null
          ) {
            turnThreadByIdRef.current.set(
              agentEvent.turnId,
              currentThreadRef.current,
            );
          }
          if (agentEvent.type === "turn_started") {
            runningTurnRef.current = agentEvent.turnId;
            setRunningTurnId(agentEvent.turnId);
          }
          if (
            agentEvent.turnId &&
            agentEvent.turnId === runningTurnRef.current &&
            typeof agentEvent.sequence === "number"
          ) {
            // The subscribe-then-replay window can redeliver events that
            // were already seen; drop them before they duplicate content.
            if (agentEvent.sequence <= lastSequenceRef.current) return;
            lastSequenceRef.current = agentEvent.sequence;
          }
          timelineRevisionRef.current += 1;
          handleEvent(agentEvent);
        } else if (message.type === "ack" && message.status === "queued" && message.turnId) {
          const pending = pendingSubmissionRef.current;
          const ownerThreadId =
            pending?.threadId ??
            turnThreadByIdRef.current.get(message.turnId);
          // A queued ack has no threadId of its own. Without a matching
          // pending start or established ownership, adopting it would let an
          // unrelated subscription take over the selected thread.
          if (ownerThreadId === undefined) return;
          turnThreadByIdRef.current.set(message.turnId, ownerThreadId);
          const belongsToCurrentThread =
            ownerThreadId === currentThreadRef.current;
          // turn.stop targets the running turn; remember it from the ack,
          // which always precedes the turn_started event.
          if (belongsToCurrentThread) {
            runningTurnRef.current = message.turnId;
            setRunningTurnId(message.turnId);
            lastSequenceRef.current = 0;
            if (stopRequestedRef.current && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "turn.stop", turnId: message.turnId }));
            }
          }
          // The turn is now durably bound server-side; only now may the
          // sidebar rename the session after its first message.
          acceptPendingSubmission(message.turnId);
        } else if (message.type === "error") {
          if (message.turnId) {
            const errorThreadId = turnThreadByIdRef.current.get(message.turnId);
            if (
              errorThreadId === undefined ||
              errorThreadId !== currentThreadRef.current
            ) {
              return;
            }
            const hasCurrentPendingStart =
              pendingSubmissionRef.current?.threadId ===
              currentThreadRef.current;
            if (runningTurnRef.current === message.turnId) {
              runningTurnRef.current = null;
              setRunningTurnId(null);
            }
            if (!hasCurrentPendingStart) stopRequestedRef.current = false;
            timelineRevisionRef.current += 1;
            setState((prev) => ({
              ...prev,
              error: message.message ?? message.code ?? "未知错误",
              isRunning: hasCurrentPendingStart,
            }));
            return;
          }

          // Request errors without a turn id are turn.start rejections when
          // an optimistic submission is awaiting its queued ack.
          const pending = pendingSubmissionRef.current;
          pendingSubmissionRef.current = null;
          if (pending) setPendingAckToken((current) => current + 1);
          const affectsCurrentThread =
            pending === null ||
            pending.threadId === currentThreadRef.current;
          if (affectsCurrentThread) {
            runningTurnRef.current = null;
            stopRequestedRef.current = false;
            setRunningTurnId(null);
            timelineRevisionRef.current += 1;
            setState((prev) => ({
              ...prev,
              items: pending
                ? prev.items.filter((item) => item.id !== pending.userItemId)
                : prev.items,
              error: message.message ?? message.code ?? "未知错误",
              isRunning: false,
            }));
          }
          if (pending) {
            rejectedCallbackRef.current?.(
              pending.submission,
              message.code ?? "turn_rejected",
              pending.threadId,
            );
          }
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onerror = () => {
      if (wsRef.current !== ws) return;
      setState((prev) => ({
        ...prev,
        error: "WebSocket 连接错误",
      }));
    };

    ws.onclose = () => {
      // A stale socket (e.g. the one closed by StrictMode's simulated
      // unmount) reports close only after the replacement connected; it must
      // not clear the reference to the live connection.
      if (wsRef.current !== ws) return;
      wsRef.current = null;
      replaceApprovalSubmissions({});
      setConnectionReady(false);
      scheduleReconnect();
    };
  }, [acceptPendingSubmission, loadHistory, replaceApprovalSubmissions, scheduleReconnect]);
  connectRef.current = connect;

  useEffect(() => {
    disposedRef.current = false;
    connect();
    return () => {
      disposedRef.current = true;
      if (reconnectTimerRef.current !== null) {
        globalThis.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      if (flushTimerRef.current !== null) {
        globalThis.clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  // Buffered streaming deltas: apply all pending text and reasoning in a
  // single state update per flush interval.
  const flushDeltasNow = useCallback(() => {
    if (flushTimerRef.current !== null) {
      globalThis.clearTimeout(flushTimerRef.current);
      flushTimerRef.current = null;
    }
    const pending = pendingRef.current;
    if (!pending.text && !pending.reasoning) return;
    pendingRef.current = { text: "", reasoning: "" };
    setState((prev) => bufferDeltas(prev, pending));
  }, []);

  const scheduleFlush = useCallback(() => {
    if (flushTimerRef.current !== null) return;
    flushTimerRef.current = globalThis.setTimeout(() => {
      flushTimerRef.current = null;
      flushDeltasNow();
    }, STREAM_FLUSH_INTERVAL_MS);
  }, [flushDeltasNow]);

  function handleEvent(event: AgentEvent) {
    const { type, payload } = event;

    if (type === "message_delta") {
      const kind = payload.kind as string | undefined;
      if (kind !== "text" && kind !== "reasoning") return;
      const delta = payload.delta as string | undefined;
      if (!delta) return;
      if (kind === "reasoning") {
        pendingRef.current.reasoning += delta;
      } else {
        pendingRef.current.text += delta;
      }
      scheduleFlush();
      return;
    }

    // Any other event must land after the deltas that preceded it: flush
    // the buffer first so tool cards and notices stay in order.
    if (
      type === "approval_resolved" &&
      payload.decision === "denied" &&
      typeof payload.approvalId === "string"
    ) {
      const deniedWebTool = stateRef.current.items.find((item) =>
        item.kind === "tool" &&
        item.turnId === event.turnId &&
        item.approvalId === payload.approvalId &&
        item.approvalBehavior === "thread_first_use",
      );
      if (deniedWebTool) webAccessDeniedCallbackRef.current?.();
    }

    flushDeltasNow();
    setState((prev) =>
      applyTimelineEvent(prev, { ...event, occurredAt: Date.now() }),
    );

    if (type === "approval_resolved" && typeof payload.toolCallId === "string") {
      const key = approvalKey(event.turnId, payload.toolCallId);
      if (approvalSubmissionsRef.current[key] !== undefined) {
        const next = { ...approvalSubmissionsRef.current };
        delete next[key];
        replaceApprovalSubmissions(next);
      }
    }

    if (type === "turn_completed" || type === "turn_failed" || type === "turn_cancelled") {
      const settledThreadId = event.threadId ?? turnThreadByIdRef.current.get(event.turnId) ?? currentThreadRef.current;
      if (settledThreadId) contractEditorRef.current.refresh(settledThreadId);
      turnThreadByIdRef.current.delete(event.turnId);
      runningTurnRef.current = null;
      stopRequestedRef.current = false;
      setRunningTurnId(null);
      lastSequenceRef.current = 0;
      setTerminalToken((current) => current + 1);
      settledCallbackRef.current?.();
    }
  }

  const editorDiscussion=useEditorDiscussion();
  const litigationMaterials=useLitigationMaterials();
  const sendMessage = useCallback(
    (input: string | ComposerSubmission): boolean => {
      const submission: ComposerSubmission = typeof input === "string"
        ? { text: input, attachmentUploadIds: [], attachmentNames: [] }
        : {
            text: input.text,
            ...(input.litigationMaterials?{litigationMaterials:structuredClone(input.litigationMaterials)}:{}),
            ...(input.editorContext?{editorContext:structuredClone(input.editorContext)}:{}),
            preserveDraft: input.preserveDraft,
            replaceTurnId: input.replaceTurnId,
            ...(input.knowledgeLibraryIds?.length ? {knowledgeLibraryIds: [...input.knowledgeLibraryIds]} : {}),
            attachmentUploadIds: [...input.attachmentUploadIds],
            attachmentNames: [...input.attachmentNames],
            ...(input.attachments
              ? { attachments: input.attachments.map((meta) => ({ ...meta })) }
              : {}),
            approvalMode: input.approvalMode,
            reasoningEffort: input.reasoningEffort,
          };
      const ws = wsRef.current;
      if (
        !threadId ||
        (!submission.text.trim() && submission.attachmentUploadIds.length === 0 && !submission.replaceTurnId)
      ) return false;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        setState((prev) => ({
          ...prev,
          error: CONNECTION_NOT_READY_ERROR,
        }));
        return false;
      }

      if (submission.replaceTurnId && (stateRef.current.isRunning ||
        !stateRef.current.items.some(item => item.kind === "user" && item.turnId === submission.replaceTurnId))) return false;

      if (pendingSubmissionRef.current) {
        const pendingThreadId = pendingSubmissionRef.current.threadId;
        setState((prev) => ({
          ...prev,
          error: pendingThreadId === threadId
            ? "上一条消息仍在确认，请稍后再试。"
            : "另一个任务的消息仍在确认，请稍后再试。",
        }));
        return false;
      }

      const materialSelection=submission.litigationMaterials??litigationMaterials;
      if(materialSelection)submission.litigationMaterials=structuredClone(materialSelection);
      const editorContext=submission.editorContext??editorDiscussion?.capture();
      if(editorDiscussion?.target&&!editorContext){setState(prev=>({...prev,error:"处理文书已有变化，请返回文书重新选择后发送。"}));return false;}
      let contractEditorContext;
      try { contractEditorContext = contractEditor?.capture(threadId); }
      catch (error) {
        setState(prev => ({...prev, error: error instanceof Error ? error.message : "合同文字尚未保存，请稍后重试。"}));
        return false;
      }
      if(submission.editorContext)editorDiscussion?.activate(submission.editorContext.product_id);
      const userItemId = `user-${Date.now()}`;

      timelineRevisionRef.current += 1;
      setState((prev) => ({
        ...appendUserMessage(
          prev,
          submission.text,
          userItemId,
          submission.attachmentNames,
          submission.attachments ?? [],
        ),
        error: null,
        isRunning: true,
      }));

      pendingSubmissionRef.current = {
        submission, userItemId, threadId,
        sentAt: Date.now(),
        knownTurnIds: new Set([
          ...knownTurnIdsRef.current,
          ...stateRef.current.items.flatMap((item) => item.turnId ? [item.turnId] : []),
        ]),
      };
      stopRequestedRef.current = false;
      ws.send(
        JSON.stringify({
          type: "turn.start",
          threadId,
          content: submission.text,
          ...(submission.replaceTurnId ? {replaceTurnId: submission.replaceTurnId} : {}),
          ...(submission.litigationMaterials?{litigationMaterials:submission.litigationMaterials}:{}),
          ...(editorContext?{editorContext}:{}),
          ...(contractEditorContext?{contractEditorContext}:{}),
          attachmentUploadIds: submission.attachmentUploadIds,
          ...(submission.knowledgeLibraryIds?.length ? {knowledgeLibraryIds: submission.knowledgeLibraryIds} : {}),
          approvalMode: submission.approvalMode ?? "ask",
          reasoningEffort: submission.reasoningEffort ?? "auto",
        }),
      );
      editorDiscussion?.sent();
      contractEditor?.sent(threadId);
      return true;
    },
    [threadId,editorDiscussion,contractEditor,litigationMaterials],
  );

  const stop = useCallback(() => {
    const ws = wsRef.current;
    const turnId = runningTurnRef.current;
    if (!turnId) {
      if (pendingSubmissionRef.current) stopRequestedRef.current = true;
      return;
    }
    stopRequestedRef.current = true;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify({ type: "turn.stop", turnId }));
  }, []);

  const resolveApproval = useCallback(
    (request: ResolveApprovalRequest): boolean => {
      const ws = wsRef.current;
      const key = approvalKey(request.turnId, request.toolCallId);
      if (
        !threadId ||
        currentThreadRef.current !== threadId ||
        turnThreadByIdRef.current.get(request.turnId) !== threadId ||
        stateRef.current.activeTurnId !== request.turnId ||
        !ws ||
        ws.readyState !== WebSocket.OPEN
      ) return false;
      const stillPending = stateRef.current.items.some(
        (item) =>
          item.kind === "tool" &&
          item.turnId === request.turnId &&
          item.id === request.toolCallId &&
          typeof item.approvalId === "string" &&
          item.status === "awaiting_approval",
      );
      if (!stillPending) return false;
      if (approvalSubmissionsRef.current[key]?.status === "submitting") return false;

      const next = {
        ...approvalSubmissionsRef.current,
        [key]: { status: "submitting" } satisfies ApprovalSubmissionState,
      };
      replaceApprovalSubmissions(next);
      ws.send(JSON.stringify({
        type: "turn.resolve",
        turnId: request.turnId,
        toolCallId: request.toolCallId,
        decision: request.decision,
        reason: request.reason.slice(0, 500),
      }));
      return true;
    },
    [replaceApprovalSubmissions, threadId],
  );

  return {
    items: state.items,
    isRunning: state.isRunning,
    isThinking: state.isThinking,
    error: state.error,
    historyError,
    historyLoading,
    retryHistory,
    connectionReady,
    runningTurnId,
    terminalToken,
    pendingAckToken,
    historyLoadedThreadId,
    historyToken,
    approvalSubmissions,
    turnTimes: state.turnTimes,
    sendMessage,
    resolveApproval,
    stop,
  };
}
