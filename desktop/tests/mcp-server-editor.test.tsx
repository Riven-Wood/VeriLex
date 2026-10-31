import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { McpServerEditor } from "../src/components/McpServerEditor";
import * as runtimeApi from "../src/runtime/api";
import type {
  McpParsedServerDraft,
  McpServerRecord,
  McpTestResult,
} from "../src/runtime/contracts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const HASH_C = "c".repeat(64);
const HASH_D = "d".repeat(64);

function draft(overrides: Partial<McpParsedServerDraft> = {}): McpParsedServerDraft {
  return {
    displayName: "案例检索",
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
    risks: [],
    ...overrides,
  };
}

function testResult(receipt = "receipt-1"): McpTestResult {
  return {
    testReceipt: receipt,
    riskCodes: [],
    tools: [
      {
        toolName: "search_cases",
        description: "检索案例",
        descriptionHash: HASH_A,
        inputSchema: { type: "object" },
        schemaHash: HASH_B,
      },
      {
        toolName: "read_case",
        description: "读取案例",
        descriptionHash: HASH_C,
        inputSchema: { type: "object" },
        schemaHash: HASH_D,
      },
    ],
  };
}

function record(overrides: Partial<McpServerRecord> = {}): McpServerRecord {
  return {
    id: "server-1",
    ...draft(),
    configRevision: 4,
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
    ...overrides,
  };
}

const WRAPPED_CONFIG = JSON.stringify({
  mcpServers: {
    "案例检索": { command: "node", args: ["server.js"] },
  },
});

test("tests before save, submits every tool decision, and invalidates the receipt after a connection edit", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({ drafts: [draft()] });
  vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());
  const create = vi.spyOn(runtimeApi, "createMcpServer").mockResolvedValue(record());
  const activate = vi.spyOn(runtimeApi, "activateMcpView");

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  expect(screen.queryByLabelText("传输方式")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: /读取案例/ }));
  fireEvent.click(screen.getByRole("button", { name: "保存" }));

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[0].toolDecisions).toEqual([
    {
      toolName: "search_cases",
      enabled: true,
      expectedDescriptionHash: HASH_A,
      expectedSchemaHash: HASH_B,
    },
    {
      toolName: "read_case",
      enabled: false,
      expectedDescriptionHash: HASH_C,
      expectedSchemaHash: HASH_D,
    },
  ]);
  expect(activate).not.toHaveBeenCalled();

  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG.replace("server.js", "changed.js") },
  });
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
});

test("keeps advanced connection fields in the same panel and clears a tested receipt when timeout changes", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({ drafts: [draft()] });
  vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();

  fireEvent.click(screen.getByText("高级选项"));
  fireEvent.change(screen.getByLabelText("请求超时"), { target: { value: "45000" } });

  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
  expect(screen.queryByText("找到 2 个工具")).toBeNull();
});

test("keeps a reviewed receipt when only the display name or selected import changes", async () => {
  const secondDraft = draft({ displayName: "法规检索", command: "uvx" });
  vi.spyOn(runtimeApi, "parseMcpImport").mockImplementation(async (text, _signal, selectedNames) => {
    if (selectedNames?.[0] === "案例检索") return { drafts: [draft()] };
    if (selectedNames?.[0] === "法规检索") return { drafts: [secondDraft] };
    return {
      drafts: [draft(), secondDraft].filter((item) => text.includes(item.displayName)),
    };
  });
  vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());
  const create = vi.spyOn(runtimeApi, "createMcpServer").mockImplementation(async (input) =>
    record({ id: input.draft.displayName, displayName: input.draft.displayName }));

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "案例检索": { command: "node" },
          "法规检索": { command: "uvx" },
        },
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(screen.getAllByText("找到 2 个工具")).toHaveLength(2));

  const nameInputs = screen.getAllByLabelText("名称");
  fireEvent.change(nameInputs[0]!, { target: { value: "案例检索（本地）" } });
  fireEvent.click(screen.getByRole("checkbox", { name: "保存 法规检索" }));
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "保存" }));

  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[0]).toMatchObject({
    draft: { displayName: "案例检索（本地）" },
    testReceipt: "receipt-1",
  });
});

