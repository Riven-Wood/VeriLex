import type {
  AttachmentUploadRecord,
  BackgroundTaskDetailRecord,
  OpenPathRequest,
  OpenPathResponse,
  BackgroundTaskOutputResponse,
  BackgroundTaskRecord,
  BackgroundTaskStopResponse,
  ContextUsageRecord,
  AtomicThreadSelectionRequest,
  AtomicThreadSelectionResult,
  CreateMcpServerInput,
  McpActiveViewRecord,
  McpDeleteResult,
  McpParseResult,
  McpProbeDraftRequest,
  McpSavedProbeRequest,
  McpSecretPatch,
  McpSelectionRecord,
  PreferencesResponse,
  HealthResponse,
  McpServerRecord,
  McpServerDraft,
  McpServerPatch,
  McpTestResult,
  McpThreadSnapshot,
  McpToolDefinition,
  McpToolRecord,
  McpToolDecision,
  McpToolReviewRequest,
  ProviderDraft,
  ProviderProbeResult,
  ProviderModelContextWindowRecord,
  ProviderRecord,
  RuntimeErrorBody,
  SkillDeleteResult,
  SkillRevealResult,
  SkillSummary,
  ThreadArtifactRecord,
  ThreadAttachmentRecord,
  ThreadCreateRequest,
  ThreadEventRecord,
  ThreadRecord,
  ThreadScope,
  ToolCapabilityRecord,
  MessageRecord,
  ModelCapabilityRecord,
  ThreadContextUpdateRequest,
  TurnRecord,
  UpdateMcpServerInput,
  WorkspaceCreateRequest,
  WorkspaceFilePreview,
  WorkspaceRecord,
} from "./contracts";

const runtimeBase = (): string =>
  globalThis.__VERILEX_HTTP__ ??
  import.meta.env.VITE_VERILEX_HTTP ??
  "http://127.0.0.1:8766";

const launchToken = (): string =>
  globalThis.__VERILEX_API_TOKEN__ ??
  import.meta.env.VITE_VERILEX_API_TOKEN ??
  "";

export class RuntimeApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly status: number;

  constructor(
    message: string,
    options: { code: string; retryable: boolean; status: number },
  ) {
    super(message);
    this.name = "RuntimeApiError";
    this.code = options.code;
    this.retryable = options.retryable;
    this.status = options.status;
  }
}

