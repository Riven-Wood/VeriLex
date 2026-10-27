import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { StrictMode, type ReactNode } from "react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { App } from "../src/app/App";
import { McpThreadPicker } from "../src/components/McpThreadPicker";
import { YuandianSetup } from "../src/components/YuandianSetup";
import {
  useThreadMcp,
  type DraftMcpSelectionOutcome,
  type ThreadMcpApi,
} from "../src/hooks/useThreadMcp";
import type {
  AtomicThreadSelectionResult,
  DraftThreadInput,
  HealthResponse,
  McpServerRecord,
  McpThreadSnapshot,
  ProviderRecord,
  ThreadRecord,
} from "../src/runtime/contracts";
import type { ProviderApi } from "../src/runtime/providerClient";
import type { ThreadApi } from "../src/runtime/threadClient";
import type { UpdaterClient } from "../src/updater/contracts";
import { YUANDIAN_SERVICES } from "../src/mcp/yuandian";
import { openYuandianConsole } from "../src/platform/yuandianConsole";

const nativeDrop = vi.hoisted(() => ({ listen: vi.fn(), unlisten: vi.fn() }));
const nativeBridge = vi.hoisted(() => ({ invoke: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ ...nativeBridge, isTauri: () => false }));
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({ onDragDropEvent: nativeDrop.listen }),
}));

beforeEach(() => {
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.mocked(updaterClient.getCurrentVersion).mockResolvedValue("0.3.0");
  vi.mocked(updaterClient.check).mockResolvedValue(null);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  nativeDrop.listen.mockReset();
  nativeDrop.unlisten.mockReset();
  nativeBridge.invoke.mockReset();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const draftThread: DraftThreadInput = {
  title: "新会话",
  providerId: "provider-1",
  model: "model-a",
  workspaceId: null,
  reasoningEffort: "auto",
};

function server(id: string, displayName = id): McpServerRecord {
  return {
    id,
    displayName,
    transport: "http",
    command: null,
    args: [],
    cwd: null,
    url: "https://example.test/mcp",
    env: {},
    headers: {},
    secretEnvKeys: [],
    secretHeaderKeys: [],
    connectTimeoutMs: 10_000,
    requestTimeoutMs: 30_000,
    configRevision: 1,
    secretRevision: 0,
    configReviewState: "reviewed",
    riskFindings: [],
    connectionStatus: "idle",
    errorCode: null,
    retryable: false,
    lastTestStatus: "passed",
    lastTestedAt: "2026-08-29T00:00:00Z",
    selectedThreadCount: 0,
    createdAt: "2026-08-29T00:00:00Z",
    updatedAt: "2026-08-29T00:00:00Z",
  };
}

function snapshot(threadId: string, selected: string[] = []): McpThreadSnapshot {
  return {
    threadId,
    servers: ["server-1", "server-2", "server-removed"].map((serverId) => ({
      serverId,
      displayName: serverId,
      selected: selected.includes(serverId),
      connectionStatus: selected.includes(serverId) ? "ready" : "idle",
      toolCount: selected.includes(serverId) ? 3 : 0,
      errorCode: null,
      retryable: false,
    })),
  };
}

function atomicResult(serverId = "server-1"): AtomicThreadSelectionResult {
  return {
    thread: {
      id: "draft-1",
      title: "新会话",
      providerId: "provider-1",
      model: "model-a",
      workspaceId: null,
      status: "idle",
      createdAt: "2026-08-29T00:00:00Z",
      updatedAt: "2026-08-29T00:00:00Z",
      isDraft: true,
      reasoningEffort: "auto",
    },
    server: server(serverId),
    selection: {
      serverId,
      selected: true,
      connectionStatus: "connecting",
      toolCount: 0,
      errorCode: null,
      retryable: true,
      selectedAt: "2026-08-29T00:00:00Z",
    },
    createdThread: true,
    createdServer: false,
  };
}

function api(overrides: Partial<ThreadMcpApi> = {}): ThreadMcpApi {
  return {
    listServers: vi.fn().mockResolvedValue([server("server-1"), server("server-2")]),
    getSnapshot: vi.fn().mockImplementation((threadId: string) =>
      Promise.resolve(snapshot(threadId))),
    selectServer: vi.fn().mockImplementation((_threadId: string, serverId: string) =>
      Promise.resolve({
        serverId,
        selected: true,
        connectionStatus: "ready" as const,
        toolCount: 1,
        errorCode: null,
        retryable: false,
      })),
    deselectServer: vi.fn().mockImplementation((_threadId: string, serverId: string) =>
      Promise.resolve({
        serverId,
        selected: false,
        connectionStatus: "idle" as const,
        errorCode: null,
        retryable: false,
      })),
    atomicSelection: vi.fn().mockResolvedValue(atomicResult()),
    activateView: vi.fn().mockImplementation((viewId: string, threadId: string) =>
      Promise.resolve({
        viewId,
        active: true as const,
        threadId,
        connectionStatus: "ready" as const,
        errorCode: null,
        retryable: false,
      })),
    deactivateView: vi.fn().mockImplementation((viewId: string) =>
      Promise.resolve({ viewId, active: false as const })),
    ...overrides,
  };
}

const oneModelHealth: HealthResponse = {
  ok: true,
  service: "verilex-runtime",
  version: "0.3.0",
  storage: "local",
  cloud_backend: false,
  configured: { models: 1, mcp_servers: 1, skills: 0, folders: 0 },
};

const provider: ProviderRecord = {
  id: "provider-1",
  name: "测试模型",
  kind: "openai_compatible",
  base_url: "https://models.test/v1",
  default_model: "model-a",
  auth_mode: "none",
  has_secret: false,
  enabled: true,
  created_at: "2026-08-29T00:00:00Z",
  updated_at: "2026-08-29T00:00:00Z",
};

function threadRecord(id: string): ThreadRecord {
  return {
    id,
    title: "新会话",
    provider_id: "provider-1",
    model: "model-a",
    workspace_id: null,
    status: "idle",
    created_at: "2026-08-29T00:00:00Z",
    updated_at: "2026-08-29T00:00:00Z",
    archived_at: null,
    deleted_at: null,
    is_draft: true,
    reasoning_effort: "auto",
  };
}

function providerApi(): ProviderApi {
  return {
    listProviders: vi.fn().mockResolvedValue([provider]),
    testProviderConnection: vi.fn(),
    createProvider: vi.fn(),
    testSavedProvider: vi.fn(),
    discoverProviderModels: vi.fn(),
    listModelContextWindows: vi.fn().mockResolvedValue([]),
    refreshModelContextWindows: vi.fn().mockResolvedValue([]),
    setModelContextWindowOverride: vi.fn(),
    resetModelContextWindowOverride: vi.fn(),
    deleteProvider: vi.fn(),
  };
}

function threadApi(overrides: Partial<ThreadApi> = {}): ThreadApi {
  return {
    createThread: vi.fn().mockResolvedValue(threadRecord("ordinary-draft")),
    getThread: vi.fn(),
    listThreads: vi.fn().mockResolvedValue([]),
    updateThreadContext: vi.fn(),
    discardDraftThread: vi.fn().mockResolvedValue(undefined),
    renameThread: vi.fn(),
    archiveThread: vi.fn(),
    unarchiveThread: vi.fn(),
    trashThread: vi.fn(),
    restoreThread: vi.fn(),
    deleteThread: vi.fn(),
    ...overrides,
  };
}

const updaterClient: UpdaterClient = {
  supported: true,
  getCurrentVersion: vi.fn().mockResolvedValue("0.3.0"),
  check: vi.fn().mockResolvedValue(null),
  relaunch: vi.fn(),
};

function stubWorkspaceFetch() {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url.endsWith("/v1/workspaces")) return Promise.resolve(new Response("[]", { status: 200 }));
    if (url.includes("/attachment-uploads") && init?.method === "POST") {
      const file = init.body as File;
      return Promise.resolve(new Response(JSON.stringify({
        id: "upload-1",
        thread_id: "draft-1",
        display_name: file.name,
        media_type: file.type,
        size_bytes: file.size,
        sha256: "a".repeat(64),
        created_at: "2026-08-29T00:00:00Z",
        expires_at: "2026-08-30T00:00:00Z",
      }), { status: 201 }));
    }
    return Promise.resolve(new Response("[]", { status: 200 }));
  }));
}

