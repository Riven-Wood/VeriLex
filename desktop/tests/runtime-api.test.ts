import { afterEach, expect, test, vi } from "vitest";

import {
  createProvider,
  createThread,
  createWorkspace,
  deleteAttachmentUpload,
  discardDraftThread,
  deleteProvider,
  deleteThread,
  discoverProviderModels,
  getBackgroundTask,
  getBackgroundTaskOutput,
  getHealth,
  getPreferences,
  getThreadContextUsage,
  getThread,
  listAttachmentUploads,
  listModelCapabilities,
  listModelContextWindows,
  listProviders,
  listToolCapabilities,
  listThreadAttachments,
  listThreadEvents,
  listThreads,
  listTurns,
  listWorkspaces,
  listBackgroundTasks,
  putModelCapability,
  putPreference,
  refreshModelContextWindows,
  resetModelContextWindowOverride,
  renameThread,
  restoreThread,
  RuntimeApiError,
  runtimeRequest,
  setModelContextWindowOverride,
  stopBackgroundTask,
  archiveThread,
  testSavedProvider,
  testProviderConnection,
  trashThread,
  unarchiveThread,
  updateThreadContext,
  uploadAttachment,
} from "../src/runtime/api";

afterEach(() => {
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

test("adds the launch token and returns real health", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        ok: true,
        service: "verilex-runtime",
        version: "0.3.0",
        storage: "local",
        cloud_backend: false,
        configured: { models: 0, mcp_servers: 0, skills: 0, folders: 0 },
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(getHealth()).resolves.toMatchObject({ configured: { models: 0 } });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:43210/v1/health",
    expect.objectContaining({ headers: expect.any(Headers) }),
  );
  const headers = fetchMock.mock.calls[0][1].headers as Headers;
  expect(headers.get("X-VeriLex-Token")).toBe("launch-token");
});

test("does not expose a launch token in an error", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "never-log-this";
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ error: "runtime unavailable" }), { status: 503 }),
    ),
  );

  await expect(getHealth()).rejects.toThrow("runtime unavailable");
  await expect(getHealth()).rejects.not.toThrow("never-log-this");
});

test("forwards cancellation to the runtime request", async () => {
  const controller = new AbortController();
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({
        ok: true,
        service: "verilex-runtime",
        version: "0.3.0",
        storage: "local",
        cloud_backend: false,
        configured: { models: 0, mcp_servers: 0, skills: 0, folders: 0 },
      }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await getHealth(controller.signal);

  expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
});

test("lists tool capabilities with launch authentication and caller cancellation", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  const records = [
    {
      name: "run_command",
      description: "Run a validation command inside the authorized workspace.",
      source: "builtin",
      source_name: null,
      category: "terminal",
      scopes: ["execute"],
      risk_level: "high",
      default_approval: "required",
      approval_behavior: "risk_default",
      requires_workspace: true,
      availability: "available",
      availability_reason: null,
    },
  ] as const;
  const controller = new AbortController();
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(records), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(listToolCapabilities(controller.signal)).resolves.toEqual(records);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:43210/v1/capabilities/tools",
    expect.objectContaining({
      signal: controller.signal,
      headers: expect.any(Headers),
    }),
  );
  const headers = fetchMock.mock.calls[0][1].headers as Headers;
  expect(headers.get("X-VeriLex-Token")).toBe("launch-token");
});

test("preserves the sanitized provider error contract from the runtime envelope", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: {
            code: "provider_auth_failed",
            message: "Provider rejected the configured credentials.",
            retryable: false,
          },
        }),
        { status: 401 },
      ),
    ),
  );

  const request = runtimeRequest("/v1/providers/test");

  await expect(request).rejects.toBeInstanceOf(RuntimeApiError);
  await expect(request).rejects.toMatchObject({
    message: "Provider rejected the configured credentials.",
    code: "provider_auth_failed",
    retryable: false,
    status: 401,
  });
});