test("invalidates a receipt immediately when a secret or accepted warning changes", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({
    drafts: [draft({
      secretEnvKeys: ["API_TOKEN"],
      risks: [{ code: "mcp_insecure_http", severity: "warn" }],
    })],
  });
  vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "案例检索": {
            command: "node",
            args: ["server.js"],
            env: { API_TOKEN: "first-secret" },
          },
        },
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  fireEvent.click(await screen.findByRole("checkbox", { name: "我了解并继续" }));
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();

  fireEvent.click(screen.getByText("高级选项"));
  fireEvent.change(screen.getByLabelText("秘密环境变量"), {
    target: { value: "API_TOKEN=second-secret" },
  });
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect((await screen.findByRole("checkbox", { name: "我了解并继续" }) as HTMLInputElement).checked).toBe(false);
  fireEvent.click(screen.getByRole("checkbox", { name: "我了解并继续" }));
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: "我了解并继续" }));
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
});

test("shows manual transport fields only when local recognition fails", () => {
  const view = render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  expect(screen.queryByLabelText("传输方式")).toBeNull();

  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: "{invalid" },
  });
  expect(screen.getByLabelText("传输方式")).toBeTruthy();
  expect(screen.getByLabelText("命令")).toBeTruthy();
  expect(view.getByText("没有识别成功，请填写连接信息。")).toBeTruthy();
});

test("lets Runtime recognize raw input that the local preview cannot understand", async () => {
  const runtimeDraft = draft({
    displayName: "运行时识别",
    transport: "http",
    command: null,
    args: [],
    url: "https://example.test/mcp",
  });
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({ drafts: [runtimeDraft] });
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: '{"runtime_only":true}' },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[0].draft).toMatchObject({
    displayName: "运行时识别",
    transport: "http",
    url: "https://example.test/mcp",
  });
  expect(screen.queryByLabelText("传输方式")).toBeNull();
});

test("fails closed when Runtime rejects raw input that the preview recognized", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockRejectedValue(new runtimeApi.RuntimeApiError(
    "do not show raw details",
    { code: "mcp_config_invalid", retryable: false, status: 422 },
  ));
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft");

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  expect((await screen.findByRole("alert")).textContent).toBe("无法识别这份配置，请修改后重试。");
  expect(screen.queryByText("do not show raw details")).toBeNull();
  expect(probe).not.toHaveBeenCalled();
});

test("shows a fixed recovery reason when raw import places a secret in command arguments", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockRejectedValue(new runtimeApi.RuntimeApiError(
    "npx pkg --token private-raw-value",
    { code: "mcp_secret_in_arguments", retryable: false, status: 403 },
  ));
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft");

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: "npx pkg --token private-raw-value" },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toBe("命令参数中可能包含秘密，请移除后改用秘密环境变量保存。");
  expect(alert.textContent).not.toContain("private-raw-value");
  expect(probe).not.toHaveBeenCalled();
});

test("fails closed when Runtime and preview disagree about secret fields", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({
    drafts: [draft({ secretEnvKeys: ["DIFFERENT_SECRET"] })],
  });
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft");

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: { "案例检索": { command: "node", env: { API_TOKEN: "secret" } } },
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  expect((await screen.findByRole("alert")).textContent).toBe("无法识别这份配置，请修改后重试。");
  expect(probe).not.toHaveBeenCalled();
});

test("tests the edited connection values instead of replacing them with the parsed baseline", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({ drafts: [draft()] });
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByText("高级选项"));
  fireEvent.change(screen.getByLabelText("请求超时"), { target: { value: "45000" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[0].draft.requestTimeoutMs).toBe(45_000);
});

test("validates manual command, network URL, and timeout before probing Runtime", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockRejectedValue(new runtimeApi.RuntimeApiError(
    "invalid raw input",
    { code: "mcp_config_invalid", retryable: false, status: 422 },
  ));
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft");
  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: "{invalid" },
  });
  expect(screen.getByText("请输入要启动的命令。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(
    screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled"),
  ).toBe(true));
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(true);

  fireEvent.change(screen.getByLabelText("传输方式"), { target: { value: "http" } });
  fireEvent.change(screen.getByLabelText("网络地址"), { target: { value: "not-a-url" } });
  expect(screen.getByText("请输入有效且不含凭据的 http 或 https 地址。")).toBeTruthy();

  fireEvent.change(screen.getByLabelText("网络地址"), { target: { value: "https://example.test/mcp" } });
  fireEvent.click(screen.getByText("高级选项"));
  fireEvent.change(screen.getByLabelText("请求超时"), { target: { value: "0" } });
  expect(screen.getByText("请求超时应为 1000 到 600000 毫秒的整数。")).toBeTruthy();
  expect(probe).not.toHaveBeenCalled();
});