async function openPluginPicker() {
  await screen.findByRole("button", { name: "选择模型，当前 model-a" });
  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "插件" }));
  return screen.findByRole("dialog", { name: "当前对话的插件" });
}

test("blank selection uses one atomic request without activating a view first", async () => {
  const mcpApi = api();
  const { result } = renderHook(() => useThreadMcp({
    threadId: null,
    draftThread,
    api: mcpApi,
  }));

  await waitFor(() => expect(result.current.snapshot?.servers).toHaveLength(2));
  await act(async () => {
    await result.current.select("server-1");
  });

  expect(mcpApi.atomicSelection).toHaveBeenCalledTimes(1);
  expect(mcpApi.atomicSelection).toHaveBeenCalledWith({
    existingThreadId: null,
    draftThread,
    existingServerId: "server-1",
    createServer: null,
  });
  expect(mcpApi.activateView).not.toHaveBeenCalled();
  expect(mcpApi.selectServer).not.toHaveBeenCalled();
});

test("new tasks list saved servers with no inherited selections", async () => {
  const mcpApi = api({
    getSnapshot: vi.fn().mockResolvedValue(snapshot("old-thread", ["server-1"])),
  });
  const { result } = renderHook(() => useThreadMcp({
    threadId: null,
    draftThread,
    api: mcpApi,
  }));

  await waitFor(() => expect(result.current.snapshot?.servers).toHaveLength(2));
  expect(result.current.snapshot?.servers.every((item) => !item.selected)).toBe(true);
  expect(mcpApi.getSnapshot).not.toHaveBeenCalled();
});

test("thread changes clear the old snapshot immediately and ignore its late response", async () => {
  const oldResponse = deferred<McpThreadSnapshot>();
  const mcpApi = api({
    getSnapshot: vi.fn().mockImplementation((threadId: string) =>
      threadId === "thread-a"
        ? oldResponse.promise
        : Promise.resolve(snapshot("thread-b", ["server-2"]))),
  });
  const { result, rerender } = renderHook(
    ({ threadId }) => useThreadMcp({ threadId, draftThread: null, api: mcpApi }),
    { initialProps: { threadId: "thread-a" } },
  );

  rerender({ threadId: "thread-b" });
  expect(result.current.snapshot).toBeNull();
  await waitFor(() => expect(result.current.snapshot?.threadId).toBe("thread-b"));

  await act(async () => {
    oldResponse.resolve(snapshot("thread-a", ["server-1"]));
    await oldResponse.promise;
  });
  expect(result.current.snapshot?.threadId).toBe("thread-b");
  expect(result.current.snapshot?.servers.find((item) => item.serverId === "server-2")?.selected)
    .toBe(true);
});

test("thread changes expose fresh loading and error state in the same render", async () => {
  const mcpApi = api({
    getSnapshot: vi.fn().mockImplementation((threadId: string) =>
      threadId === "thread-a"
        ? Promise.reject(new Error("https://example.test/?token=old-thread-secret"))
        : Promise.resolve(snapshot("thread-b"))),
  });
  const { result, rerender } = renderHook(
    ({ threadId }) => useThreadMcp({ threadId, draftThread: null, api: mcpApi }),
    { initialProps: { threadId: "thread-a" } },
  );
  await waitFor(() => expect(result.current.error).toBe(
    "连接没有成功，请检查配置后重试。",
  ));

  rerender({ threadId: "thread-b" });
  expect(result.current.snapshot).toBeNull();
  expect(result.current.loading).toBe(true);
  expect(result.current.error).toBe("");
  await waitFor(() => expect(result.current.snapshot?.threadId).toBe("thread-b"));
});