test("lists the runtime provider records without secret values", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:43210";
  globalThis.__VERILEX_API_TOKEN__ = "launch-token";
  const provider = {
    id: "provider-1",
    name: "Local Ollama",
    kind: "ollama",
    base_url: "http://127.0.0.1:11434",
    default_model: "qwen2.5:7b",
    auth_mode: "none",
    has_secret: false,
    enabled: true,
    created_at: "2026-08-09T12:00:00Z",
    updated_at: "2026-08-09T12:00:00Z",
  } as const;
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify([provider]), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(listProviders()).resolves.toEqual([provider]);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:43210/v1/providers",
    expect.objectContaining({ headers: expect.any(Headers) }),
  );
  const headers = fetchMock.mock.calls[0][1].headers as Headers;
  expect(headers.get("X-VeriLex-Token")).toBe("launch-token");
});

function keyedProbeDraft(key = "transient-key") {
  return {
    name: "Firm Gateway",
    kind: "openai_compatible",
    base_url: "https://models.example.test/v1",
    default_model: "legal-model",
    auth_mode: "api_key",
    api_key: key,
  } as const;
}

test("tests an unsaved provider without writing it to the collection route", async () => {
  const draft = keyedProbeDraft();
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ models: ["legal-model", "review-model"], selected_model: "legal-model" }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(testProviderConnection(draft)).resolves.toEqual({
    models: ["legal-model", "review-model"],
    selected_model: "legal-model",
  });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/providers/test",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify(draft),
      headers: expect.any(Headers),
    }),
  );
  const headers = fetchMock.mock.calls[0][1].headers as Headers;
  expect(headers.get("Content-Type")).toBe("application/json");
});

test("creates a provider only through the explicit save operation", async () => {
  const draft = {
    name: "Local Ollama",
    kind: "ollama",
    base_url: "http://127.0.0.1:11434",
    default_model: "qwen2.5:7b",
    auth_mode: "none",
  } as const;
  const saved = {
    id: "provider-1",
    ...draft,
    has_secret: false,
    enabled: true,
    created_at: "2026-08-09T12:00:00Z",
    updated_at: "2026-08-09T12:00:00Z",
  };
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(saved), { status: 201 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(createProvider(draft)).resolves.toEqual(saved);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/providers",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify(draft),
    }),
  );
});

test("retests a saved provider by id without resubmitting a key", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(
      JSON.stringify({ models: ["legal-model"], selected_model: "legal-model" }),
      { status: 200 },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(testSavedProvider("provider-1")).resolves.toEqual({
    models: ["legal-model"],
    selected_model: "legal-model",
  });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/providers/provider-1/test",
    expect.objectContaining({ method: "POST" }),
  );
  expect(fetchMock.mock.calls[0][1].body).toBeUndefined();
});

test("discovers models for a saved provider through the read route", async () => {
  const result = {
    models: ["legal-model", "review-model"],
    selected_model: "legal-model",
  };
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(result), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(discoverProviderModels("provider-1")).resolves.toEqual(result);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/providers/provider-1/models",
    expect.not.objectContaining({ method: "POST" }),
  );
});

test("deletes a saved provider by encoded id and accepts an empty 204 body", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(null, { status: 204 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(deleteProvider("p id")).resolves.toBeUndefined();
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/providers/p%20id",
    expect.objectContaining({ method: "DELETE" }),
  );
});