export async function runtimeRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const token = launchToken();
  if (token) {
    headers.set("X-VeriLex-Token", token);
  }

  const response = await globalThis.fetch(`${runtimeBase()}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as RuntimeErrorBody;
    const details = typeof body.error === "object" && body.error !== null
      ? body.error
      : null;
    const message = typeof body.error === "string"
      ? body.error
      : details?.message;
    throw new RuntimeApiError(
      message ?? `VeriLex Runtime 请求失败（${response.status}）`,
      {
        code: details?.code ?? "runtime_request_failed",
        retryable: details?.retryable ?? response.status >= 500,
        status: response.status,
      },
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return runtimeRequest<HealthResponse>("/v1/health", { signal });
}

export function listToolCapabilities(
  signal?: AbortSignal,
): Promise<ToolCapabilityRecord[]> {
  return runtimeRequest<ToolCapabilityRecord[]>("/v1/capabilities/tools", { signal });
}

export function listProviders(signal?: AbortSignal): Promise<ProviderRecord[]> {
  return runtimeRequest<ProviderRecord[]>("/v1/providers", { signal });
}

export function testProviderConnection(
  draft: ProviderDraft,
  signal?: AbortSignal,
): Promise<ProviderProbeResult> {
  return runtimeRequest<ProviderProbeResult>("/v1/providers/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
    signal,
  });
}

export function createProvider(
  draft: ProviderDraft,
  signal?: AbortSignal,
): Promise<ProviderRecord> {
  return runtimeRequest<ProviderRecord>("/v1/providers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
    signal,
  });
}

export function testSavedProvider(
  providerId: string,
  signal?: AbortSignal,
): Promise<ProviderProbeResult> {
  return runtimeRequest<ProviderProbeResult>(
    `/v1/providers/${encodeURIComponent(providerId)}/test`,
    { method: "POST", signal },
  );
}

export function discoverProviderModels(
  providerId: string,
  signal?: AbortSignal,
): Promise<ProviderProbeResult> {
  return runtimeRequest<ProviderProbeResult>(
    `/v1/providers/${encodeURIComponent(providerId)}/models`,
    { signal },
  );
}

export function listModelContextWindows(
  providerId: string,
  signal?: AbortSignal,
): Promise<ProviderModelContextWindowRecord[]> {
  return runtimeRequest<ProviderModelContextWindowRecord[]>(
    `/v1/providers/${encodeURIComponent(providerId)}/model-context-windows`,
    { signal },
  );
}

export function refreshModelContextWindows(
  providerId: string,
  signal?: AbortSignal,
): Promise<ProviderModelContextWindowRecord[]> {
  return runtimeRequest<ProviderModelContextWindowRecord[]>(
    `/v1/providers/${encodeURIComponent(providerId)}/model-context-windows/refresh`,
    { method: "POST", signal },
  );
}

export function setModelContextWindowOverride(
  providerId: string,
  model: string,
  tokens: number,
  signal?: AbortSignal,
): Promise<ProviderModelContextWindowRecord> {
  return runtimeRequest<ProviderModelContextWindowRecord>(
    `/v1/providers/${encodeURIComponent(providerId)}/model-context-windows/override`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, context_window_tokens: tokens }),
      signal,
    },
  );
}

export function resetModelContextWindowOverride(
  providerId: string,
  model: string,
  signal?: AbortSignal,
): Promise<ProviderModelContextWindowRecord> {
  return runtimeRequest<ProviderModelContextWindowRecord>(
    `/v1/providers/${encodeURIComponent(providerId)}/model-context-windows/override/reset`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
      signal,
    },
  );
}

export function deleteProvider(providerId: string, signal?: AbortSignal): Promise<void> {
  return runtimeRequest<void>(
    `/v1/providers/${encodeURIComponent(providerId)}`,
    { method: "DELETE", signal },
  );
}

export function createThread(
  request: ThreadCreateRequest,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return runtimeRequest<ThreadRecord>("/v1/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
}

export function listThreads(
  scope?: ThreadScope,
  signal?: AbortSignal,
  scene: "general" | "legal-retrieval" | "case-retrieval" | "litigation-work" | "contract-review" | "scheduled" | "all" = "all",
): Promise<ThreadRecord[]> {
  const params = new URLSearchParams();
  if (scope) params.set("scope", scope);
  if (scene !== "all") params.set("scene", scene);
  const query = params.size ? `?${params.toString()}` : "";
  return runtimeRequest<ThreadRecord[]>(`/v1/threads${query}`, { signal });
}

export function getThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return runtimeRequest<ThreadRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}`,
    { signal },
  );
}

export function getThreadContextUsage(
  threadId: string,
  signal?: AbortSignal,
): Promise<ContextUsageRecord> {
  return runtimeRequest<ContextUsageRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/context-usage`,
    { signal },
  );
}

export function updateThreadContext(
  threadId: string,
  request: ThreadContextUpdateRequest,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return runtimeRequest<ThreadRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/context`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    },
  );
}

export function discardDraftThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<void> {
  return runtimeRequest<void>(
    `/v1/threads/${encodeURIComponent(threadId)}/draft`,
    { method: "DELETE", signal },
  );
}

export function renameThread(
  threadId: string,
  title: string,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return runtimeRequest<ThreadRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
      signal,
    },
  );
}

