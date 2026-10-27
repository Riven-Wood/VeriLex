// Rebuilds the timeline of a whole thread from three persisted sources:
// messages (ordering backbone), turns (status fallback), and thread events
// (process metadata). Live events and replayed events share the reducer's
// item rules; this builder replays persisted events through the same
// reducer so history and live views cannot drift.

import type { AgentEvent, AgentEventType, MessageBlock, MessageRecord } from "../runtime/contracts";
import type {
  TimelineAttachment,
  TimelineItem,
  ToolItem,
  UserItem,
} from "./items";
import {
  applyTimelineEvent,
  bufferDeltas,
  initialTimeline,
  settleTurnTools,
  type TimelineState,
} from "./reducer";
import {
  hasPriorSideEffectForTurn,
  recoveryKindFor,
  sourceTextForTurn,
} from "./recovery";
import { riskLevelForTool } from "./risk";
import { citationSourcesFrom } from "./citations";
import { webResultSummaryFromOutput } from "./toolPresentation";

const ACTIVE_TURN_STATUSES = new Set(["queued", "running", "awaiting_approval"]);

const KNOWN_EVENT_TYPES = new Set<AgentEventType>([
  "turn_started",
  "message_delta",
  "message_reset",
  "message_completed",
  "turn_completed",
  "turn_failed",
  "turn_cancelled",
  "tool_call_started",
  "tool_call_completed",
  "approval_requested",
  "approval_resolved",
  "attachment_degraded",
  "reasoning_effort_degraded",
  "turn_retry",
  "turn_compacted",
]);

const PREVIEW_LIMIT = 400;

export interface TurnView {
  id: string;
  status: string;
  error_code?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ThreadEventView {
  turn_id: string;
  sequence: number;
  type: string;
  payload?: Record<string, unknown>;
  created_at?: string | null;
}

export interface HistorySources {
  messages: MessageRecord[];
  turns: TurnView[];
  events: ThreadEventView[];
  attachmentNamesById?: Record<string, string>;
  attachmentRecordsById?: Record<string, TimelineAttachment>;
}

export interface HistoryBuild {
  state: TimelineState;
  activeTurnId: string | null;
}

function epochMs(value: string | null | undefined): number | undefined {
  if (!value) return undefined;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

function userText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((block) => typeof block === "object" && block !== null && block.type === "text")
      .map((block) => block.text ?? "")
      .join("\n");
  }
  if (typeof content === "object" && content !== null) {
    const text = (content as { text?: unknown }).text;
    if (typeof text === "string") return text;
  }
  return "";
}

function userAttachmentIds(content: unknown): string[] {
  if (
    typeof content === "object" &&
    content !== null &&
    !Array.isArray(content) &&
    (content as { version?: unknown }).version === 1
  ) {
    const ids = (content as { attachmentIds?: unknown }).attachmentIds;
    if (Array.isArray(ids)) {
      return ids.filter((id): id is string => typeof id === "string");
    }
  }
  return [];
}

function truncate(content: string): { preview: string; truncated: boolean } {
  if (content.length <= PREVIEW_LIMIT) return { preview: content, truncated: false };
  return { preview: content.slice(0, PREVIEW_LIMIT), truncated: true };
}

function blocksOf(record: MessageRecord): MessageBlock[] {
  return Array.isArray(record.content) ? record.content : [];
}

interface PersistedAssistantMetadata {
  sources?: NonNullable<ReturnType<typeof citationSourcesFrom>>;
  citationWarning: boolean;
  webAccessOffer: boolean;
}

function assistantMetadataFromBlocks(record: MessageRecord): PersistedAssistantMetadata {
  const blocks = blocksOf(record);
  const sourceBlocks = blocks.filter((block) => block.type === "sources");
  const parsedSources = sourceBlocks.length === 1
    ? citationSourcesFrom(sourceBlocks[0].sources)
    : null;
  return {
    ...(parsedSources !== null && parsedSources.length > 0
      ? { sources: parsedSources }
      : {}),
    citationWarning: blocks.some((block) => block.type === "citation_warning"),
    webAccessOffer: blocks.some((block) => block.type === "web_access_offer"),
  };
}

