import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { McpConnections } from "../src/components/McpConnections";
import { YUANDIAN_SERVICES } from "../src/mcp/yuandian";
import * as runtimeApi from "../src/runtime/api";
import type { McpServerRecord, McpToolRecord } from "../src/runtime/contracts";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function server(overrides: Partial<McpServerRecord> = {}): McpServerRecord {
  return {
    id: "server-1",
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
    configRevision: 1,
    secretRevision: 0,
    configReviewState: "reviewed",
    riskFindings: [],
    connectionStatus: "idle",
    errorCode: null,
    retryable: false,
    lastTestStatus: null,
    lastTestedAt: null,
    selectedThreadCount: 3,
    createdAt: "2026-08-29T00:00:00Z",
    updatedAt: "2026-08-29T00:00:00Z",
    ...overrides,
  };
}

function tool(
  toolName: string,
  reviewState: McpToolRecord["reviewState"],
  enabled: boolean,
  hash: string,
): McpToolRecord {
  return {
    serverId: "server-1",
    toolName,
    description: `${toolName} 说明`,
    descriptionHash: hash.repeat(64),
    inputSchema: { type: "object" },
    schemaHash: hash.toUpperCase().repeat(64).toLowerCase(),
    enabled,
    reviewState,
    firstSeenAt: "2026-08-29T00:00:00Z",
    lastSeenAt: "2026-08-29T00:00:00Z",
  };
}

function yuandianServers(): McpServerRecord[] {
  return YUANDIAN_SERVICES.map((service, index) => server({
    id: `yuandian-${index}`,
    displayName: `yuandian-${service.id}`,
    transport: "http",
    command: null,
    args: [],
    url: service.url,
    secretHeaderKeys: ["Authorization"],
    selectedThreadCount: 1,
  }));
}

test("leads with five guided recommendations and opens the Yuan Dian key form in place", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([]);

  render(<McpConnections />);

  expect(await screen.findByRole("heading", { name: "推荐" })).toBeTruthy();
  // The honest count line lives on the MCP page itself, not in the sidebar.
  expect(screen.getByText("本机 0 个 MCP 连接")).toBeTruthy();
  expect(screen.getByText("共 5 个")).toBeTruthy();
  expect(screen.getByRole("heading", { name: "法律与检索" })).toBeTruthy();
  expect(screen.getByRole("article", { name: "元典智库" })).toBeTruthy();
  expect(screen.queryByText("尚未配置 MCP 服务器。")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: "添加元典智库" }));
  expect(screen.getByLabelText("元典 API Key")).toBeTruthy();
  expect(screen.getByRole("button", { name: "确认添加" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "登录元典并获取 API Key" })).toBeTruthy();
});

test("presents the four official Yuan Dian endpoints as one installed service", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue(yuandianServers());

  render(<McpConnections />);

  const card = await screen.findByRole("article", { name: "元典智库" });
  expect(await screen.findByText("本机 4 个 MCP 连接")).toBeTruthy();
  expect(within(card).getByText("已添加")).toBeTruthy();
  expect(within(card).getByRole("button", { name: "更新元典智库密钥" })).toBeTruthy();
  expect(screen.queryByText("yuandian-law")).toBeNull();
  expect(screen.queryByText("yuandian-case")).toBeNull();
  expect(screen.queryByText("yuandian-company")).toBeNull();
  expect(screen.queryByText("yuandian-securities")).toBeNull();
});

test("removes Yuan Dian as one service while deleting every hidden endpoint", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue(yuandianServers());
  const remove = vi.spyOn(runtimeApi, "deleteMcpServer").mockResolvedValue({
    deleted: true,
    selectedThreadCount: 1,
  });

  render(<McpConnections />);

  expect(await screen.findByText("已添加")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "移除元典智库" }));
  const dialog = screen.getByRole("dialog", { name: "删除 元典智库" });
  expect(dialog.textContent).toContain("连接配置会全部删除");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

  await waitFor(() => expect(remove).toHaveBeenCalledTimes(YUANDIAN_SERVICES.length));
  expect(remove.mock.calls.map(([serverId]) => serverId)).toEqual(
    yuandianServers().map((item) => item.id),
  );
  expect(screen.getByRole("button", { name: "添加元典智库" })).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("已移除元典智库");
});

