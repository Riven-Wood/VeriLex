import type { ComposerSubmission } from "../hooks/useAgentChat";

export type ComposerQueueMode = "after_current" | "after_stop";

export interface ComposerQueueState {
  threadId: string | null;
  submission: ComposerSubmission | null;
  mode: ComposerQueueMode | null;
  editing: boolean;
  queuedAtTerminalToken: number;
  queuedAtHistoryToken: number;
}

export function emptyComposerQueue(): ComposerQueueState {
  return {
    threadId: null,
    submission: null,
    mode: null,
    editing: false,
    queuedAtTerminalToken: 0,
    queuedAtHistoryToken: 0,
  };
}

export function queueSubmission(
  current: ComposerQueueState,
  submission: ComposerSubmission,
  mode: ComposerQueueMode,
  identity: { threadId: string; terminalToken: number; historyToken: number },
): { state: ComposerQueueState; replacedSubmission: ComposerSubmission | null } {
  return {
    state: {
      threadId: identity.threadId,
      submission,
      mode,
      editing: false,
      queuedAtTerminalToken: identity.terminalToken,
      queuedAtHistoryToken: identity.historyToken,
    },
    replacedSubmission: current.submission,
  };
}

export function editQueue(current: ComposerQueueState): ComposerQueueState {
  return current.submission ? { ...current, editing: true } : current;
}

export function cancelQueue(_current: ComposerQueueState): ComposerQueueState {
  return emptyComposerQueue();
}

export function nextQueueAction({
  queue,
  runningTurnId,
  connectionReady,
  terminalToken,
  currentThreadId,
  historyLoadedThreadId,
  historyToken,
}: {
  queue: ComposerQueueState;
  runningTurnId: string | null;
  connectionReady: boolean;
  terminalToken: number;
  currentThreadId: string | null;
  historyLoadedThreadId: string | null;
  historyToken: number;
}): { type: "send"; submission: ComposerSubmission } | null {
  if (
    !queue.submission ||
    !queue.threadId ||
    queue.threadId !== currentThreadId ||
    historyLoadedThreadId !== currentThreadId ||
    queue.editing ||
    runningTurnId ||
    !connectionReady ||
    (
      terminalToken <= queue.queuedAtTerminalToken
      && historyToken <= queue.queuedAtHistoryToken
    )
  ) return null;
  return { type: "send", submission: queue.submission };
}