test("global server list is intersected with the thread snapshot", async () => {
  const mcpApi = api({
    listServers: vi.fn().mockResolvedValue([server("server-1", "证据库")]),
    getSnapshot: vi.fn().mockResolvedValue(snapshot("thread-a", ["server-1", "server-removed"])),
  });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));

  await waitFor(() => expect(result.current.snapshot?.servers).toHaveLength(1));
  expect(result.current.snapshot?.servers[0]).toMatchObject({
    serverId: "server-1",
    displayName: "证据库",
    selected: true,
  });
});

test("an unselected server never inherits global connection errors from another thread", async () => {
  const globallyFailed = {
    ...server("server-1", "证据库"),
    connectionStatus: "error" as const,
    errorCode: "secret-bearing-error",
    retryable: true,
  };
  const mcpApi = api({
    listServers: vi.fn().mockResolvedValue([globallyFailed]),
    getSnapshot: vi.fn().mockResolvedValue(snapshot("thread-a")),
  });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));

  await waitFor(() => expect(result.current.snapshot?.servers).toHaveLength(1));
  expect(result.current.snapshot?.servers[0]).toMatchObject({
    selected: false,
    connectionStatus: "idle",
    errorCode: null,
    retryable: false,
  });
});

test("existing-thread atomic server creation reacquires the active view without sending viewId", async () => {
  let created = false;
  const createdServer = server("server-new", "新插件");
  const mcpApi = api({
    listServers: vi.fn().mockImplementation(() => Promise.resolve(
      created
        ? [server("server-1"), server("server-2"), createdServer]
        : [server("server-1"), server("server-2")],
    )),
    getSnapshot: vi.fn().mockImplementation((threadId: string) => Promise.resolve({
      threadId,
      servers: created ? [{
        serverId: createdServer.id,
        displayName: createdServer.displayName,
        selected: true,
        connectionStatus: "ready" as const,
        errorCode: null,
        retryable: false,
      }] : [],
    })),
    atomicSelection: vi.fn().mockImplementation(() => {
      created = true;
      const result = atomicResult(createdServer.id);
      return Promise.resolve({ ...result, server: createdServer });
    }),
  });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());
  await waitFor(() => expect(mcpApi.activateView).toHaveBeenCalled());
  const activationsBeforeCreate = vi.mocked(mcpApi.activateView).mock.calls.length;
  await act(async () => {
    await result.current.select({
      draft: {
        displayName: "新插件",
        transport: "http",
        command: null,
        args: [],
        cwd: null,
        url: "https://example.test/mcp",
        env: {},
        headers: {},
        secretEnvKeys: [],
        secretHeaderKeys: [],
        connectTimeoutMs: 10_000,
        requestTimeoutMs: 30_000,
      },
      secretPatch: { env: {}, headers: {} },
      acceptedRiskCodes: [],
      testReceipt: "receipt-1",
      toolDecisions: [],
    });
  });
  expect(mcpApi.atomicSelection).toHaveBeenCalledWith(expect.not.objectContaining({
    viewId: expect.anything(),
  }));
  expect(vi.mocked(mcpApi.activateView).mock.calls.length).toBeGreaterThan(
    activationsBeforeCreate,
  );
  expect(mcpApi.activateView).toHaveBeenLastCalledWith(result.current.viewId, "thread-a");
  expect(result.current.snapshot?.servers.find((item) => item.serverId === "server-new"))
    .toMatchObject({ selected: true, connectionStatus: "ready" });
});

test("a new blank task does not inherit an older blank task write, pending state, or error", async () => {
  const first = deferred<AtomicThreadSelectionResult>();
  const second = deferred<AtomicThreadSelectionResult>();
  const atomicSelection = vi.fn()
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(second.promise);
  const mcpApi = api({ atomicSelection });
  const { result, rerender } = renderHook(
    ({ generation }) => useThreadMcp({
      threadId: null,
      draftThread,
      draftGeneration: generation,
      api: mcpApi,
    }),
    { initialProps: { generation: 0 } },
  );
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());

  let firstWrite!: Promise<void>;
  act(() => { firstWrite = result.current.select("server-1"); });
  expect(result.current.pendingServerIds.has("server-1")).toBe(true);

  rerender({ generation: 1 });
  expect(result.current.pendingServerIds.has("server-1")).toBe(false);
  expect(result.current.error).toBe("");
  let secondWrite!: Promise<void>;
  act(() => { secondWrite = result.current.select("server-1"); });
  expect(atomicSelection).toHaveBeenCalledTimes(2);

  await act(async () => {
    first.reject(new Error("https://example.test/?token=old-task-secret"));
    await expect(firstWrite).rejects.toThrow();
  });
  expect(result.current.error).toBe("");
  expect(result.current.pendingServerIds.has("server-1")).toBe(true);

  await act(async () => {
    second.resolve(atomicResult());
    await secondWrite;
  });
  expect(result.current.error).toBe("");
  expect(result.current.pendingServerIds.has("server-1")).toBe(false);
});