test("manages per-model context windows with model ids kept in JSON bodies", async () => {
  const record = {
    provider_id: "provider-1",
    model: "openai/gpt-4",
    context_window_tokens: 64_000,
    context_window_source: "user_declared",
    automatic_tokens: 128_000,
    automatic_source: "provider",
    manual_tokens: 64_000,
    is_available: true,
    updated_at: "2026-08-24T00:00:00Z",
  } as const;
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([record]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify([record]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(record), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(record), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(listModelContextWindows("provider 1")).resolves.toEqual([record]);
  await expect(refreshModelContextWindows("provider 1")).resolves.toEqual([record]);
  await expect(
    setModelContextWindowOverride("provider 1", "openai/gpt-4", 64_000),
  ).resolves.toEqual(record);
  await expect(
    resetModelContextWindowOverride("provider 1", "openai/gpt-4"),
  ).resolves.toEqual(record);

  expect(fetchMock.mock.calls[0][0]).toBe(
    "http://127.0.0.1:8766/v1/providers/provider%201/model-context-windows",
  );
  expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST" }));
  expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({
    method: "PUT",
    body: JSON.stringify({
      model: "openai/gpt-4",
      context_window_tokens: 64_000,
    }),
  }));
  expect(fetchMock.mock.calls[2][0]).not.toContain("openai/gpt-4");
  expect(fetchMock.mock.calls[3][1]).toEqual(expect.objectContaining({
    method: "POST",
    body: JSON.stringify({ model: "openai/gpt-4" }),
  }));
});

const threadRecord = {
  id: "thread-1",
  title: "Case A",
  provider_id: null,
  model: "model-1",
  workspace_id: null,
  status: "idle",
  created_at: "2026-08-18T00:00:00Z",
  updated_at: "2026-08-18T00:00:00Z",
  archived_at: null,
  deleted_at: null,
  is_draft: false,
};

test("creates and discards a hidden draft thread", async () => {
  const draft = { ...threadRecord, is_draft: true };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(draft), { status: 201 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);

  const request = {
    title: "New task",
    provider_id: "provider-1",
    model: "model-1",
    workspace_id: null,
    is_draft: true,
  } as const;
  await expect(createThread(request)).resolves.toEqual(draft);
  await expect(discardDraftThread("thread 1")).resolves.toBeUndefined();

  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "http://127.0.0.1:8766/v1/threads",
    expect.objectContaining({ method: "POST", body: JSON.stringify(request) }),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8766/v1/threads/thread%201/draft",
    expect.objectContaining({ method: "DELETE" }),
  );
});

test("updates all thread context fields through one PUT", async () => {
  const updated = {
    ...threadRecord,
    provider_id: "provider-1",
    model: "model-2",
    workspace_id: "workspace-1",
    reasoning_effort: "high",
  };
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(updated), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  const request = {
    provider_id: "provider-1",
    model: "model-2",
    workspace_id: "workspace-1",
  };

  await expect(updateThreadContext("thread 1", request)).resolves.toEqual(updated);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/threads/thread%201/context",
    expect.objectContaining({ method: "PUT", body: JSON.stringify(request) }),
  );
});

test("reads a thread context usage snapshot without a provider call", async () => {
  const usage = {
    used_tokens: 7200,
    context_window_tokens: 100000,
    effective_window_tokens: 90000,
    used_percent: 8,
    estimated: true,
    warning: "normal",
    breakdown: {
      messages: 4000,
      system_and_tools: 2000,
      skills: 700,
      attachments: 500,
      other: 0,
    },
    updated_at: "2026-08-24T00:00:00Z",
  } as const;
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(usage), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(getThreadContextUsage("thread 1")).resolves.toEqual(usage);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/threads/thread%201/context-usage",
    expect.any(Object),
  );
});

test("lists and creates workspaces with explicit read-write permission", async () => {
  const workspace = {
    id: "workspace-1",
    root_path: "/Cases/A",
    display_name: "A",
    allow_read: true,
    allow_write: true,
    created_at: "2026-08-19T00:00:00Z",
    last_used_at: "2026-08-19T00:00:00Z",
  };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([workspace]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(workspace), { status: 201 }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(listWorkspaces()).resolves.toEqual([workspace]);
  const request = {
    root_path: "/Cases/A",
    display_name: "A",
    allow_read: true,
    allow_write: true,
  };
  await expect(createWorkspace(request)).resolves.toEqual(workspace);
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8766/v1/workspaces",
    expect.objectContaining({ method: "POST", body: JSON.stringify(request) }),
  );
});

test("reads and declares exact model vision capability", async () => {
  const capability = {
    provider_id: "provider-1",
    model: "Exact-Vision",
    supports_vision: true,
    supports_native_pdf: false,
    source: "user_declared",
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
    context_window_tokens: null,
    supported_reasoning_efforts: null,
    context_window_source: "unknown",
    reasoning_efforts_source: "unknown",
  } as const;
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([capability]), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(capability), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(listModelCapabilities("provider 1")).resolves.toEqual([capability]);
  await expect(
    putModelCapability("provider 1", "Exact-Vision", true),
  ).resolves.toEqual(capability);
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8766/v1/providers/provider%201/model-capabilities",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ model: "Exact-Vision", supports_vision: true }),
    }),
  );
});