test("keeps KEY=value text while typing and blocks malformed or duplicate lines locally", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport")
    .mockResolvedValueOnce({ drafts: [draft()] })
    .mockResolvedValueOnce({ drafts: [draft({ env: { A: "1" } })] });
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByText("高级选项"));
  const input = screen.getByLabelText("普通环境变量") as HTMLTextAreaElement;
  fireEvent.change(input, { target: { value: "A" } });
  expect(input.value).toBe("A");
  expect(screen.getByText("每行请使用 KEY=值，字段名不能留空或重复。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(true);

  fireEvent.change(input, { target: { value: "A=1\nA=2" } });
  expect(input.value).toBe("A=1\nA=2");
  expect(screen.getByText("每行请使用 KEY=值，字段名不能留空或重复。")).toBeTruthy();

  fireEvent.change(input, { target: { value: "__proto__=unsafe" } });
  expect(input.value).toBe("__proto__=unsafe");
  expect(screen.getByText("每行请使用 KEY=值，字段名不能留空或重复。")).toBeTruthy();

  fireEvent.change(input, { target: { value: "A=1" } });
  expect(screen.queryByText("每行请使用 KEY=值，字段名不能留空或重复。")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[0].draft.env).toEqual({ A: "1" });
});

test("treats ordinary and secret HTTP header names as case-insensitive locally", () => {
  const existing = record({
    transport: "http",
    command: null,
    args: [],
    url: "https://example.test/mcp",
  });
  render(<McpServerEditor mode="settings" server={existing} />);
  fireEvent.click(screen.getByText("高级选项"));

  fireEvent.change(screen.getByLabelText("普通请求头"), {
    target: { value: "X-Trace=one\nx-trace=two" },
  });
  expect(screen.getByText("每行请使用 KEY=值，字段名不能留空或重复。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(true);

  fireEvent.change(screen.getByLabelText("普通请求头"), { target: { value: "X-Trace=one" } });
  fireEvent.change(screen.getByLabelText("秘密请求头"), {
    target: { value: "Authorization=one\nauthorization=two" },
  });
  expect(screen.getByText("每行请使用 KEY=值，字段名不能留空或重复。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(true);

  fireEvent.change(screen.getByLabelText("秘密请求头"), {
    target: { value: "x-trace=secret" },
  });
  expect(screen.getByText("普通请求头和秘密请求头的字段名不能重复。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(true);
});

test("matches secret HTTP header patches to the saved canonical spelling", async () => {
  const existing = record({
    transport: "http",
    command: null,
    args: [],
    url: "https://example.test/mcp",
    secretHeaderKeys: ["Authorization"],
  });
  const parsedWithSecret = { drafts: [draft({
      transport: "http",
      command: null,
      args: [],
      url: "https://example.test/mcp",
      secretHeaderKeys: ["Authorization"],
    })] };
  vi.spyOn(runtimeApi, "parseMcpImport")
    .mockResolvedValueOnce(parsedWithSecret)
    .mockResolvedValueOnce({ drafts: [draft({
      transport: "http",
      command: null,
      args: [],
      url: "https://example.test/mcp",
      secretHeaderKeys: [],
    })] })
    .mockResolvedValueOnce(parsedWithSecret);
  const probe = vi.spyOn(runtimeApi, "testSavedMcpServer").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" server={existing} />);
  fireEvent.click(screen.getByText("高级选项"));
  const secret = screen.getByLabelText("秘密请求头");

  fireEvent.change(secret, { target: { value: "authorization=new-secret" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[1].secretPatch.headers).toEqual({ Authorization: "new-secret" });

  fireEvent.change(secret, { target: { value: "authorization=" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(2));
  expect(probe.mock.calls[1]?.[1]).toMatchObject({
    patch: { secretHeaderKeys: [] },
    secretPatch: { headers: { Authorization: null } },
  });

  fireEvent.change(secret, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(3));
  expect(probe.mock.calls[2]?.[1]).toMatchObject({ patch: {}, secretPatch: { headers: {} } });
});

test("points an ordinary sensitive environment key to the secret field without showing its value", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport")
    .mockResolvedValueOnce({ drafts: [draft()] })
    .mockResolvedValueOnce({ drafts: [draft({ secretEnvKeys: ["API_TOKEN"] })] });
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft");

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByText("高级选项"));
  fireEvent.change(screen.getByLabelText("普通环境变量"), {
    target: { value: "API_TOKEN=private-env-value" },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toBe("请把 API_TOKEN 移到秘密环境变量。");
  expect(alert.textContent).not.toContain("private-env-value");
  expect(probe).not.toHaveBeenCalled();
});

test("points an ordinary sensitive HTTP header to the secret field without showing its value", async () => {
  const existing = record({
    transport: "http",
    command: null,
    args: [],
    url: "https://example.test/mcp",
  });
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({
    drafts: [draft({
      transport: "http",
      command: null,
      args: [],
      url: "https://example.test/mcp",
      secretHeaderKeys: ["Authorization"],
    })],
  });
  const probe = vi.spyOn(runtimeApi, "testSavedMcpServer");

  render(<McpServerEditor mode="settings" server={existing} />);
  fireEvent.click(screen.getByText("高级选项"));
  fireEvent.change(screen.getByLabelText("普通请求头"), {
    target: { value: "authorization=private-header-value" },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toBe("请把 Authorization 移到秘密请求头。");
  expect(alert.textContent).not.toContain("private-header-value");
  expect(probe).not.toHaveBeenCalled();
});

test("keeps an already blocked saved configuration blocked", () => {
  render(<McpServerEditor mode="settings" server={record({
    configReviewState: "blocked",
    riskFindings: ["mcp_config_invalid"],
  })} />);

  expect(screen.getByText("这份配置存在不安全内容，不能添加。")).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: "我了解并继续" })).toBeNull();
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(true);
});

test("explains stable block reasons without echoing unsafe values", () => {
  const view = render(<McpServerEditor mode="settings" server={record({
    configReviewState: "blocked",
    args: ["--token", "private-argument-secret"],
    env: { NODE_OPTIONS: "private-environment-value" },
    riskFindings: [
      "mcp_config_invalid",
      "mcp_secret_in_arguments",
      "mcp_url_credentials",
      "mcp_secret_in_url",
    ],
  })} />);

  const reasons = view.container.querySelector(".mcp-block-reasons");
  expect(screen.getByRole("alert")).toBe(reasons);
  expect(screen.getByRole("alert").textContent).toContain("这份配置存在不安全内容，不能添加。");
  expect(reasons?.textContent).toContain("配置格式或字段不符合要求");
  expect(reasons?.textContent).toContain("命令参数中可能包含秘密");
  expect(reasons?.textContent).toContain("网络地址中包含账号或密码");
  expect(reasons?.textContent).toContain("网络地址中可能包含令牌等秘密");
  expect(reasons?.textContent).not.toContain("private-argument-secret");
  expect(reasons?.textContent).not.toContain("private-environment-value");
});

test("rejects duplicate names, control characters, and oversized fields locally", () => {
  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          first: { command: "node" },
          second: { command: "uvx" },
        },
      }),
    },
  });
  const names = screen.getAllByLabelText("名称");
  fireEvent.change(names[1]!, { target: { value: "first" } });
  expect(screen.getAllByText("服务器名称不能重复。")).toHaveLength(2);
  fireEvent.change(names[1]!, { target: { value: "second" } });
  fireEvent.click(screen.getAllByText("高级选项")[0]!);
  fireEvent.change(screen.getAllByLabelText("命令")[0]!, { target: { value: "node\u0000bad" } });
  expect(screen.getByText("连接信息过长或包含不可用字符。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "测试并查看工具" }).hasAttribute("disabled")).toBe(true);
});

test("lists secret field names without echoing their values", () => {
  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          law: { command: "node", env: { API_TOKEN: "never-show-this" } },
        },
      }),
    },
  });

  const copy = screen.getByText(/秘密字段 API_TOKEN/);
  expect(copy.textContent).toContain("本地私有配置");
  expect(copy.textContent).not.toContain("never-show-this");
});