function threadAction(
  threadId: string,
  action: string,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return runtimeRequest<ThreadRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/${action}`,
    { method: "POST", signal },
  );
}

export function archiveThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return threadAction(threadId, "archive", signal);
}

export function unarchiveThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return threadAction(threadId, "unarchive", signal);
}

export function trashThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return threadAction(threadId, "trash", signal);
}

export function restoreThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadRecord> {
  return threadAction(threadId, "restore", signal);
}

export function deleteThread(
  threadId: string,
  signal?: AbortSignal,
): Promise<void> {
  return runtimeRequest<void>(
    `/v1/threads/${encodeURIComponent(threadId)}`,
    { method: "DELETE", signal },
  );
}

export function listMessages(
  threadId: string,
  signal?: AbortSignal,
): Promise<MessageRecord[]> {
  return runtimeRequest<MessageRecord[]>(
    `/v1/threads/${encodeURIComponent(threadId)}/messages`,
    { signal },
  );
}

export function listTurns(
  threadId: string,
  signal?: AbortSignal,
): Promise<TurnRecord[]> {
  return runtimeRequest<TurnRecord[]>(
    `/v1/threads/${encodeURIComponent(threadId)}/turns`,
    { signal },
  );
}

export function listThreadEvents(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadEventRecord[]> {
  return runtimeRequest<ThreadEventRecord[]>(
    `/v1/threads/${encodeURIComponent(threadId)}/events`,
    { signal },
  );
}

export function listThreadAttachments(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadAttachmentRecord[]> {
  return runtimeRequest<ThreadAttachmentRecord[]>(
    `/v1/threads/${encodeURIComponent(threadId)}/attachments`,
    { signal },
  );
}

export function listWorkspaces(signal?: AbortSignal): Promise<WorkspaceRecord[]> {
  return runtimeRequest<WorkspaceRecord[]>("/v1/workspaces", { signal });
}

export function getWorkspaceFilePreview(
  workspaceId: string,
  path: string,
  signal?: AbortSignal,
): Promise<WorkspaceFilePreview> {
  return runtimeRequest<WorkspaceFilePreview>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/file-preview?path=${encodeURIComponent(path)}`,
    { signal },
  );
}

export function openWorkspacePath(
  workspaceId: string,
  request: OpenPathRequest,
  signal?: AbortSignal,
): Promise<OpenPathResponse> {
  return runtimeRequest<OpenPathResponse>(
    `/v1/workspaces/${encodeURIComponent(workspaceId)}/open-path`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal,
    },
  );
}

export function listThreadArtifacts(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadArtifactRecord[]> {
  return runtimeRequest<{ artifacts: ThreadArtifactRecord[] }>(
    `/v1/threads/${encodeURIComponent(threadId)}/artifacts`,
    { signal },
  ).then((body) => body.artifacts);
}

export async function fetchAttachmentContent(
  threadId: string,
  attachmentId: string,
  signal?: AbortSignal,
): Promise<Blob> {
  const headers = new Headers();
  const token = launchToken();
  if (token) {
    headers.set("X-VeriLex-Token", token);
  }
  const response = await globalThis.fetch(
    `${runtimeBase()}/v1/threads/${encodeURIComponent(threadId)}/attachments/${encodeURIComponent(attachmentId)}/content`,
    { headers, signal },
  );
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as RuntimeErrorBody;
    const details = typeof body.error === "object" && body.error !== null
      ? body.error
      : null;
    throw new RuntimeApiError(
      details?.message ?? `VeriLex Runtime 请求失败（${response.status}）`,
      {
        code: details?.code ?? "runtime_request_failed",
        retryable: details?.retryable ?? response.status >= 500,
        status: response.status,
      },
    );
  }
  return response.blob();
}

export function createWorkspace(
  request: WorkspaceCreateRequest,
  signal?: AbortSignal,
): Promise<WorkspaceRecord> {
  return runtimeRequest<WorkspaceRecord>("/v1/workspaces", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
}

export function listModelCapabilities(
  providerId: string,
  signal?: AbortSignal,
): Promise<ModelCapabilityRecord[]> {
  return runtimeRequest<ModelCapabilityRecord[]>(
    `/v1/providers/${encodeURIComponent(providerId)}/model-capabilities`,
    { signal },
  );
}

export function putModelCapability(
  providerId: string,
  model: string,
  supportsVision: boolean,
  signal?: AbortSignal,
): Promise<ModelCapabilityRecord> {
  return runtimeRequest<ModelCapabilityRecord>(
    `/v1/providers/${encodeURIComponent(providerId)}/model-capabilities`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, supports_vision: supportsVision }),
      signal,
    },
  );
}