test("does not delete before confirmation, shows the affected count, and treats DELETE count as final", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([server()]);
  const remove = vi.spyOn(runtimeApi, "deleteMcpServer").mockResolvedValue({
    deleted: true,
    selectedThreadCount: 4,
  });

  render(<McpConnections />);
  expect(await screen.findByText("案例检索")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "删除 案例检索" }));

  expect(remove).not.toHaveBeenCalled();
  const dialog = screen.getByRole("dialog", { name: "删除 案例检索" });
  expect(dialog.textContent).toContain("3 个对话");
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

  await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
  expect(screen.queryByText("案例检索")).toBeNull();
  expect(screen.getByRole("status").textContent).toBe("已删除服务器，并从 4 个对话中移除。");
});

test("focuses the safe delete action, closes with Escape, and returns focus to its trigger", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([server()]);
  vi.spyOn(runtimeApi, "deleteMcpServer");

  render(<McpConnections />);
  expect(await screen.findByText("案例检索")).toBeTruthy();
  const trigger = screen.getByRole("button", { name: "删除 案例检索" });
  trigger.focus();
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "删除 案例检索" });
  await waitFor(() => expect(document.activeElement).toBe(
    within(dialog).getByRole("button", { name: "取消" }),
  ));
  fireEvent.keyDown(dialog, { key: "Escape" });

  expect(screen.queryByRole("dialog", { name: "删除 案例检索" })).toBeNull();
  expect(document.activeElement).toBe(trigger);
});

test("keeps the delete dialog locked after DELETE is submitted", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([server()]);
  let rejectDelete!: (reason: unknown) => void;
  vi.spyOn(runtimeApi, "deleteMcpServer").mockImplementation(
    () => new Promise((_resolve, reject) => { rejectDelete = reject; }),
  );

  render(<McpConnections />);
  expect(await screen.findByText("案例检索")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "删除 案例检索" }));
  const dialog = screen.getByRole("dialog", { name: "删除 案例检索" });
  fireEvent.click(within(dialog).getByRole("button", { name: "确认删除" }));

  expect((within(dialog).getByRole("button", { name: "取消" }) as HTMLButtonElement).disabled).toBe(true);
  expect((within(dialog).getByRole("button", { name: "正在删除…" }) as HTMLButtonElement).disabled).toBe(true);
  expect(within(dialog).getByRole("status").textContent).toContain("正在删除");
  await waitFor(() => expect(document.activeElement).toBe(dialog));
  fireEvent.keyDown(dialog, { key: "Escape" });
  fireEvent.keyDown(dialog, { key: "Tab" });
  expect(screen.getByRole("dialog", { name: "删除 案例检索" })).toBeTruthy();
  expect(document.activeElement).toBe(dialog);

  rejectDelete(new runtimeApi.RuntimeApiError(
    "private delete details",
    { code: "mcp_delete_failed", retryable: true, status: 500 },
  ));
  await waitFor(() => expect(document.activeElement).toBe(
    within(dialog).getByRole("button", { name: "取消" }),
  ));
  expect((within(dialog).getByRole("button", { name: "确认删除" }) as HTMLButtonElement).disabled).toBe(false);
  expect(within(dialog).getByRole("alert").textContent).not.toContain("private delete details");
});

test("removes a stale row when DELETE reports the server was already absent", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([server()]);
  vi.spyOn(runtimeApi, "deleteMcpServer").mockResolvedValue({
    deleted: false,
    selectedThreadCount: 2,
  });

  render(<McpConnections />);
  expect(await screen.findByText("案例检索")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "删除 案例检索" }));
  fireEvent.click(screen.getByRole("button", { name: "确认删除" }));

  await waitFor(() => expect(screen.queryByText("案例检索")).toBeNull());
  expect(screen.getByRole("status").textContent).toBe(
    "服务器已不存在，已刷新列表；已从 2 个对话中移除。",
  );
});

test("puts new and changed tools first and reviews with both current hashes", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([server()]);
  vi.spyOn(runtimeApi, "listMcpServerTools").mockResolvedValue([
    tool("known_tool", "known", true, "a"),
    tool("changed_tool", "changed", false, "b"),
    tool("new_tool", "new", false, "c"),
  ]);
  const review = vi.spyOn(runtimeApi, "reviewMcpServerTool").mockImplementation(
    async (_serverId, toolName, input) => tool(toolName, "known", input.enabled, toolName === "changed_tool" ? "b" : "c"),
  );

  render(<McpConnections />);
  expect(await screen.findByText("案例检索")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看 案例检索 的工具" }));
  const list = await screen.findByRole("list", { name: "案例检索 的工具" });
  const rows = within(list).getAllByRole("listitem");
  expect(rows.map((row) => row.querySelector("strong")?.textContent)).toEqual([
    "changed_tool",
    "new_tool",
    "known_tool",
  ]);

  fireEvent.click(within(rows[0]!).getByRole("checkbox", { name: "启用 changed_tool" }));
  await waitFor(() => expect(review).toHaveBeenCalledTimes(1));
  expect(review.mock.calls[0]).toEqual([
    "server-1",
    "changed_tool",
    {
      enabled: true,
      expectedDescriptionHash: "b".repeat(64),
      expectedSchemaHash: "b".repeat(64),
    },
    expect.any(AbortSignal),
  ]);
  await waitFor(() => expect(within(list).getAllByRole("listitem")
    .map((row) => row.querySelector("strong")?.textContent)).toEqual([
    "new_tool",
    "changed_tool",
    "known_tool",
  ]));
});