test("reads one bounded JSON drop in memory and ignores a late file after newer input", async () => {
  let resolveLate!: (value: ArrayBuffer) => void;
  const lateFile = {
    name: "old.json",
    type: "application/json",
    size: 20,
    arrayBuffer: () => new Promise<ArrayBuffer>((resolve) => { resolveLate = resolve; }),
  } as File;
  render(<McpServerEditor mode="settings" />);
  const dropZone = screen.getByLabelText("粘贴 MCP 配置");
  fireEvent.drop(dropZone, { dataTransfer: { files: [lateFile] } });
  fireEvent.change(dropZone, { target: { value: WRAPPED_CONFIG } });
  resolveLate(new TextEncoder().encode(JSON.stringify({ mcpServers: { old: { command: "node" } } })).buffer);
  await Promise.resolve();
  expect((dropZone as HTMLTextAreaElement).value).toBe(WRAPPED_CONFIG);

  const current = JSON.stringify({ mcpServers: { dropped: { command: "uvx" } } });
  const file = {
    name: "server.json",
    type: "application/json",
    size: new TextEncoder().encode(current).byteLength,
    arrayBuffer: async () => new TextEncoder().encode(current).buffer,
  } as File;
  fireEvent.drop(dropZone, { dataTransfer: { files: [file] } });
  await waitFor(() => expect((dropZone as HTMLTextAreaElement).value).toBe(current));
  expect(screen.getByDisplayValue("dropped")).toBeTruthy();
});