function toolBlocksOf(records: MessageRecord[], turnId: string): Array<{ record: MessageRecord; block: MessageBlock }> {
  const collected: Array<{ record: MessageRecord; block: MessageBlock }> = [];
  for (const record of records) {
    if (record.turn_id !== turnId || record.role !== "tool") continue;
    for (const block of blocksOf(record)) {
      if (typeof block === "object" && block !== null && block.type === "tool_result") {
        collected.push({ record, block });
      }
    }
  }
  return collected;
}

function toAgentEvent(view: ThreadEventView): AgentEvent | null {
  if (!KNOWN_EVENT_TYPES.has(view.type as AgentEventType)) return null;
  return {
    type: view.type as AgentEventType,
    turnId: view.turn_id,
    sequence: view.sequence,
    payload: view.payload ?? {},
    occurredAt: epochMs(view.created_at),
  };
}

/** Build assistant items directly from persisted message blocks (no events). */
function appendAssistantFromBlocks(
  state: TimelineState,
  record: MessageRecord,
): TimelineState {
  // Event-less history must retain turn ownership too. Otherwise distinct
  // responses share an empty React group key and can survive an edited tail.
  let next = { ...state, activeTurnId: record.turn_id };
  const itemStart = state.items.length;
  if (typeof record.content === "string") {
    return record.content ? bufferDeltas(next, { text: record.content }) : next;
  }
  for (const block of blocksOf(record)) {
    if (block.type === "reasoning" && block.text) {
      next = bufferDeltas(next, { reasoning: block.text });
    } else if (block.type === "text" && block.text) {
      next = bufferDeltas(next, { text: block.text });
    }
    // tool_call blocks become cards only when no event built them already;
    // handled by the caller via pairToolResults.
  }
  const metadata = assistantMetadataFromBlocks(record);
  const hasSources = metadata.sources !== undefined;
  const hasCitationWarning = metadata.citationWarning;
  const hasWebAccessOffer = metadata.webAccessOffer;
  if (hasSources || hasCitationWarning || hasWebAccessOffer) {
    let index = next.items.length - 1;
    while (index >= itemStart && next.items[index].kind !== "assistant") index -= 1;
    if (index >= itemStart) {
      const current = next.items[index];
      if (current.kind === "assistant") {
        const items = next.items.slice();
        items[index] = {
          ...current,
          ...(hasSources ? { sources: metadata.sources } : {}),
          ...(hasCitationWarning ? { citationWarning: true } : {}),
          ...(hasWebAccessOffer ? { webAccessOffer: true } : {}),
        };
        next = { ...next, items };
      }
    } else {
      next = {
        ...next,
        items: next.items.concat({
          kind: "assistant",
          id: `assistant-${next.nextItemId}`,
          ...(record.turn_id ? { turnId: record.turn_id } : {}),
          segments: [],
          isStreaming: false,
          ...(hasSources ? { sources: metadata.sources } : {}),
          ...(hasCitationWarning ? { citationWarning: true } : {}),
          ...(hasWebAccessOffer ? { webAccessOffer: true } : {}),
          createdAt: record.created_at,
        }),
        nextItemId: next.nextItemId + 1,
      };
    }
  }
  return next;
}