export function uploadAttachment(
  threadId: string,
  file: File,
  signal?: AbortSignal,
): Promise<AttachmentUploadRecord> {
  const headers = file.type ? { "Content-Type": file.type } : undefined;
  return runtimeRequest<AttachmentUploadRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/attachment-uploads?filename=${encodeURIComponent(file.name)}`,
    { method: "POST", headers, body: file, signal },
  );
}

export function listAttachmentUploads(
  threadId: string,
  signal?: AbortSignal,
): Promise<AttachmentUploadRecord[]> {
  return runtimeRequest<AttachmentUploadRecord[]>(
    `/v1/threads/${encodeURIComponent(threadId)}/attachment-uploads`,
    { signal },
  );
}

export function deleteAttachmentUpload(
  threadId: string,
  uploadId: string,
  signal?: AbortSignal,
): Promise<void> {
  return runtimeRequest<void>(
    `/v1/threads/${encodeURIComponent(threadId)}/attachment-uploads/${encodeURIComponent(uploadId)}`,
    { method: "DELETE", signal },
  );
}

export function listMcpServers(signal?: AbortSignal): Promise<McpServerRecord[]> {
  return runtimeRequest<McpServerRecord[]>("/v1/mcp/servers", { signal });
}

function mcpDraftBody(draft: McpServerDraft): McpServerDraft {
  return {
    displayName: draft.displayName,
    transport: draft.transport,
    command: draft.command,
    args: draft.args,
    cwd: draft.cwd,
    url: draft.url,
    env: draft.env,
    headers: draft.headers,
    secretEnvKeys: draft.secretEnvKeys,
    secretHeaderKeys: draft.secretHeaderKeys,
    connectTimeoutMs: draft.connectTimeoutMs,
    requestTimeoutMs: draft.requestTimeoutMs,
  };
}

function mcpSecretPatchBody(patch: McpSecretPatch): McpSecretPatch {
  return {
    ...(patch.env !== undefined ? { env: patch.env } : {}),
    ...(patch.headers !== undefined ? { headers: patch.headers } : {}),
  };
}

function mcpToolDecisionBody(decision: McpToolDecision): McpToolDecision {
  return {
    toolName: decision.toolName,
    enabled: decision.enabled,
    expectedDescriptionHash: decision.expectedDescriptionHash,
    expectedSchemaHash: decision.expectedSchemaHash,
  };
}

function mcpProbeDraftBody(input: McpProbeDraftRequest): McpProbeDraftRequest {
  return {
    draft: mcpDraftBody(input.draft),
    secretPatch: mcpSecretPatchBody(input.secretPatch),
    acceptedRiskCodes: input.acceptedRiskCodes,
  };
}

function createMcpServerBody(input: CreateMcpServerInput): CreateMcpServerInput {
  return {
    ...mcpProbeDraftBody(input),
    testReceipt: input.testReceipt,
    toolDecisions: input.toolDecisions.map(mcpToolDecisionBody),
  };
}

function mcpServerPatchBody(patch: McpServerPatch): McpServerPatch {
  return {
    displayName: patch.displayName,
    transport: patch.transport,
    command: patch.command,
    args: patch.args,
    cwd: patch.cwd,
    url: patch.url,
    env: patch.env,
    headers: patch.headers,
    secretEnvKeys: patch.secretEnvKeys,
    secretHeaderKeys: patch.secretHeaderKeys,
    connectTimeoutMs: patch.connectTimeoutMs,
    requestTimeoutMs: patch.requestTimeoutMs,
  };
}

function mcpSavedProbeBody(input: McpSavedProbeRequest): McpSavedProbeRequest {
  return {
    expectedRevision: input.expectedRevision,
    patch: mcpServerPatchBody(input.patch),
    secretPatch: mcpSecretPatchBody(input.secretPatch),
    acceptedRiskCodes: input.acceptedRiskCodes,
  };
}

export function parseMcpImport(
  text: string,
  signal?: AbortSignal,
  selectedDisplayNames?: readonly string[],
): Promise<McpParseResult> {
  return runtimeRequest<McpParseResult>("/v1/mcp/import/parse", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      ...(selectedDisplayNames ? { selectedDisplayNames } : {}),
    }),
    signal,
  });
}

export function testMcpServerDraft(
  input: McpProbeDraftRequest,
  signal?: AbortSignal,
): Promise<McpTestResult> {
  return runtimeRequest<McpTestResult>("/v1/mcp/servers/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(mcpProbeDraftBody(input)),
    signal,
  });
}

export function createMcpServer(
  input: CreateMcpServerInput,
  signal?: AbortSignal,
): Promise<McpServerRecord> {
  return runtimeRequest<McpServerRecord>("/v1/mcp/servers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(createMcpServerBody(input)),
    signal,
  });
}

export function updateMcpServer(
  serverId: string,
  input: UpdateMcpServerInput,
  signal?: AbortSignal,
): Promise<McpServerRecord> {
  return runtimeRequest<McpServerRecord>(
    `/v1/mcp/servers/${encodeURIComponent(serverId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...mcpSavedProbeBody(input),
        ...(input.testReceipt !== undefined ? { testReceipt: input.testReceipt } : {}),
        toolDecisions: input.toolDecisions.map(mcpToolDecisionBody),
      }),
      signal,
    },
  );
}

