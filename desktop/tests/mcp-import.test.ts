import { afterEach, expect, test, vi } from "vitest";
import { createElement } from "react";
import { cleanup, render, screen } from "@testing-library/react";

import { McpConnections } from "../src/components/McpConnections";
import { mcpErrorCopy } from "../src/mcp/errorCopy";
import { previewMcpImport, safeMcpUrl } from "../src/mcp/import";
import {
  activateMcpView,
  createAtomicThreadMcpSelection,
  createMcpServer,
  deactivateMcpView,
  deleteMcpServer,
  deselectThreadMcpServer,
  getThreadMcpSnapshot,
  listMcpServers,
  listMcpServerTools,
  listThreadMcpTools,
  parseMcpImport,
  reviewMcpServerTool,
  selectThreadMcpServer,
  testMcpServerDraft,
  testSavedMcpServer,
  updateMcpServer,
} from "../src/runtime/api";
import type {
  AtomicThreadSelectionRequest,
  CreateMcpServerInput,
  McpServerDraft,
  McpToolDecision,
} from "../src/runtime/contracts";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete globalThis.__VERILEX_HTTP__;
  delete globalThis.__VERILEX_API_TOKEN__;
});

test("shows the saved server management actions without a global enable switch", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([{
    id: "server-1",
    displayName: "Law server",
    transport: "stdio",
    command: "node",
    args: ["server.js"],
    cwd: null,
    url: null,
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
    lastTestStatus: null,
    lastTestedAt: null,
    selectedThreadCount: 0,
    createdAt: "2026-08-29T00:00:00Z",
    updatedAt: "2026-08-29T00:00:00Z",
  }]), { status: 200 })));

  render(createElement(McpConnections));

  expect(await screen.findByText("Law server")).toBeTruthy();
  expect(screen.getByRole("button", { name: "删除 Law server" })).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: /全局启用|启动/ })).toBeNull();
});

test("recognizes a wrapped JSON config without running its command", () => {
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);

  const [draft] = previewMcpImport(JSON.stringify({
    mcpServers: {
      law: {
        command: "node",
        args: ["server.js", "$(touch /tmp/never)"],
        env: { LANG: "zh_CN.UTF-8", API_TOKEN: "private-value" },
      },
    },
  }));

  expect(draft).toMatchObject({
    displayName: "law",
    transport: "stdio",
    command: "node",
    args: ["server.js", "$(touch /tmp/never)"],
    env: {},
    secretEnvKeys: ["API_TOKEN", "LANG"],
    secretPatch: { env: { API_TOKEN: "private-value", LANG: "zh_CN.UTF-8" }, headers: {} },
  });
  expect(fetchMock).not.toHaveBeenCalled();
});

test("recognizes common Claude, Codex, and Gemini exports using the supported wrapper", () => {
  const inputs = [
    { mcpServers: { claude: { command: "npx", args: ["-y", "claude-law"] } } },
    { mcpServers: { codex: { command: "uvx", args: ["codex-law"] } } },
    { mcpServers: { gemini: { url: "https://example.test/mcp", type: "sse" } } },
  ];

  expect(inputs.map((input) => previewMcpImport(JSON.stringify(input))[0])).toMatchObject([
    { displayName: "claude", transport: "stdio", command: "npx" },
    { displayName: "codex", transport: "stdio", command: "uvx" },
    { displayName: "gemini", transport: "sse", url: "https://example.test/mcp" },
  ]);
});

test("recognizes a Claude add command without expanding shell syntax", () => {
  expect(
    previewMcpImport(
      "claude mcp add --env TOKEN='$TOKEN' law -- npx -y @example/law",
    )[0],
  ).toMatchObject({
    displayName: "law",
    transport: "stdio",
    command: "npx",
    args: ["-y", "@example/law"],
    secretEnvKeys: ["TOKEN"],
    secretPatch: { env: { TOKEN: "$TOKEN" }, headers: {} },
  });
});