test("server creation that joins a materializing draft reacquires the already active view", async () => {
  let created = false;
  const createdServer = server("server-new", "新插件");
  const selection = deferred<DraftMcpSelectionOutcome | null>();
  const selectDraftMcp = vi.fn().mockReturnValue(selection.promise);
  const mcpApi = api({
    listServers: vi.fn().mockImplementation(() => Promise.resolve(
      created
        ? [server("server-1"), server("server-2"), createdServer]
        : [server("server-1"), server("server-2")],
    )),
    getSnapshot: vi.fn().mockImplementation((threadId: string) => Promise.resolve({
      threadId,
      servers: created ? [{
        serverId: createdServer.id,
        displayName: createdServer.displayName,
        selected: true,
        connectionStatus: "ready" as const,
        errorCode: null,
        retryable: false,
      }] : [],
    })),
  });
  const { result, rerender } = renderHook(
    ({ threadId }) => useThreadMcp({
      threadId,
      draftThread,
      api: mcpApi,
      selectDraftMcp,
    }),
    { initialProps: { threadId: null as string | null } },
  );
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());

  let write!: Promise<void>;
  act(() => {
    write = result.current.select({
      draft: {
        displayName: "新插件",
        transport: "http",
        command: null,
        args: [],
        cwd: null,
        url: "https://example.test/mcp",
        env: {},
        headers: {},
        secretEnvKeys: [],
        secretHeaderKeys: [],
        connectTimeoutMs: 10_000,
        requestTimeoutMs: 30_000,
      },
      secretPatch: { env: {}, headers: {} },
      acceptedRiskCodes: [],
      testReceipt: "receipt-1",
      toolDecisions: [],
    });
  });
  await waitFor(() => expect(selectDraftMcp).toHaveBeenCalledTimes(1));

  rerender({ threadId: "draft-1" });
  await waitFor(() => expect(result.current.snapshot?.threadId).toBe("draft-1"));
  await waitFor(() => expect(mcpApi.activateView).toHaveBeenCalled());
  const activationsBeforeCreate = vi.mocked(mcpApi.activateView).mock.calls.length;

  await act(async () => {
    created = true;
    const atomic = atomicResult(createdServer.id);
    selection.resolve({
      threadId: "draft-1",
      result: {
        ...atomic,
        server: createdServer,
        createdThread: false,
        createdServer: true,
      },
    });
    await write;
  });

  expect(vi.mocked(mcpApi.activateView).mock.calls.length).toBeGreaterThan(
    activationsBeforeCreate,
  );
  expect(result.current.snapshot?.servers.find((item) => item.serverId === "server-new"))
    .toMatchObject({ selected: true, connectionStatus: "ready" });
});

test("StrictMode remount restores the active view lifecycle", async () => {
  const mcpApi = api();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <StrictMode>{children}</StrictMode>
  );
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }), { wrapper });

  await waitFor(() => expect(mcpApi.activateView).toHaveBeenCalled());
  fireEvent.blur(window);
  await waitFor(() => expect(mcpApi.deactivateView).toHaveBeenCalledWith(result.current.viewId));
  fireEvent.focus(window);
  await waitFor(() => expect(mcpApi.activateView).toHaveBeenLastCalledWith(
    result.current.viewId,
    "thread-a",
  ));
});

test("view reconciliation serializes A to B and leaves the backend at B", async () => {
  const activateA = deferred<Awaited<ReturnType<ThreadMcpApi["activateView"]>>>();
  const activateView = vi.fn().mockImplementation((viewId: string, threadId: string) =>
    threadId === "thread-a"
      ? activateA.promise
      : Promise.resolve({
          viewId,
          active: true as const,
          threadId,
          connectionStatus: "ready" as const,
          errorCode: null,
          retryable: false,
        }));
  const mcpApi = api({ activateView });
  const { result, rerender, unmount } = renderHook(
    ({ threadId }) => useThreadMcp({ threadId, draftThread: null, api: mcpApi }),
    { initialProps: { threadId: "thread-a" } },
  );
  await waitFor(() => expect(activateView).toHaveBeenCalledTimes(1));
  const viewId = result.current.viewId;

  rerender({ threadId: "thread-b" });
  expect(result.current.viewId).toBe(viewId);
  expect(activateView).toHaveBeenCalledTimes(1);

  await act(async () => {
    activateA.resolve({
      viewId,
      active: true,
      threadId: "thread-a",
      connectionStatus: "ready",
      errorCode: null,
      retryable: false,
    });
    await activateA.promise;
  });
  await waitFor(() => expect(activateView).toHaveBeenLastCalledWith(viewId, "thread-b"));

  unmount();
  await waitFor(() => expect(mcpApi.deactivateView).toHaveBeenCalledWith(viewId));
});

test("blur releases the view and focus reactivates the same view id", async () => {
  const mcpApi = api();
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));
  await waitFor(() => expect(mcpApi.activateView).toHaveBeenCalledTimes(1));
  const viewId = result.current.viewId;

  fireEvent.blur(window);
  await waitFor(() => expect(mcpApi.deactivateView).toHaveBeenCalledWith(viewId));
  fireEvent.focus(window);
  await waitFor(() => expect(mcpApi.activateView).toHaveBeenCalledTimes(2));
  expect(mcpApi.activateView).toHaveBeenLastCalledWith(viewId, "thread-a");
});

test("double selection shares one server write and deselect only calls selection delete", async () => {
  const write = deferred<{
    serverId: string;
    selected: true;
    connectionStatus: "ready";
    toolCount: number;
    errorCode: null;
    retryable: false;
  }>();
  const selectServer = vi.fn().mockReturnValue(write.promise);
  const deselectServer = vi.fn().mockResolvedValue({
    serverId: "server-1",
    selected: false,
    connectionStatus: "idle" as const,
    errorCode: null,
    retryable: false,
  });
  const mcpApi = api({ selectServer, deselectServer });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());

  let first!: Promise<void>;
  let second!: Promise<void>;
  act(() => {
    first = result.current.select("server-1");
    second = result.current.select("server-1");
  });
  expect(selectServer).toHaveBeenCalledTimes(1);
  await act(async () => {
    write.resolve({
      serverId: "server-1",
      selected: true,
      connectionStatus: "ready",
      toolCount: 4,
      errorCode: null,
      retryable: false,
    });
    await Promise.all([first, second]);
  });
  expect(result.current.snapshot?.servers[0]?.toolCount).toBe(4);

  await act(async () => {
    await result.current.deselect("server-1");
  });
  expect(deselectServer).toHaveBeenCalledTimes(1);
  expect(mcpApi.deactivateView).not.toHaveBeenCalled();
});

