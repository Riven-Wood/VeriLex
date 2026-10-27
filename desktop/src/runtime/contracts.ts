export interface ConfiguredCapabilities {
  models: number;
  mcp_servers: number;
  skills: number;
  folders: number;
}

export interface HealthResponse {
  ok: boolean;
  service: "verilex-runtime";
  version: string;
  storage: "local";
  cloud_backend: false;
  configured: ConfiguredCapabilities;
}

export type ToolCapabilitySource = "builtin" | "skill" | "mcp";
export type ToolCapabilityCategory =
  | "files"
  | "terminal"
  | "skills"
  | "external"
  | "documents"
  | "other";
export type ToolCapabilityScope = "read" | "write" | "execute" | "network" | "external_service";
export type ToolApprovalBehavior =
  | "risk_default"
  | "thread_first_use"
  | "always_human"
  | "additive_artifact";

export interface ToolCapabilityRecord {
  name: string;
  description: string;
  source: ToolCapabilitySource;
  source_name: string | null;
  category: ToolCapabilityCategory;
  scopes: ToolCapabilityScope[];
  risk_level: "low" | "high";
  default_approval: "automatic" | "required";
  approval_behavior: ToolApprovalBehavior;
  requires_workspace: boolean;
  availability: "available";
  availability_reason: null;
}

export type ProviderKind = "openai_compatible" | "ollama";
export type ProviderAuthMode = "api_key" | "none";

export interface ProviderRecord {
  id: string;
  name: string;
  kind: ProviderKind;
  base_url: string;
  default_model: string;
  auth_mode: ProviderAuthMode;
  has_secret: boolean;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  model_context_metadata_status?: "ready" | "partial" | "unavailable";
}

export interface ProviderDraft {
  name: string;
  kind: ProviderKind;
  base_url: string;
  default_model: string;
  auth_mode: ProviderAuthMode;
  api_key?: string;
  default_model_context_window_tokens?: number;
}

export interface ProviderContextWindowHint {
  model: string;
  tokens: number | null;
  source: "provider" | "curated" | "unknown";
}

export interface ProviderProbeResult {
  models: string[];
  selected_model: string;
  context_windows?: ProviderContextWindowHint[];
}

export interface ProviderModelContextWindowRecord {
  provider_id: string;
  model: string;
  context_window_tokens: number | null;
  context_window_source: "unknown" | "provider" | "curated" | "user_declared";
  automatic_tokens: number | null;
  automatic_source: "unknown" | "provider" | "curated";
  manual_tokens: number | null;
  is_available: boolean;
  updated_at: string;
}

export interface RuntimeErrorBody {
  error?: string | {
    code: string;
    message: string;
    retryable: boolean;
  };
}

export type ThreadScope = "active" | "archived" | "trash";
export type ReasoningEffort = "auto" | "low" | "medium" | "high";

export interface ThreadRecord {
  id: string;
  title: string;
  provider_id: string | null;
  model: string;
  workspace_id: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  deleted_at: string | null;
  is_draft: boolean;
  reasoning_effort: ReasoningEffort;
  scene: string;
  legal_channel?: "web" | "plugin" | null;
  legal_skills?: string[];
}

export interface ThreadCreateRequest {
  title: string;
  model: string;
  provider_id: string;
  workspace_id?: string | null;
  is_draft?: boolean;
  scene?: "general" | "legal-retrieval" | "case-retrieval" | "litigation-work";
  legal_channel?: "web" | "plugin";
  legal_skills?: string[];
}

export interface ThreadContextUpdateRequest {
  provider_id: string;
  model: string;
  workspace_id: string | null;
  reasoning_effort: ReasoningEffort;
}

export interface WebAccessRecord {
  enabled: boolean;
  granted: boolean;
  enabledAt: string | null;
  grantedAt: string | null;
}

export interface WorkspaceRecord {
  id: string;
  root_path: string;
  display_name: string;
  allow_read: boolean;
  allow_write: boolean;
  created_at: string;
  last_used_at: string;
}

export interface WorkspaceCreateRequest {
  root_path: string;
  display_name: string;
  allow_read: boolean;
  allow_write: boolean;
}

export interface AttachmentUploadRecord {
  id: string;
  thread_id: string;
  display_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
  expires_at: string;
}