test("fails closed when the local preview exceeds its byte, count, or depth budget", () => {
  const tooMany = Object.fromEntries(
    Array.from({ length: 33 }, (_, index) => [`server-${index}`, { command: "node" }]),
  );
  let nested: unknown = { command: "node" };
  for (let index = 0; index < 65; index += 1) nested = { child: nested };

  expect(previewMcpImport("x".repeat(256 * 1024 + 1))).toEqual([]);
  expect(previewMcpImport(JSON.stringify({ mcpServers: tooMany }))).toEqual([]);
  expect(previewMcpImport(JSON.stringify(nested))).toEqual([]);
});

test("rejects prototype keys, overlong inferred names, and explicit null timeouts", () => {
  const polluted = previewMcpImport(
    '{"mcpServers":{"law":{"command":"node","env":{"__proto__":"sentinel"}}}}',
  );
  const longName = previewMcpImport(JSON.stringify({
    command: "node",
    args: ["x".repeat(257)],
  }));
  const nullTimeout = previewMcpImport(
    '{"mcpServers":{"law":{"command":"node","connectTimeoutMs":null}}}',
  );

  expect(polluted).toEqual([]);
  expect(longName).toEqual([]);
  expect(nullTimeout).toEqual([]);
  expect((Object.prototype as Record<string, unknown>).sentinel).toBeUndefined();
});

test("maps known MCP errors to fixed Chinese recovery copy", () => {
  expect(mcpErrorCopy("mcp_credentials_missing")).toBe("需要补充密钥后再试。");
  expect(mcpErrorCopy("mcp_tool_changed")).toBe(
    "这个服务器改变了工具，请先查看后再继续。",
  );
  expect(mcpErrorCopy("mcp_result_unknown")).toBe(
    "服务中断，操作可能已经完成，请先检查结果再决定是否重试。",
  );
  expect(mcpErrorCopy("mcp_secret_in_arguments")).toBe(
    "命令参数中可能包含秘密，请移除后改用秘密环境变量保存。",
  );
  expect(mcpErrorCopy("mcp_url_credentials")).toBe(
    "网络地址中包含账号或密码，请从地址中移除。",
  );
  expect(mcpErrorCopy("mcp_secret_in_url")).toBe(
    "网络地址中可能包含令牌等秘密，请移除后改用秘密请求头保存。",
  );
});

test("maps unknown Runtime text to fixed copy without echoing URL, command, or secret", () => {
  const unsafe = "unknown_stack https://private.test node --token top-secret";
  const copy = mcpErrorCopy(unsafe);

  expect(copy).toBe("连接没有成功，请检查配置后重试。");
  expect(copy).not.toContain("private.test");
  expect(copy).not.toContain("node");
  expect(copy).not.toContain("top-secret");
});

const DRAFT: McpServerDraft = {
  displayName: "law",
  transport: "stdio",
  command: "node",
  args: ["server.js"],
  cwd: null,
  url: null,
  env: {},
  headers: {},
  secretEnvKeys: [],
  secretHeaderKeys: [],
  connectTimeoutMs: 10_000,
  requestTimeoutMs: 30_000,
};

const DECISION: McpToolDecision = {
  toolName: "read_case",
  enabled: true,
  expectedDescriptionHash: "a".repeat(64),
  expectedSchemaHash: "b".repeat(64),
};

const CREATE_INPUT: CreateMcpServerInput = {
  draft: DRAFT,
  secretPatch: {},
  acceptedRiskCodes: [],
  testReceipt: "receipt-1",
  toolDecisions: [DECISION],
};