test("requires warning acknowledgement in place and blocks unsafe drafts without testing", async () => {
  const warningParse = {
    drafts: [draft({
      risks: [{ code: "mcp_insecure_http", severity: "warn" }],
    })],
  };
  const parse = vi.spyOn(runtimeApi, "parseMcpImport")
    .mockResolvedValueOnce(warningParse)
    .mockResolvedValueOnce(warningParse)
    .mockResolvedValueOnce(warningParse)
    .mockResolvedValue({
    drafts: [draft({
      risks: [{ code: "mcp_config_invalid", severity: "block" }],
    })],
  });
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  const view = render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("会在本机启动命令。请只添加你信任的服务器。")).toBeTruthy();
  expect(screen.getByText("数据将通过未加密的网络连接发送。")).toBeTruthy();
  expect(probe).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("checkbox", { name: "我了解并继续" }));
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[0].acceptedRiskCodes).toEqual(["mcp_insecure_http"]);

  view.unmount();
  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("这份配置存在不安全内容，不能添加。")).toBeTruthy();
  expect(parse).toHaveBeenCalledTimes(5);
  expect(probe).toHaveBeenCalledTimes(1);
});

test("keeps independent receipts for multiple imports and preserves an item that did not save", async () => {
  const secondDraft = draft({
    displayName: "法规检索",
    command: "uvx",
    args: ["law-server"],
  });
  vi.spyOn(runtimeApi, "parseMcpImport").mockImplementation(async (text, _signal, selectedNames) => {
    if (selectedNames?.[0] === "案例检索") return { drafts: [draft()] };
    if (selectedNames?.[0] === "法规检索") return { drafts: [secondDraft] };
    return {
      drafts: [draft(), secondDraft].filter((item) => text.includes(item.displayName)),
    };
  });
  vi.spyOn(runtimeApi, "testMcpServerDraft")
    .mockResolvedValueOnce(testResult("receipt-first"))
    .mockResolvedValueOnce(testResult("receipt-second"));
  const create = vi.spyOn(runtimeApi, "createMcpServer")
    .mockResolvedValueOnce(record())
    .mockRejectedValueOnce(new runtimeApi.RuntimeApiError(
      "https://private.test?token=secret",
      { code: "mcp_name_conflict", retryable: false, status: 409 },
    ));

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "案例检索": { command: "node", args: ["server.js"] },
          "法规检索": { command: "uvx", args: ["law-server"] },
        },
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(screen.getAllByText("找到 2 个工具")).toHaveLength(2));

  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(2));

  expect(screen.queryByText("案例检索")).toBeNull();
  expect(screen.getByDisplayValue("法规检索")).toBeTruthy();
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toBe("这个名称已经被使用，请换一个名称。");
  expect(alert.textContent).not.toContain("private.test");
  expect(alert.textContent).not.toContain("secret");
});

test("allows selecting only some entries from a multi-server import", async () => {
  const secondDraft = draft({ displayName: "法规检索", command: "uvx" });
  vi.spyOn(runtimeApi, "parseMcpImport")
    .mockResolvedValueOnce({ drafts: [draft()] })
    .mockResolvedValueOnce({ drafts: [draft()] });
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "案例检索": { command: "node" },
          "法规检索": { command: "uvx" },
        },
      }),
    },
  });
  const choices = await screen.findAllByRole("checkbox", { name: /保存/ });
  fireEvent.click(choices[1]!);
  fireEvent.change(screen.getAllByLabelText("名称")[1]!, { target: { value: "案例检索" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[0].draft.displayName).toBe("案例检索");
});