test("the same server can write independently after switching threads", async () => {
  const writeA = deferred<{
    serverId: string;
    selected: true;
    connectionStatus: "ready";
    errorCode: null;
    retryable: false;
  }>();
  const writeB = deferred<{
    serverId: string;
    selected: true;
    connectionStatus: "ready";
    errorCode: null;
    retryable: false;
  }>();
  const selectServer = vi.fn().mockImplementation((threadId: string) =>
    threadId === "thread-a" ? writeA.promise : writeB.promise);
  const mcpApi = api({ selectServer });
  const { result, rerender } = renderHook(
    ({ threadId }) => useThreadMcp({ threadId, draftThread: null, api: mcpApi }),
    { initialProps: { threadId: "thread-a" } },
  );
  await waitFor(() => expect(result.current.snapshot?.threadId).toBe("thread-a"));

  let pendingA!: Promise<void>;
  act(() => { pendingA = result.current.select("server-1"); });
  expect(result.current.pendingServerIds.has("server-1")).toBe(true);

  rerender({ threadId: "thread-b" });
  expect(result.current.pendingServerIds.has("server-1")).toBe(false);
  let pendingB!: Promise<void>;
  act(() => { pendingB = result.current.select("server-1"); });
  expect(selectServer).toHaveBeenCalledTimes(2);
  expect(result.current.pendingServerIds.has("server-1")).toBe(true);

  await act(async () => {
    writeA.resolve({
      serverId: "server-1",
      selected: true,
      connectionStatus: "ready",
      errorCode: null,
      retryable: false,
    });
    await pendingA;
  });
  expect(result.current.pendingServerIds.has("server-1")).toBe(true);

  await act(async () => {
    writeB.resolve({
      serverId: "server-1",
      selected: true,
      connectionStatus: "ready",
      errorCode: null,
      retryable: false,
    });
    await pendingB;
  });
  expect(result.current.pendingServerIds.has("server-1")).toBe(false);
});

test("a successful view activation refreshes idle selection state and focus refreshes again", async () => {
  let snapshotRead = 0;
  const getSnapshot = vi.fn().mockImplementation((threadId: string) => {
    snapshotRead += 1;
    const status = snapshotRead === 1 ? "idle" as const : "ready" as const;
    return Promise.resolve({
      threadId,
      servers: [{
        serverId: "server-1",
        displayName: "server-1",
        selected: true,
        connectionStatus: status,
        errorCode: null,
        retryable: false,
      }],
    });
  });
  const mcpApi = api({ getSnapshot });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));

  await waitFor(() => expect(
    result.current.snapshot?.servers[0]?.connectionStatus,
  ).toBe("ready"));
  expect(getSnapshot.mock.calls.length).toBeGreaterThanOrEqual(2);
  const readsBeforeFocus = getSnapshot.mock.calls.length;

  fireEvent.blur(window);
  await waitFor(() => expect(mcpApi.deactivateView).toHaveBeenCalled());
  fireEvent.focus(window);
  await waitFor(() => expect(getSnapshot.mock.calls.length).toBeGreaterThan(readsBeforeFocus));
});

test("a hidden or unfocused document does not activate its thread on mount", async () => {
  const visibility = Object.getOwnPropertyDescriptor(document, "visibilityState");
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => "hidden",
  });
  const mcpApi = api();
  try {
    const { result } = renderHook(() => useThreadMcp({
      threadId: "thread-a",
      draftThread: null,
      api: mcpApi,
    }));
    await waitFor(() => expect(result.current.snapshot).not.toBeNull());
    expect(mcpApi.activateView).not.toHaveBeenCalled();
  } finally {
    if (visibility) Object.defineProperty(document, "visibilityState", visibility);
  }
});

test("a visible but unfocused document does not activate its thread on mount", async () => {
  vi.mocked(document.hasFocus).mockReturnValue(false);
  const mcpApi = api();
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());
  expect(mcpApi.activateView).not.toHaveBeenCalled();
});

test("unknown runtime errors use fixed copy instead of reflecting secrets", async () => {
  const mcpApi = api({
    getSnapshot: vi.fn().mockRejectedValue(new Error("https://example.test/?token=case-secret")),
  });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));

  await waitFor(() => expect(result.current.error).toBe(
    "连接没有成功，请检查配置后重试。",
  ));
  expect(result.current.error).not.toContain("case-secret");
});

test("a successful same-thread select retry clears the earlier operation error", async () => {
  const selectServer = vi.fn()
    .mockRejectedValueOnce(new Error("https://example.test/?token=case-secret"))
    .mockResolvedValue({
      serverId: "server-1",
      selected: true,
      connectionStatus: "ready" as const,
      errorCode: null,
      retryable: false,
    });
  const mcpApi = api({ selectServer });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));
  await waitFor(() => expect(result.current.snapshot).not.toBeNull());

  await act(async () => {
    await expect(result.current.select("server-1")).rejects.toThrow();
  });
  expect(result.current.error).toBe("连接没有成功，请检查配置后重试。");

  await act(async () => {
    await result.current.select("server-1");
  });
  expect(result.current.error).toBe("");
  expect(result.current.snapshot?.servers[0]?.selected).toBe(true);
});

test("a successful same-thread deselect retry clears the earlier operation error", async () => {
  const deselectServer = vi.fn()
    .mockRejectedValueOnce(new Error("https://example.test/?token=case-secret"))
    .mockResolvedValue({
      serverId: "server-1",
      selected: false,
      connectionStatus: "idle" as const,
      errorCode: null,
      retryable: false,
    });
  const mcpApi = api({
    getSnapshot: vi.fn().mockResolvedValue(snapshot("thread-a", ["server-1"])),
    deselectServer,
  });
  const { result } = renderHook(() => useThreadMcp({
    threadId: "thread-a",
    draftThread: null,
    api: mcpApi,
  }));
  await waitFor(() => expect(result.current.snapshot?.servers[0]?.selected).toBe(true));

  await act(async () => {
    await expect(result.current.deselect("server-1")).rejects.toThrow();
  });
  expect(result.current.error).toBe("连接没有成功，请检查配置后重试。");

  await act(async () => {
    await result.current.deselect("server-1");
  });
  expect(result.current.error).toBe("");
  expect(result.current.snapshot?.servers[0]?.selected).toBe(false);
});