test("every Task 7 MCP API forwards the caller AbortSignal and exact camelCase body", async () => {
  globalThis.__VERILEX_HTTP__ = "http://127.0.0.1:9999";
  const calls: Array<[string, RequestInit]> = [];
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string, init: RequestInit) => {
    calls.push([url, init]);
    return Promise.resolve(new Response("{}", { status: 200 }));
  }));
  const controller = new AbortController();
  const atomic: AtomicThreadSelectionRequest = {
    existingThreadId: "thread-1",
    draftThread: null,
    existingServerId: "server-1",
    createServer: null,
    viewId: "view-1",
  };

  await listMcpServers(controller.signal);
  await parseMcpImport("node server.js", controller.signal);
  await testMcpServerDraft({
    draft: DRAFT,
    secretPatch: {},
    acceptedRiskCodes: [],
  }, controller.signal);
  await createMcpServer(CREATE_INPUT, controller.signal);
  await updateMcpServer("server / 1", {
    expectedRevision: 4,
    patch: { requestTimeoutMs: 45_000 },
    secretPatch: {},
    acceptedRiskCodes: [],
    testReceipt: "receipt-2",
    toolDecisions: [DECISION],
  }, controller.signal);
  await deleteMcpServer("server / 1", controller.signal);
  await testSavedMcpServer("server / 1", {
    expectedRevision: 4,
    patch: {},
    secretPatch: {},
    acceptedRiskCodes: [],
  }, controller.signal);
  await listMcpServerTools("server / 1", controller.signal);
  await reviewMcpServerTool("server / 1", "folder/read", {
    enabled: false,
    expectedDescriptionHash: "a".repeat(64),
    expectedSchemaHash: "b".repeat(64),
  }, controller.signal);
  await getThreadMcpSnapshot("thread / 1", controller.signal);
  await selectThreadMcpServer("thread / 1", "server / 1", controller.signal);
  await deselectThreadMcpServer("thread / 1", "server / 1", controller.signal);
  await listThreadMcpTools("thread / 1", controller.signal);
  await createAtomicThreadMcpSelection(atomic, controller.signal);
  await activateMcpView("view / 1", "thread / 1", controller.signal);
  await deactivateMcpView("view / 1", controller.signal);

  expect(calls.map(([url, init]) => [
    url.replace("http://127.0.0.1:9999", ""),
    init.method ?? "GET",
    init.body ?? null,
  ])).toEqual([
    ["/v1/mcp/servers", "GET", null],
    ["/v1/mcp/import/parse", "POST", JSON.stringify({ text: "node server.js" })],
    ["/v1/mcp/servers/test", "POST", JSON.stringify({
      draft: DRAFT,
      secretPatch: {},
      acceptedRiskCodes: [],
    })],
    ["/v1/mcp/servers", "POST", JSON.stringify(CREATE_INPUT)],
    ["/v1/mcp/servers/server%20%2F%201", "PATCH", JSON.stringify({
      expectedRevision: 4,
      patch: { requestTimeoutMs: 45_000 },
      secretPatch: {},
      acceptedRiskCodes: [],
      testReceipt: "receipt-2",
      toolDecisions: [DECISION],
    })],
    ["/v1/mcp/servers/server%20%2F%201", "DELETE", null],
    ["/v1/mcp/servers/server%20%2F%201/test", "POST", JSON.stringify({
      expectedRevision: 4,
      patch: {},
      secretPatch: {},
      acceptedRiskCodes: [],
    })],
    ["/v1/mcp/servers/server%20%2F%201/tools", "GET", null],
    ["/v1/mcp/servers/server%20%2F%201/tools/folder%2Fread", "PATCH", JSON.stringify({
      enabled: false,
      expectedDescriptionHash: "a".repeat(64),
      expectedSchemaHash: "b".repeat(64),
    })],
    ["/v1/threads/thread%20%2F%201/mcp", "GET", null],
    ["/v1/threads/thread%20%2F%201/mcp/server%20%2F%201", "PUT", "{}"],
    ["/v1/threads/thread%20%2F%201/mcp/server%20%2F%201", "DELETE", null],
    ["/v1/threads/thread%20%2F%201/mcp/tools", "GET", null],
    ["/v1/mcp/thread-selections", "POST", JSON.stringify(atomic)],
    ["/v1/mcp/active-views/view%20%2F%201", "PUT", JSON.stringify({
      threadId: "thread / 1",
    })],
    ["/v1/mcp/active-views/view%20%2F%201", "DELETE", null],
  ]);
  expect(calls.every(([, init]) => init.signal === controller.signal)).toBe(true);
});