test("does not let an unselected blocked import stop a selected safe server", async () => {
  const safe = draft({ displayName: "安全服务器", args: [] });
  const blocked = draft({
    displayName: "阻止服务器",
    command: "npx",
    args: ["pkg", "--token", "private-source-value"],
  });
  const parse = vi.spyOn(runtimeApi, "parseMcpImport").mockImplementation(
    async (_text, _signal, selectedNames) => {
      if (selectedNames?.[0] === "安全服务器") return { drafts: [safe] };
      if (selectedNames?.[0] === "阻止服务器") {
        throw new runtimeApi.RuntimeApiError(
          "private-source-value",
          { code: "mcp_secret_in_arguments", retryable: false, status: 403 },
        );
      }
      return { drafts: [safe] };
    },
  );
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());
  const create = vi.spyOn(runtimeApi, "createMcpServer").mockResolvedValue(record({
    displayName: "安全服务器",
  }));

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "安全服务器": { command: "node" },
          "阻止服务器": { command: "npx", args: ["pkg", "--token", "private-source-value"] },
        },
      }),
    },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "保存 阻止服务器" }));
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();
  expect(probe).toHaveBeenCalledTimes(1);
  expect(parse.mock.calls.some((call) => call[2]?.[0] === "阻止服务器")).toBe(false);

  fireEvent.click(screen.getByRole("checkbox", { name: "保存 阻止服务器" }));
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toBe("命令参数中可能包含秘密，请移除后改用秘密环境变量保存。");
  expect(alert.textContent).not.toContain("private-source-value");
  expect(probe).toHaveBeenCalledTimes(1);
  expect(parse.mock.calls.some((call) => call[2]?.[0] === "阻止服务器")).toBe(true);

  fireEvent.click(screen.getByRole("checkbox", { name: "保存 阻止服务器" }));
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[0].testReceipt).toBe("receipt-1");
});

test("tests the safe item when two selected imports contain one blocked item", async () => {
  const safe = draft({ displayName: "安全服务器", args: [] });
  vi.spyOn(runtimeApi, "parseMcpImport").mockImplementation(
    async (_text, _signal, selectedNames) => {
      if (selectedNames?.[0] === "安全服务器") return { drafts: [safe] };
      if (selectedNames?.[0] === "阻止服务器") {
        throw new runtimeApi.RuntimeApiError(
          "private-blocked-value",
          { code: "mcp_secret_in_arguments", retryable: false, status: 403 },
        );
      }
      return { drafts: [safe] };
    },
  );
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "安全服务器": { command: "node" },
          "阻止服务器": { command: "npx", args: ["pkg", "--token", "private-blocked-value"] },
        },
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();
  expect(probe).toHaveBeenCalledTimes(1);
  expect(probe.mock.calls[0]?.[0].draft.displayName).toBe("安全服务器");
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toContain("命令参数中可能包含秘密");
  expect(alert.textContent).not.toContain("private-blocked-value");
});

test("keeps testing a safe item when another edited item gains a blocked secret argument", async () => {
  const safe = draft({ displayName: "安全服务器", args: [] });
  const edited = draft({ displayName: "待修改服务器", command: "npx", args: ["pkg"] });
  vi.spyOn(runtimeApi, "parseMcpImport").mockImplementation(
    async (text, _signal, selectedNames) => {
      if (selectedNames?.[0] === "安全服务器") return { drafts: [safe] };
      if (selectedNames?.[0] === "待修改服务器") return { drafts: [edited] };
      if (text.includes("private-current-value")) {
        throw new runtimeApi.RuntimeApiError(
          "private-current-value",
          { code: "mcp_secret_in_arguments", retryable: false, status: 403 },
        );
      }
      return { drafts: [safe] };
    },
  );
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());
  const create = vi.spyOn(runtimeApi, "createMcpServer").mockResolvedValue(record({
    displayName: "安全服务器",
  }));

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "安全服务器": { command: "node" },
          "待修改服务器": { command: "npx", args: ["pkg"] },
        },
      }),
    },
  });
  fireEvent.click(screen.getAllByText("高级选项")[1]!);
  fireEvent.change(screen.getAllByLabelText("参数（每行一个）")[1]!, {
    target: { value: "pkg\n--token\nprivate-current-value" },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();
  expect(probe).toHaveBeenCalledTimes(1);
  expect(probe.mock.calls[0]?.[0].draft.displayName).toBe("安全服务器");
  const alert = screen.getByRole("alert");
  expect(alert.textContent).toBe("命令参数中可能包含秘密，请移除后改用秘密环境变量保存。");
  expect(alert.textContent).not.toContain("private-current-value");

  fireEvent.click(screen.getByRole("checkbox", { name: "保存 待修改服务器" }));
  fireEvent.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
  expect(create.mock.calls[0]?.[0].draft.displayName).toBe("安全服务器");
});