export interface ModelCapabilityRecord {
  provider_id: string;
  model: string;
  supports_vision: boolean;
  supports_native_pdf: false;
  source: "curated" | "user_declared";
  created_at: string;
  updated_at: string;
  context_window_tokens: number | null;
  supported_reasoning_efforts: Exclude<ReasoningEffort, "auto">[] | null;
  context_window_source: "unknown" | "curated" | "user_declared" | "provider";
  reasoning_efforts_source: "unknown" | "curated" | "user_declared" | "provider";
}

export interface ContextUsageBreakdown {
  messages: number;
  system_and_tools: number;
  skills: number;
  attachments: number;
  other: number;
}

export interface ContextUsageRecord {
  used_tokens: number;
  context_window_tokens: number | null;
  context_window_source: "unknown" | "provider" | "curated" | "user_declared";
  effective_window_tokens: number | null;
  used_percent: number | null;
  estimated: boolean;
  warning: "normal" | "near_limit" | "critical";
  breakdown: ContextUsageBreakdown;
  updated_at: string;
}

export interface CitationSource {
  number: number;
  sourceId: string;
  title: string;
  url: string;
  site: string;
  fetchedAt: string;
  truncated: boolean;
}

export interface MessageBlock {
  type:
    | "text"
    | "reasoning"
    | "tool_call"
    | "tool_result"
    | "sources"
    | "citation_warning"
    | "web_access_offer";
  id?: string;
  text?: string;
  toolCallId?: string;
  name?: string;
  arguments?: unknown;
  content?: string;
  sources?: CitationSource[];
}

export interface MessageRecord {
  id: string;
  thread_id: string;
  turn_id: string | null;
  role: "user" | "assistant" | "tool";
  content: string | MessageBlock[];
  created_at: string;
}