export function deleteMcpServer(
  serverId: string,
  signal?: AbortSignal,
): Promise<McpDeleteResult> {
  return runtimeRequest<McpDeleteResult>(
    `/v1/mcp/servers/${encodeURIComponent(serverId)}`,
    { method: "DELETE", signal },
  );
}

export function testSavedMcpServer(
  serverId: string,
  input: McpSavedProbeRequest,
  signal?: AbortSignal,
): Promise<McpTestResult> {
  return runtimeRequest<McpTestResult>(
    `/v1/mcp/servers/${encodeURIComponent(serverId)}/test`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(mcpSavedProbeBody(input)),
      signal,
    },
  );
}

export function refreshMcpServerTools(
  serverId: string, expectedRevision: number, signal?: AbortSignal,
): Promise<McpToolRecord[]> {
  return runtimeRequest<McpToolRecord[]>(`/v1/mcp/servers/${encodeURIComponent(serverId)}/refresh-tools`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ expectedRevision }), signal,
  });
}

export function listMcpServerTools(
  serverId: string,
  signal?: AbortSignal,
): Promise<McpToolRecord[]> {
  return runtimeRequest<McpToolRecord[]>(
    `/v1/mcp/servers/${encodeURIComponent(serverId)}/tools`,
    { signal },
  );
}

export function reviewMcpServerTool(
  serverId: string,
  toolName: string,
  input: McpToolReviewRequest,
  signal?: AbortSignal,
): Promise<McpToolRecord> {
  return runtimeRequest<McpToolRecord>(
    `/v1/mcp/servers/${encodeURIComponent(serverId)}/tools/${encodeURIComponent(toolName)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: input.enabled,
        expectedDescriptionHash: input.expectedDescriptionHash,
        expectedSchemaHash: input.expectedSchemaHash,
      }),
      signal,
    },
  );
}

export function getThreadMcpSnapshot(
  threadId: string,
  signal?: AbortSignal,
): Promise<McpThreadSnapshot> {
  return runtimeRequest<McpThreadSnapshot>(
    `/v1/threads/${encodeURIComponent(threadId)}/mcp`,
    { signal },
  );
}

export function selectThreadMcpServer(
  threadId: string,
  serverId: string,
  signal?: AbortSignal,
): Promise<McpSelectionRecord> {
  return runtimeRequest<McpSelectionRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/mcp/${encodeURIComponent(serverId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: "{}",
      signal,
    },
  );
}

export function deselectThreadMcpServer(
  threadId: string,
  serverId: string,
  signal?: AbortSignal,
): Promise<McpSelectionRecord> {
  return runtimeRequest<McpSelectionRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/mcp/${encodeURIComponent(serverId)}`,
    { method: "DELETE", signal },
  );
}