test("does not cancel an in-flight tool review with a second tool change", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([server()]);
  vi.spyOn(runtimeApi, "listMcpServerTools").mockResolvedValue([
    tool("first_tool", "new", false, "a"),
    tool("second_tool", "new", false, "b"),
  ]);
  let resolveReview!: (value: McpToolRecord) => void;
  const review = vi.spyOn(runtimeApi, "reviewMcpServerTool").mockImplementation(
    () => new Promise((resolve) => { resolveReview = resolve; }),
  );

  render(<McpConnections />);
  expect(await screen.findByText("案例检索")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "查看 案例检索 的工具" }));
  const first = await screen.findByRole("checkbox", { name: "启用 first_tool" });
  const second = screen.getByRole("checkbox", { name: "启用 second_tool" });
  fireEvent.click(first);
  await waitFor(() => expect((second as HTMLInputElement).disabled).toBe(true));
  fireEvent.click(second);
  expect(review).toHaveBeenCalledTimes(1);

  resolveReview(tool("first_tool", "known", true, "a"));
  await waitFor(() => expect((second as HTMLInputElement).disabled).toBe(false));
});

test("filters saved servers by name locally and clearing the search restores them", async () => {
  const list = vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([
    server(),
    server({ id: "server-2", displayName: "法规查询" }),
  ]);

  render(<McpConnections />);
  expect(await screen.findByText("案例检索")).toBeTruthy();
  expect(screen.getByText("法规查询")).toBeTruthy();
  const search = screen.getByRole("searchbox", { name: "搜索 MCP 服务器" });
  fireEvent.change(search, { target: { value: "法规" } });
  expect(screen.queryByText("案例检索")).toBeNull();
  expect(screen.getByText("法规查询")).toBeTruthy();

  fireEvent.change(search, { target: { value: "不存在" } });
  expect(screen.getByText("没有找到匹配的 MCP 服务器。")).toBeTruthy();
  fireEvent.change(search, { target: { value: "" } });
  expect(screen.getByText("案例检索")).toBeTruthy();
  expect(screen.getByText("法规查询")).toBeTruthy();
  expect(list).toHaveBeenCalledTimes(1);
});

test("opens one same-page editor for add and edit without a global start switch", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockResolvedValue([server()]);
  render(<McpConnections />);

  expect(await screen.findByText("案例检索")).toBeTruthy();
  expect(screen.queryByRole("checkbox", { name: /启动|全局启用/ })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "添加其他 MCP" }));
  expect(screen.getByRole("textbox", { name: "粘贴 MCP 配置" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));

  fireEvent.click(screen.getByRole("button", { name: "编辑 案例检索" }));
  expect(screen.getByDisplayValue("案例检索")).toBeTruthy();
});

test("maps unknown list and tool failures to fixed copy without raw Runtime text", async () => {
  vi.spyOn(runtimeApi, "listMcpServers").mockRejectedValue(new runtimeApi.RuntimeApiError(
    "node --token secret https://private.test",
    { code: "unknown_internal", retryable: false, status: 500 },
  ));

  render(<McpConnections />);
  const alert = await screen.findByRole("alert");
  expect(alert.textContent).toBe("连接没有成功，请检查配置后重试。");
  expect(alert.textContent).not.toContain("node");
  expect(alert.textContent).not.toContain("secret");
  expect(alert.textContent).not.toContain("private.test");
});

test("aborts the settings list request when the panel unmounts", () => {
  let signal: AbortSignal | undefined;
  vi.spyOn(runtimeApi, "listMcpServers").mockImplementation((received) => {
    signal = received;
    return new Promise(() => undefined);
  });

  const view = render(<McpConnections />);
  expect(signal?.aborted).toBe(false);
  view.unmount();
  expect(signal?.aborted).toBe(true);
});