test("sends selected import names without changing the raw source", async () => {
  let body = "";
  vi.stubGlobal("fetch", vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    body = String(init.body);
    return Promise.resolve(new Response(JSON.stringify({ drafts: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  }));

  await parseMcpImport("raw source with blocked sibling", undefined, ["safe"]);

  expect(JSON.parse(body)).toEqual({
    text: "raw source with blocked sibling",
    selectedDisplayNames: ["safe"],
  });
});

test("projects parse-only and preview-only fields out of every draft request body", async () => {
  const calls: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn().mockImplementation((_url: string, init: RequestInit) => {
    calls.push(JSON.parse(String(init.body)) as Record<string, unknown>);
    return Promise.resolve(new Response("{}", { status: 200 }));
  }));
  const enrichedDraft = {
    ...DRAFT,
    risks: [{ code: "mcp_insecure_http", severity: "warn" }],
    secretPatch: { env: { TOKEN: "must-stay-outside-draft" }, headers: {} },
  };
  const input = {
    ...CREATE_INPUT,
    draft: enrichedDraft,
  };

  await testMcpServerDraft(input, undefined);
  await createMcpServer(input, undefined);
  await createAtomicThreadMcpSelection({
    existingThreadId: "thread-1",
    draftThread: null,
    existingServerId: null,
    createServer: input,
  });

  expect(calls.map((call) => {
    const container = (call.createServer ?? call) as Record<string, unknown>;
    return container.draft;
  })).toEqual([DRAFT, DRAFT, DRAFT]);
});

test("keeps atomic selectedAt inside the Task 7 selection object", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
    thread: {
      id: "thread-1",
      title: "New task",
      providerId: "provider-1",
      model: "model-1",
      workspaceId: null,
      status: "idle",
      createdAt: "2026-08-29T00:00:00Z",
      updatedAt: "2026-08-29T00:00:00Z",
      isDraft: true,
      reasoningEffort: "auto",
    },
    server: { id: "server-1" },
    selection: {
      serverId: "server-1",
      selected: true,
      selectedAt: "2026-08-29T00:00:00Z",
      connectionStatus: "ready",
      errorCode: null,
      retryable: false,
    },
    createdThread: true,
    createdServer: false,
  }), { status: 201 })));

  const result = await createAtomicThreadMcpSelection({
    existingThreadId: null,
    draftThread: {
      title: "New task",
      providerId: "provider-1",
      model: "model-1",
      workspaceId: null,
      reasoningEffort: "auto",
    },
    existingServerId: "server-1",
    createServer: null,
    viewId: null,
  });

  expect(result.selection.selectedAt).toBe("2026-08-29T00:00:00Z");
  expect("selectedAt" in result).toBe(false);
});


test("ordinary query names containing credential words stay usable; complete credential fields do not", () => {
  expect(safeMcpUrl("https://example.com/mcp?author=alice&keyword=contract&tokenizer=unicode")).toBe(true);
  for (const field of ["api_key", "API-KEY", "token", "password", "authorization", "sig", "access_token", "client_secret", "api_token", "X-Amz-Signature"]) {
    expect(safeMcpUrl(`https://example.com/mcp?${field}=value`)).toBe(false);
  }
});

test("import keeps all environment and header values in the existing private patch without guessing names", () => {
  const drafts = previewMcpImport(JSON.stringify({mcpServers:{custom:{command:"node",env:{LANG:"en",CUSTOM:"private"},headers:{"X-Custom":"private", "Content-Type":"application/json"}}}}));
  expect(drafts?.[0]).toMatchObject({ env: {}, headers: {}, secretEnvKeys: ["CUSTOM", "LANG"], secretHeaderKeys: ["Content-Type", "X-Custom"],
    secretPatch: {env:{LANG:"en",CUSTOM:"private"},headers:{"X-Custom":"private", "Content-Type":"application/json"}} });
});


test.each(["python3", "deno", "/opt/my tools/adapter"])('previews arbitrary executable %s without executing or shell expansion', command => {
  expect(previewMcpImport(`'${command}' --tokenizer unicode '$HOME'`)[0]).toMatchObject({command, args:["--tokenizer", "unicode", "$HOME"]});
});