test("picker focuses search, supports arrow and Space selection, and closes with Escape", async () => {
  const onClose = vi.fn();
  const onSelect = vi.fn().mockResolvedValue(undefined);
  render(
    <McpThreadPicker
      snapshot={snapshot("thread-a")}
      loading={false}
      error=""
      pendingServerIds={new Set()}
      onSelect={onSelect}
      onDeselect={vi.fn()}
      onRetry={vi.fn()}
      onAddServer={vi.fn()}
      onOpenSettings={vi.fn()}
      onClose={onClose}
    />,
  );

  const dialog = screen.getByRole("dialog", { name: "当前对话的插件" });
  const search = screen.getByRole("searchbox", { name: "搜索插件" });
  await waitFor(() => expect(document.activeElement).toBe(search));

  fireEvent.keyDown(search, { key: "ArrowDown" });
  const firstServer = screen.getByRole("button", { name: /选择 server-1/ });
  expect(document.activeElement).toBe(firstServer);
  fireEvent.keyDown(firstServer, { key: " " });
  expect(onSelect).toHaveBeenCalledWith("server-1");

  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(onClose).toHaveBeenCalledTimes(1);
});

test("ready servers show the Runtime usable tool count", () => {
  render(
    <McpThreadPicker
      snapshot={snapshot("thread-a", ["server-1"])}
      loading={false}
      error=""
      pendingServerIds={new Set()}
      onSelect={vi.fn()}
      onDeselect={vi.fn()}
      onRetry={vi.fn()}
      onAddServer={vi.fn()}
      onOpenSettings={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByText("可用工具 3 个")).toBeTruthy();
});

test("canceling the inline server editor returns focus to plugin search", async () => {
  render(
    <McpThreadPicker
      snapshot={snapshot("thread-a")}
      loading={false}
      error=""
      pendingServerIds={new Set()}
      onSelect={vi.fn()}
      onDeselect={vi.fn()}
      onRetry={vi.fn()}
      onAddServer={vi.fn()}
      onOpenSettings={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  const search = screen.getByRole("searchbox", { name: "搜索插件" });
  await waitFor(() => expect(document.activeElement).toBe(search));

  fireEvent.click(screen.getByRole("button", { name: "添加其他 MCP" }));
  fireEvent.click(screen.getByRole("button", { name: "取消" }));

  await waitFor(() => expect(document.activeElement).toBe(
    screen.getByRole("searchbox", { name: "搜索插件" }),
  ));
});

test("picker offers Yuan Dian as one built-in plugin before any server is saved", async () => {
  render(
    <McpThreadPicker
      snapshot={{ threadId: "thread-a", servers: [] }}
      loading={false}
      error=""
      pendingServerIds={new Set()}
      onSelect={vi.fn()}
      onDeselect={vi.fn()}
      onRetry={vi.fn()}
      onAddServer={vi.fn()}
      onOpenSettings={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "连接 元典智库" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "连接 元典智库" }));
  expect(screen.getByLabelText("元典 API Key")).toBeTruthy();
  expect(screen.queryByText(/stream|Authorization|请求头|传输方式/u)).toBeNull();
});

test("Yuan Dian setup opens the built-in console and connects all official services from one key", async () => {
  const openConsole = vi.fn().mockResolvedValue(undefined);
  const probe = vi.fn().mockImplementation((input) => Promise.resolve({
    testReceipt: `receipt-${input.draft.displayName}`,
    tools: [{
      toolName: "search",
      description: "search",
      descriptionHash: "d".repeat(64),
      inputSchema: { type: "object" },
      schemaHash: "s".repeat(64),
    }],
    riskCodes: [],
  }));
  const onAddServer = vi.fn().mockResolvedValue(undefined);
  const onComplete = vi.fn();
  render(
    <YuandianSetup
      onAddServer={onAddServer}
      onCancel={vi.fn()}
      onComplete={onComplete}
      openConsole={openConsole}
      probe={probe}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "登录元典并获取 API Key" }));
  await waitFor(() => expect(openConsole).toHaveBeenCalledTimes(1));

  fireEvent.change(screen.getByLabelText("元典 API Key"), {
    target: { value: "  yuan-secret  " },
  });
  fireEvent.click(screen.getByRole("button", { name: "连接到当前对话" }));

  await waitFor(() => expect(onAddServer).toHaveBeenCalledTimes(YUANDIAN_SERVICES.length));
  expect(probe).toHaveBeenCalledTimes(YUANDIAN_SERVICES.length);
  for (const [index, service] of YUANDIAN_SERVICES.entries()) {
    const probeInput = probe.mock.calls[index]![0];
    expect(probeInput.draft.url).toBe(service.url);
    expect(probeInput.draft.headers).toEqual({});
    expect(probeInput.draft.secretHeaderKeys).toEqual(["Authorization"]);
    expect(probeInput.secretPatch.headers).toEqual({
      Authorization: "Bearer yuan-secret",
    });
    expect(JSON.stringify(probeInput.draft)).not.toContain("yuan-secret");

    const addInput = onAddServer.mock.calls[index]![0];
    expect(addInput.testReceipt).toBe(`receipt-${service.displayName}`);
    expect(addInput.toolDecisions).toEqual([{
      toolName: "search",
      enabled: true,
      expectedDescriptionHash: "d".repeat(64),
      expectedSchemaHash: "s".repeat(64),
    }]);
  }
  expect(onComplete).toHaveBeenCalledTimes(1);
});

test("the production Yuan Dian console bridge invokes only its fixed native command", async () => {
  nativeBridge.invoke.mockResolvedValue(undefined);

  await openYuandianConsole();

  expect(nativeBridge.invoke).toHaveBeenCalledWith("open_yuandian_console");
  expect(nativeBridge.invoke).toHaveBeenCalledTimes(1);
});