test("uploads raw file bytes and manages the returned upload handle", async () => {
  const upload = {
    id: "upl-1",
    thread_id: "thread 1",
    display_name: "证据 A.txt",
    media_type: "text/plain",
    size_bytes: 4,
    sha256: "a".repeat(64),
    created_at: "2026-08-19T00:00:00Z",
    expires_at: "2026-08-20T00:00:00Z",
  };
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify(upload), { status: 201 }))
    .mockResolvedValueOnce(new Response(JSON.stringify([upload]), { status: 200 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  const file = new File(["memo"], "证据 A.txt", { type: "text/plain" });

  await expect(uploadAttachment("thread 1", file)).resolves.toEqual(upload);
  await expect(listAttachmentUploads("thread 1")).resolves.toEqual([upload]);
  await expect(deleteAttachmentUpload("thread 1", "upl 1")).resolves.toBeUndefined();

  const [uploadUrl, uploadInit] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(uploadUrl).toBe(
    "http://127.0.0.1:8766/v1/threads/thread%201/attachment-uploads?filename=%E8%AF%81%E6%8D%AE%20A.txt",
  );
  expect(uploadInit.method).toBe("POST");
  expect(uploadInit.body).toBe(file);
  expect((uploadInit.headers as Headers).get("Content-Type")).toBe("text/plain");
  expect(fetchMock).toHaveBeenNthCalledWith(
    3,
    "http://127.0.0.1:8766/v1/threads/thread%201/attachment-uploads/upl%201",
    expect.objectContaining({ method: "DELETE" }),
  );
});

test("lists threads per scope with the scope query parameter", async () => {
  const fetchMock = vi.fn().mockImplementation(
    async () =>
      new Response(JSON.stringify([threadRecord]), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(listThreads()).resolves.toEqual([threadRecord]);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8766/v1/threads",
    expect.anything(),
  );

  await expect(listThreads("archived")).resolves.toEqual([threadRecord]);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8766/v1/threads?scope=archived",
    expect.anything(),
  );

  await expect(listThreads("trash")).resolves.toEqual([threadRecord]);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8766/v1/threads?scope=trash",
    expect.anything(),
  );
});

test("fetches a single thread detail", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(threadRecord), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(getThread("thread 1")).resolves.toEqual(threadRecord);
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/threads/thread%201",
    expect.anything(),
  );
});

test("renames a thread through PATCH with a json body", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(threadRecord), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(renameThread("thread-1", "Renamed")).resolves.toEqual(
    threadRecord,
  );
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/threads/thread-1",
    expect.objectContaining({
      method: "PATCH",
      body: JSON.stringify({ title: "Renamed" }),
    }),
  );
});

test.each([
  ["archive", "POST", archiveThread],
  ["unarchive", "POST", unarchiveThread],
  ["trash", "POST", trashThread],
  ["restore", "POST", restoreThread],
  ["delete", "DELETE", deleteThread],
] as const)("thread lifecycle action %s issues %s", async (_label, method, action) => {
  const fetchMock = vi.fn().mockResolvedValue(
    method === "DELETE"
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify(threadRecord), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await action("thread 1");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url.startsWith("http://127.0.0.1:8766/v1/threads/thread%201")).toBe(
    true,
  );
  expect(init.method).toBe(method);
});