export function listThreadMcpTools(
  threadId: string,
  signal?: AbortSignal,
): Promise<McpToolDefinition[]> {
  return runtimeRequest<McpToolDefinition[]>(
    `/v1/threads/${encodeURIComponent(threadId)}/mcp/tools`,
    { signal },
  );
}

export function createAtomicThreadMcpSelection(
  input: AtomicThreadSelectionRequest,
  signal?: AbortSignal,
): Promise<AtomicThreadSelectionResult> {
  return runtimeRequest<AtomicThreadSelectionResult>("/v1/mcp/thread-selections", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      existingThreadId: input.existingThreadId,
      draftThread: input.draftThread === null ? null : {
        title: input.draftThread.title,
        providerId: input.draftThread.providerId,
        model: input.draftThread.model,
        workspaceId: input.draftThread.workspaceId,
        reasoningEffort: input.draftThread.reasoningEffort,
      },
      existingServerId: input.existingServerId,
      createServer: input.createServer === null
        ? null
        : createMcpServerBody(input.createServer),
      ...(input.viewId !== undefined ? { viewId: input.viewId } : {}),
    }),
    signal,
  });
}

export function activateMcpView(
  viewId: string,
  threadId: string,
  signal?: AbortSignal,
): Promise<McpActiveViewRecord> {
  return runtimeRequest<McpActiveViewRecord>(
    `/v1/mcp/active-views/${encodeURIComponent(viewId)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ threadId }),
      signal,
    },
  );
}

export function deactivateMcpView(
  viewId: string,
  signal?: AbortSignal,
): Promise<McpActiveViewRecord> {
  return runtimeRequest<McpActiveViewRecord>(
    `/v1/mcp/active-views/${encodeURIComponent(viewId)}`,
    { method: "DELETE", signal },
  );
}

export function listSkills(signal?: AbortSignal): Promise<SkillSummary[]> {
  return runtimeRequest<SkillSummary[]>("/v1/skills", { signal });
}

export function listSkillCategories(): Promise<{id: string; name: string}[]> {
  return runtimeRequest("/v1/skills/categories");
}

export function createSkillCategory(name: string): Promise<{id: string; name: string}> {
  return runtimeRequest("/v1/skills/categories", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({name}) });
}

export function renameSkillCategory(id: string, name: string): Promise<void> {
  return runtimeRequest(`/v1/skills/categories/${encodeURIComponent(id)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({name}) });
}

export function deleteSkillCategory(id: string): Promise<void> {
  return runtimeRequest(`/v1/skills/categories/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export function setSkillCategory(name: string, category: string | null): Promise<void> {
  return runtimeRequest(`/v1/skills/${encodeURIComponent(name)}/category`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({category}) });
}

export function setSkillEnabled(
  name: string,
  enabled: boolean,
  signal?: AbortSignal,
): Promise<void> {
  return runtimeRequest<void>(`/v1/skills/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
    signal,
  });
}

export function deleteSkill(
  name: string,
  signal?: AbortSignal,
): Promise<SkillDeleteResult> {
  return runtimeRequest<SkillDeleteResult>(
    `/v1/skills/${encodeURIComponent(name)}`,
    { method: "DELETE", signal },
  );
}

export function revealSkill(
  name: string,
  signal?: AbortSignal,
): Promise<SkillRevealResult> {
  return runtimeRequest<SkillRevealResult>(
    `/v1/skills/${encodeURIComponent(name)}/reveal`,
    { method: "POST", signal },
  );
}

export function listBackgroundTasks(
  threadId: string,
  signal?: AbortSignal,
): Promise<BackgroundTaskRecord[]> {
  return runtimeRequest<BackgroundTaskRecord[]>(
    `/v1/threads/${encodeURIComponent(threadId)}/background-tasks`,
    { signal },
  );
}