function supplementAssistantMetadataFromBlocks(
  state: TimelineState,
  start: number,
  turnId: string,
  records: MessageRecord[],
): TimelineState {
  const record = records
    .filter((candidate) => candidate.turn_id === turnId && candidate.role === "assistant")
    .at(-1);
  if (!record) return state;
  const metadata = assistantMetadataFromBlocks(record);
  if (!metadata.sources && !metadata.citationWarning && !metadata.webAccessOffer) {
    return state;
  }
  let index = state.items.length - 1;
  while (index >= start) {
    const item = state.items[index];
    if (item.kind === "assistant" && item.turnId === turnId) break;
    index -= 1;
  }
  if (index < start) return appendAssistantFromBlocks(state, record);
  const item = state.items[index];
  if (item.kind !== "assistant") return state;
  const items = state.items.slice();
  items[index] = {
    ...item,
    ...(metadata.sources ? { sources: metadata.sources } : {}),
    ...(metadata.citationWarning ? { citationWarning: true } : {}),
    ...(metadata.webAccessOffer ? { webAccessOffer: true } : {}),
  };
  return { ...state, items };
}

function pairToolResults(
  state: TimelineState,
  start: number,
  turnId: string,
  results: Array<{ block: MessageBlock }>,
): TimelineState {
  const cards: number[] = [];
  state.items.forEach((item, index) => {
    if (index >= start && item.kind === "tool") cards.push(index);
  });
  const paired = new Set<number>();

  const patches: Array<{ index: number; patch: Partial<ToolItem> }> = [];
  const standalone: ToolItem[] = [];

  for (const { block } of results) {
    const content = typeof block.content === "string" ? block.content : "";
    const name = typeof block.name === "string" ? block.name : "(unknown)";
    const id = typeof block.toolCallId === "string" ? block.toolCallId : "";
    const riskLevel = riskLevelForTool(undefined, name);
    const { preview, truncated } = truncate(content);
    const resultSummary = webResultSummaryFromOutput(name, content);

    let cardIndex = cards.find(
      (index) =>
        !paired.has(index) &&
        (state.items[index] as ToolItem).id === id,
    );
    if (cardIndex === undefined) {
      // Provider call ids differ from the engine's internal record ids used
      // in events; fall back to name + order within the turn.
      const byName = cards.find(
        (index) => !paired.has(index) && (state.items[index] as ToolItem).name === name,
      );
      if (byName !== undefined) cardIndex = byName;
    }
    if (cardIndex !== undefined) {
      paired.add(cardIndex);
      const existing = state.items[cardIndex] as ToolItem;
      const patch: Partial<ToolItem> = {};
      if (existing.name === "(unknown)" && name !== "(unknown)") {
        patch.name = name;
        if (existing.riskLevel === undefined && riskLevel !== undefined) {
          patch.riskLevel = riskLevel;
        }
      }
      if (existing.resultPreview === undefined) {
        patch.resultPreview = preview;
        patch.resultTruncated = truncated;
      }
      if (resultSummary !== undefined) {
        patch.resultSummary = resultSummary;
      }
      if (existing.status === "running" || existing.status === "awaiting_approval") {
        patch.status = content === "denied" ? "denied" : "succeeded";
      }
      if (Object.keys(patch).length > 0) {
        patches.push({ index: cardIndex, patch });
      }
      continue;
    }
    standalone.push({
      kind: "tool",
      id: id || `tool-result-${name}-${standalone.length}`,
      turnId,
      name,
      ...(riskLevel !== undefined ? { riskLevel } : {}),
      status: content === "denied" ? "denied" : "succeeded",
      resultPreview: preview,
      resultTruncated: truncated,
      ...(resultSummary !== undefined ? { resultSummary } : {}),
    });
  }

  if (patches.length === 0 && standalone.length === 0) return state;
  let items = state.items.slice();
  for (const { index, patch } of patches) {
    items[index] = { ...(items[index] as ToolItem), ...patch } as ToolItem;
  }
  if (standalone.length > 0) {
    // Insert standalone cards after the last card/assistant content of the
    // turn rather than at the very end (later turns may already exist).
    const insertAt = cards.length > 0 ? cards[cards.length - 1] + 1 : start;
    items = items.slice(0, insertAt).concat(standalone, items.slice(insertAt));
  }
  const hasPriorSideEffect = hasPriorSideEffectForTurn(items, turnId);
  items = items.map((item) =>
    item.kind === "notice" &&
    item.notice === "turn_failed" &&
    item.turnId === turnId &&
    item.hasPriorSideEffect !== hasPriorSideEffect
      ? { ...item, hasPriorSideEffect }
      : item,
  );
  return { ...state, items };
}

