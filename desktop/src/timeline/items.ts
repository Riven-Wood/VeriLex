// Timeline item model shared by the live reducer and the history builder.

import type {
  ApprovalDecision,
  CitationSource,
  RiskLevel,
  ToolApprovalBehavior,
} from "../runtime/contracts";

export type RecoveryKind =
  | "open_settings"
  | "restore_text"
  | "reselect_attachment"
  | "restart_app"
  | "none";

export type ToolCallStatus =
  | "running"
  | "awaiting_approval"
  | "succeeded"
  | "failed"
  | "denied"
  | "cancelled";

export interface TimelineAttachment {
  id: string;
  name: string;
  mediaType: string;
  sizeBytes: number;
  /** Owning thread; used to fetch content for preview without depending on
   * the currently selected conversation. */
  threadId?: string;
}

export interface UserItem {
  kind: "user";
  id: string;
  turnId?: string;
  text: string;
  /** Rich metadata for click-to-preview rendering; preferred over names. */
  attachments?: TimelineAttachment[];
  /** Fallback labels when metadata is unavailable (e.g. legacy history). */
  attachmentNames?: string[];
  attachmentCount?: number;
  createdAt?: string;
}

export interface ReasoningSegment {
  type: "reasoning";
  text: string;
  isStreaming: boolean;
}

export interface TextSegment {
  type: "text";
  text: string;
  isStreaming: boolean;
}

export type AssistantSegment = ReasoningSegment | TextSegment;

export interface AssistantItem {
  kind: "assistant";
  id: string;
  turnId?: string;
  segments: AssistantSegment[];
  isStreaming: boolean;
  sources?: CitationSource[];
  citationWarning?: boolean;
  webAccessOffer?: boolean;
  createdAt?: string;
}

export interface ToolItem {
  kind: "tool";
  id: string;
  turnId?: string;
  name: string;
  approvalId?: string;
  riskLevel?: RiskLevel;
  approvalDecision?: ApprovalDecision;
  approvalBehavior?: ToolApprovalBehavior;
  externalRecipient?: string;
  externalValue?: string;
  argumentsSummary?: Record<string, unknown>;
  status: ToolCallStatus;
  durationMs?: number;
  resultPreview?: string;
  resultTruncated?: boolean;
  resultSummary?: Record<string, unknown>;
}

export type NoticeKind =
  | "turn_cancelled"
  | "turn_retry"
  | "turn_compacted"
  | "attachment_degraded"
  | "reasoning_effort_degraded"
  | "turn_interrupted"
  | "turn_failed";

export interface NoticeItem {
  kind: "notice";
  id: string;
  notice: NoticeKind;
  turnId?: string;
  total?: number;
  attempt?: number;
  degraded?: boolean;
  truncated?: boolean;
  attachmentNames?: string[];
  reasoningEffortFrom?: string;
  reasoningEffortTo?: string;
  errorCode?: string;
  retryable?: boolean;
  recoveryKind?: RecoveryKind;
  hasPriorSideEffect?: boolean;
  sourceText?: string;
}

export type TimelineItem = UserItem | AssistantItem | ToolItem | NoticeItem;
