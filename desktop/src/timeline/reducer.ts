// Pure event -> timeline-item rules shared by the live stream, the replay
// path, and (via the same entry points) rebuilt history. Message deltas are
// buffered by the caller and applied through `bufferDeltas`; the reducer
// itself ignores `message_delta` events on purpose.
//
// Events are deduplicated per turn by sequence: replayed events that were
// already seen (or that arrive out of order behind the newest sequence) leave
// the state untouched.

import type {
  AgentEvent,
  ApprovalDecision,
} from "../runtime/contracts";
import type {
  AssistantItem,
  NoticeItem,
  TimelineAttachment,
  TimelineItem,
  ToolCallStatus,
} from "./items";
import { citationSourcesFrom } from "./citations";
import {
  hasPriorSideEffectForTurn,
  recoveryKindFor,
  sourceTextForTurn,
} from "./recovery";
import { riskLevelForTool } from "./risk";

export interface TimelineState {
  items: TimelineItem[];
  activeTurnId: string | null;
  isRunning: boolean;
  isThinking: boolean;
  error: string | null;
  maxSequenceByTurn: Record<string, number>;
  toolCallByApproval: Record<string, { turnId: string; toolCallId: string }>;
  /** Wall-clock stamps per turn (epoch ms) powering the "用时" header. */
  turnTimes: Record<string, { started?: number; ended?: number }>;
  nextItemId: number;
}

export function initialTimeline(): TimelineState {
  return {
    items: [],
    activeTurnId: null,
    isRunning: false,
    isThinking: false,
    error: null,
    maxSequenceByTurn: {},
    toolCallByApproval: {},
    turnTimes: {},
    nextItemId: 1,
  };
}