export function buildTimelineHistory(sources: HistorySources): HistoryBuild {
  const { messages, turns, events } = sources;
  let state = initialTimeline();
  let activeTurnId: string | null = null;

  // Real wall-clock durations from the turns table seed the "用时" header;
  // replayed events keep them unless a stamp is missing.
  const seededTurnTimes: TimelineState["turnTimes"] = {};
  for (const turn of turns) {
    const started = epochMs(turn.started_at);
    const ended = epochMs(turn.finished_at);
    if (started !== undefined || ended !== undefined) {
      seededTurnTimes[turn.id] = {
        ...(started !== undefined ? { started } : {}),
        ...(ended !== undefined ? { ended } : {}),
      };
    }
  }
  state = { ...state, turnTimes: seededTurnTimes };

  // Messages without a turn predate the turn model; keep them on top.
  for (const record of messages) {
    if (record.turn_id !== null) continue;
    if (record.role === "user") {
      const item: UserItem = {
        kind: "user",
        id: record.id,
        text: userText(record.content),
      };
      state = { ...state, items: [...state.items, item], nextItemId: state.nextItemId + 1 };
    } else if (record.role === "assistant") {
      state = appendAssistantFromBlocks(state, record);
    }
  }

  const eventsByTurn = new Map<string, ThreadEventView[]>();
  for (const view of events) {
    const list = eventsByTurn.get(view.turn_id);
    if (list) list.push(view);
    else eventsByTurn.set(view.turn_id, [view]);
  }

  for (const turn of turns) {
    const turnEvents = eventsByTurn.get(turn.id) ?? [];

    // User message(s) of this turn always lead the turn.
    for (const record of messages) {
      if (record.turn_id !== turn.id || record.role !== "user") continue;
      const ids = userAttachmentIds(record.content);
      const attachments = ids
        .map((id) => sources.attachmentRecordsById?.[id])
        .filter((meta): meta is TimelineAttachment => meta !== undefined);
      const names = ids
        .map((id) => sources.attachmentNamesById?.[id])
        .filter((name): name is string => name !== undefined);
      const item: UserItem = {
        kind: "user",
        id: record.id,
        turnId: turn.id,
        text: userText(record.content),
        ...(attachments.length > 0 ? { attachments } : {}),
        ...(attachments.length === 0 && names.length > 0
          ? { attachmentNames: names }
          : {}),
        ...(attachments.length === 0 && names.length === 0 && ids.length > 0
          ? { attachmentCount: ids.length }
          : {}),
        createdAt: record.created_at,
      };
      state = { ...state, items: [...state.items, item], nextItemId: state.nextItemId + 1 };
    }

    const start = state.items.length;
    const hasEvents = turnEvents.length > 0;

    if (hasEvents) {
      for (const view of turnEvents) {
        const agentEvent = toAgentEvent(view);
        if (agentEvent === null) continue;
        // Route every event through the reducer first so the sequence
        // window advances (replay dedupe), then buffer deltas separately.
        const advanced = applyTimelineEvent(state, agentEvent);
        if (advanced === state) continue;
        state = advanced;
        if (agentEvent.type === "message_delta") {
          const kind = agentEvent.payload.kind;
          const delta = agentEvent.payload.delta;
          if (typeof delta !== "string") continue;
          if (kind === "reasoning") state = bufferDeltas(state, { reasoning: delta });
          else if (kind === "text") state = bufferDeltas(state, { text: delta });
        }
      }
      // The persisted assistant message is the historical source of truth.
      // Older or partially persisted event streams may omit completion
      // metadata even though SQLite contains the final structured blocks.
      state = supplementAssistantMetadataFromBlocks(state, start, turn.id, messages);
    } else {
      // No persisted events (defensive path): build from message blocks.
      for (const record of messages) {
        if (record.turn_id !== turn.id || record.role !== "assistant") continue;
        state = appendAssistantFromBlocks(state, record);
        for (const block of blocksOf(record)) {
          if (block.type === "tool_call") {
            const name =
              typeof block.name === "string" ? block.name : "(unknown)";
            const riskLevel = riskLevelForTool(undefined, name);
            const card: ToolItem = {
              kind: "tool",
              id: typeof block.id === "string" ? block.id : `tool-${record.id}`,
              turnId: turn.id,
              name,
              ...(riskLevel !== undefined ? { riskLevel } : {}),
              argumentsSummary:
                typeof block.arguments === "object" && block.arguments !== null
                  ? (block.arguments as Record<string, unknown>)
                  : undefined,
              status: "running",
            };
            state = { ...state, items: [...state.items, card], nextItemId: state.nextItemId + 1 };
          }
        }
      }
    }

    // Supplement previews and standalone result cards from tool messages.
    const results = toolBlocksOf(messages, turn.id).map(({ block }) => ({ block }));
    state = pairToolResults(state, start, turn.id, results);

    // Persisted Turn status is authoritative when the final event was missed
    // during a disconnect. Never rebuild an actionable approval card for a
    // Turn that the runtime has already closed.
    if (
      turn.status === "failed" ||
      turn.status === "cancelled" ||
      turn.status === "interrupted" ||
      turn.status === "completed"
    ) {
      const terminalToolStatus = turn.status === "failed" ? "failed" : "cancelled";
      state = {
        ...state,
        items: settleTurnTools(state.items, turn.id, terminalToolStatus),
      };
    }

    if (turn.status === "interrupted") {
      const sourceText = sourceTextForTurn(state.items, turn.id);
      state = {
        ...state,
        items: [
          ...state.items,
          {
            kind: "notice",
            id: `notice-interrupted-${turn.id}`,
            notice: "turn_interrupted",
            turnId: turn.id,
            recoveryKind: recoveryKindFor(undefined, sourceText),
            hasPriorSideEffect: hasPriorSideEffectForTurn(state.items, turn.id),
            sourceText,
          },
        ],
      };
    }
    if (
      turn.status === "failed" &&
      !state.items.some(
        (item) =>
          item.kind === "notice" &&
          item.notice === "turn_failed" &&
          item.turnId === turn.id,
      )
    ) {
      const sourceText = sourceTextForTurn(state.items, turn.id);
      const errorCode =
        typeof turn.error_code === "string" ? turn.error_code : undefined;
      state = {
        ...state,
        items: [
          ...state.items,
          {
            kind: "notice",
            id: `notice-failed-${turn.id}-status-fallback`,
            notice: "turn_failed",
            turnId: turn.id,
            ...(errorCode !== undefined ? { errorCode } : {}),
            recoveryKind: recoveryKindFor(errorCode, sourceText),
            hasPriorSideEffect: hasPriorSideEffectForTurn(state.items, turn.id),
            sourceText,
          },
        ],
      };
    }
    if (ACTIVE_TURN_STATUSES.has(turn.status)) {
      activeTurnId = turn.id;
    }
  }

  // Defensive close: terminal turns already closed via their events.
  state = {
    ...state,
    items: state.items.map((item) =>
      item.kind === "assistant" && item.isStreaming
        ? {
            ...item,
            isStreaming: false,
            segments: item.segments.map((segment) =>
              segment.isStreaming ? { ...segment, isStreaming: false } : segment,
            ),
          }
        : item,
    ),
  };
  state = {
    ...state,
    isRunning: activeTurnId !== null,
    isThinking: activeTurnId === null ? false : state.isThinking,
    activeTurnId,
  };

  return { state, activeTurnId };
}