test("entering a new Yuan Dian key safely refreshes an existing preset server", async () => {
  const existing = {
    ...server("yuandian-law", "元典智库·法律法规"),
    url: YUANDIAN_SERVICES[0].url,
    secretHeaderKeys: ["Authorization"],
    riskFindings: ["mcp_vendor_data_egress"],
  };
  const probeSaved = vi.fn().mockResolvedValue({
    testReceipt: "saved-receipt",
    tools: [{
      toolName: "law-search",
      description: "search",
      descriptionHash: "d".repeat(64),
      inputSchema: { type: "object" },
      schemaHash: "s".repeat(64),
    }],
    riskCodes: ["mcp_vendor_data_egress"],
  });
  const updateSaved = vi.fn().mockResolvedValue({ ...existing, configRevision: 2 });
  const onSelectServer = vi.fn().mockResolvedValue(undefined);
  const onAddServer = vi.fn().mockResolvedValue(undefined);
  const probe = vi.fn().mockResolvedValue({
    testReceipt: "new-receipt",
    tools: [],
    riskCodes: [],
  });
  const listSavedTools = vi.fn().mockResolvedValue([{
    serverId: "yuandian-law",
    toolName: "law-search",
    description: "search",
    descriptionHash: "d".repeat(64),
    inputSchema: { type: "object" },
    schemaHash: "s".repeat(64),
    enabled: true,
    reviewState: "known",
    firstSeenAt: "2026-08-29T00:00:00Z",
    lastSeenAt: "2026-08-29T00:00:00Z",
  }]);
  const reviewSavedTool = vi.fn().mockResolvedValue(undefined);
  render(
    <YuandianSetup
      existingServers={[existing]}
      onSelectServer={onSelectServer}
      onAddServer={onAddServer}
      onCancel={vi.fn()}
      onComplete={vi.fn()}
      openConsole={vi.fn()}
      probe={probe}
      probeSaved={probeSaved}
      updateSaved={updateSaved}
      listSavedTools={listSavedTools}
      reviewSavedTool={reviewSavedTool}
    />,
  );

  fireEvent.change(screen.getByLabelText("元典 API Key"), {
    target: { value: "replacement-key" },
  });
  fireEvent.click(screen.getByRole("button", { name: "连接到当前对话" }));

  await waitFor(() => expect(updateSaved).toHaveBeenCalledTimes(1));
  expect(probeSaved).toHaveBeenCalledWith("yuandian-law", {
    expectedRevision: 1,
    patch: {},
    secretPatch: { env: {}, headers: { Authorization: "Bearer replacement-key" } },
    acceptedRiskCodes: ["mcp_vendor_data_egress"],
  }, expect.any(AbortSignal));
  expect(updateSaved.mock.calls[0]![1].testReceipt).toBe("saved-receipt");
  expect(reviewSavedTool).toHaveBeenCalledWith("yuandian-law", "law-search", {
    enabled: true,
    expectedDescriptionHash: "d".repeat(64),
    expectedSchemaHash: "s".repeat(64),
  }, expect.any(AbortSignal));
  expect(onSelectServer).toHaveBeenCalledWith("yuandian-law");
  expect(onAddServer).toHaveBeenCalledTimes(YUANDIAN_SERVICES.length - 1);
});

test("saved Yuan Dian endpoints are presented and selected as one plugin", async () => {
  const onSelect = vi.fn().mockResolvedValue(undefined);
  const servers = YUANDIAN_SERVICES.map((service, index) => ({
    serverId: `yuandian-${index}`,
    displayName: service.displayName,
    selected: false,
    connectionStatus: "idle" as const,
    toolCount: 0,
    errorCode: null,
    retryable: false,
    presetId: "yuandian" as const,
    presetServiceId: service.id,
  }));
  render(
    <McpThreadPicker
      snapshot={{ threadId: "thread-a", servers }}
      loading={false}
      error=""
      pendingServerIds={new Set()}
      onSelect={onSelect}
      onDeselect={vi.fn()}
      onRetry={vi.fn()}
      onAddServer={vi.fn()}
      onOpenSettings={vi.fn()}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getAllByText("元典智库")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "选择 元典智库" }));
  await waitFor(() => expect(onSelect).toHaveBeenCalledTimes(YUANDIAN_SERVICES.length));
  expect(onSelect.mock.calls.map(([serverId]) => serverId)).toEqual(
    servers.map((item) => item.serverId),
  );
});

test("opening plugins creates nothing and first selection is the only blank-draft creator", async () => {
  stubWorkspaceFetch();
  const threads = threadApi();
  const mcpApi = api();
  render(
    <App
      initialHealth={oneModelHealth}
      providerApi={providerApi()}
      threadApi={threads}
      mcpApi={mcpApi}
      updaterClient={updaterClient}
    />,
  );

  const picker = await openPluginPicker();
  expect(threads.createThread).not.toHaveBeenCalled();
  expect(mcpApi.atomicSelection).not.toHaveBeenCalled();

  fireEvent.keyDown(picker, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog", {
    name: "当前对话的插件",
  })).toBeNull());
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "更多选项" }));
  await openPluginPicker();

  fireEvent.click(screen.getByRole("button", { name: "选择 server-1" }));
  await waitFor(() => expect(mcpApi.atomicSelection).toHaveBeenCalledTimes(1));
  expect(mcpApi.atomicSelection).toHaveBeenCalledWith({
    existingThreadId: null,
    draftThread,
    existingServerId: "server-1",
    createServer: null,
  });
  expect(threads.createThread).not.toHaveBeenCalled();
});