test("lists turns, thread events, and attachments for a thread", async () => {
  const turnRecord = {
    id: "turn-1",
    thread_id: "thread 1",
    status: "completed",
    error_code: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-08-18T00:00:00Z",
  };
  const eventRecord = {
    id: "event-1",
    thread_id: "thread 1",
    turn_id: "turn-1",
    sequence: 1,
    type: "turn_started",
    payload: {},
    created_at: "2026-08-18T00:00:00Z",
  };
  const attachmentRecord = {
    id: "att-1",
    thread_id: "thread 1",
    turn_id: "turn-1",
    display_name: "contract.png",
    media_type: "image/png",
    size_bytes: 12,
    sha256: "ab",
    created_at: "2026-08-18T00:00:00Z",
  };
  let index = 0;
  const fetchMock = vi.fn().mockImplementation(
    async () =>
      new Response(
        JSON.stringify([[turnRecord], [eventRecord], [attachmentRecord]][index++ % 3]),
        { status: 200 },
      ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(listTurns("thread 1")).resolves.toEqual([turnRecord]);
  await expect(listThreadEvents("thread 1")).resolves.toEqual([eventRecord]);
  await expect(listThreadAttachments("thread 1")).resolves.toEqual([attachmentRecord]);

  expect(fetchMock).toHaveBeenNthCalledWith(
    1,
    "http://127.0.0.1:8766/v1/threads/thread%201/turns",
    expect.anything(),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    2,
    "http://127.0.0.1:8766/v1/threads/thread%201/events",
    expect.anything(),
  );
  expect(fetchMock).toHaveBeenNthCalledWith(
    3,
    "http://127.0.0.1:8766/v1/threads/thread%201/attachments",
    expect.anything(),
  );
});

const backgroundTaskRecord = {
  id: "bt-1",
  toolCallId: "tc-1",
  command: "python3 hold.py",
  description: "hold",
  status: "running" as const,
  exitCode: null,
  omittedBytes: 0,
  startedAt: "2026-08-25T00:00:00Z",
  finishedAt: null,
  createdAt: "2026-08-25T00:00:00Z",
};

test("lists background tasks for a thread", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify([backgroundTaskRecord]), { status: 200 }),
    ),
  );

  await expect(listBackgroundTasks("thread 1")).resolves.toEqual([
    backgroundTaskRecord,
  ]);
  expect(fetchMockUrl(0)).toBe(
    "http://127.0.0.1:8766/v1/threads/thread%201/background-tasks",
  );
});

test("fetches one background task detail", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({ ...backgroundTaskRecord, outputText: "tick" }),
        { status: 200 },
      ),
    ),
  );

  await expect(getBackgroundTask("t1", "bt 1")).resolves.toMatchObject({
    id: "bt-1",
    outputText: "tick",
  });
  expect(fetchMockUrl(0)).toBe(
    "http://127.0.0.1:8766/v1/threads/t1/background-tasks/bt%201",
  );
});

test("reads background task output with a cursor", async () => {
  const fetchMock = vi.fn().mockImplementation(
    async () =>
      new Response(
        JSON.stringify({
          cursor: 12,
          text: "hello",
          omittedBytes: 0,
          status: "running",
        }),
        { status: 200 },
      ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(
    getBackgroundTaskOutput("t1", "bt-1", 5, undefined),
  ).resolves.toEqual({
    cursor: 12,
    text: "hello",
    omittedBytes: 0,
    status: "running",
  });
  expect(fetchMockUrl(0)).toBe(
    "http://127.0.0.1:8766/v1/threads/t1/background-tasks/bt-1/output?after=5",
  );

  await getBackgroundTaskOutput("t1", "bt-1", 0, undefined);
  expect(fetchMockUrl(1)).toBe(
    "http://127.0.0.1:8766/v1/threads/t1/background-tasks/bt-1/output",
  );
});

test("stops a background task via POST", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ taskId: "bt-1", status: "stopped" }), {
      status: 200,
    }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(stopBackgroundTask("t1", "bt-1")).resolves.toEqual({
    taskId: "bt-1",
    status: "stopped",
  });
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8766/v1/threads/t1/background-tasks/bt-1/stop",
    expect.objectContaining({ method: "POST" }),
  );
});

test("reads and updates preferences", async () => {
  const fetchMock = vi.fn().mockImplementation(
    async () =>
      new Response(
        JSON.stringify({ background_task_max_minutes: 45 }),
        { status: 200 },
      ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(getPreferences()).resolves.toEqual({
    background_task_max_minutes: 45,
  });
  expect(fetchMockUrl(0)).toBe("http://127.0.0.1:8766/v1/preferences");

  await putPreference("background_task_max_minutes", 45);
  expect(fetchMock).toHaveBeenLastCalledWith(
    "http://127.0.0.1:8766/v1/preferences/background_task_max_minutes",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ value: 45 }),
    }),
  );
});

function fetchMockUrl(index: number): string {
  const mock = vi.mocked(globalThis.fetch);
  return mock.mock.calls[index][0] as string;
}