test("continues safe and acknowledged entries while another selected risk remains blocked", async () => {
  const safe = draft({ displayName: "安全服务器", args: [] });
  const warned = draft({
    displayName: "警告服务器",
    command: "uvx",
    args: [],
    risks: [{ code: "mcp_insecure_http", severity: "warn" }],
  });
  const blocked = draft({
    displayName: "阻止服务器",
    command: "deno",
    args: [],
    risks: [{ code: "mcp_config_invalid", severity: "block" }],
  });
  vi.spyOn(runtimeApi, "parseMcpImport").mockImplementation(
    async (text, _signal, selectedNames) => {
      if (selectedNames?.[0] === "安全服务器") return { drafts: [safe] };
      if (selectedNames?.[0] === "警告服务器") return { drafts: [warned] };
      if (selectedNames?.[0] === "阻止服务器") return { drafts: [blocked] };
      return {
        drafts: [safe, warned, blocked].filter((item) => text.includes(item.displayName)),
      };
    },
  );
  const probe = vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: {
      value: JSON.stringify({
        mcpServers: {
          "安全服务器": { command: "node" },
          "警告服务器": { command: "uvx" },
          "阻止服务器": { command: "deno" },
        },
      }),
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[0].draft.displayName).toBe("安全服务器");
  expect(screen.getByText("这份配置存在不安全内容，不能添加。")).toBeTruthy();

  fireEvent.click(screen.getByRole("checkbox", { name: "我了解并继续" }));
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  await waitFor(() => expect(probe).toHaveBeenCalledTimes(2));
  expect(probe.mock.calls[1]?.[0].draft.displayName).toBe("警告服务器");
});

test("edits with the saved revision and a receipt bound to the reviewed connection", async () => {
  const review = vi.spyOn(runtimeApi, "reviewMcpServerTool").mockResolvedValue({} as never);
  const existing = record();
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({ drafts: [draft()] });
  vi.spyOn(runtimeApi, "testSavedMcpServer").mockResolvedValue(testResult("edit-receipt"));
  const update = vi.spyOn(runtimeApi, "updateMcpServer").mockResolvedValue({
    ...existing,
    configRevision: 5,
  });

  render(<McpServerEditor mode="settings" server={existing} />);
  fireEvent.click(screen.getByText("高级选项"));
  fireEvent.change(screen.getByLabelText("秘密环境变量"), {
    target: { value: "API_TOKEN=replaced-secret\nOLD_TOKEN=" },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();
  fireEvent.click(screen.getByRole("checkbox", { name: /读取案例/ }));
  fireEvent.click(screen.getByRole("button", { name: "保存" }));

  await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(review).toHaveBeenCalledTimes(2));
  expect(update.mock.calls[0]?.[0]).toBe("server-1");
  expect(update.mock.calls[0]?.[1]).toMatchObject({
    expectedRevision: 4,
    testReceipt: "edit-receipt",
    secretPatch: {
      env: { API_TOKEN: "replaced-secret", OLD_TOKEN: null },
      headers: {},
    },
    toolDecisions: [
      {
        toolName: "search_cases",
        enabled: true,
        expectedDescriptionHash: HASH_A,
        expectedSchemaHash: HASH_B,
      },
      {
        toolName: "read_case",
        enabled: false,
        expectedDescriptionHash: HASH_C,
        expectedSchemaHash: HASH_D,
      },
    ],
  });
  expect(within(screen.getByRole("status")).getByText("已保存")).toBeTruthy();
});

test("clearing a staged secret deletion restores the saved secret-key baseline", async () => {
  const existing = record({ secretEnvKeys: ["API_TOKEN"] });
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({
    drafts: [draft({ secretEnvKeys: ["API_TOKEN"] })],
  });
  const probe = vi.spyOn(runtimeApi, "testSavedMcpServer").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" server={existing} />);
  fireEvent.click(screen.getByText("高级选项"));
  const secretInput = screen.getByLabelText("秘密环境变量") as HTMLTextAreaElement;
  fireEvent.change(secretInput, { target: { value: "API_TOKEN=" } });
  fireEvent.change(secretInput, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));

  await waitFor(() => expect(probe).toHaveBeenCalledTimes(1));
  expect(probe.mock.calls[0]?.[1]).toMatchObject({ patch: {}, secretPatch: { env: {}, headers: {} } });
});

test("does not save a retained receipt after the display name becomes invalid", async () => {
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({ drafts: [draft()] });
  vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  fireEvent.change(screen.getByLabelText("粘贴 MCP 配置"), {
    target: { value: WRAPPED_CONFIG },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: `bad${"x".repeat(300)}` } });
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
});