test("first send and MCP selection share the same pending blank-draft creation", async () => {
  stubWorkspaceFetch();
  const atomic = deferred<AtomicThreadSelectionResult>();
  const threads = threadApi();
  const mcpApi = api({ atomicSelection: vi.fn().mockReturnValue(atomic.promise) });
  render(
    <App
      initialHealth={oneModelHealth}
      providerApi={providerApi()}
      threadApi={threads}
      mcpApi={mcpApi}
      updaterClient={updaterClient}
    />,
  );

  await openPluginPicker();
  fireEvent.click(screen.getByRole("button", { name: "选择 server-1" }));
  fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
    target: { value: "分析证据" },
  });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  await act(async () => { await Promise.resolve(); });

  expect(mcpApi.atomicSelection).toHaveBeenCalledTimes(1);
  expect(threads.createThread).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
  await act(async () => {
    atomic.resolve(atomicResult());
    await atomic.promise;
  });
  await waitFor(() => expect(threads.discardDraftThread).toHaveBeenCalledWith("draft-1"));
});

test("atomic rejection is safely shared by send and attachment without reflecting secrets", async () => {
  stubWorkspaceFetch();
  const atomic = deferred<AtomicThreadSelectionResult>();
  const threads = threadApi();
  const mcpApi = api({ atomicSelection: vi.fn().mockReturnValue(atomic.promise) });
  const unhandled = vi.fn();
  window.addEventListener("unhandledrejection", unhandled);
  try {
    render(
      <App
        initialHealth={oneModelHealth}
        providerApi={providerApi()}
        threadApi={threads}
        mcpApi={mcpApi}
        updaterClient={updaterClient}
      />,
    );

    await openPluginPicker();
    fireEvent.click(screen.getByRole("button", { name: "选择 server-1" }));
    fireEvent.change(screen.getByRole("textbox", { name: "向智能体提问" }), {
      target: { value: "分析证据" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(screen.getByLabelText("选择附件文件"), {
      target: { files: [new File(["证据"], "证据.txt", { type: "text/plain" })] },
    });

    await act(async () => {
      atomic.reject(new Error("https://example.test/?token=case-secret"));
      try { await atomic.promise; } catch { /* expected test input */ }
    });

    expect(await screen.findByText(
      "连接没有成功，请检查配置后重试。",
    )).toBeTruthy();
    await waitFor(() => expect(document.querySelector(
      '.composer-attachment[title="无法创建附件任务"]',
    )).not.toBeNull());
    expect(document.body.textContent).not.toContain("case-secret");
    expect(document.body.textContent).not.toContain("https://example.test");
    expect(unhandled).not.toHaveBeenCalled();
    expect(threads.createThread).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("unhandledrejection", unhandled);
  }
});

test("attachment and MCP selection share one App-level blank-draft single-flight", async () => {
  stubWorkspaceFetch();
  const atomic = deferred<AtomicThreadSelectionResult>();
  const threads = threadApi();
  const mcpApi = api({ atomicSelection: vi.fn().mockReturnValue(atomic.promise) });
  render(
    <App
      initialHealth={oneModelHealth}
      providerApi={providerApi()}
      threadApi={threads}
      mcpApi={mcpApi}
      updaterClient={updaterClient}
    />,
  );

  await openPluginPicker();
  fireEvent.click(screen.getByRole("button", { name: "选择 server-1" }));
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [new File(["证据"], "证据.txt", { type: "text/plain" })] },
  });

  expect(mcpApi.atomicSelection).toHaveBeenCalledTimes(1);
  expect(threads.createThread).not.toHaveBeenCalled();
  await act(async () => {
    atomic.resolve(atomicResult());
    await atomic.promise;
  });
  expect(await screen.findByText("证据.txt")).toBeTruthy();
  expect(threads.createThread).not.toHaveBeenCalled();
});

test("an existing hidden draft is reused and never replaced by MCP selection", async () => {
  stubWorkspaceFetch();
  const threads = threadApi({
    createThread: vi.fn().mockResolvedValue(threadRecord("existing-draft")),
  });
  const mcpApi = api();
  render(
    <App
      initialHealth={oneModelHealth}
      providerApi={providerApi()}
      threadApi={threads}
      mcpApi={mcpApi}
      updaterClient={updaterClient}
    />,
  );
  await screen.findByRole("button", { name: "选择模型，当前 model-a" });
  fireEvent.change(screen.getByLabelText("选择附件文件"), {
    target: { files: [new File(["证据"], "证据.txt", { type: "text/plain" })] },
  });
  await screen.findByText("证据.txt");

  await openPluginPicker();
  fireEvent.click(screen.getByRole("button", { name: "选择 server-1" }));
  await waitFor(() => expect(mcpApi.selectServer).toHaveBeenCalledWith(
    "existing-draft",
    "server-1",
  ));
  expect(threads.createThread).toHaveBeenCalledTimes(1);
  expect(mcpApi.atomicSelection).not.toHaveBeenCalled();
});

test("late atomic success is discarded and never inherited by the new task", async () => {
  stubWorkspaceFetch();
  const atomic = deferred<AtomicThreadSelectionResult>();
  const threads = threadApi();
  const mcpApi = api({ atomicSelection: vi.fn().mockReturnValue(atomic.promise) });
  render(
    <App
      initialHealth={oneModelHealth}
      providerApi={providerApi()}
      threadApi={threads}
      mcpApi={mcpApi}
      updaterClient={updaterClient}
    />,
  );

  await openPluginPicker();
  fireEvent.click(screen.getByRole("button", { name: "选择 server-1" }));
  fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
  await waitFor(() => expect(screen.queryByRole("dialog", {
    name: "当前对话的插件",
  })).toBeNull());
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "更多选项" }));
  await act(async () => {
    atomic.resolve(atomicResult());
    await atomic.promise;
  });

  await waitFor(() => expect(threads.discardDraftThread).toHaveBeenCalledWith("draft-1"));
  fireEvent.click(screen.getByRole("button", { name: "更多选项" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "插件" }));
  const fresh = await screen.findByRole("dialog", { name: "当前对话的插件" });
  expect(fresh.querySelector('[aria-pressed="true"]')).toBeNull();
});