export function getBackgroundTask(
  threadId: string,
  taskId: string,
  signal?: AbortSignal,
): Promise<BackgroundTaskDetailRecord> {
  return runtimeRequest<BackgroundTaskDetailRecord>(
    `/v1/threads/${encodeURIComponent(threadId)}/background-tasks/${encodeURIComponent(taskId)}`,
    { signal },
  );
}

export function getBackgroundTaskOutput(
  threadId: string,
  taskId: string,
  after = 0,
  signal?: AbortSignal,
): Promise<BackgroundTaskOutputResponse> {
  const query = after > 0 ? `?after=${after}` : "";
  return runtimeRequest<BackgroundTaskOutputResponse>(
    `/v1/threads/${encodeURIComponent(threadId)}/background-tasks/${encodeURIComponent(taskId)}/output${query}`,
    { signal },
  );
}

export function stopBackgroundTask(
  threadId: string,
  taskId: string,
): Promise<BackgroundTaskStopResponse> {
  return runtimeRequest<BackgroundTaskStopResponse>(
    `/v1/threads/${encodeURIComponent(threadId)}/background-tasks/${encodeURIComponent(taskId)}/stop`,
    { method: "POST" },
  );
}

export function getPreferences(signal?: AbortSignal): Promise<PreferencesResponse> {
  return runtimeRequest<PreferencesResponse>("/v1/preferences", { signal });
}

export function putPreference(
  key: string,
  value: number | string,
): Promise<PreferencesResponse> {
  return runtimeRequest<PreferencesResponse>(
    `/v1/preferences/${encodeURIComponent(key)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    },
  );
}

export function wsUrl(): string {
  const base = globalThis.__VERILEX_HTTP__
    ?? import.meta.env.VITE_VERILEX_HTTP
    ?? "http://127.0.0.1:8766";
  return base.replace(/^http/, "ws") + "/v1/ws";
}

export function wsToken(): string {
  return globalThis.__VERILEX_API_TOKEN__
    ?? import.meta.env.VITE_VERILEX_API_TOKEN
    ?? "";
}

export interface LegalCacheStats {
  count: number;
  oldestFetchedAt: string | null;
}

export function getLegalCacheStats(
  signal?: AbortSignal,
): Promise<LegalCacheStats> {
  return runtimeRequest<LegalCacheStats>("/v1/legal/cache", { signal });
}

export function getCachedLawText(bbbs: string, clause?: string): Promise<{ paragraphs: string[] }> {
  const query = clause ? `?clause=${encodeURIComponent(clause)}` : "";
  return runtimeRequest(`/v1/legal/cache/${encodeURIComponent(bbbs)}/text${query}`);
}

export function clearLegalCache(signal?: AbortSignal): Promise<void> {
  return runtimeRequest<void>("/v1/legal/cache", { method: "DELETE", signal });
}

export function openLegalOfficialPage(signal?: AbortSignal): Promise<void> {
  return runtimeRequest<void>("/v1/legal/official-page", {
    method: "POST",
    signal,
  });
}

export interface ThreadSearchResult {
  thread: ThreadRecord;
  snippet: {text: string; start: number; end: number};
}
export function searchThreads(q: string, signal?: AbortSignal): Promise<ThreadSearchResult[]> {
  return runtimeRequest<ThreadSearchResult[]>(`/v1/threads/search?q=${encodeURIComponent(q)}`, {signal});
}

export function updateWorkspacePermissions(id: string, allowRead: boolean, allowWrite: boolean): Promise<WorkspaceRecord> {
  return runtimeRequest(`/v1/workspaces/${encodeURIComponent(id)}`, {method:"PATCH",headers:{"Content-Type":"application/json"},body:JSON.stringify({allow_read:allowRead,allow_write:allowWrite})});
}
export function revokeWorkspace(id: string): Promise<void> {
  return runtimeRequest(`/v1/workspaces/${encodeURIComponent(id)}`, {method:"DELETE"});
}

export function renameWorkspace(id: string, displayName: string): Promise<WorkspaceRecord> {
  return runtimeRequest(`/v1/workspaces/${encodeURIComponent(id)}/rename`, {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({display_name:displayName})});
}