test("saves a display-name-only edit without testing or sending tool decisions", async () => {
  const existing = record();
  const update = vi.spyOn(runtimeApi, "updateMcpServer").mockResolvedValue({
    ...existing,
    displayName: "案例检索（团队）",
    configRevision: 5,
  });
  const probe = vi.spyOn(runtimeApi, "testSavedMcpServer");

  render(<McpServerEditor mode="settings" server={existing} />);
  fireEvent.change(screen.getByLabelText("名称"), {
    target: { value: "案例检索（团队）" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存" }));

  await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
  expect(update.mock.calls[0]?.[1]).toEqual({
    expectedRevision: 4,
    patch: { displayName: "案例检索（团队）" },
    secretPatch: {},
    acceptedRiskCodes: [],
    toolDecisions: [],
  });
  expect(probe).not.toHaveBeenCalled();
});

test("retests an unchanged saved server with an empty patch and does not offer a noop save", async () => {
  const existing = record();
  vi.spyOn(runtimeApi, "parseMcpImport").mockResolvedValue({ drafts: [draft()] });
  const probe = vi.spyOn(runtimeApi, "testSavedMcpServer").mockResolvedValue(testResult());
  const update = vi.spyOn(runtimeApi, "updateMcpServer");

  render(<McpServerEditor mode="settings" server={existing} />);
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByText("找到 2 个工具")).toBeTruthy();

  expect(probe.mock.calls[0]?.[1].patch).toEqual({});
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
  expect(update).not.toHaveBeenCalled();
});

test("disables edits during a test, aborts on unmount, and ignores the late response", async () => {
  let firstSignal: AbortSignal | undefined;
  let resolveFirst!: (value: { drafts: McpParsedServerDraft[] }) => void;
  vi.spyOn(runtimeApi, "parseMcpImport")
    .mockImplementationOnce((_text, signal) => {
      firstSignal = signal;
      return new Promise((resolve) => {
        resolveFirst = resolve;
      });
    })
    .mockResolvedValue({ drafts: [draft({ displayName: "新配置" })] });
  vi.spyOn(runtimeApi, "testMcpServerDraft").mockResolvedValue(testResult());

  render(<McpServerEditor mode="settings" />);
  const input = screen.getByLabelText("粘贴 MCP 配置");
  fireEvent.change(input, { target: { value: WRAPPED_CONFIG } });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(firstSignal?.aborted).toBe(false);
  expect((input as HTMLTextAreaElement).disabled).toBe(true);
  cleanup();
  expect(firstSignal?.aborted).toBe(true);
  resolveFirst({ drafts: [draft({ displayName: "迟到的旧配置" })] });

  render(<McpServerEditor mode="settings" />);
  const nextInput = screen.getByLabelText("粘贴 MCP 配置");
  fireEvent.change(nextInput, {
    target: { value: JSON.stringify({ mcpServers: { "新配置": { command: "uvx" } } }) },
  });
  fireEvent.click(screen.getByRole("button", { name: "测试并查看工具" }));
  expect(await screen.findByDisplayValue("新配置")).toBeTruthy();
  expect(screen.queryByText("迟到的旧配置")).toBeNull();
});

test('retrying failed tool review does not update the saved configuration twice', async () => {
  const existing = record();
  vi.spyOn(runtimeApi, 'parseMcpImport').mockResolvedValue({ drafts: [draft()] });
  vi.spyOn(runtimeApi, 'testSavedMcpServer').mockResolvedValue(testResult('review-retry'));
  const update = vi.spyOn(runtimeApi, 'updateMcpServer').mockResolvedValue({ ...existing, configRevision: 5 });
  const review = vi.spyOn(runtimeApi, 'reviewMcpServerTool').mockRejectedValueOnce(new Error('offline')).mockResolvedValue({} as never);
  const complete = vi.fn();
  render(<McpServerEditor mode="settings" server={existing} onComplete={complete} />);
  fireEvent.click(screen.getByText('高级选项'));
  fireEvent.change(screen.getByLabelText('秘密环境变量'), { target: { value: 'API_TOKEN=synthetic-new-value' } });
  fireEvent.click(screen.getByRole('button', { name: '测试并查看工具' }));
  await screen.findByText('找到 2 个工具');
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
  await screen.findByText('连接没有成功，请检查配置后重试。');
  expect(complete).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
  await waitFor(() => expect(complete).toHaveBeenCalledTimes(1));
  expect(update).toHaveBeenCalledTimes(1);
  expect(review).toHaveBeenCalledTimes(3);
  expect(review.mock.calls[2][2]).toMatchObject({ expectedDescriptionHash: HASH_C, expectedSchemaHash: HASH_D });
});