export interface TurnRecord {
  id: string;
  thread_id: string;
  status: string;
  error_code: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface ThreadEventRecord {
  id: string;
  thread_id: string;
  turn_id: string;
  sequence: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ThreadAttachmentRecord {
  id: string;
  thread_id: string;
  turn_id: string;
  display_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
}

export type WorkspacePreviewKind = "text" | "image" | "pdf" | "docx" | "unsupported";

export interface WorkspaceFilePreview {
  file_hash?:string;
  kind: WorkspacePreviewKind;
  name: string;
  path: string;
  size_bytes: number;
  modified_at: string;
  truncated: boolean;
  content?: string;
  media_type?: string;
  data_url?: string;
}

export type ThreadArtifactRecord =
  | {
      kind: "artifact";
      id: string;
      turn_id: string;
      name: string;
      published_relative_path: string;
      published_workspace_id: string;
      operation: string;
      size_bytes: number;
      structure_status: "passed" | "not_required";
      render_status: "passed" | "not_required";
      vision_status:
        | "passed"
        | "partial"
        | "unsupported"
        | "failed"
        | "not_run";
      created_at: string;
    }
  | {
      kind: "legacy_workspace";
      path: string;
      name: string;
      last_operation: string;
      last_operated_at: string;
    };

export interface OpenPathRequest {
  path: string;
  mode: "open" | "reveal";
}

export interface OpenPathResponse {
  path: string;
  mode: "open" | "reveal";
}

export type AgentEventType =
  | "turn_started"
  | "message_delta"
  | "message_reset"
  | "message_completed"
  | "turn_completed"
  | "turn_failed"
  | "turn_cancelled"
  | "tool_call_started"
  | "tool_call_completed"
  | "approval_requested"
  | "approval_resolved"
  | "attachment_degraded"
  | "reasoning_effort_degraded"
  | "turn_retry"
  | "turn_compacted";

export interface AgentEvent {
  type: AgentEventType;
  /** Present on Runtime wire events; optional for legacy persisted fixtures. */
  threadId?: string;
  turnId: string;
  sequence: number;
  payload: Record<string, unknown>;
  /** Local receive time or persisted created_at (epoch ms), for turn timing. */
  occurredAt?: number;
}

export type RiskLevel = "low" | "medium" | "high";
export type ApprovalDecision = "allowed" | "denied";

// Narrow payload shapes for the events the timeline consumes. Fields stay
// optional because older persisted events predate newer additions (e.g.
// resultPreview); readers must tolerate missing keys.
export interface MessageDeltaPayload {
  kind: "text" | "reasoning";
  delta: string;
}

export interface ToolCallStartedPayload {
  toolCallId: string;
  name: string;
  riskLevel?: RiskLevel;
  argumentsSummary?: Record<string, unknown>;
}

export interface ToolCallCompletedPayload {
  toolCallId: string;
  status: "succeeded" | "failed" | "denied";
  durationMs?: number;
  resultPreview?: string;
  resultTruncated?: boolean;
  resultSummary?: Record<string, unknown>;
}

export interface ApprovalRequestedPayload {
  approvalId: string;
  toolCallId: string;
  name: string;
  riskLevel?: RiskLevel;
  argumentsSummary?: Record<string, unknown>;
  approvalBehavior?: ToolApprovalBehavior;
  externalRecipient?: string;
  externalValue?: string;
}

export interface ApprovalResolvedPayload {
  approvalId: string;
  toolCallId?: string;
  decision: ApprovalDecision;
}

export interface TurnFailedPayload {
  errorCode: string;
  retryable: boolean;
}

export interface AttachmentDegradedEntry {
  id: string;
  name: string;
}

export interface AttachmentDegradedPayload {
  reason: string;
  total: number;
  truncated: boolean;
  attachments: AttachmentDegradedEntry[];
}

export type McpTransport = "stdio" | "http" | "sse";
export type McpConnectionStatus =
  | "idle"
  | "connecting"
  | "ready"
  | "retrying"
  | "needs_credentials"
  | "error"
  | "stopping";
export type McpReviewState = "known" | "new" | "changed";
export type McpConfigReviewState = "reviewed" | "needs_review" | "blocked";

export interface McpRiskFinding {
  code: string;
  severity: "block" | "warn";
}

export interface McpServerDraft {
  displayName: string;
  transport: McpTransport;
  command: string | null;
  args: string[];
  cwd: string | null;
  url: string | null;
  env: Record<string, string>;
  headers: Record<string, string>;
  secretEnvKeys: string[];
  secretHeaderKeys: string[];
  connectTimeoutMs: number;
  requestTimeoutMs: number;
}

export interface McpParsedServerDraft extends McpServerDraft {
  risks: McpRiskFinding[];
}

export interface McpParseResult {
  drafts: McpParsedServerDraft[];
}

export interface McpSecretPatch {
  env?: Record<string, string | null> | null;
  headers?: Record<string, string | null> | null;
}

export interface McpToolDecision {
  toolName: string;
  enabled: boolean;
  expectedDescriptionHash: string;
  expectedSchemaHash: string;
}

export interface McpProbeDraftRequest {
  draft: McpServerDraft;
  secretPatch: McpSecretPatch;
  acceptedRiskCodes: string[];
}

export interface CreateMcpServerInput extends McpProbeDraftRequest {
  testReceipt: string;
  toolDecisions: McpToolDecision[];
}

export interface McpServerPatch {
  displayName?: string | null;
  transport?: McpTransport | null;
  command?: string | null;
  args?: string[] | null;
  cwd?: string | null;
  url?: string | null;
  env?: Record<string, string> | null;
  headers?: Record<string, string> | null;
  secretEnvKeys?: string[] | null;
  secretHeaderKeys?: string[] | null;
  connectTimeoutMs?: number | null;
  requestTimeoutMs?: number | null;
}

export interface McpSavedProbeRequest {
  expectedRevision: number;
  patch: McpServerPatch;
  secretPatch: McpSecretPatch;
  acceptedRiskCodes: string[];
}

export interface UpdateMcpServerInput extends McpSavedProbeRequest {
  testReceipt?: string | null;
  toolDecisions: McpToolDecision[];
}

export interface McpServerRecord {
  id: string;
  displayName: string;
  transport: McpTransport;
  command: string | null;
  args: string[];
  cwd: string | null;
  url: string | null;
  env: Record<string, string>;
  headers: Record<string, string>;
  secretEnvKeys: string[];
  secretHeaderKeys: string[];
  connectTimeoutMs: number;
  requestTimeoutMs: number;
  configRevision: number;
  secretRevision: number;
  configReviewState: McpConfigReviewState;
  riskFindings: string[];
  connectionStatus: McpConnectionStatus;
  errorCode: string | null;
  retryable: boolean;
  lastTestStatus: "passed" | "failed" | null;
  lastTestedAt: string | null;
  selectedThreadCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface McpDiscoveredTool {
  toolName: string;
  description: string;
  descriptionHash: string;
  inputSchema: Record<string, unknown>;
  schemaHash: string;
}

export interface McpTestResult {
  testReceipt: string;
  tools: McpDiscoveredTool[];
  riskCodes: string[];
}

export interface McpToolRecord extends McpDiscoveredTool {
  serverId: string;
  enabled: boolean;
  reviewState: McpReviewState;
  firstSeenAt: string;
  lastSeenAt: string;
}

export interface McpToolReviewRequest {
  enabled: boolean;
  expectedDescriptionHash: string;
  expectedSchemaHash: string;
}

export interface McpThreadServerRecord {
  serverId: string;
  displayName: string;
  selected: boolean;
  connectionStatus: McpConnectionStatus;
  toolCount: number;
  errorCode: string | null;
  retryable: boolean;
  /** Frontend-only presentation metadata for an official built-in preset. */
  presetId?: "yuandian";
  presetServiceId?: "law" | "case" | "company" | "securities";
}

export interface McpThreadSnapshot {
  threadId: string;
  servers: McpThreadServerRecord[];
}

export interface McpSelectionRecord {
  serverId: string;
  selected: boolean;
  connectionStatus: McpConnectionStatus;
  toolCount: number;
  errorCode?: string | null;
  retryable: boolean;
  selectedAt?: string;
}

export interface McpAtomicSelectionRecord {
  serverId: string;
  selected: true;
  connectionStatus: McpConnectionStatus;
  toolCount: number;
  errorCode: string | null;
  retryable: boolean;
  selectedAt: string;
}

export interface McpToolDefinition {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export interface DraftThreadInput {
  title: string;
  providerId: string;
  model: string;
  workspaceId: string | null;
  reasoningEffort: ReasoningEffort;
}

type AtomicThreadTarget =
  | { existingThreadId: string; draftThread: null }
  | { existingThreadId: null; draftThread: DraftThreadInput };

type AtomicServerTarget =
  | { existingServerId: string; createServer: null }
  | { existingServerId: null; createServer: CreateMcpServerInput };

export type AtomicThreadSelectionRequest = AtomicThreadTarget & AtomicServerTarget & {
  viewId?: string | null;
};

export interface McpAtomicThreadRecord {
  id: string;
  title: string;
  providerId: string | null;
  model: string;
  workspaceId: string | null;
  status: string;
  createdAt: string;
  updatedAt: string;
  isDraft: boolean;
  reasoningEffort: ReasoningEffort;
  scene?: string;
}

export interface AtomicThreadSelectionResult {
  thread: McpAtomicThreadRecord;
  server: McpServerRecord;
  selection: McpAtomicSelectionRecord;
  createdThread: boolean;
  createdServer: boolean;
}

export type McpActiveViewRecord =
  | {
    viewId: string;
    active: true;
    threadId: string;
    connectionStatus: McpConnectionStatus;
    errorCode: string | null;
    retryable: boolean;
  }
  | {
    viewId: string;
    active: false;
  };

export interface McpDeleteResult {
  deleted: boolean;
  selectedThreadCount: number;
}

export interface SkillSummary {
  category?: string;
  name: string;
  description: string;
  instructions: string;
  enabled: boolean;
  valid: boolean;
  error: string | null;
  path: string;
}

export interface SkillDeleteResult {
  trashedPath: string;
}

export interface SkillRevealResult {
  path: string;
  mode: string;
}

export type BackgroundTaskStatus =
  | "running"
  | "completed"
  | "failed"
  | "stopped"
  | "expired"
  | "interrupted";

export interface BackgroundTaskRecord {
  id: string;
  toolCallId: string;
  command: string;
  description: string;
  status: BackgroundTaskStatus;
  exitCode: number | null;
  omittedBytes: number;
  startedAt: string;
  finishedAt: string | null;
  createdAt: string;
}

export interface BackgroundTaskDetailRecord extends BackgroundTaskRecord {
  outputText: string;
}

export interface BackgroundTaskOutputResponse {
  cursor: number;
  text: string;
  omittedBytes: number;
  status: BackgroundTaskStatus;
}

export interface BackgroundTaskStopResponse {
  taskId: string;
  status: BackgroundTaskStatus;
}

export interface PreferencesResponse {
  personal_instructions?: string;
  notify_completed?: number; notify_failed?: number; notify_approval?: number;
  notify_calendar?: number; notification_sound?: number; keep_awake?: number;
  background_task_max_minutes: number;
}