function str(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function num(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function summary(value: unknown): Record<string, unknown> | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function approvalDecision(value: unknown): ApprovalDecision | undefined {
  return value === "allowed" || value === "denied" ? value : undefined;
}

function assistantId(state: TimelineState): string {
  return `assistant-${state.nextItemId}`;
}

function closeStreamingAssistant(
  items: TimelineItem[],
  turnId?: string,
): TimelineItem[] {
  return items.map((item) => {
    if (
      item.kind !== "assistant" ||
      !item.isStreaming ||
      (turnId !== undefined && item.turnId !== turnId)
    ) return item;
    return {
      ...item,
      isStreaming: false,
      segments: item.segments.map((segment) =>
        segment.isStreaming ? { ...segment, isStreaming: false } : segment,
      ),
    };
  });
}

function attachCompletionToAssistant(
  state: TimelineState,
  turnId: string,
  metadata: {
    sources?: ReturnType<typeof citationSourcesFrom>;
    citationWarning: boolean;
    webAccessOffer: boolean;
  },
): TimelineState {
  let index = state.items.length - 1;
  while (index >= 0) {
    const item = state.items[index];
    if (
      item.kind === "assistant" &&
      item.isStreaming &&
      (
        item.turnId === turnId ||
        (item.turnId === undefined && state.activeTurnId === null)
      )
    ) break;
    index -= 1;
  }
  if (index < 0) {
    if (
      !metadata.webAccessOffer &&
      !metadata.citationWarning &&
      (!metadata.sources || metadata.sources.length === 0)
    ) {
      return state;
    }
    return {
      ...state,
      items: state.items.concat({
        kind: "assistant",
        id: assistantId(state),
        turnId,
        segments: [],
        isStreaming: false,
        ...(metadata.sources && metadata.sources.length > 0
          ? { sources: metadata.sources }
          : {}),
        ...(metadata.citationWarning ? { citationWarning: true } : {}),
        ...(metadata.webAccessOffer ? { webAccessOffer: true } : {}),
      }),
      nextItemId: state.nextItemId + 1,
    };
  }
  const current = state.items[index];
  if (current.kind !== "assistant") return state;
  const items = state.items.slice();
  items[index] = {
    ...current,
    isStreaming: false,
    segments: current.segments.map((segment) =>
      segment.isStreaming ? { ...segment, isStreaming: false } : segment,
    ),
    ...(metadata.sources && metadata.sources.length > 0
      ? { sources: metadata.sources }
      : {}),
    ...(metadata.citationWarning ? { citationWarning: true } : {}),
    ...(metadata.webAccessOffer ? { webAccessOffer: true } : {}),
  };
  return { ...state, items };
}

export function appendUserMessage(
  state: TimelineState,
  text: string,
  id?: string,
  attachmentNames: string[] = [],
  attachments: TimelineAttachment[] = [],
): TimelineState {
  const item: TimelineItem = {
    kind: "user",
    id: id ?? `user-${state.nextItemId}`,
    text,
    ...(attachments.length > 0 ? { attachments } : {}),
    ...(attachments.length === 0 && attachmentNames.length > 0
      ? { attachmentNames }
      : {}),
  };
  return { ...state, items: [...state.items, item], nextItemId: state.nextItemId + 1 };
}

export function bindUserMessageToTurn(
  state: TimelineState,
  userItemId: string,
  turnId: string,
): TimelineState {
  const items = state.items.map((item) =>
    item.kind === "user" && item.id === userItemId ? { ...item, turnId } : item,
  );
  return items.some((item, index) => item !== state.items[index])
    ? { ...state, items }
    : state;
}

export function bufferDeltas(
  state: TimelineState,
  pending: { text?: string; reasoning?: string },
): TimelineState {
  let items = state.items;
  let isThinking = state.isThinking;

  const append = (type: "reasoning" | "text", chunk: string) => {
    if (!chunk) return;
    const last = items[items.length - 1];
    if (
      last !== undefined &&
      last.kind === "assistant" &&
      last.isStreaming &&
      (last.turnId ?? null) === state.activeTurnId &&
      last.segments.length > 0
    ) {
      const lastSegment = last.segments[last.segments.length - 1];
      if (lastSegment.type === type && lastSegment.isStreaming) {
        const segments = last.segments.slice(0, -1).concat({
          ...lastSegment,
          text: lastSegment.text + chunk,
        });
        items = items.slice(0, -1).concat({ ...last, segments });
        return;
      }
      // Different kind: close the trailing segment and start a new one in
      // the same assistant item (reasoning -> text within one step).
      const segments = last.segments
        .map((segment) =>
          segment.isStreaming ? { ...segment, isStreaming: false } : segment,
        )
        .concat({ type, text: chunk, isStreaming: true });
      items = items.slice(0, -1).concat({ ...last, segments });
      return;
    }
    const assistant: AssistantItem = {
      kind: "assistant",
      id: assistantId(state),
      ...(state.activeTurnId ? { turnId: state.activeTurnId } : {}),
      segments: [{ type, text: chunk, isStreaming: true }],
      isStreaming: true,
    };
    items = items.concat(assistant);
  };

  if (pending.reasoning) {
    append("reasoning", pending.reasoning);
    isThinking = true;
  }
  if (pending.text) {
    append("text", pending.text);
    isThinking = false;
  }
  if (items === state.items) return state;
  return { ...state, items, isThinking, nextItemId: state.nextItemId + 1 };
}

function withToolItem(
  state: TimelineState,
  turnId: string,
  toolCallId: string,
  update: (item: Extract<TimelineItem, { kind: "tool" }>) => Extract<TimelineItem, { kind: "tool" }>,
): TimelineState {
  const index = state.items.findIndex(
    (item) =>
      item.kind === "tool" && item.turnId === turnId && item.id === toolCallId,
  );
  if (index === -1) return state;
  const current = state.items[index] as Extract<TimelineItem, { kind: "tool" }>;
  const next = update(current);
  if (next === current) return state;
  const items = state.items.slice();
  items[index] = next;
  return { ...state, items };
}

export function settleTurnTools(
  items: TimelineItem[],
  turnId: string,
  status: Extract<ToolCallStatus, "cancelled" | "failed">,
): TimelineItem[] {
  return items.map((item) =>
    item.kind === "tool" &&
    item.turnId === turnId &&
    (item.status === "awaiting_approval" || item.status === "running")
      ? { ...item, status }
      : item,
  );
}

export function applyTimelineEvent(
  state: TimelineState,
  event: AgentEvent,
): TimelineState {
  const seen = state.maxSequenceByTurn[event.turnId] ?? 0;
  if (event.sequence <= seen) return state;
  const base: TimelineState = {
    ...state,
    maxSequenceByTurn: {
      ...state.maxSequenceByTurn,
      [event.turnId]: event.sequence,
    },
  };

  switch (event.type) {
    case "turn_started":
      return {
        ...base,
        activeTurnId: event.turnId,
        isRunning: true,
        isThinking: false,
        turnTimes: {
          ...base.turnTimes,
          [event.turnId]: {
            ...base.turnTimes[event.turnId],
            // Never overwrite a real persisted start with the local clock.
            started:
              base.turnTimes[event.turnId]?.started ??
              event.occurredAt ?? Date.now(),
          },
        },
      };

    case "message_delta":
      return base;

    case "message_reset":
      return {
        ...base,
        items: base.items.filter(
          (item) =>
            !(
              item.kind === "assistant" &&
              item.turnId === event.turnId &&
              item.isStreaming
            ),
        ),
        isThinking: false,
      };

    case "message_completed": {
      const sources = citationSourcesFrom(event.payload.sources);
      const completed = attachCompletionToAssistant(base, event.turnId, {
        ...(sources !== null ? { sources } : {}),
        citationWarning: event.payload.citationWarning === true,
        webAccessOffer: event.payload.webAccessOffer === true,
      });
      return { ...completed, isThinking: false };
    }

    case "turn_completed":
      return {
        ...base,
        items: closeStreamingAssistant(settleTurnTools(base.items, event.turnId, "cancelled"), event.turnId),
        activeTurnId: null,
        isRunning: false,
        isThinking: false,
        turnTimes: {
          ...base.turnTimes,
          [event.turnId]: {
            ...base.turnTimes[event.turnId],
            ended: event.occurredAt ?? base.turnTimes[event.turnId]?.ended ?? Date.now(),
          },
        },
      };

    case "turn_cancelled":
      return {
        ...base,
        items: closeStreamingAssistant(
          settleTurnTools(base.items, event.turnId, "cancelled"),
          event.turnId,
        ).concat({ kind: "notice", id: `notice-cancelled-${event.turnId}-${event.sequence}`, notice: "turn_cancelled", turnId: event.turnId }),
        activeTurnId: null,
        isRunning: false,
        isThinking: false,
        turnTimes: {
          ...base.turnTimes,
          [event.turnId]: {
            ...base.turnTimes[event.turnId],
            ended: event.occurredAt ?? base.turnTimes[event.turnId]?.ended ?? Date.now(),
          },
        },
      };

    case "turn_failed": {
      const errorCode = str(event.payload.errorCode);
      const sourceText = sourceTextForTurn(base.items, event.turnId);
      const retryable =
        typeof event.payload.retryable === "boolean"
          ? event.payload.retryable
          : undefined;
      const notice: NoticeItem = {
        kind: "notice",
        id: `notice-failed-${event.turnId}-${event.sequence}`,
        notice: "turn_failed",
        turnId: event.turnId,
        errorCode,
        ...(retryable !== undefined ? { retryable } : {}),
        recoveryKind: recoveryKindFor(errorCode, sourceText),
        hasPriorSideEffect: hasPriorSideEffectForTurn(base.items, event.turnId),
        sourceText,
      };
      return {
        ...base,
        items: closeStreamingAssistant(
          settleTurnTools(base.items, event.turnId, "failed"),
          event.turnId,
        ).concat(notice),
        activeTurnId: null,
        isRunning: false,
        isThinking: false,
        turnTimes: {
          ...base.turnTimes,
          [event.turnId]: {
            ...base.turnTimes[event.turnId],
            ended: event.occurredAt ?? base.turnTimes[event.turnId]?.ended ?? Date.now(),
          },
        },
      };
    }

    case "tool_call_started": {
      const name = str(event.payload.name) ?? "(unknown)";
      const resolvedRiskLevel = riskLevelForTool(event.payload.riskLevel, name);
      const item: Extract<TimelineItem, { kind: "tool" }> = {
        kind: "tool",
        id: str(event.payload.toolCallId) ?? `tool-${event.turnId}-${event.sequence}`,
        turnId: event.turnId,
        name,
        argumentsSummary: summary(event.payload.argumentsSummary),
        ...(resolvedRiskLevel !== undefined ? { riskLevel: resolvedRiskLevel } : {}),
        status: "running" satisfies ToolCallStatus,
      };
      return { ...base, items: base.items.concat(item) };
    }

    case "approval_requested": {
      const toolCallId = str(event.payload.toolCallId);
      const approvalId = str(event.payload.approvalId);
      if (!toolCallId || !approvalId) return base;
      const tool = base.items.find(
        (item) =>
          item.kind === "tool" &&
          item.turnId === event.turnId &&
          item.id === toolCallId,
      );
      if (
        tool?.kind !== "tool" ||
        tool.status !== "running" ||
        tool.approvalId !== undefined ||
        tool.approvalDecision !== undefined
      ) {
        return base;
      }
      const existingBinding = base.toolCallByApproval[approvalId];
      if (
        existingBinding !== undefined &&
        (existingBinding.turnId !== event.turnId ||
          existingBinding.toolCallId !== toolCallId)
      ) {
        return base;
      }
      const mappedBase: TimelineState = {
        ...base,
        toolCallByApproval: {
          ...base.toolCallByApproval,
          [approvalId]: { turnId: event.turnId, toolCallId },
        },
      };
      return withToolItem(mappedBase, event.turnId, toolCallId, (item) => {
        const resolvedRiskLevel =
          event.payload.riskLevel !== undefined
            ? riskLevelForTool(event.payload.riskLevel, item.name)
            : item.riskLevel ?? riskLevelForTool(undefined, item.name);
        return {
          ...item,
          approvalId: approvalId ?? item.approvalId,
          // Read-only preparation may add the bounded review projection only
          // when approval is requested. The start event deliberately has none.
          argumentsSummary: summary(event.payload.argumentsSummary) ?? item.argumentsSummary,
          ...(event.payload.approvalBehavior === "thread_first_use"
            ? { approvalBehavior: "thread_first_use" as const }
            : {}),
          ...(str(event.payload.externalRecipient)
            ? { externalRecipient: str(event.payload.externalRecipient) }
            : {}),
          ...(str(event.payload.externalValue)
            ? { externalValue: str(event.payload.externalValue) }
            : {}),
          ...(resolvedRiskLevel !== undefined ? { riskLevel: resolvedRiskLevel } : {}),
          status: "awaiting_approval",
        };
      });
    }

    case "approval_resolved": {
      const approvalId = str(event.payload.approvalId);
      const decision = approvalDecision(event.payload.decision);
      if (!approvalId || !decision) return base;
      const binding = base.toolCallByApproval[approvalId];
      if (binding === undefined || binding.turnId !== event.turnId) return base;
      const payloadToolCallId = str(event.payload.toolCallId);
      if (
        payloadToolCallId !== undefined &&
        payloadToolCallId !== binding.toolCallId
      ) {
        return base;
      }
      const tool = base.items.find(
        (item) =>
          item.kind === "tool" &&
          item.turnId === event.turnId &&
          item.id === binding.toolCallId,
      );
      if (
        tool?.kind !== "tool" ||
        tool.approvalId !== approvalId ||
        tool.status !== "awaiting_approval"
      ) {
        return base;
      }
      return withToolItem(base, event.turnId, binding.toolCallId, (item) => ({
        ...item,
        status: "running",
        approvalDecision: decision,
      }));
    }

    case "tool_call_completed": {
      const toolCallId = str(event.payload.toolCallId) ?? "";
      const status = str(event.payload.status);
      const terminal: ToolCallStatus | undefined =
        status === "succeeded" || status === "failed" || status === "denied"
          ? status
          : undefined;
      const updated = withToolItem(base, event.turnId, toolCallId, (item) => ({
        ...item,
        status: terminal ?? item.status,
        durationMs: num(event.payload.durationMs) ?? item.durationMs,
        resultPreview: str(event.payload.resultPreview) ?? item.resultPreview,
        resultTruncated:
          typeof event.payload.resultTruncated === "boolean"
            ? event.payload.resultTruncated
            : item.resultTruncated,
        resultSummary:
          summary(event.payload.resultSummary) ?? item.resultSummary,
      }));
      if (updated !== base) return updated;
      // Completion without a start event (missed frame): render defensively.
      const item: Extract<TimelineItem, { kind: "tool" }> = {
        kind: "tool",
        id: toolCallId || `tool-${event.turnId}-${event.sequence}`,
        turnId: event.turnId,
        name: "(unknown)",
        status: terminal ?? "failed",
        durationMs: num(event.payload.durationMs),
        resultPreview: str(event.payload.resultPreview),
        resultTruncated:
          event.payload.resultTruncated === true ? true : undefined,
        resultSummary: summary(event.payload.resultSummary),
      };
      return { ...base, items: base.items.concat(item) };
    }

    case "turn_retry":
    case "turn_compacted": {
      const notice: NoticeItem = {kind: "notice", id: `notice-${event.type}-${event.turnId}-${event.sequence}`, turnId: event.turnId, notice: event.type, attempt: num(event.payload.attempt), degraded: event.payload.degraded === true};
      return {...base, items: base.items.concat(notice)};
    }
    case "attachment_degraded": {
      const entries = Array.isArray(event.payload.attachments)
        ? event.payload.attachments
        : [];
      const names = entries
        .map((entry) =>
          typeof entry === "object" && entry !== null
            ? str((entry as Record<string, unknown>).name)
            : undefined,
        )
        .filter((name): name is string => name !== undefined);
      const notice: NoticeItem = {
        kind: "notice",
        id: `notice-degraded-${event.turnId}-${event.sequence}`,
        notice: "attachment_degraded",
        total: num(event.payload.total),
        truncated:
          typeof event.payload.truncated === "boolean"
            ? event.payload.truncated
            : undefined,
        attachmentNames: names,
      };
      return { ...base, items: base.items.concat(notice) };
    }

    case "reasoning_effort_degraded": {
      const notice: NoticeItem = {
        kind: "notice",
        id: `notice-reasoning-degraded-${event.turnId}-${event.sequence}`,
        notice: "reasoning_effort_degraded",
        turnId: event.turnId,
        reasoningEffortFrom: str(event.payload.from),
        reasoningEffortTo: str(event.payload.to),
      };
      return { ...base, items: base.items.concat(notice) };
    }

    default:
      return base;
  }
}
